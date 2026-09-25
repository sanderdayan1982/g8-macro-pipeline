"""freshness — motor de frescura F3 (en paralelo; no avisa ni sustituye al motor oficial). stdlib, Python ≥ 3.9.

Dos ejes que nunca se mezclan:
  A · estado de CALENDARIO por fichero de salida (qué observación es exigible ahora y si está):
      CURRENT               la fecha máxima publicada ≥ la última observación exigible
      NO_PUBLICATION        como CURRENT, y hoy (día local de la salida) no es día de publicación
      DUE                   pasó la hora esperada de publicación, aún dentro del margen G, y falta la observación
      PENDING_TIME_UNKNOWN  hora desconocida: estamos en el día local de publicación y la observación aún no está
      OVERDUE               falta exactamente la última observación exigible
      STALE                 faltan ≥ 2 observaciones exigibles
      BEHIND_INPUTS         derivado: su salida no cubre lo que sus entradas del repo permitían en la última FINAL
      UNKNOWN_SCHEDULE      sin observación exigible documentada (evento sin calendario, serie dispersa…)
      NOT_MONITORED         fuera de vigilancia por decisión documentada (terminada, manual, excluida…)
      NO_OBS_DATE           el fichero no tiene fecha de observación legible
      INPUT_REVISED         derivado: una entrada PUBLICADA se revisó después de su última escritura (posible desactualización)
      INPUT_REVISION_UNRESOLVED  derivado: hay revisión publicada de una entrada y no se sabe si fue antes o después
  Causa de un atraso (C3-1), de lo EJECUTADO y nunca de un cron programado: SIN_PASADA_PROGRAMADA (configuración),
  PROGRAMADA_SIN_EJECUCION_REGISTRADA (incierta: cron retrasado/omitido o sin evidencia), EJECUTADA_CON_FALLO,
  EJECUTADA_SIN_DESCARGA_DEL_FICHERO, CONSULTADA_SIN_NOVEDAD, RECIBIDA_RETENIDA (registradas). RULE_PROVISIONAL =
  la regla no es oficial.
  B · HECHOS de ingestión (último intento, resultado, errores, candidatos retenidos): se adjuntan y ningún estado
      del eje A los borra.

Reglas (sources/freshness_rules.csv + freshness_params.csv + freshness_outputs.csv):
  · bd_offset: la observación del día hábil D se publica el día hábil P = D + pub_offset_bd (calendario de la salida).
      – Con hora (pub_local): DUE desde P a esa hora, exigible desde esa hora + G.
      – Sin hora: PENDING_TIME_UNKNOWN durante el día local P; exigible al terminar P. NUNCA un día extra.
  · weekly_before:N — publicación semanal (frequency weekly:XXX) con datos hasta el último día hábil ≤ P − N días.
  · prev_month_end — publicación el primer día hábil del mes con datos hasta el último día hábil del mes anterior.
  · derived — exigible = mínimo de las fechas máximas de sus entradas MOTORAS (columna drivers; por defecto todas)
    del repo en la última pasada FINAL
    (hora programada + G); además marca INPUT_VERSION_UNKNOWN (los derivados no registran qué versión usaron) y
    INPUTS_REVISED_AFTER si hay evidencia de una revisión de una entrada posterior a la última escritura.
  · event / none — UNKNOWN_SCHEDULE (sin obligación inventada). · not_monitored — NOT_MONITORED con su motivo.
G solo gobierna DUE → OVERDUE: no retrasa ninguna consulta ni hace CURRENT un dato que falta.
"""
import csv
import os
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import schedule as SC

UTC = timezone.utc
LOOKBACK_BD = 40                     # observaciones exigibles que se miran hacia atrás (≥ 2 meses de días hábiles)
FINAL_CRON = (21, 30)                # pasada FINAL programada (UTC), días laborables — acta S01B, no se toca
LATE_STATES = ("OVERDUE", "STALE", "BEHIND_INPUTS")
# no saludables sin ser un atraso de calendario: entrada publicada revisada después (o sin orden conocido)
UNHEALTHY_STATES = LATE_STATES + ("INPUT_REVISED", "INPUT_REVISION_UNRESOLVED")


def _rows(root, name):
    with open(os.path.join(root, "sources", name), encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_config(root):
    rules = {r["rule_id"]: r for r in _rows(root, "freshness_rules.csv")}
    params = {r["rule_id"]: r for r in _rows(root, "freshness_params.csv")}
    outputs = _rows(root, "freshness_outputs.csv")
    return rules, params, outputs, SC.load_calendars(root)


# ── fechas de observación de un fichero ─────────────────────────────────────────────────────────────────────────
def _parse_date(s):
    s = (s or "").strip().replace("-", "")[:8]
    if len(s) == 8 and s.isdigit():
        try:
            return date(int(s[:4]), int(s[4:6]), int(s[6:]))
        except ValueError:
            return None
    return None


def obs_dates_from_bytes(data, date_key=""):
    """Conjunto de fechas de observación de un CSV (primera columna) o de un JSON (clave date_key)."""
    if data is None:
        return None
    if date_key:
        import json
        try:
            d = _parse_date(str(json.loads(data.decode("utf-8")).get(date_key, "")))
        except (ValueError, AttributeError):
            return set()
        return {d} if d else set()
    out = set()
    for line in data.decode("utf-8", errors="ignore").splitlines():
        d = _parse_date(line.split(",", 1)[0])
        if d:
            out.add(d)
    return out


# ── calendario ───────────────────────────────────────────────────────────────────────────────────────────────────
def _bd(cals, cal, d):
    return SC.is_bd(cals, cal, d)


def _prev_bd(cals, cal, d):
    d -= timedelta(days=1)
    while not _bd(cals, cal, d):
        d -= timedelta(days=1)
    return d


def _next_bd(cals, cal, d):
    d += timedelta(days=1)
    while not _bd(cals, cal, d):
        d += timedelta(days=1)
    return d


def _last_bd_on_or_before(cals, cal, d):
    while not _bd(cals, cal, d):
        d -= timedelta(days=1)
    return d


def _local_midnight_utc(d, tz):
    return datetime.combine(d, time(0, 0), tzinfo=ZoneInfo(tz)).astimezone(UTC)


def _at_local(d, hhmm, tz):
    hh, mm = [int(x) for x in hhmm.split(":")]
    return datetime.combine(d, time(hh, mm), tzinfo=ZoneInfo(tz)).astimezone(UTC)


def publications(rule, param, cal, tz, cals, now):
    """Lista de (obs_date, due_utc, exigible_utc) de las publicaciones cuyo due_utc ≤ now, de la más reciente a la
    más antigua (LOOKBACK_BD como máximo). due = empieza a esperarse; exigible = su falta ya es un atraso."""
    model = param["model"]
    pub_local = (rule.get("pub_local") or "").strip()
    g = timedelta(minutes=int(param["g_min"])) if (param.get("g_min") or "").strip() else timedelta(0)
    today_local = now.astimezone(ZoneInfo(tz)).date()
    out = []
    if model == "bd_offset":
        k = int(rule.get("pub_offset_bd") or 0)
        p = _last_bd_on_or_before(cals, cal, today_local + timedelta(days=1))
        while len(out) < LOOKBACK_BD:
            if _bd(cals, cal, p):
                d = p
                for _ in range(k):
                    d = _prev_bd(cals, cal, d)
                if pub_local:
                    due = _at_local(p, pub_local, tz)
                    exig = due + g
                else:
                    due = _local_midnight_utc(p, tz)
                    exig = _local_midnight_utc(p + timedelta(days=1), tz)
                if due <= now:
                    out.append((d, due, exig))
            p -= timedelta(days=1)
        return out
    if model.startswith("weekly_before:") or model == "prev_month_end":
        start = today_local - timedelta(days=400)
        nominal = SC.publication_days(rule, cals, start, today_local + timedelta(days=7))
        pairs = [(p, p) for p in nominal]
        if model.startswith("weekly_before:"):
            # SUPUESTO PROVISIONAL (documentado): una publicación semanal que cae en festivo de su calendario se
            # desplaza al siguiente día hábil (schedule.publication_days no mira festivos en las semanales). La
            # observación exigible sigue anclada al día NOMINAL (p. ej. «datos hasta el miércoles»).
            pairs = [(p, p if _bd(cals, cal, p) else _next_bd(cals, cal, p)) for p in nominal]
        pairs = [(n, p) for n, p in pairs if p <= today_local]
        for n, p in reversed(pairs):
            if model == "prev_month_end":
                d = _last_bd_on_or_before(cals, cal, date(n.year, n.month, 1) - timedelta(days=1))
            else:
                d = _last_bd_on_or_before(cals, cal, n - timedelta(days=int(model.split(":")[1])))
            due = _at_local(p, pub_local, tz) if pub_local else _local_midnight_utc(p, tz)
            exig = due + g if pub_local else _local_midnight_utc(p + timedelta(days=1), tz)
            if due <= now:
                out.append((d, due, exig))
            if len(out) >= LOOKBACK_BD:
                break
        return out
    raise ValueError("modelo sin publicaciones: %s" % model)


def is_publication_day(rule, param, cal, cals, d):
    model = param["model"]
    if model == "bd_offset":
        return _bd(cals, cal, d)
    if model == "prev_month_end":
        return d in SC.publication_days(rule, cals, d, d)
    if model.startswith("weekly_before:"):
        wk = SC.publication_days(rule, cals, d - timedelta(days=7), d)
        return d in {p if _bd(cals, cal, p) else _next_bd(cals, cal, p) for p in wk}
    return False


def last_final(now):
    """Última pasada FINAL programada (UTC, lunes–viernes) ≤ now."""
    t = datetime(now.year, now.month, now.day, FINAL_CRON[0], FINAL_CRON[1], tzinfo=UTC)
    while t > now or t.weekday() >= 5:
        t -= timedelta(days=1)
        t = t.replace(hour=FINAL_CRON[0], minute=FINAL_CRON[1])
    return t


# ── evaluación de una salida ────────────────────────────────────────────────────────────────────────────────────
def scheduled_queries(rule, passes, since, until):
    """Pasadas ACTIVAS de los grupos de consulta de la regla programadas en [since, until] (cron programado, no la
    ejecución real: GitHub puede retrasarlas; la ejecución real está en el historial de evidencia)."""
    groups = set(filter(None, (rule.get("query_groups") or "").split("|")))
    out = []
    for p in passes or []:
        if p.get("status") == "ACTIVE" and p.get("group") in groups:
            out += [(p["pass_id"], t) for t in SC.pass_times(p, since, until)]
    return sorted(out, key=lambda x: x[1])


def _ts(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def classify_cause(fname, obs, due_first, now, scheduled, runs, downloads, evidence_available):
    """C3-1 · causa del atraso de `obs` a partir de lo EJECUTADO, nunca de un cron programado.
    Niveles separados: consulta programada → intento ejecutado → respuesta/descarga correcta → dato recibido →
    dato publicado o retenido. Devuelve (causa, certeza, detalle)."""
    since = _ts(due_first)
    obs_iso = obs.isoformat()
    ran = [r for r in runs if (r.get("started_utc") or r.get("finished_utc") or "") >= since]
    dls = [r for r in downloads if r.get("ok") and (r.get("query_utc") or "") >= since]
    detail = {"scheduled_passes": [(pid, t.strftime("%Y-%m-%dT%H:%MZ")) for pid, t in scheduled],
              "executed_runs": [{"source": r.get("source"), "run_id": r.get("run_id"), "started_utc": r.get("started_utc"),
                                 "rc": r.get("rc"), "file": (r.get("files") or {}).get(fname)} for r in ran][-5:],
              "downloads": len(dls), "evidence_available": evidence_available}
    # 1) dato recibido pero no publicado (retenido/inválido)
    for r in dls:
        if any(o.get("date") == obs_iso and not o.get("accepted") for o in r.get("obs", [])):
            return "RECIBIDA_RETENIDA", "registrada", detail
    for r in ran:
        f = (r.get("files") or {}).get(fname) or {}
        if f.get("src_max") and f["src_max"] >= obs_iso and not f.get("written") and f.get("status") != "PUBLISH":
            return "RECIBIDA_RETENIDA", "registrada", detail
    # 2) consulta correcta sin la observación (la fuente aún no la tenía en el endpoint consultado)
    if any((r.get("src_max") or "") < obs_iso for r in dls):
        return "CONSULTADA_SIN_NOVEDAD", "registrada", detail
    for r in ran:
        f = (r.get("files") or {}).get(fname) or {}
        if f.get("src_max") and f["src_max"] < obs_iso and f.get("status") not in ("INVALID", None):
            return "CONSULTADA_SIN_NOVEDAD", "registrada", detail
    # 3) intento ejecutado que falló o se aplazó
    for r in ran:
        bad_req = [q for q in (r.get("requests") or []) if q.get("cls") not in (None, "OK")]
        if (r.get("rc") not in (0, "0", None)) or bad_req or r.get("status") in ("FAILED", "TIMEOUT", "ABORTED", "SKIPPED_NO_TIME"):
            return "EJECUTADA_CON_FALLO", "registrada", detail
    if ran:
        return "EJECUTADA_SIN_DESCARGA_DEL_FICHERO", "registrada", detail
    # 4) nada ejecutado registrado
    if scheduled:
        # cron retrasado u omitido, o evidencia no disponible (reproducción histórica): NO se atribuye a la fuente
        return "PROGRAMADA_SIN_EJECUCION_REGISTRADA", "incierta", detail
    return "SIN_PASADA_PROGRAMADA", "configuracion", detail


def evaluate(output, rule, param, cals, now, have_dates, input_max_at=None, evidence=None, facts=None, passes=None,
             runs=None, evidence_available=True, written_at=None):
    """→ dict con el estado del eje A, sus fundamentos y los hechos del eje B.
    have_dates: fechas de observación presentes en la salida (None = fichero ausente).
    input_max_at(file, instant) → fecha máxima de esa entrada en ese instante (o None si no se puede saber).
    evidence: historial de descargas de la salida y de sus entradas ({file: [registros]}).
    runs: ejecuciones REALES registradas de las fuentes de la salida (Actions, g8step, latido del Mac).
    evidence_available: False en reproducciones históricas (no existía el historial): la causa queda incierta.
    written_at: instante de la última escritura publicada de la salida (derivados), o None si se desconoce."""
    model = param["model"]
    cal = output.get("calendar") or rule.get("calendar") or ""
    tz = output.get("timezone") or rule.get("timezone") or "UTC"
    res = {"file": output["file"], "rule_id": rule["rule_id"], "model": model, "calendar": cal, "timezone": tz,
           "basis": rule.get("status", ""), "g_min": param.get("g_min") or None, "g_status": param.get("g_status"),
           "have_max": None, "expected_obs": None, "missing": [], "flags": [], "facts": facts or {}}
    if have_dates is not None and have_dates:
        res["have_max"] = max(have_dates).isoformat()
    if model == "not_monitored":
        res.update(state="NOT_MONITORED", reason=param.get("g_basis", ""))
        return res
    if have_dates is None:
        res.update(state="NO_OBS_DATE", reason="fichero ausente")
        return res
    if not have_dates:
        res.update(state="NO_OBS_DATE", reason="sin fecha de observación legible")
        return res
    have_max = max(have_dates)
    if model in ("event", "none"):
        res.update(state="UNKNOWN_SCHEDULE", reason=param.get("g_basis", ""),
                   age_days=(now.date() - have_max).days)
        return res
    if model == "derived":
        return _evaluate_derived(res, output, param, now, have_max, input_max_at, evidence, written_at)

    pubs = publications(rule, param, cal, tz, cals, now)
    exigible = [(d, due, ex) for d, due, ex in pubs if ex <= now]
    pending = [(d, due, ex) for d, due, ex in pubs if ex > now]
    missing = [d for d, _, _ in exigible if d > have_max]
    res["expected_obs"] = exigible[0][0].isoformat() if exigible else None
    res["missing"] = sorted({d.isoformat() for d in missing})
    if len(set(missing)) >= 2:
        state = "STALE"
    elif missing:
        state = "OVERDUE"
    elif pending and pending[0][0] > have_max:
        state = "DUE" if (rule.get("pub_local") or "").strip() else "PENDING_TIME_UNKNOWN"
        res["due_obs"] = pending[0][0].isoformat()
        res["due_since"] = pending[0][1].strftime("%Y-%m-%dT%H:%M:%SZ")
        res["overdue_at"] = pending[0][2].strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        today_local = now.astimezone(ZoneInfo(tz)).date()
        state = "CURRENT" if is_publication_day(rule, param, cal, cals, today_local) else "NO_PUBLICATION"
    if missing:
        first = min(missing)
        res["overdue_since"] = min(ex for d, _, ex in exigible if d == first).strftime("%Y-%m-%dT%H:%M:%SZ")
        due_first = min(due for d, due, _ in exigible if d == first)
        sq = scheduled_queries(rule, passes, due_first, now)
        cause, certainty, detail = classify_cause(output["file"], first, due_first, now, sq, runs or [],
                                                  (evidence or {}).get(output["file"], []), evidence_available)
        res.update(cause=cause, cause_certainty=certainty, cause_detail=detail)
        res["flags"].append("CAUSA_" + cause)
        if not evidence_available:
            res["flags"].append("EVIDENCIA_NO_DISPONIBLE")
    res["state"] = state
    if (rule.get("status") or "") != "OFICIAL_GENERAL" and state in LATE_STATES:
        res["flags"].append("RULE_PROVISIONAL")          # atraso según una regla no oficial: no «demostrado»
    return res


def _evaluate_derived(res, output, param, now, have_max, input_max_at, evidence, written_at=None):
    inputs = [x for x in (output.get("inputs") or "").split(";") if x]
    res["flags"].append("INPUT_VERSION_UNKNOWN")         # los derivados no registran qué versión de entradas usaron
    if output.get("external_inputs"):
        res["flags"].append("EXTERNAL_INPUTS_UNVERSIONED")
    if not inputs or input_max_at is None:
        res.update(state="UNKNOWN_SCHEDULE", reason="derivado sin entradas del repo evaluables")
        return res
    g = timedelta(minutes=int(param["g_min"])) if (param.get("g_min") or "").strip() else None
    if g is None:
        # sin hora exigible documentada (METALS): solo se compara con las entradas actuales, sin OVERDUE
        cur = [input_max_at(f, now) for f in inputs]
        res["inputs_max"] = {f: (d.isoformat() if d else None) for f, d in zip(inputs, cur)}
        known = [d for d in cur if d]
        res.update(state="UNKNOWN_SCHEDULE", reason="derivado sin hora exigible documentada",
                   covers_inputs=bool(known) and have_max >= min(known))
        return res
    drivers = [x for x in (output.get("drivers") or "").split(";") if x] or inputs
    fin = last_final(now)
    ref = fin if fin + g <= now else last_final(fin - timedelta(minutes=1))
    at_ref = [input_max_at(f, ref) for f in drivers]      # la fecha exigible la marcan las entradas «motoras»
    res["inputs_max_at_final"] = {f: (d.isoformat() if d else None) for f, d in zip(drivers, at_ref)}
    res["final_ref"] = ref.strftime("%Y-%m-%dT%H:%M:%SZ")
    if any(d is None for d in at_ref):
        res["flags"].append("INPUT_TIMING_UNKNOWN")
    known = [d for d in at_ref if d]
    if not known:
        res.update(state="UNKNOWN_SCHEDULE", reason="fechas de las entradas desconocidas")
        return res
    need = min(known)
    res["expected_obs"] = need.isoformat()
    if have_max >= need:
        res["state"] = "CURRENT"
    else:
        res["state"] = "BEHIND_INPUTS"
        res["missing"] = [need.isoformat()]
    # C3-3 · revisiones de las entradas: observada ≠ aceptada ≠ publicada ≠ consumida por el derivado
    ev = evidence or {}
    if written_at is None:                               # sin git: última escritura registrada de la propia salida
        w = [r.get("query_utc") for r in ev.get(output["file"], []) if r.get("wrote") and r.get("query_utc")]
        if w:
            written_at = datetime.strptime(max(w), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            res["written_at_source"] = "evidencia de la salida"
    res["written_at"] = _ts(written_at) if written_at else None
    published, held = [], []
    for f in inputs:
        for r in ev.get(f, []):
            when = r.get("query_utc", "")
            if r.get("obs_complete") is False:
                for rg in r.get("obs_ranges", []):
                    if rg["kind"] == "revision" and rg["from"] <= have_max.isoformat():
                        (published if r.get("wrote") else held).append(
                            {"file": f, "dates": [rg["from"], rg["to"]], "seen_utc": when, "complete": False})
                continue
            for o in r.get("obs", []):
                if o.get("kind") != "revision" or o.get("date", "9999") > have_max.isoformat():
                    continue
                item = {"file": f, "date": o["date"], "version": o.get("version"), "seen_utc": when}
                (published if (r.get("wrote") and o.get("accepted")) else held).append(item)
    if held:
        res["flags"].append("INPUT_REVISION_HELD")        # candidata visible: NO cambió la entrada publicada
        res["held_input_revisions"] = held[-20:]
    if any(x.get("complete") is False for x in published + held):
        res["flags"].append("EVIDENCE_INCOMPLETE")
    if published:
        if written_at is None:
            after, order = published, "desconocido"
        else:
            after = [x for x in published if x["seen_utc"] > _ts(written_at)]
            order = "posterior"
        if after:
            res["published_input_revisions"] = after[-20:]
            res["revision_order"] = order
            if res["state"] == "CURRENT":
                # nunca «al día»: una entrada publicada cambió después (o no se sabe si antes) de calcular la salida
                res["state"] = "INPUT_REVISED" if order == "posterior" else "INPUT_REVISION_UNRESOLVED"
            res["flags"].append("INPUTS_REVISED_AFTER" if order == "posterior" else "INPUT_REVISION_ORDER_UNKNOWN")
        else:
            res["revisions_consumed"] = len(published)   # la salida se recalculó después: estado cerrado
    return res


# ── evidencia: intervalo de disponibilidad observado ───────────────────────────────────────────────────────────
def availability(records, obs_date, version=None):
    """C3-4 · intervalo de disponibilidad OBSERVADO en el endpoint consultado para (fecha, versión):
    (última consulta correcta en la que la fecha no estaba o tenía OTRA versión, primera consulta correcta con esa
    versión]. version=None → la versión más reciente vista de esa fecha. Devuelve también todos los episodios
    (una versión puede volver). Las consultas fallidas no fijan extremos; un extremo desconocido queda en None.
    La versión presente en una consulta sin cambios (NOOP) es la publicada en ese momento: se conoce desde la
    primera observación registrada o, hacia atrás, por la versión previa que declara la primera revisión."""
    recs = sorted((r for r in records if r.get("ok")), key=lambda x: x.get("query_utc", ""))
    limitation = None
    seen = []                                           # [(query_utc, versión presente o None si ausente/desconocida)]
    published = None                                    # versión publicada de la fecha tras cada consulta
    pending_unknown = []
    for r in recs:
        if r.get("obs_complete") is False and any(rg["from"] <= obs_date <= rg["to"] for rg in r.get("obs_ranges", [])):
            limitation = "evidencia sin versión por fecha en %s (%s)" % (r.get("query_utc"), r.get("obs_limitation", ""))
            seen.append((r["query_utc"], "?"))
            continue
        entry = next((o for o in r.get("obs", []) if o.get("date") == obs_date), None)
        if (r.get("src_max") or "") < obs_date and entry is None:
            seen.append((r["query_utc"], None))         # la fecha aún no estaba en la descarga
            continue
        if entry is not None:
            if entry.get("kind") == "revision" and published is None and entry.get("previous_version"):
                for k in pending_unknown:               # la versión previa declarada resuelve las NOOP anteriores
                    seen[k] = (seen[k][0], entry["previous_version"])
                pending_unknown = []
            seen.append((r["query_utc"], entry.get("version")))
            if r.get("wrote") and entry.get("accepted"):
                published = entry.get("version")
        else:
            # NOOP: la descarga coincidía con lo publicado; "~" = presente con versión aún desconocida
            seen.append((r["query_utc"], published or "~"))
            if published is None:
                pending_unknown.append(len(seen) - 1)
    episodes = []
    prev = ("__start__", None)
    for q, v in seen:
        if v not in (None, "?") and v != prev[1]:
            lower = prev[0] if prev[0] != "__start__" and prev[1] != "?" else None
            episodes.append({"version": None if v == "~" else v, "lower_bound": lower, "upper_bound": q,
                             "version_known": v != "~"})
        prev = (q, v)
    target = version or (episodes[-1]["version"] if episodes else None)
    mine = [e for e in episodes if e["version"] == target] if (version or not episodes) else episodes[-1:]
    ep = mine[-1] if mine else {"version": target, "lower_bound": None, "upper_bound": None}
    if ep.get("version_known") is False and limitation is None:
        limitation = "versión de la fecha desconocida en ese episodio (registro sin versión por fecha)"
    return {"obs_date": obs_date, "version": target, "lower_bound": ep["lower_bound"], "upper_bound": ep["upper_bound"],
            "episodes": episodes, "limitation": limitation,
            "scope": "endpoint consultado; no demuestra la publicación en otros sistemas del proveedor"}

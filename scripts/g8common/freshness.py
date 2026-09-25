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
  Banderas de atraso: SIN_CONSULTA_DESDE_PUBLICACION = ninguna pasada activa (ni consulta real registrada) desde
  la publicación esperada: latencia de la PROGRAMACIÓN, no fallo de la fuente. CONSULTADA_Y_FALTA = hubo consulta
  (programada o real) y la observación sigue faltando. RULE_PROVISIONAL = la regla no es oficial.
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


def evaluate(output, rule, param, cals, now, have_dates, input_max_at=None, evidence=None, facts=None, passes=None):
    """→ dict con el estado del eje A, sus fundamentos y los hechos del eje B.
    have_dates: fechas de observación presentes en la salida (None = fichero ausente).
    input_max_at(file, instant) → fecha máxima de esa entrada en ese instante (o None si no se puede saber).
    evidence: registros del historial de evidencia de la salida y de sus entradas ({file: [registros]})."""
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
        return _evaluate_derived(res, output, param, now, have_max, input_max_at, evidence)

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
        res["scheduled_passes_since_due"] = [(pid, t.strftime("%Y-%m-%dT%H:%MZ")) for pid, t in sq]
        # consultas REALES (historial de evidencia): descargas correctas posteriores a la publicación esperada
        real = [r for r in (evidence or {}).get(output["file"], [])
                if r.get("ok") and r.get("query_utc", "") >= due_first.strftime("%Y-%m-%dT%H:%M:%SZ")]
        res["real_queries_since_due"] = len(real)
        if not sq and not real:
            res["flags"].append("SIN_CONSULTA_DESDE_PUBLICACION")   # latencia de la programación, no fallo de fuente
        else:
            res["flags"].append("CONSULTADA_Y_FALTA")               # hubo (al menos programada) consulta y falta
    res["state"] = state
    if (rule.get("status") or "") != "OFICIAL_GENERAL" and state in LATE_STATES:
        res["flags"].append("RULE_PROVISIONAL")          # atraso según una regla no oficial: no «demostrado»
    return res


def _evaluate_derived(res, output, param, now, have_max, input_max_at, evidence):
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
    # revisiones de una entrada posteriores a la última escritura conocida de la salida
    ev = evidence or {}
    wrote = [r.get("query_utc") for r in ev.get(output["file"], []) if r.get("wrote")]
    last_write = max(wrote) if wrote else None
    for f in inputs:
        for r in ev.get(f, []):
            for o in r.get("obs", []):
                if o.get("kind") == "revision" and o.get("date", "9999") <= have_max.isoformat() and \
                        (last_write is None or r.get("query_utc", "") > last_write):
                    if "INPUTS_REVISED_AFTER" not in res["flags"]:
                        res["flags"].append("INPUTS_REVISED_AFTER")
                    res.setdefault("revised_inputs", []).append({"file": f, "date": o["date"],
                                                                 "seen_utc": r.get("query_utc")})
    if "INPUTS_REVISED_AFTER" in res["flags"] and res["state"] == "CURRENT":
        res["state_note"] = "posiblemente desactualizado: entrada revisada después de la última escritura"
    return res


# ── evidencia: intervalo de disponibilidad observado ───────────────────────────────────────────────────────────
def availability(records, obs_date):
    """Intervalo (última consulta correcta SIN la observación, primera consulta correcta CON ella] en el endpoint
    consultado, a partir del historial de evidencia de un fichero. Las consultas fallidas no fijan extremos."""
    lo = hi = None
    for r in sorted(records, key=lambda x: x.get("query_utc", "")):
        if not r.get("ok") or not r.get("src_max"):
            continue
        if r["src_max"] < obs_date:
            if hi is None:
                lo = r["query_utc"]
        elif hi is None:
            hi = r["query_utc"]
    return {"obs_date": obs_date, "lower_bound": lo, "upper_bound": hi,
            "scope": "endpoint consultado; no demuestra la publicación en otros sistemas del proveedor"}

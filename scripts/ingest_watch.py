#!/usr/bin/env python3
"""ingest_watch.py — v1.1 (revisión 24-sep) · vigilancia desde Actions de ejecutores externos, descargadores de
Actions, plazos del job y credenciales.

v1.3 (tercera revisión, R3-2/R3-3): un aviso de fallo solo se resuelve con una descarga correcta posterior (un
  aplazamiento no lo cierra: «sigue abierta, a la espera del proveedor»); lo mismo para un paso fallido del job;
  el periodo sin éxito tiene un inicio estable (failing_since_utc) aunque nunca haya habido un éxito.
v1.2 (segunda revisión, R2-3/R2-4): fallo FINAL de cada descargador (tras reintentos y alternativas; un
  intento intermedio recuperado no avisa; un aplazamiento por Retry-After tampoco), pasos del job fallidos o
  con salidas restauradas, y SILENCIO: un registro que envejece genera aviso de ausencia de ejecución y nunca
  «resuelto»; un aviso cuyo registro desaparece se mantiene; solo una retirada explícita en
  sources/actions_jobs.csv lo cierra, y se anuncia como «retirado», no como recuperado.
v1.1 (hallazgo #4): también lee los registros de los descargadores de Actions (data/_ingest/latest/actions__*.json)
  y su cuarentena (data/_ingest/quarantine/<FICHERO>.json): candidato retenido (T06) → aviso con su id, fecha,
  valor y motivo, deduplicado (un aviso por fichero; cambia si cambia el conjunto de candidatos), recordatorio
  diario mientras siga pendiente y aviso de resolución cuando se acepta, se rechaza o la fuente lo sustituye.
  Descarga inválida o regresión bloqueada en la última ejecución → aviso. Pasos del job omitidos o cortados por
  el plazo global (actions__job_<job>.json, hallazgo #3) → aviso.

Hace visible, SIN depender del Mac ni de su token, que:
  · el Mac no publicó su latido para una ejecución programada (apagado, dormido, launchd roto, Python roto,
    token caducado/revocado: en todos esos casos el latido no llega);
  · el Mac sí publicó, pero alguna familia no se publicó (descarga inválida, publicación fallida), quedó
    en confirmación (T06) o su descarga falló;
  · una credencial registrada en sources/credentials.csv caduca en ≤14/7/3/1 días o no tiene caducidad
    registrada; o el Mac informa ≤7 días de vida del token.

Plazo de AVISO ≠ plazo de recuperación: aquí solo se avisa (alert_after_min de sources/executors.csv).
La recuperación automática (respaldo residencial) es F9 y no está instalada.

Estado (qué se avisó y cuándo): data/_ingest/watch_state.json — un único escritor (este script, dentro del
grupo de concurrencia g8-shared-data-alerts). Avisa al aparecer o cambiar una alerta, recuerda cada 24 h
las críticas y avisa al resolverse. --dry-run no envía ni guarda.
Salida: escribe --result (JSON) con el estado de entrega; el workflow marca el job en rojo si Telegram falló.
"""
import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from g8common import notify, runlog  # noqa: E402

try:
    from zoneinfo import ZoneInfo
except ImportError:                                           # pragma: no cover (Python < 3.9)
    ZoneInfo = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
FAIL_FAMILY = {"INVALID", "PUBLISH_FAIL"}


def _read_csv(path):
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _days(spec):
    spec = (spec or "mon-sun").strip().lower()
    if "-" in spec:
        a, b = spec.split("-")
        i, j = DAYS[a], DAYS[b]
        return set(range(i, j + 1)) if i <= j else set(range(i, 7)) | set(range(0, j + 1))
    return {DAYS[x] for x in spec.split("|")}


def last_due_slot(row, now_utc):
    """Última ejecución programada (UTC) cuyo plazo de aviso ya venció, o None."""
    tz = ZoneInfo(row["timezone"])
    after = timedelta(minutes=float(row["alert_after_min"]))
    days = _days(row.get("days"))
    local_now = now_utc.astimezone(tz)
    best = None
    for back in range(0, 8):
        day = (local_now - timedelta(days=back)).date()
        if day.weekday() not in days:
            continue
        for hm in row["run_times_local"].split("|"):
            h, m = [int(x) for x in hm.split(":")]
            slot = datetime(day.year, day.month, day.day, h, m, tzinfo=tz).astimezone(timezone.utc)
            if slot + after <= now_utc and (best is None or slot > best):
                best = slot
        if best is not None:
            return best
    return best


def _parse(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def check_executors(root, now_utc, alerts, info):
    for row in _read_csv(os.path.join(root, "sources", "executors.csv")):
        ex, job = row["executor_id"], row["job"]
        key = "exec:%s:%s" % (ex, job)
        af = (row.get("active_from") or "").strip()
        if not af:
            info.append("%s/%s: vigilancia PENDIENTE-INSTALAR (sin active_from)" % (ex, job))
            continue
        if now_utc < _parse(af + "T00:00:00Z" if len(af) == 10 else af):
            continue
        slot = last_due_slot(row, now_utc)
        lp = os.path.join(root, runlog.latest_path(ex, job))
        hb = None
        if os.path.exists(lp):
            try:
                with open(lp, encoding="utf-8") as fh:
                    hb = json.load(fh)
            except ValueError:
                alerts[key + ":unreadable"] = "%s: latido ilegible en el repo" % ex
                continue
        started = _parse(hb["started_utc"]) if hb else None
        if slot is not None and (started is None or started < slot - timedelta(minutes=5)):
            local = slot.astimezone(ZoneInfo(row["timezone"]))
            alerts[key + ":missing"] = ("%s: sin latido de la ejecución de las %s (%s) del %s; último latido %s. "
                                        "NZD/CHF/TONA pueden no estar actualizándose." % (
                                            ex, local.strftime("%H:%M"), row["timezone"], local.strftime("%d-%b"),
                                            hb["started_utc"] if hb else "ninguno"))
            continue
        if not hb:
            continue
        for fam, fr in sorted((hb.get("families") or {}).items()):
            st = fr.get("status")
            if st in FAIL_FAMILY:
                alerts["%s:fam:%s" % (key, fam)] = "%s %s: %s — %s" % (ex, fam, st, (fr.get("detail") or fr.get("cls") or "")[:200])
            elif st == "HELD":
                held = fr.get("held") or []
                alerts["%s:held:%s" % (key, fam)] = "%s %s: %d valor(es) en confirmación: %s" % (
                    ex, fam, len(held), ", ".join("%s %s=%s" % (h.get("file"), h.get("date"), h.get("value")) for h in held[:4]))
        for k, v in sorted((hb.get("fetch_status") or {}).items()):
            if str(v) != "0":
                alerts["%s:fetch:%s" % (key, k)] = "%s: la descarga %s terminó con código %s" % (ex, k, v)
        cred = hb.get("credentials") or {}
        if cred.get("days_left") is not None and cred["days_left"] <= 7:
            alerts["%s:token" % key] = "%s: el token de GitHub caduca en %.1f días (%s)" % (ex, cred["days_left"], cred.get("expires_utc"))


DEFAULT_MAX_SILENCE_H = 96        # Daily: lun–vie; 96 h cubren el fin de semana. PROVISIONAL (sources/actions_jobs.csv)
QUIET_CLS = {"OK", "DEFERRED", "NO_PUBLICATION"}


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_jobs(root):
    """sources/actions_jobs.csv → {job: {status, max_silence_h}}; fila «*» = valores por defecto.
    Retirar un descargador es EXPLÍCITO (status RETIRED): su silencio no es una recuperación."""
    out = {"*": {"status": "ACTIVE", "max_silence_h": DEFAULT_MAX_SILENCE_H}}
    p = os.path.join(root, "sources", "actions_jobs.csv")
    if os.path.exists(p):
        for r in _read_csv(p):
            out[r["job"].strip()] = {"status": (r.get("status") or "ACTIVE").strip().upper(),
                                     "max_silence_h": float(r.get("max_silence_h") or DEFAULT_MAX_SILENCE_H)}
    return out


def _job_cfg(jobs, job):
    return jobs.get(job) or jobs["*"]


def _failure_text(rec):
    """Estado FINAL de una ejecución fallida (tras reintentos y alternativas), o None si no es un fallo."""
    if not rec.get("rc"):
        return None                                   # terminó bien: un intento intermedio fallido no cuenta
    files = rec.get("files") or {}
    if any(v.get("status") in ("INVALID", "REGRESSION_BLOCKED") for v in files.values()):
        return None                                   # ya tiene su propio aviso (fichero rechazado)
    reqs = rec.get("requests") or []
    last_bad = next((r for r in reversed(reqs) if r.get("cls") not in QUIET_CLS), None)
    if last_bad is None and reqs and all(r.get("cls") in ("DEFERRED", "NO_PUBLICATION") for r in reqs) and not files:
        return None                                   # aplazamiento o sin publicación: espera prevista, no fallo
    if last_bad is not None:
        return "descarga fallida — %s %s (%s)" % (last_bad.get("cls"), (last_bad.get("detail") or "")[:120],
                                                 last_bad.get("url", "")[:120])
    if reqs:
        return "el script terminó con error tras obtener respuesta (p. ej. formato inesperado al procesarla)"
    return "el script terminó con error sin llegar a descargar"


def check_actions(root, now_utc, alerts, info, prev_active=None, retired_keys=None):
    """Descargadores de Actions: cuarentena, fallos finales, ficheros rechazados, pasos del job cortados,
    fallidos o con salidas restauradas, y SILENCIO. Nada se da por resuelto sin un registro posterior que lo
    demuestre (R2-4): si el registro envejece se avisa de ausencia de ejecución; si desaparece, se mantiene el
    aviso; solo una retirada explícita (sources/actions_jobs.csv) lo cierra como «retirado»."""
    prev_active = prev_active or {}
    retired_keys = retired_keys if retired_keys is not None else set()
    jobs = load_jobs(root)
    sources = {}                                      # clave de aviso → (fichero de origen, job)
    qdir = os.path.join(root, "data", "_ingest", "quarantine")
    if os.path.isdir(qdir):
        for n in sorted(os.listdir(qdir)):
            if not n.endswith(".csv.json"):           # <FAMILIA>.json es del Mac: ya lo cubre su latido
                continue
            try:
                doc = _load(os.path.join(qdir, n))
            except ValueError:
                alerts["actions:quarantine:%s:unreadable" % n] = "Cuarentena ilegible: data/_ingest/quarantine/%s" % n
                continue
            fname = n[:-5]
            pend = sorted((c for c in (doc.get("candidates") or {}).values() if c.get("status") == "PENDING"),
                          key=lambda c: (c.get("date", ""), c.get("id", "")))
            if pend:
                alerts["actions:held:%s" % fname] = (
                    "Actions %s: %d valor(es) retenido(s) en confirmación, se conserva el último válido: %s. "
                    "Se aceptan si otra descarga correcta los confirma, o por decisión en data/_ingest/decisions/%s/" % (
                        fname, len(pend), "; ".join("%s=%s (antes %s; %s; id %s)" % (
                            c.get("date"), c.get("value"), c.get("previous_value"), (c.get("reason") or "")[:60],
                            c.get("id")) for c in pend[:4]) + (" …" if len(pend) > 4 else ""), fname))
    ldir = os.path.join(root, runlog.LATEST_DIR)
    names = sorted(os.listdir(ldir)) if os.path.isdir(ldir) else []
    pointers, ledgers = {}, {}
    for n in names:
        if not (n.startswith("actions__") and n.endswith(".json")):
            continue
        try:
            rec = _load(os.path.join(ldir, n))
        except ValueError:
            alerts["actions:latest:%s:unreadable" % n] = "Registro ilegible: %s/%s" % (runlog.LATEST_DIR, n)
            continue
        if n.startswith("actions__job_"):
            ledgers[n[len("actions__job_"):-5]] = rec
        else:
            pointers[rec.get("job") or n[len("actions__"):-5]] = rec
    covered = set()                                   # (run de GitHub, paso) ya cubiertos por el registro del descargador
    for job, rec in sorted(pointers.items()):
        cfg = _job_cfg(jobs, job)
        if cfg["status"] == "RETIRED":
            continue
        if rec.get("step") and rec.get("gh_run_id"):
            covered.add((str(rec["gh_run_id"]), rec["step"]))
        _silence(alerts, "actions:%s:silence" % job, "Actions %s" % job, rec.get("finished_utc"), cfg, now_utc)
        for fname, fr in sorted((rec.get("files") or {}).items()):
            if fr.get("status") in ("INVALID", "REGRESSION_BLOCKED"):
                alerts["actions:%s:%s:%s" % (job, fname, fr["status"].lower())] = (
                    "Actions %s → %s: %s, se conserva lo publicado — %s" % (
                        job, fname, fr["status"], (fr.get("detail") or "")[:200]))
        if rec.get("rc"):
            # referencia: último éxito; si nunca lo hubo, inicio estable del periodo sin éxito (R3-3); para un
            # registro de la versión anterior sin ninguno de los dos campos, su propio inicio
            lok, fs = rec.get("last_ok_utc"), rec.get("failing_since_utc")
            ref = lok or fs or rec.get("started_utc")
            try:
                age = (now_utc - _parse(ref)).total_seconds() / 3600.0 if ref else 0
            except ValueError:
                age = 0
            if age > cfg["max_silence_h"]:
                desde = ("desde la última correcta, %s" % lok) if lok else (
                    "desde el primer intento sin éxito registrado, %s (no consta ninguna descarga correcta)" % ref)
                alerts["actions:%s:no_success" % job] = (
                    "Actions %s: sin descarga correcta %s; límite %.0f h. El job se ejecuta pero falla o el proveedor "
                    "le hace esperar una y otra vez." % (job, desde, cfg["max_silence_h"]))
        why = _failure_text(rec)
        if why:
            # texto estable (sin la hora) para que fallos idénticos consecutivos no se reenvíen: recordatorio diario
            alerts["actions:%s:fail" % job] = "Actions %s: %s. Se conserva lo publicado; el dato no se actualizó." % (job, why)
    for job, led in sorted(ledgers.items()):
        cfg = _job_cfg(jobs, "job_" + job)
        if cfg["status"] == "RETIRED":
            continue
        _silence(alerts, "actions:job:%s:silence" % job, "Workflow %s" % job, led.get("written_utc"), cfg, now_utc)
        lim = led.get("time_limited") or []
        if lim:
            alerts["actions:job:%s:time" % job] = (
                "Actions %s: %d paso(s) sin terminar por el plazo global del job: %s. Esos feeds no se "
                "actualizaron en esta ejecución." % (job, len(lim), ", ".join("%s %s" % (x["name"], x["status"]) for x in lim[:8])))
        run = str(led.get("run_id"))
        for st in led.get("failed") or []:
            if (run, st["name"]) in covered:
                continue                              # su descargador ya informa del fallo con más detalle
            alerts["actions:job:%s:step:%s" % (job, st["name"])] = (
                "Actions %s: el paso %s terminó con código %s (ver el registro del job). Sus salidas válidas se "
                "conservan; las inválidas se restauraron al último válido." % (job, st["name"], st.get("rc")))
        rest = led.get("restored") or []
        if rest:
            alerts["actions:job:%s:restored" % job] = (
                "Actions %s: %d salida(s) restaurada(s) al último válido: %s" % (job, len(rest), "; ".join(
                    "%s (%s: %s)" % (r["file"], r.get("step"), r.get("reason")) for r in rest[:6])))
        unr = led.get("unrestorable") or []
        if unr:
            alerts["actions:job:%s:unrestorable" % job] = (
                "Actions %s: %d cambio(s) NO restaurable(s) — revisar antes del siguiente commit: %s" % (
                    job, len(unr), "; ".join("%s (%s)" % (r["file"], r.get("reason")) for r in unr[:6])))
    # R2-4: un aviso anterior cuyo registro ya no existe NO se da por resuelto
    for k, v in sorted(prev_active.items()):
        if not k.startswith("actions:") or k in alerts:
            continue
        job = _job_of_key(k)
        if job is None:
            continue
        exists = (job in pointers) if not k.startswith("actions:job:") else (job in ledgers)
        if k.startswith("actions:held:"):
            exists = os.path.exists(os.path.join(qdir, job + ".json"))
        cfg = _job_cfg(jobs, ("job_" + job) if k.startswith("actions:job:") else job)
        txt = v.get("text", k)
        if cfg["status"] == "RETIRED":
            retired_keys.add(k)
        elif not exists:
            alerts[k] = _keep(txt, " — su registro ya no existe: sin evidencia de recuperación")
        else:
            still = _still_open(k, pointers.get(job), ledgers.get(job))
            if still:
                alerts[k] = _keep(txt, still)


# Claves cuya resolución exige EVIDENCIA de una descarga correcta posterior (R3-2): que la última ejecución no
# vuelva a generar la misma alerta (p. ej. porque quedó aplazada) no demuestra que el feed se haya recuperado.
_NEEDS_SUCCESS = (":fail", ":no_success", ":invalid", ":regression_blocked")


def _still_open(k, pointer, ledger):
    """Texto de «sigue abierta» si no hay evidencia de recuperación para la clave k; None si la hay."""
    if k.startswith("actions:job:"):
        parts = k.split(":")
        if len(parts) >= 5 and parts[3] == "step":
            ok = any(st.get("name") == parts[4] and st.get("status") == "OK" for st in (ledger or {}).get("steps") or [])
            return None if ok else " — sigue abierta: el paso no ha vuelto a terminar correctamente"
        return None                                   # plazos/restauraciones: incidencias de una ejecución concreta
    if k.startswith("actions:held:") or k.endswith(":silence"):
        return None                                   # cuarentena: la resuelve su decisión; silencio: hay actividad
    if k.endswith(_NEEDS_SUCCESS) and pointer is not None and pointer.get("rc"):
        deferred = any(r.get("cls") == "DEFERRED" for r in pointer.get("requests") or [])
        return (" — sigue abierta: la última ejecución quedó aplazada por el proveedor (Retry-After); sin descarga "
                "correcta posterior" if deferred else
                " — sigue abierta: sin descarga correcta posterior")
    return None


def _keep(txt, suffix):
    base = txt.split(" — sigue abierta:")[0].split(" — su registro ya no existe:")[0]
    return base + suffix


def _job_of_key(k):
    parts = k.split(":")
    if len(parts) >= 3 and parts[1] == "job":
        return parts[2]
    if len(parts) >= 3 and parts[1] == "held":
        return ":".join(parts[2:])
    if len(parts) >= 3 and parts[1] not in ("quarantine", "latest"):
        return parts[1]
    return None


def _silence(alerts, key, label, when, cfg, now_utc):
    """Sin registro reciente → aviso de AUSENCIA (no resolución)."""
    try:
        age_h = (now_utc - _parse(when)).total_seconds() / 3600.0 if when else None
    except ValueError:
        age_h = None
    if age_h is None or age_h > cfg["max_silence_h"]:
        alerts[key] = "%s: sin ejecución registrada desde %s (límite %.0f h). Nada indica que se haya recuperado." % (
            label, when or "nunca", cfg["max_silence_h"])


def check_credentials(root, now_utc, alerts, info_keys):
    for row in _read_csv(os.path.join(root, "sources", "credentials.csv")):
        cid, exp = row["credential_id"], (row.get("expires_utc") or "").strip()
        if exp == "NONE":
            continue
        if exp in ("", "UNKNOWN"):
            k = "cred:%s:unknown" % cid
            alerts[k] = "Credencial %s sin fecha de caducidad registrada en sources/credentials.csv (afecta: %s)" % (cid, row.get("affects"))
            info_keys.add(k)
            continue
        try:
            d = datetime.strptime(exp[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            alerts["cred:%s:bad" % cid] = "Credencial %s: fecha ilegible %r" % (cid, exp)
            continue
        left = (d - now_utc).total_seconds() / 86400.0
        bucket = next((b for b in (0, 1, 3, 7, 14) if left <= b), None)
        if bucket is not None:
            txt = ("CADUCADA" if left <= 0 else "caduca en ≤%d días (%s)" % (bucket, exp[:10]))
            alerts["cred:%s" % cid] = "Credencial %s %s — afecta: %s" % (cid, txt, row.get("affects"))


def main(argv=None, now=time.time, root=ROOT):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--result", default="")
    a = ap.parse_args(argv)
    now_ts = now()
    now_utc = datetime.fromtimestamp(now_ts, tz=timezone.utc)
    alerts, info, info_keys, retired = {}, [], set(), set()
    book = notify.AlertBook(os.path.join(root, "data", "_ingest", "watch_state.json"))
    for fn in (check_executors, check_actions, check_credentials):
        try:
            if fn is check_actions:
                fn(root, now_utc, alerts, info, prev_active=book.state.get("active", {}), retired_keys=retired)
            elif fn is check_executors:
                fn(root, now_utc, alerts, info)
            else:
                fn(root, now_utc, alerts, info_keys)
        except Exception as e:                                      # Ley 2: el vigilante nunca calla un fallo propio
            alerts["watch:error:%s" % fn.__name__] = "ingest_watch: error en %s: %s" % (fn.__name__, e)
    plan = book.plan(alerts, now_ts, no_remind=info_keys, retired=retired)
    for line in info:
        print("[ingest_watch] " + line)
    delivery, sent, undelivered = "NOTHING", set(), []
    if plan:
        text = "<b>G8 · vigilancia de ingestión</b>\n" + "\n".join(
            {"RESOLVED": "🟢 resuelto: ", "RETIRED": "⚪ retirado por configuración (no es una recuperación): ",
             "REMIND": "🔁 "}.get(kind, "🟠 ") + t for kind, _, t in plan)
        delivery = notify.send(text, dry=a.dry_run or None)
        print(text)
        if delivery in ("SENT", "DRY"):
            sent = {k for _, k, _ in plan}
        else:
            undelivered = [text]
    if not a.dry_run:
        book.commit(alerts, sent, now_ts, undelivered)
    res = {"utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "alerts": sorted(alerts), "delivery": delivery}
    if a.result:
        with open(a.result, "w", encoding="utf-8") as fh:
            json.dump(res, fh)
    print("[ingest_watch] alertas activas: %d · entrega: %s" % (len(alerts), delivery))
    return 0


if __name__ == "__main__":
    sys.exit(main())

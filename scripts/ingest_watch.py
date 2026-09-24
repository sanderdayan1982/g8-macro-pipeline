#!/usr/bin/env python3
"""ingest_watch.py — v1.0 (lote 1, 2026-09-24) · vigilancia desde Actions de los ejecutores externos y credenciales.

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
    alerts, info, info_keys = {}, [], set()
    for fn in (check_executors, check_credentials):
        try:
            if fn is check_executors:
                fn(root, now_utc, alerts, info)
            else:
                fn(root, now_utc, alerts, info_keys)
        except Exception as e:                                      # Ley 2: el vigilante nunca calla un fallo propio
            alerts["watch:error:%s" % fn.__name__] = "ingest_watch: error en %s: %s" % (fn.__name__, e)
    book = notify.AlertBook(os.path.join(root, "data", "_ingest", "watch_state.json"))
    plan = book.plan(alerts, now_ts, no_remind=info_keys)
    for line in info:
        print("[ingest_watch] " + line)
    delivery, sent, undelivered = "NOTHING", set(), []
    if plan:
        text = "<b>G8 · vigilancia de ingestión</b>\n" + "\n".join(
            ("🟢 resuelto: " if kind == "RESOLVED" else ("🔁 " if kind == "REMIND" else "🟠 ")) + t for kind, _, t in plan)
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

#!/usr/bin/env python3
"""watch_vps_jobs.py — aviso Telegram si un job VPS (SNB/nzd-b2) falla 2 veces seguidas (t334u).

Se lanza como segundo ExecStart del timer g8-watch-prod. No toca watch_prod.py ni el dispatcher.
Respeta G8_NO_SEND=1. Nunca imprime tokens.
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone

DEFAULT_STATE_DIR = "/srv/g8/prod/state/vps_jobs"
DEFAULT_ALERTS = "/srv/g8/prod/state/vps_job_alerts.json"
JOBS = ("snb1a", "nzd-b2")


def _utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def telegram(text, transport=None):
    if os.environ.get("G8_NO_SEND") == "1":
        return False, None
    tok = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not tok or not chat:
        print("telegram: faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID", file=__import__("sys").stderr)
        return False, None
    data = urllib.parse.urlencode({"chat_id": chat, "text": text, "parse_mode": "HTML"}).encode()
    try:
        if transport:
            return transport(data)
        req = urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % tok, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read().decode())
        mid = body.get("result", {}).get("message_id")
        return True, mid
    except Exception as e:
        print("telegram: fallo de envío (%s)" % type(e).__name__, file=__import__("sys").stderr)
        return False, None


def evaluate(state_dir, alerts, jobs=JOBS, now=None):
    """Return (nuevo_aviso list, alerts actualizado). Alerta solo al pasar a >=2 fallos seguidos."""
    now = now or _utcnow()
    nuevos = []
    for job in jobs:
        rec = read_json(os.path.join(state_dir, "%s.json" % job), {})
        streak = int(rec.get("consecutive_fail") or 0)
        ok = bool(rec.get("ok"))
        key = "fail2:%s" % job
        if ok or streak < 2:
            alerts.pop(key, None)
            continue
        if key in alerts:
            continue
        err = rec.get("error") or rec.get("error_type") or "rc=%s" % rec.get("rc")
        text = ("G8 VPS · job <b>%s</b> falló 2 veces seguidas (%s). "
                "Último: %s" % (job, err, rec.get("utc") or now))
        nuevos.append((job, text))
        alerts[key] = {"utc": now, "streak": streak}
    return nuevos, alerts


def main(argv=None, transport_tg=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", default=os.environ.get("G8_VPS_JOB_DIR", DEFAULT_STATE_DIR))
    ap.add_argument("--alerts", default=os.environ.get("G8_VPS_JOB_ALERTS", DEFAULT_ALERTS))
    ap.add_argument("--no-send", action="store_true")
    args = ap.parse_args(argv)
    alerts = read_json(args.alerts, {})
    nuevos, alerts = evaluate(args.state_dir, alerts)
    sent = []
    for job, text in nuevos:
        if args.no_send:
            ok, mid = False, None
        else:
            ok, mid = telegram(text, transport=transport_tg)
        sent.append({"job": job, "telegram_ok": ok, "message_id": mid})
        print("AVISO %s (telegram ok=%s)" % (text, str(ok).lower()))
    write_json(args.alerts, alerts)
    print("watch_vps_jobs avisos_nuevos=%d abiertos=%d" % (len(nuevos), len(alerts)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

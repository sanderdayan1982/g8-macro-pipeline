#!/usr/bin/env python3
"""swpt_weekly.py — job systemd VPS: descarga SWPT (H.4.1 / FRED) y publica solo data/SWPT.csv (t348u).

No es un turno del dispatcher. Usa G8_FULL para git push. Nunca imprime el token
ni FRED_API_KEY. El cron de GitHub queda retirado; este job es la publicación
calendarizada, con el workflow público solo como respaldo manual.

El timer dispara jueves y viernes 22:00 UTC (orden Sander 25-nov t378u: swpt a las 22:00 UTC). El fetch solo corre si hoy
(America/New_York) es el día oficial de publicación de la regla SWPT ya
existente (weekly:THU, festivo US → siguiente día hábil). Así se cubre
Thanksgiving y no hay dos publicaciones en una semana hábil normal.
"""
from __future__ import annotations

import argparse
import job_flock
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT_DEFAULT = os.path.abspath(os.path.join(HERE, ".."))
SWPT_ALLOWED = ("data/SWPT.csv",)
NY = ZoneInfo("America/New_York")


def _utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_state(path, rec):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def load_prev(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def is_official_pub_day(now, root):
    """True si la fecha local NY de *now* es día oficial de publicación SWPT.

    Reutiliza la regla de frescura ya vigente (no cambia umbrales ni fórmulas).
    Consulta el calendario a final del día NY para no depender de si la hora
    oficial (16:30 ET) ya ha pasado.
    """
    sys.path.insert(0, os.path.join(root, "scripts"))
    from g8common import freshness as FR  # noqa: WPS433

    today = now.astimezone(NY).date()
    asof = datetime(today.year, today.month, today.day, 23, 59, tzinfo=NY)
    rules, params, _outputs, cals = FR.load_config(root)
    pubs = FR.publications(rules["SWPT"], params["SWPT"], "US", "America/New_York", cals, asof)
    return any(due.astimezone(NY).date() == today for _obs, due, *_rest in pubs)


def run(workdir, token):
    sys.path.insert(0, os.path.join(workdir, "scripts"))
    sys.path.insert(0, HERE)
    import fetch_swpt as F  # noqa: WPS433
    import vps_git_push as P  # noqa: WPS433

    P.fetch_ff_only(workdir, token, allowed=SWPT_ALLOWED)
    out = F.run(data_dir=os.path.join(workdir, "data"))
    last = out.get("last")
    msg = "[vps-push] swpt last=%s channel=%s [skip netlify]" % (last or "none", out.get("channel") or "none")
    pub_out = P.commit_and_push(workdir, SWPT_ALLOWED, msg, token, owned=SWPT_ALLOWED)
    return {"ok": True, "last_obs": last, "channel": out.get("channel"), "n": out.get("n"), "push": pub_out}


def main(argv=None):
    ap = argparse.ArgumentParser(description="VPS SWPT semanal (t348u)")
    ap.add_argument("--workdir", default=os.environ.get("G8_SWPT_WORKDIR", ROOT_DEFAULT))
    ap.add_argument("--state", default=os.environ.get("G8_VPS_JOB_STATE", ""))
    ap.add_argument("--now", default=None, help="ISO-8601 UTC de prueba (p. ej. 2026-11-27T22:00:00Z)")
    ap.add_argument("--force", action="store_true", help="publicar aunque hoy no sea día oficial")
    args = ap.parse_args(argv)
    if args.now:
        raw = args.now.replace("Z", "+00:00")
        now = datetime.fromisoformat(raw)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
    else:
        now = datetime.now(timezone.utc)
    token = os.environ.get("G8_FULL", "").strip()
    state_path = args.state or os.environ.get("G8_VPS_JOB_STATE") or "/srv/g8/prod/state/vps_jobs/swpt.json"
    prev = load_prev(state_path)
    rec = {"job": "swpt", "utc": _utcnow(), "ok": False, "rc": 1, "consecutive_fail": 0}
    try:
        official = is_official_pub_day(now, args.workdir)
        rec["official_pub_day"] = official
        rec["ny_date"] = now.astimezone(NY).date().isoformat()
        if not official and not args.force:
            rec["ok"] = True
            rec["rc"] = 0
            rec["skipped"] = True
            rec["consecutive_fail"] = 0
            rec["reason"] = "hoy no es día oficial de publicación SWPT"
            write_state(state_path, rec)
            print("[swpt_weekly] skip ny=%s official=no" % rec["ny_date"])
            return 0
        if not token:
            raise RuntimeError("G8_FULL ausente")
        lock = os.environ.get("G8_SWPT_LOCK") or "/srv/g8/prod/state/locks/swpt.lock"
        with job_flock.exclusive(lock):
            result = run(args.workdir, token)
        rec["ok"] = True
        rec["rc"] = 0
        rec["skipped"] = False
        rec["consecutive_fail"] = 0
        rec["last_obs"] = result.get("last_obs")
        rec["channel"] = result.get("channel")
        rec["n"] = result.get("n")
        rec["sha"] = (result.get("push") or {}).get("sha")
        rec["changed"] = (result.get("push") or {}).get("changed")
        rec["files"] = (result.get("push") or {}).get("files")
    except Exception as e:
        rec["ok"] = False
        rec["rc"] = 1
        rec["error_type"] = type(e).__name__
        rec["error"] = str(e)[:400]
        rec["consecutive_fail"] = int(prev.get("consecutive_fail") or 0) + 1
        print("[swpt_weekly] FATAL: %s: %s" % (type(e).__name__, e), file=sys.stderr)
        traceback.print_exc()
    write_state(state_path, rec)
    print("[swpt_weekly] ok=%s skip=%s last=%s sha=%s fail_streak=%s" % (
        rec.get("ok"), rec.get("skipped"), rec.get("last_obs"), rec.get("sha"), rec.get("consecutive_fail")))
    return rec["rc"]


if __name__ == "__main__":
    sys.exit(main())

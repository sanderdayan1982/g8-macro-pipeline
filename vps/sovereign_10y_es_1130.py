#!/usr/bin/env python3
"""sovereign_10y_es_1130.py — job systemd VPS: pasada extra de ES 10Y a las 11:30 WAT (t372u).

Timer y flock propios. No toca DE. Recompone el spread. Prefijo [vps-push] y [skip netlify].
Si esta pasada no trae el dato esperado, la de las 14:00 lo recoge como respaldo.
"""
from __future__ import annotations

import argparse
import job_flock
import json
import os
import sys
import traceback
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT_DEFAULT = os.path.abspath(os.path.join(HERE, ".."))

ALLOWED = (
    "data/ES_10Y_DAILY.csv",
    "data/ES_DE_10Y_SPREAD_DAILY.csv",
    "data/_ingest/derived_provenance/ES_DE_10Y_SPREAD_DAILY.json",
)


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


def run(workdir, token, no_push=False, opener=None):
    sys.path.insert(0, os.path.join(workdir, "scripts"))
    sys.path.insert(0, HERE)
    import fetch_sovereign_10y_daily as F  # noqa: WPS433
    import vps_git_push as P  # noqa: WPS433

    if not no_push:
        P.fetch_ff_only(workdir, token, allowed=ALLOWED)
    data = os.path.join(workdir, "data")
    result = F.run(data_dir=data, opener=opener, fetch_de=False, fetch_es=True)
    last = result.get("es_last")
    msg = "[vps-push] sovereign-10y-es-1130 es=%s [skip netlify]" % last
    if no_push:
        pub_out = {"changed": False, "sha": None, "files": [], "skipped": True, "no_push": True}
    else:
        pub_out = P.commit_and_push(workdir, ALLOWED, msg, token)
    return {"ok": True, "push": pub_out, "fetch": result, "last": last}


def main(argv=None):
    ap = argparse.ArgumentParser(description="VPS pasada extra ES 10Y 11:30 WAT (t372u)")
    ap.add_argument("--workdir", default=os.environ.get("G8_SOV10Y_ES1130_WORKDIR", ROOT_DEFAULT))
    ap.add_argument("--state", default=os.environ.get("G8_VPS_JOB_STATE", "/srv/g8/prod/state/vps_jobs/sovereign-10y-es-1130.json"))
    ap.add_argument("--no-push", action="store_true", help="pasada en seco: no hace fetch ff ni push")
    args = ap.parse_args(argv)
    token = os.environ.get("G8_FULL", "").strip()
    prev = load_prev(args.state)
    rec = {"job": "sovereign-10y-es-1130", "utc": _utcnow(), "ok": False, "rc": 1, "consecutive_fail": 0}
    try:
        if not args.no_push and not token:
            raise RuntimeError("G8_FULL ausente")
        lock = os.environ.get("G8_SOV10Y_ES1130_LOCK") or "/srv/g8/prod/state/locks/sovereign-10y-es-1130.lock"
        with job_flock.exclusive(lock):
            result = run(args.workdir, token, no_push=args.no_push)
        rec["ok"] = True
        rec["rc"] = 0
        rec["consecutive_fail"] = 0
        rec["last"] = result.get("last")
        rec["sha"] = (result.get("push") or {}).get("sha")
        rec["changed"] = (result.get("push") or {}).get("changed")
        rec["files"] = (result.get("push") or {}).get("files")
    except Exception as e:
        rec["ok"] = False
        rec["rc"] = 1
        rec["error_type"] = type(e).__name__
        rec["error"] = str(e)[:400]
        rec["consecutive_fail"] = int(prev.get("consecutive_fail") or 0) + 1
        print("[sovereign_10y_es_1130] FATAL: %s: %s" % (type(e).__name__, e), file=sys.stderr)
        traceback.print_exc()
    write_state(args.state, rec)
    print("[sovereign_10y_es_1130] ok=%s last=%s sha=%s fail_streak=%s no_push=%s" % (
        rec.get("ok"), rec.get("last"), rec.get("sha"), rec.get("consecutive_fail"), args.no_push))
    return rec["rc"]


if __name__ == "__main__":
    sys.exit(main())

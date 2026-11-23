#!/usr/bin/env python3
"""sovereign_10y_daily.py — job systemd VPS: DE par + ES 10Y + spread (t345u).

No es un turno del dispatcher. Cron de GitHub se conserva (test_t339u lo exige)
hasta que Sander autorice retirarlo. Prefijo [vps-push] y [skip netlify].
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
    "data/DE_10Y_PAR_DAILY.csv",
    "data/ES_10Y_DAILY.csv",
    "data/ES_DE_10Y_SPREAD_DAILY.csv",
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


def run(workdir, token):
    sys.path.insert(0, os.path.join(workdir, "scripts"))
    sys.path.insert(0, HERE)
    import fetch_sovereign_10y_daily as F  # noqa: WPS433
    import vps_git_push as P  # noqa: WPS433

    P.fetch_ff_only(workdir, token, allowed=ALLOWED)
    data = os.path.join(workdir, "data")
    result = F.run(data_dir=data)
    last = "%s/%s" % (result.get("de_last"), result.get("es_last"))
    msg = "[vps-push] sovereign-10y de=%s es=%s [skip netlify]" % (
        result.get("de_last"), result.get("es_last"))
    pub_out = P.commit_and_push(workdir, ALLOWED, msg, token)
    return {"ok": True, "push": pub_out, "fetch": result, "last": last}


def main(argv=None):
    ap = argparse.ArgumentParser(description="VPS DE/ES 10Y diario (t345u)")
    ap.add_argument("--workdir", default=os.environ.get("G8_SOV10Y_WORKDIR", ROOT_DEFAULT))
    ap.add_argument("--state", default=os.environ.get("G8_VPS_JOB_STATE", "/srv/g8/prod/state/vps_jobs/sovereign-10y.json"))
    args = ap.parse_args(argv)
    token = os.environ.get("G8_FULL", "").strip()
    prev = load_prev(args.state)
    rec = {"job": "sovereign-10y", "utc": _utcnow(), "ok": False, "rc": 1, "consecutive_fail": 0}
    try:
        if not token:
            raise RuntimeError("G8_FULL ausente")
        lock = os.environ.get("G8_SOV10Y_LOCK") or "/srv/g8/prod/state/locks/sovereign-10y.lock"
        with job_flock.exclusive(lock):
            result = run(args.workdir, token)
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
        print("[sovereign_10y_daily] FATAL: %s: %s" % (type(e).__name__, e), file=sys.stderr)
        traceback.print_exc()
    write_state(args.state, rec)
    print("[sovereign_10y_daily] ok=%s last=%s sha=%s fail_streak=%s" % (
        rec.get("ok"), rec.get("last"), rec.get("sha"), rec.get("consecutive_fail")))
    return rec["rc"]


if __name__ == "__main__":
    sys.exit(main())

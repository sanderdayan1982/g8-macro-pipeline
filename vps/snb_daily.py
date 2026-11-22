#!/usr/bin/env python3
"""snb_daily.py — job systemd VPS: descarga SNB1A y publica solo las rutas diarias (t334u).

No es un turno del dispatcher. Usa G8_FULL para git push. Nunca imprime el token.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT_DEFAULT = os.path.abspath(os.path.join(HERE, ".."))


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


def snb_opener():
    headers = {
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"),
        "Accept": "text/csv,text/plain,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://data.snb.ch/en",
    }

    def get(url, timeout=120):
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    return get


def run(workdir, state_path, token, from_date=None):
    sys.path.insert(0, os.path.join(workdir, "scripts"))
    sys.path.insert(0, HERE)
    import fetch_chf_snb1a as F  # noqa: WPS433
    import vps_git_push as P  # noqa: WPS433

    P.fetch_ff_only(workdir, token)
    out = F.run(data_dir=os.path.join(workdir, "data"), from_date=from_date, opener=snb_opener())
    last = out.get("last_observation")
    pub = out.get("publishing_date")
    msg = "[vps-push] snb1a last=%s pub=%s [skip netlify]" % (last or "none", pub or "none")
    pub_out = P.commit_and_push(workdir, P.SNB_ALLOWED, msg, token)
    return {"ok": True, "last_obs": last, "publishing_date": pub, "push": pub_out, "n_files": out.get("n_daily_files")}


def main(argv=None):
    ap = argparse.ArgumentParser(description="VPS SNB1A diario (t334u)")
    ap.add_argument("--workdir", default=os.environ.get("G8_SNB_WORKDIR", ROOT_DEFAULT))
    ap.add_argument("--state", default=os.environ.get("G8_VPS_JOB_STATE", "/srv/g8/prod/state/vps_jobs/snb1a.json"))
    ap.add_argument("--from-date", default=None)
    args = ap.parse_args(argv)
    token = os.environ.get("G8_FULL", "").strip()
    prev = load_prev(args.state)
    rec = {"job": "snb1a", "utc": _utcnow(), "ok": False, "rc": 1, "consecutive_fail": 0}
    try:
        if not token:
            raise RuntimeError("G8_FULL ausente")
        result = run(args.workdir, args.state, token, from_date=args.from_date)
        rec.update(ok=True, rc=0, consecutive_fail=0, **{k: result[k] for k in result if k != "push"})
        rec["sha"] = (result.get("push") or {}).get("sha")
        rec["changed"] = (result.get("push") or {}).get("changed")
        rec["files"] = (result.get("push") or {}).get("files")
    except Exception as e:
        rec["ok"] = False
        rec["rc"] = 1
        rec["error_type"] = type(e).__name__
        rec["error"] = str(e)[:400]
        rec["consecutive_fail"] = int(prev.get("consecutive_fail") or 0) + 1
        print("[snb_daily] FATAL: %s: %s" % (type(e).__name__, e), file=sys.stderr)
        traceback.print_exc()
    write_state(args.state, rec)
    print("[snb_daily] ok=%s last=%s sha=%s fail_streak=%s" % (
        rec.get("ok"), rec.get("last_obs"), rec.get("sha"), rec.get("consecutive_fail")))
    return rec["rc"]


if __name__ == "__main__":
    sys.exit(main())

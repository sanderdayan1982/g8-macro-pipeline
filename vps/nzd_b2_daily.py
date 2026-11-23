#!/usr/bin/env python3
"""nzd_b2_daily.py — job systemd VPS: RBNZ B2 (vía G8NET) + CHF mensual + TONA (t334u).

Sustituye a com.g8.nzd-b2 del Mac. Conserva push_nzd_to_github (PROXY honesto, OCR de main,
recuperación automática). Prefijo [vps-push]. No es un turno del dispatcher.
"""
from __future__ import annotations

import argparse
import shutil
import job_flock
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT_DEFAULT = os.path.abspath(os.path.join(HERE, ".."))
G8NET_RBNZ = "https://g8-institutional.netlify.app/proxy/rbnz"


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



def stage_fetch_records(workdir):
    """Copy workdir/state/fetch_*.json into mac/state so push_nzd_to_github can attach fetch_detail (t345u)."""
    src = os.path.join(workdir, "state")
    dst = os.path.join(workdir, "mac", "state")
    os.makedirs(dst, exist_ok=True)
    copied = []
    if not os.path.isdir(src):
        return copied
    for name in os.listdir(src):
        if name.startswith("fetch_") and name.endswith(".json"):
            shutil.copy2(os.path.join(src, name), os.path.join(dst, name))
            copied.append(name)
    return copied


def refresh_argos_and_drop_mac_latest(workdir, token):
    """Regenerate argos_rates.json if inputs changed; drop leftover Mac latest pointer (t345u)."""
    sys.path.insert(0, os.path.join(workdir, "scripts"))
    sys.path.insert(0, HERE)
    import argos_rates as AR  # noqa: WPS433
    import vps_git_push as P  # noqa: WPS433
    result = AR.refresh_if_inputs_changed(root=workdir)
    mac_latest = os.path.join(workdir, "data", "_ingest", "latest", "mac-primary__nzchf-tona.json")
    removed = False
    if os.path.isfile(mac_latest):
        os.remove(mac_latest)
        removed = True
    allowed = (
        "data/argos_rates.json",
        "data/_ingest/latest/mac-primary__nzchf-tona.json",
    )
    if not result.get("changed") and not removed:
        return {"argos": result, "removed_mac_latest": False, "push": None}
    msg = "[vps-push] argos refresh / clear mac-primary latest [skip netlify]"
    # If the mac-primary file was deleted, it must already be tracked for commit_and_push
    # to pick it up (tracked dirty). Untracked deletions are N/A.
    push = P.commit_and_push(workdir, allowed, msg, token)
    return {"argos": result, "removed_mac_latest": removed, "push": push}

def _run(cmd, cwd, env, timeout=600):
    r = subprocess.run(cmd, cwd=cwd, env=env, timeout=timeout)
    return r.returncode


def run(workdir, token):
    sys.path.insert(0, HERE)
    import vps_git_push as P  # noqa: WPS433

    P.fetch_ff_only(workdir, token)
    data = os.path.join(workdir, "data")
    mac = os.path.join(workdir, "mac")
    mac_data = os.path.join(mac, "data")
    os.makedirs(data, exist_ok=True)
    if os.path.islink(mac_data) or os.path.exists(mac_data):
        if os.path.islink(mac_data):
            os.remove(mac_data)
        elif not os.path.samefile(mac_data, data):
            raise RuntimeError("mac/data existe y no es el data del clon")
    if not os.path.exists(mac_data):
        os.symlink(os.path.relpath(data, mac), mac_data)

    env = os.environ.copy()
    env["G8_FULL"] = token
    env["GITHUB_TOKEN"] = token
    env["G8_EXECUTOR_ID"] = env.get("G8_EXECUTOR_ID") or "vps-prod"
    env["G8_COMMIT_PREFIX"] = "[vps-push]"
    env["G8_COMMIT_SUFFIX"] = "[skip netlify]"
    env["G8_RBNZ_PROXY_BASE"] = env.get("G8_RBNZ_PROXY_BASE") or G8NET_RBNZ
    py = sys.executable
    t0 = int(time.time())
    rc_nzd = _run([py, os.path.join(workdir, "scripts", "fetch_nzd_b2.py")], workdir, env)
    rc_chf = _run([py, os.path.join(workdir, "scripts", "fetch_chf_snb.py")], workdir, env)
    tona = os.path.join(workdir, "scripts", "fetch_tona.py")
    rc_tona = _run([py, tona], workdir, env)
    status = "nzd=%d,chf=%d,tona=%d" % (rc_nzd, rc_chf, rc_tona)
    staged = stage_fetch_records(workdir)
    rc_push = _run(
        [py, os.path.join(mac, "push_nzd_to_github.py"),
         "--fetch-status", status, "--fetch-started", str(t0)],
        mac, env, timeout=900,
    )
    extra = {}
    if rc_push == 0:
        try:
            extra = refresh_argos_and_drop_mac_latest(workdir, token)
        except Exception as e:
            # the B2 publish already succeeded; argos/mac-latest is a follow-up
            extra = {"argos_error": "%s: %s" % (type(e).__name__, e)}
            print("[nzd_b2_daily] AVISO argos/mac-latest: %s: %s" % (type(e).__name__, e), file=sys.stderr)
    return {"rc_nzd": rc_nzd, "rc_chf": rc_chf, "rc_tona": rc_tona, "rc_push": rc_push,
            "proxy": env["G8_RBNZ_PROXY_BASE"], "ok": rc_push == 0,
            "fetch_records": staged, "argos": extra}


def main(argv=None):
    ap = argparse.ArgumentParser(description="VPS nzd-b2 diario (t334u)")
    ap.add_argument("--workdir", default=os.environ.get("G8_NZD_WORKDIR", ROOT_DEFAULT))
    ap.add_argument("--state", default=os.environ.get("G8_VPS_JOB_STATE", "/srv/g8/prod/state/vps_jobs/nzd-b2.json"))
    args = ap.parse_args(argv)
    token = os.environ.get("G8_FULL", "").strip()
    prev = load_prev(args.state)
    rec = {"job": "nzd-b2", "utc": _utcnow(), "ok": False, "rc": 1, "consecutive_fail": 0}
    try:
        if not token:
            raise RuntimeError("G8_FULL ausente")
        lock = os.environ.get("G8_NZD_LOCK") or "/srv/g8/prod/state/locks/nzd-b2.lock"
        with job_flock.exclusive(lock):
            result = run(args.workdir, token)
        rec.update(result)
        rec["ok"] = bool(result["ok"])
        rec["rc"] = 0 if result["ok"] else 1
        rec["consecutive_fail"] = 0 if rec["ok"] else int(prev.get("consecutive_fail") or 0) + 1
    except Exception as e:
        rec["ok"] = False
        rec["rc"] = 1
        rec["error_type"] = type(e).__name__
        rec["error"] = str(e)[:400]
        rec["consecutive_fail"] = int(prev.get("consecutive_fail") or 0) + 1
        print("[nzd_b2_daily] FATAL: %s: %s" % (type(e).__name__, e), file=sys.stderr)
        traceback.print_exc()
    write_state(args.state, rec)
    print("[nzd_b2_daily] ok=%s rcs nzd=%s chf=%s tona=%s push=%s fail_streak=%s" % (
        rec.get("ok"), rec.get("rc_nzd"), rec.get("rc_chf"), rec.get("rc_tona"),
        rec.get("rc_push"), rec.get("consecutive_fail")))
    return rec["rc"]


if __name__ == "__main__":
    sys.exit(main())

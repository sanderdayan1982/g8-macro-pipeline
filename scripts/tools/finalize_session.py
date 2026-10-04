#!/usr/bin/env python3
"""Finalize the latest elapsed 21:30 UTC TARGET session, even after midnight.

Operational wrapper only: the frozen S01B detector and historical logs are untouched.
Never backfills missed sessions using today's revised model. A skipped interval is
reported explicitly by the health monitor; the next final resumes from stored state.
"""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from s01b import is_target_day


def session_due(now):
    now = now.astimezone(timezone.utc)
    day = now.date()
    if (now.hour, now.minute) < (21, 30):
        day -= timedelta(days=1)
    while not is_target_day(day):
        day -= timedelta(days=1)
    return day


def main():
    target = session_due(datetime.now(timezone.utc)).isoformat()
    rc = subprocess.call([sys.executable, str(ROOT / "scripts/s01b.py"), "--final", "--date", target], cwd=ROOT)
    if rc:
        return rc
    try:
        log = json.loads((ROOT / "data/s01b/log" / (target + ".json")).read_text())
        state = json.loads((ROOT / "data/s01b/state.json").read_text())
        if log.get("t") != target or not log.get("final_run") or state.get("t") != target:
            raise ValueError("session did not finalize")
    except (OSError, ValueError) as exc:
        print("FINAL_FAILED: %s: %s" % (target, exc), file=sys.stderr)
        return 1
    print("FINAL_VERIFIED: " + target)
    return 0


if __name__ == "__main__":
    sys.exit(main())

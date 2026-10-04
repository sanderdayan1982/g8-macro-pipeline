#!/usr/bin/env python3
"""Bound repair loops within the alternate-day workflow; defensive attempt limits.
One preventive review on each scheduled active day when there is no incident. Manual dispatch is explicit.
State records attempts (including failed ones), never a claim of repaired feeds.
"""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def decide(health, state, now, manual=False):
    today = now.date().isoformat()
    attempts = [a for a in state.get("attempts", []) if a.get("at", "").startswith(today)]
    if manual:
        return True, "manual"
    if len(attempts) >= 2:
        return False, "límite de 2 intentos automáticos/día; incidencia sigue visible"
    last = state.get("last_attempt")
    if last and now - datetime.fromisoformat(last.replace("Z", "+00:00")) < timedelta(hours=4):
        return False, "cooldown 4h"
    if health.get("needs_repair") or not attempts:
        return True, "incidencias" if health.get("needs_repair") else "revisión preventiva del día activo"
    return False, "revisión del día activo ya realizada"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manual", action="store_true")
    ap.add_argument("--record")
    a = ap.parse_args()
    p = ROOT / "data/_ingest/maintenance_state.json"
    state = json.loads(p.read_text()) if p.exists() else {}
    now = datetime.now(timezone.utc)
    if a.record:
        stamp = now.isoformat().replace("+00:00", "Z")
        recent = [x for x in state.get("attempts", []) if x.get("at", "")[:10] >= (now.date() - timedelta(days=7)).isoformat()]
        recent.append({"at": stamp, "result": a.record})
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"last_attempt": stamp, "attempts": recent}, indent=1) + "\n")
    else:
        h = json.loads((ROOT / ".agent/health.json").read_text())
        go, reason = decide(h, state, now, a.manual)
        print("run=" + str(go).lower())
        print("reason=" + reason)


if __name__ == "__main__":
    main()

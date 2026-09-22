#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/equiv_s01b_e2.py — E2 equivalence proof for §01-b (ACTA_S01B, enmienda E2, 2026-09-22). Supersedes equiv_s01b_e1.py in CI.

Same method as E1: a PUBLISHED session (log + frozen snapshot) is re-run with the CURRENT engine, with only the E1 context
legs swapped in from live files, and every detector field must be byte-identical to the log: ACM ends, ΔTP/ΔRNY/ΔFIT,
θ/θ_off/θ95, n_pop, ΔFX, avail, signal, persist, fx_fail_streak, colour, events (type, signal, d_tp, θ, d_fx).
Allowed to differ, and reported:
  · E1 context legs (d_2y AUD/CAD, d_be NZD, their context_asof) and the context_last block;
  · E2 reading-only fields acm_input_asof_t / acm_input_asof_t0 (new) and the flag ACM_FFILL in `flags` (row and event) —
    the E2 flag is stripped before comparing flags, so any OTHER flag change is still a DIFF.
Exit 0 = EQUIVALENT, 1 = DIFFERENCES, 2 = cannot run.

Run:  python3 tests/equiv_s01b_e2.py 2026-09-21
      python3 tests/equiv_s01b_e2.py 2026-09-22 --synthetic
"""
import gzip
import json
import os
import sys
from datetime import date, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import s01b  # noqa: E402

REPAIRED = {"AUD": {"y2"}, "CAD": {"y2"}, "NZD": {"be"}}      # E1 legs
LEG_FIELD = {"y2": "d_2y", "be": "d_be", "nominal": "d_nom"}
NEW_FIELDS = {"context_last", "acm_input_asof_t", "acm_input_asof_t0"}   # E1 + E2 reading-only additions
E2_FLAGS = {"ACM_FFILL"}


def synthetic(t):
    out, d, i = [], t, 0
    while len(out) < 450:
        if d.weekday() < 5:
            out.append((d, 3.0 + 0.002 * (450 - i)))
        d -= timedelta(days=1)
        i += 1
    out.sort()
    return out


def strip_flags(v):
    return [f for f in (v or []) if f not in E2_FLAGS]


def main(t_iso, synth=False):
    data = os.path.join(ROOT, "data")
    lg_path = os.path.join(data, "s01b", "log", t_iso + ".json")
    snap = os.path.join(data, "s01b", "snap", t_iso, "inputs.json.gz")
    if not (os.path.exists(lg_path) and os.path.exists(snap)):
        print("equiv: no log/snap for %s" % t_iso)
        return 2
    lg = json.load(open(lg_path, encoding="utf-8"))
    with gzip.open(snap, "rb") as fh:
        inp = json.loads(fh.read(), object_hook=lambda x: date.fromisoformat(x["__date__"]) if set(x) == {"__date__"} else x)
    swapped = []
    for c, legs in REPAIRED.items():
        for leg in legs:
            files = s01b.Y2_FILES if leg == "y2" else s01b.BE_FILES
            col = "BE10" if leg == "be" else None
            path = os.path.join(data, files[c])
            ser = s01b.read_series(path, col=col) if os.path.exists(path) else []
            src = files[c]
            if synth and leg == "y2" and c in ("AUD", "CAD"):
                ser, src = synthetic(date.fromisoformat(t_iso)), "SYNTHETIC"
            inp[leg][c] = ser
            swapped.append("%s %s: %s (%d obs, last %s)" % (c, leg, src, len(ser), ser[-1][0].isoformat() if ser else "—"))
    state = {r["ccy"]: r["state_before"] for r in lg["rows"]}
    t = date.fromisoformat(t_iso)
    rows, events, err = s01b.run_session(t, inp, state, date.fromisoformat(lg["fx_effective"]), lg["final_run"], data)
    if err:
        print("equiv: engine error %s" % err)
        return 2
    diffs, allowed = [], []
    for new, old in zip(rows, lg["rows"]):
        c = new["ccy"]
        for k in sorted(set(new) | set(old)):
            if k in NEW_FIELDS:
                if k != "context_last" and new.get(k) is not None:
                    allowed.append("%s.%s (E2 reading): %s" % (c, k, new.get(k)))
                continue
            a, b = old.get(k), new.get(k)
            if k == "flags":
                a, b = strip_flags(a), strip_flags(b)
                if set(new.get("flags") or []) & E2_FLAGS:
                    allowed.append("%s.flags (E2): +%s" % (c, ",".join(sorted(set(new["flags"]) & E2_FLAGS))))
            if a == b:
                continue
            legs = REPAIRED.get(c, set())
            if k in {LEG_FIELD[l] for l in legs}:
                allowed.append("%s.%s: %s → %s" % (c, k, old.get(k), new.get(k)))
            elif k == "context_asof" and all(a.get(n) == b.get(n) for n in a if n not in legs):
                allowed.append("%s.context_asof[%s]: %s → %s" % (c, ",".join(sorted(legs)), {l: a.get(l) for l in legs}, {l: b.get(l) for l in legs}))
            else:
                diffs.append("%s.%s: %s → %s" % (c, k, a, b))
    ev_old = [dict(e, flags=strip_flags(e.get("flags"))) for e in lg["events"]]
    ev_new = [dict(e, flags=strip_flags(e.get("flags"))) for e in events]
    if ev_old != ev_new:
        diffs.append("events differ: %s vs %s" % (lg["events"], events))
    print("equiv %s · engine %s vs log %s%s" % (t_iso, s01b.VERSION, lg.get("version"), " · SYNTHETIC AUD/CAD 2Y" if synth else ""))
    for s in swapped:
        print("  swapped  " + s)
    for s in allowed:
        print("  allowed  " + s)
    for s in diffs:
        print("  DIFF     " + s)
    if diffs:
        print("RESULT: DIFFERENCES — detector not equivalent, do not publish")
        return 1
    print("RESULT: EQUIVALENT — every detector field identical; only E1 context legs and E2 reading-only fields changed")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sys.exit(main(args[0] if args else date.today().isoformat(), synth="--synthetic" in sys.argv))

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/equiv_s01b_e1.py — E1 equivalence proof for §01-b (ACTA_S01B, enmienda E1).

Takes a PUBLISHED session (log + snapshot) produced by the previous engine, feeds the frozen inputs of that
snapshot to the CURRENT engine with ONLY the repaired context legs swapped in from live data files, and
checks that every detector field is byte-identical to the log: ACM ends, ΔTP/ΔRNY/ΔFIT, θ/θ_off/θ95,
n_pop, ΔFX, avail, flags, signal, persist, fx_fail_streak, colour, events. Only the repaired context
fields (d_2y for AUD/CAD, d_be for NZD, their context_asof entries) and the new context_last block may
differ. Exit 0 = EQUIVALENT, 1 = DIFFERENCES, 2 = cannot run.

Run:  python3 tests/equiv_s01b_e1.py 2026-09-21              (repo root; needs data/s01b/log+snap of that day; live legs from data/)
      python3 tests/equiv_s01b_e1.py 2026-09-21 --synthetic  (same, but AUD/CAD 2Y replaced by a synthetic non-empty series so the
                                                              non-empty path is exercised even before the first workflow run)
"""
import gzip
import json
import os
import sys
from datetime import date, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import s01b  # noqa: E402

REPAIRED = {"AUD": {"y2"}, "CAD": {"y2"}, "NZD": {"be"}}      # legs E1 connects; everything else must not move
LEG_FIELD = {"y2": "d_2y", "be": "d_be", "nominal": "d_nom"}
NEW_FIELDS = {"context_last"}                                   # added by E1 for every currency


def synthetic(t):
    """Business-day series ending at t, 450 obs, deterministic."""
    out, d, i = [], t, 0
    while len(out) < 450:
        if d.weekday() < 5:
            out.append((d, 3.0 + 0.002 * (450 - i)))
        d -= timedelta(days=1)
        i += 1
    out.sort()
    return out


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
    # swap ONLY the repaired legs from live files (same reader the engine uses)
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
        keys = set(new) | set(old)
        for k in sorted(keys):
            if k in NEW_FIELDS:
                continue
            a, b = old.get(k), new.get(k)
            if a == b:
                continue
            legs = REPAIRED.get(c, set())
            if k in {LEG_FIELD[l] for l in legs}:
                allowed.append("%s.%s: %s → %s" % (c, k, a, b))
            elif k == "context_asof" and all(a.get(n) == b.get(n) for n in a if n not in legs):
                allowed.append("%s.context_asof[%s]: %s → %s" % (c, ",".join(sorted(legs)), {l: a.get(l) for l in legs}, {l: b.get(l) for l in legs}))
            else:
                diffs.append("%s.%s: %s → %s" % (c, k, a, b))
    if events != lg["events"]:
        diffs.append("events differ: %s vs %s" % (lg["events"], events))
    print("equiv %s · engine %s vs log %s%s" % (t_iso, s01b.VERSION, lg.get("version"), " · SYNTHETIC AUD/CAD 2Y" if synth else ""))
    for s in swapped:
        print("  swapped  " + s)
    for s in allowed:
        print("  repaired " + s)
    for s in diffs:
        print("  DIFF     " + s)
    if diffs:
        print("RESULT: DIFFERENCES — detector not equivalent, do not publish")
        return 1
    print("RESULT: EQUIVALENT — every detector field identical; only repaired context legs changed")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sys.exit(main(args[0] if args else date.today().isoformat(), synth="--synthetic" in sys.argv))

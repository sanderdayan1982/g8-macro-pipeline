#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_s01b.py — state machine, window and threshold rules of s01b.py (ESPEC §3). Run: python3 tests/test_s01b.py"""
import math
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import s01b  # noqa: E402

H = s01b.H


def calendar(n, start=date(2022, 1, 3)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def acm_series(cal, tp_fn, quality="ACM_K3_400m"):
    return [(d, tp_fn(i), 2.0, 2.0 + tp_fn(i), quality) for i, d in enumerate(cal)]


def run(cal, acm, r, f, state, pos, fx_eff=None, ccy="EUR"):
    t = cal[pos]
    return s01b.evaluate_ccy(ccy, t, pos, cal, acm, [], [], [], r, f, fx_eff or t, {"initialized": True, **state})


def base_inputs(n=400, tp_last=None):
    cal = calendar(n)
    # flat TP with tiny noise, then a jump in the last 22 sessions if tp_last given
    def tp(i):
        base = 0.50 + 0.10 * math.sin(i / 7.0)              # ΔTP22 spread ≈ ±20 bp → p80 ≈ +12, p60 ≈ +4
        if tp_last is not None and i >= n - H:
            return base + tp_last
        return base
    acm = acm_series(cal, tp)
    r = [0.0] * n
    f = [0.0] * n
    return cal, acm, r, f


def test_no_cal_below_252():
    cal, acm, r, f = base_inputs(n=200)
    row, ev = run(cal, acm, r, f, {}, len(cal) - 1)
    assert row["avail"] == "NO_CAL" and ev is None and row["signal"] == "OFF"


def test_threshold_excludes_t():
    cal, acm, r, f = base_inputs(n=400, tp_last=0.30)       # +30 bp jump only inside the last window
    pos = len(cal) - 1
    pop = s01b.dtp_population(acm, cal, cal[pos])
    assert all(v < 25 for v in pop[:-H]) and len(pop) >= 252
    row, ev = run(cal, acm, r, f, {}, pos)
    assert row["d_tp"] > 25 and row["theta"] < 20 and row["n_pop"] == len(pop)


def test_on_requires_positive_and_fx():
    cal, acm, r, f = base_inputs(n=400, tp_last=0.30)
    pos = len(cal) - 1
    row, ev = run(cal, acm, r, f, {}, pos)                # FX flat → OFF
    assert row["signal"] == "OFF" and ev is None
    r2 = list(r)
    r2[pos - 5] = -0.008                                 # currency −0.8 % inside (t0, t]
    row, ev = run(cal, acm, r2, f, {}, pos)
    assert row["signal"] == "ON" and ev and ev["type"] == "ON" and row["persist"] == 1
    r3 = list(r)
    r3[pos - H] = -0.05                                  # −5 % but at t0 itself → outside (t0, t] → excluded
    row, ev = run(cal, acm, r3, f, {}, pos)
    assert row["signal"] == "OFF"


def test_negative_tp_never_on_even_if_above_theta():
    cal = calendar(400)
    # population strongly negative so that p80 is negative; today's ΔTP = −5 bp is above p80 but ≤ 0
    acm = acm_series(cal, lambda i: 3.0 - 0.02 * i)      # steadily falling TP → ΔTP22 ≈ −44 bp always
    acm2 = acm[:-H] + [(d, acm[-H][1] - 0.0005 * k, 2.0, 2.5, "ACM_K3_400m") for k, (d, *_x) in enumerate(acm[-H:])]
    r = [-0.001] * 400
    row, ev = run(cal, acm2, r, [0.0] * 400, {}, 399)
    assert row["theta"] < 0 and row["d_tp"] <= 0 and row["signal"] == "OFF" and row["colour"] == "white"


def test_hysteresis_and_fx_streak():
    cal, acm, r, f = base_inputs(n=400, tp_last=0.30)
    pos = len(cal) - 1
    st = {"signal": "ON", "persist": 4, "fx_fail_streak": 0}
    row, ev = run(cal, acm, r, f, st, pos)                # FX condition fails (flat) → streak 1, still ON
    assert row["signal"] == "ON" and row["fx_fail_streak"] == 1 and row["persist"] == 5 and ev is None
    st = {"signal": "ON", "persist": 6, "fx_fail_streak": 2}
    row, ev = run(cal, acm, r, f, st, pos)                # third failure → OFF, no event
    assert row["signal"] == "OFF" and row["persist"] == 1 and ev is None
    # ΔTP between p60 and p80 keeps ON (hysteresis) when FX ok
    cal, acm, r, f = base_inputs(n=400, tp_last=0.30)
    r[pos - 3] = -0.01
    probe, _ = run(cal, acm, r, f, {}, pos)
    mid = (probe["theta"] + probe["theta_off"]) / 2.0        # ΔTP between p60 and p80
    assert probe["theta"] > probe["theta_off"] > 0
    acm_h = acm[:-1] + [(acm[-1][0], acm[-1][1] - (probe["d_tp"] - mid) / 100.0, 2.0, 2.5, "ACM_K3_400m")]
    row_off, _ = run(cal, acm_h, r, f, {"signal": "OFF", "persist": 1, "fx_fail_streak": 0}, pos)
    row_on, _ = run(cal, acm_h, r, f, {"signal": "ON", "persist": 1, "fx_fail_streak": 0}, pos)
    assert row_off["signal"] == "OFF" and row_on["signal"] == "ON" and row_on["theta_off"] <= row_on["d_tp"] < row_on["theta"]


def test_availability_freezes_signal():
    cal, acm, r, f = base_inputs(n=400, tp_last=0.30)
    pos = len(cal) - 1
    acm_short = [a for a in acm if a[0] < cal[pos - 10]]   # ACM stops 10 sessions ago → lag > 3 bd
    st = {"signal": "ON", "persist": 7, "fx_fail_streak": 1}
    row, ev = run(cal, acm_short, r, f, st, pos)
    assert row["avail"] == "NO_DATA" and row["signal"] == "ON" and row["persist"] == 7 and row["fx_fail_streak"] == 1 and ev is None


def test_quality_gate_and_chf_reading():
    cal, acm, r, f = base_inputs(n=400, tp_last=0.30)
    pos = len(cal) - 1
    acm_nc = acm[:-1] + [(acm[-1][0], acm[-1][1], 2.0, 2.5, "ACM_K3_NOWCAST_PARALLEL")]
    row, ev = run(cal, acm_nc, r, f, {}, pos)
    assert row["avail"] == "NO_QUAL" and "NOWCAST" in row["flags"] and ev is None
    acm_short = acm_series(cal, lambda i: acm[i][1], quality="ACM_K3_SHORT_SAMPLE_136m")
    r2 = list(r); r2[pos - 2] = -0.01
    row, ev = run(cal, acm_short, r2, f, {}, pos, ccy="NZD")
    assert row["avail"] == "OK" and "SHORT" in row["flags"] and row["signal"] == "ON"
    row, ev = run(cal, acm, r2, f, {}, pos, ccy="CHF")
    assert row["signal"] == "NA" and row["d_fx"] is None and row["d_tp"] is not None and ev is None


def test_desfase_uses_previous_fx_window():
    cal, acm, r, f = base_inputs(n=400, tp_last=0.30)
    pos = len(cal) - 1
    cal2 = cal + [cal[-1] + timedelta(days=1)]           # session t not yet in the FX calendar
    r2 = list(r); r2[pos - 1] = -0.01
    row, ev = run(cal2, acm + [(cal2[-1], acm[-1][1] + 0.20, 2.0, 2.5, "ACM_K3_400m")], r2, f, {}, pos + 1, fx_eff=cal[-1])
    assert "DESFASE" in row["flags"] and row["avail"] == "OK" and row["d_fx"] is not None and row["signal"] == "ON"


def load_tests(loader, tests, pattern):
    import unittest
    return unittest.TestSuite(unittest.FunctionTestCase(fn) for name, fn in sorted(globals().items()) if name.startswith("test_") and callable(fn))


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok   " + name)
            except AssertionError as e:
                fails += 1
                print("FAIL " + name + " " + str(e))
    print("%d failures" % fails)
    sys.exit(1 if fails else 0)

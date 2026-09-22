#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_s01b_e2.py — enmienda E2 §01-b (2026-09-22). unittest-compatible (CI discover) and runnable as a script.

  python3 tests/test_s01b_e2.py

Covers, with test data (no network, isolated temp data dir):
  F1 ACM_FFILL raised when the long-end probe (AUD_NOM_2Y / CAD_NOM_2Y, same connector as the ACM curve) lags the ACM row date
  F2 no flag when the probe date equals the ACM row date · F3 currencies without probe get no field
  F4 the probe never moves a detector field (avail, ΔTP, θ, ΔFX, signal, persist, colour, events)
  V1 a run WITHOUT --final publishes a PROVISIONAL S01B.json only: no log, no snap, no state, no events; signal/persist
     are those of the last --final state (not advanced); run_id ends in 'P'
  V2 the --final run then evaluates (log + snap + state + S01B.json, provisional absent); the provisional preview
     did not consume the write-once slot
  V3 a later run without --final on an evaluated session re-publishes from the log (never downgrades to provisional)
  V4 --final on an evaluated session is idempotent (one evaluation per session)
"""
import csv
import json
import math
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import s01b  # noqa: E402

H = s01b.H


def bcal(n, start=date(2022, 1, 3)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5 and s01b.is_target_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def acm_series(cal, quality="ACM_K3_400m", jump=0.0):
    n = len(cal)
    return [(d, 0.50 + 0.10 * math.sin(i / 7.0) + (jump if i >= n - H else 0.0), 2.0, 2.5 + 0.10 * math.sin(i / 7.0), quality)
            for i, d in enumerate(cal)]


def detector_fields(row):
    skip = {"acm_input_asof_t", "acm_input_asof_t0", "flags", "context_asof", "context_last", "d_2y", "resid", "d_nom", "d_be"}
    return {k: v for k, v in row.items() if k not in skip}


class AcmInputProbe(unittest.TestCase):
    def setUp(self):
        self.cal = bcal(400)
        self.pos = 399
        self.acm = acm_series(self.cal)
        self.r = [0.0] * 400
        self.f = [0.0] * 400

    def _row(self, ccy, y2):
        return s01b.evaluate_ccy(ccy, self.cal[self.pos], self.pos, self.cal, self.acm, [], y2, [], self.r, self.f,
                                 self.cal[self.pos], {"initialized": True})

    def test_F1_flag_when_probe_lags(self):
        y2 = [(d, 3.0) for d in self.cal[:-4]]                       # connector stopped 4 sessions before t
        row, ev = self._row("AUD", y2)
        self.assertEqual(row["acm_asof_t"], self.cal[self.pos].isoformat())
        self.assertEqual(row["acm_input_asof_t"], self.cal[self.pos - 4].isoformat())
        self.assertIn("ACM_FFILL", row["flags"])
        self.assertEqual(row["avail"], "OK")                          # reading only: never gates
        self.assertEqual(row["acm_input_asof_t0"], self.cal[self.pos - H].isoformat())

    def test_F2_no_flag_when_probe_current(self):
        y2 = [(d, 3.0) for d in self.cal]
        row, _ = self._row("CAD", y2)
        self.assertEqual(row["acm_input_asof_t"], row["acm_asof_t"])
        self.assertNotIn("ACM_FFILL", row["flags"])

    def test_F3_no_probe_for_other_currencies(self):
        y2 = [(d, 3.0) for d in self.cal[:-4]]
        row, _ = self._row("EUR", y2)
        self.assertNotIn("acm_input_asof_t", row)
        self.assertNotIn("ACM_FFILL", row["flags"])

    def test_F4_probe_never_moves_detector(self):
        acm = acm_series(self.cal, jump=0.30)                         # ΔTP well above θ → ON path exercised
        r = [0.0] * 400
        r[-H:] = [-0.001] * H                                          # FX condition met
        base = s01b.evaluate_ccy("AUD", self.cal[self.pos], self.pos, self.cal, acm, [], [], [], r, self.f, self.cal[self.pos], {"initialized": True})
        probe = s01b.evaluate_ccy("AUD", self.cal[self.pos], self.pos, self.cal, acm, [], [(d, 3.0) for d in self.cal[:-3]], [], r, self.f,
                                  self.cal[self.pos], {"initialized": True})
        self.assertEqual(base[0]["signal"], "ON")
        self.assertEqual(detector_fields(base[0]), detector_fields(probe[0]))
        self.assertIn("ACM_FFILL", probe[0]["flags"])
        ea, eb = dict(base[1]), dict(probe[1])
        ea.pop("flags", None), eb.pop("flags", None)                   # the event carries the reading flag, nothing else moves
        self.assertEqual(ea, eb)


class ProvisionalThenFinal(unittest.TestCase):
    """Drives main() end to end in an isolated data dir (no network, no Telegram)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="s01b_e2_")
        self.data = os.path.join(self.tmp, "data")
        os.makedirs(os.path.join(self.data, "usd_factor"))
        self.saved = {k: getattr(s01b, k) for k in ("DATA", "S01B", "LOG_DIR", "SNAP_DIR", "STAGE", "STATE_PATH", "EVENTS_PATH", "OUT_JSON")}
        s01b.DATA = self.data
        s01b.S01B = os.path.join(self.data, "s01b")
        s01b.LOG_DIR, s01b.SNAP_DIR, s01b.STAGE = (os.path.join(s01b.S01B, "log"), os.path.join(s01b.S01B, "snap"), os.path.join(s01b.S01B, ".staging"))
        s01b.STATE_PATH, s01b.EVENTS_PATH, s01b.OUT_JSON = (os.path.join(s01b.S01B, "state.json"), os.path.join(s01b.S01B, "events.jsonl"),
                                                            os.path.join(self.data, "S01B.json"))
        os.makedirs(s01b.S01B)
        cal = bcal(420)
        self.t = cal[-1]
        # ACM for the 8 currencies (identical synthetic curves, ΔTP well above θ in the last 22 sessions)
        for c in s01b.CCY8:
            with open(os.path.join(self.data, "ACM_G8_%s.csv" % c), "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["DATE", "Y10_FIT", "RNY10", "TP10", "QUALITY"])
                for d, tp, rny, fit, q in acm_series(cal, jump=0.30):
                    w.writerow([d.strftime("%Y%m%d"), "%.4f" % fit, "%.4f" % rny, "%.4f" % tp, q])
        # canonical: FX falls −0,1 %/session for every currency (condition met), f = +0,1 %
        cols = ["", "f"] + ["r_%s" % c for c in s01b.CCY8 if c != "USD"] + ["desfase_us"]
        with open(os.path.join(self.data, "usd_factor", "canonical.csv"), "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            for d in cal:
                w.writerow([d.isoformat(), "0.001"] + ["-0.001"] * 7 + ["False"])
        # a previous --final state: EUR ON persist 3, rest OFF persist 5
        prev = {c: {"signal": "ON" if c == "EUR" else "OFF", "persist": 3 if c == "EUR" else 5, "fx_fail_streak": 0,
                    "avail": "OK", "last_eval": cal[-2].isoformat(), "initialized": True} for c in s01b.CCY8}
        s01b.atomic_write(s01b.STATE_PATH, json.dumps({"version": "test", "run_id": "x", "t": cal[-2].isoformat(), "currencies": prev}))

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(s01b, k, v)

    def _main(self, *args):
        return s01b.main(list(args) + ["--date", self.t.isoformat()])

    def test_V1_V2_V3_V4_provisional_then_final(self):
        logp = os.path.join(s01b.LOG_DIR, self.t.isoformat() + ".json")
        # V1 — provisional
        self.assertEqual(self._main(), 0)
        out = json.load(open(s01b.OUT_JSON, encoding="utf-8"))
        self.assertTrue(out.get("provisional"))
        self.assertTrue(out["run_id"].endswith("P"))
        self.assertEqual(out["events"], [])
        self.assertFalse(os.path.exists(logp))
        self.assertFalse(os.path.isdir(os.path.join(s01b.SNAP_DIR, self.t.isoformat())))
        self.assertFalse(os.path.exists(s01b.EVENTS_PATH))
        st = json.load(open(s01b.STATE_PATH, encoding="utf-8"))
        self.assertEqual(st["run_id"], "x")                             # state untouched
        self.assertEqual(out["currencies"]["EUR"]["signal"], "ON")
        self.assertEqual(out["currencies"]["EUR"]["persist"], 3)       # not advanced
        self.assertEqual(out["currencies"]["USD"]["signal"], "OFF")
        self.assertEqual(out["currencies"]["USD"]["persist"], 5)
        self.assertGreater(out["currencies"]["GBP"]["d_tp"], out["currencies"]["GBP"]["theta"])   # components are live
        self.assertEqual(out["currencies"]["GBP"]["signal"], "OFF")    # …but the preview does not turn it ON
        self.assertIn("provisional", out["currencies"]["USD"]["signal_note"])
        # V2 — final evaluates
        self.assertEqual(self._main("--final"), 0)
        out2 = json.load(open(s01b.OUT_JSON, encoding="utf-8"))
        self.assertNotIn("provisional", out2)
        self.assertTrue(os.path.exists(logp))
        self.assertTrue(os.path.isdir(os.path.join(s01b.SNAP_DIR, self.t.isoformat())))
        self.assertEqual(out2["currencies"]["GBP"]["signal"], "ON")    # ΔTP ≥ θ and ΔFX ≤ −0,5 % → turns ON in the evaluation
        self.assertEqual(out2["currencies"]["GBP"]["persist"], 1)
        self.assertEqual(out2["currencies"]["USD"]["signal"], "OFF")   # USD reads f (+2,2 %): FX condition not met
        self.assertEqual(out2["currencies"]["USD"]["persist"], 6)
        self.assertEqual(out2["currencies"]["EUR"]["persist"], 4)      # ON stays ON, counter advances once
        self.assertTrue(any(e["ccy"] == "GBP" and e["type"] == "ON" for e in out2["events"]))
        st2 = json.load(open(s01b.STATE_PATH, encoding="utf-8"))
        self.assertEqual(st2["run_id"], out2["run_id"])
        # V3 — provisional after final: republish from log, never downgrade
        self.assertEqual(self._main(), 0)
        out3 = json.load(open(s01b.OUT_JSON, encoding="utf-8"))
        self.assertEqual(out3["run_id"], out2["run_id"])
        self.assertNotIn("provisional", out3)
        # V4 — final again: idempotent
        self.assertEqual(self._main("--final"), 0)
        out4 = json.load(open(s01b.OUT_JSON, encoding="utf-8"))
        self.assertEqual(out4["run_id"], out2["run_id"])
        self.assertEqual(json.load(open(logp, encoding="utf-8"))["run_id"], out2["run_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

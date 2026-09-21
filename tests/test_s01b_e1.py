#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_s01b_e1.py — enmienda E1 §01-b (2026-09-21). unittest-compatible (CI discover) and runnable as a script.

  python3 tests/test_s01b_e1.py            # 9 tests with synthetic data (no network)
  python3 tests/test_s01b_e1.py --live [--session 2026-09-22]
                                           # + 2 tests that DOWNLOAD the AUD (RBA F2) and CAD (BoC Valet) 2Y through acm_g8's own
                                           #   connectors, persist, read and evaluate on an EXPLICIT TARGET session (default: last
                                           #   TARGET day ≤ today), reporting separately «descarga y cálculo correctos» and
                                           #   «vigente / vigencia pendiente» for that session — run on the Mac (network)
Covers, with test data:
  P1 empty response never clobbers a valid CSV · P2 NaN/invalid response idem · P3 partial download MERGES (history kept, overlap
  refreshed) · P4 first write from nothing
  C1 STALE (last obs beyond tolerance) · C2 SHORT (observation today, no history at t0 — must NOT read as «atrasado hoy») ·
  C3 NONE (empty) and NONE (series starts after t) · C4 OK
  X1 AUD/CAD: synthetic 2Y → persist_extra → s01b.read_series → evaluate_ccy → Δ2Y = (v_t − v_t0)·100
  X2 the connection leaves every detector field untouched (row equality except the context legs)
With --live: L1/L2 real download AUD/CAD → persisted → read → Δ2Y (skipped without network / without --live).
"""
import math
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import s01b  # noqa: E402

LIVE = "--live" in sys.argv
if LIVE:
    sys.argv.remove("--live")
SESSION = None                                   # --session YYYY-MM-DD: explicit TARGET session for the live checks
if "--session" in sys.argv:
    i = sys.argv.index("--session")
    SESSION = date.fromisoformat(sys.argv[i + 1])
    del sys.argv[i:i + 2]
try:
    import pandas as pd  # noqa: E402
    import acm_g8  # noqa: E402
    HAVE_ACM = True
except Exception:                                                        # noqa: BLE001
    HAVE_ACM = False

H = s01b.H


def bcal(n, start=date(2022, 1, 3)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def acm_series(cal, quality="ACM_K3_400m"):
    return [(d, 0.50 + 0.10 * math.sin(i / 7.0), 2.0, 2.5 + 0.10 * math.sin(i / 7.0), quality) for i, d in enumerate(cal)]


def read_csv_rows(path):
    with open(path, encoding="utf-8") as fh:
        return [l for l in fh.read().splitlines() if l.strip()]


@unittest.skipUnless(HAVE_ACM, "pandas/acm_g8 not importable")
class PersistExtra(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "AUD_NOM_2Y.csv")
        idx = pd.bdate_range("2026-08-03", periods=30)
        self.good = pd.Series([3.5 + 0.01 * i for i in range(30)], index=idx)
        self.assertEqual(acm_g8.persist_extra("AUD", 2, self.good, data_dir=self.tmp), 30)
        self.before = read_csv_rows(self.path)

    def test_P1_empty_response_keeps_file(self):
        n = acm_g8.persist_extra("AUD", 2, pd.Series(dtype=float), data_dir=self.tmp)
        self.assertEqual(n, 0)
        self.assertEqual(read_csv_rows(self.path), self.before)
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_P2_nan_response_keeps_file(self):
        bad = pd.Series([float("nan")] * 5, index=pd.bdate_range("2026-09-14", periods=5))
        self.assertEqual(acm_g8.persist_extra("AUD", 2, bad, data_dir=self.tmp), 0)
        self.assertEqual(read_csv_rows(self.path), self.before)
        self.assertEqual(acm_g8.persist_extra("AUD", 2, None, data_dir=self.tmp), 0)
        self.assertEqual(read_csv_rows(self.path), self.before)

    def test_P3_partial_download_merges(self):
        idx = pd.bdate_range("2026-09-07", periods=10)                    # overlaps the last 5 rows (7–11 sep), adds 5 new
        part = pd.Series([9.0] * 10, index=idx)
        n = acm_g8.persist_extra("AUD", 2, part, data_dir=self.tmp)
        self.assertEqual(n, 35)
        ser = s01b.read_series(self.path)
        self.assertEqual(len(ser), 35)
        self.assertEqual(ser[0][0], date(2026, 8, 3))                        # history kept
        self.assertEqual(ser[-1][0], date(2026, 9, 18))
        self.assertEqual(ser[-1][1], 9.0)                                    # fresh download wins on overlap
        self.assertEqual(dict(ser)[date(2026, 9, 4)], round(3.5 + 0.01 * 24, 4))

    def test_P4_first_write(self):
        p2 = os.path.join(self.tmp, "CAD_NOM_2Y.csv")
        self.assertFalse(os.path.exists(p2))
        n = acm_g8.persist_extra("CAD", 2, self.good, data_dir=self.tmp)
        self.assertEqual(n, 30)
        self.assertEqual(read_csv_rows(p2)[0], "Date,Value,Source")
        self.assertEqual(acm_g8.persist_extra("CAD", 5, self.good, data_dir=self.tmp), 0)   # 5Y not persisted


class ContextLast(unittest.TestCase):
    def setUp(self):
        self.cal = bcal(400)
        self.pos = 399
        self.t, self.t0 = self.cal[self.pos], self.cal[self.pos - H]
        self.acm = acm_series(self.cal)

    def run_ccy(self, y2, be=None, nom=None, ccy="AUD"):
        r, f = [0.0] * 400, [0.0] * 400
        row, _ = s01b.evaluate_ccy(ccy, self.t, self.pos, self.cal, self.acm, nom or [], y2, be or [], r, f, self.t, {"initialized": True})
        return row

    def test_C1_stale(self):
        y2 = [(d, 3.0) for d in self.cal if d <= self.cal[self.pos - 10]]   # stops 10 bd before t
        row = self.run_ccy(y2)
        self.assertIsNone(row["d_2y"])
        cl = row["context_last"]["y2"]
        self.assertEqual(cl["status"], "STALE")
        self.assertEqual(cl["date"], self.cal[self.pos - 10].isoformat())
        self.assertEqual(cl["lag_bd"], 10)

    def test_C2_short_today_without_history(self):
        y2 = [(self.t, 3.0)]                                                 # one observation, dated today
        row = self.run_ccy(y2)
        self.assertIsNone(row["d_2y"])
        cl = row["context_last"]["y2"]
        self.assertEqual(cl["status"], "SHORT")                              # not STALE: the end is fresh
        self.assertEqual(cl["date"], self.t.isoformat())
        self.assertEqual(cl["lag_bd"], 0)
        self.assertEqual(cl["t0_needed"], self.t0.isoformat())
        # series that starts after t0 but before t → also SHORT
        y2b = [(d, 3.0) for d in self.cal[self.pos - 5:self.pos + 1]]
        self.assertEqual(self.run_ccy(y2b)["context_last"]["y2"]["status"], "SHORT")

    def test_C3_none(self):
        self.assertEqual(self.run_ccy([])["context_last"]["y2"]["status"], "NONE")
        future = [(self.t + timedelta(days=3), 3.0)]                         # only observations after t
        self.assertEqual(self.run_ccy(future)["context_last"]["y2"]["status"], "NONE")
        self.assertEqual(self.run_ccy([], ccy="CHF")["context_last"]["be"]["quality"], "NO_MARKET")

    def test_C4_ok_and_resid_cause(self):
        y2 = [(d, 3.0 + 0.001 * i) for i, d in enumerate(self.cal)]
        row = self.run_ccy(y2)
        self.assertEqual(row["context_last"]["y2"]["status"], "OK")
        self.assertAlmostEqual(row["d_2y"], round(0.001 * H * 100, 2), places=2)
        # residual blank because the ACM leg is missing must not be attributed to the nominal leg
        nom = [(d, 4.0) for d in self.cal]
        r, f = [0.0] * 400, [0.0] * 400
        row2, _ = s01b.evaluate_ccy("AUD", self.t, self.pos, self.cal, [], nom, [], [], r, f, self.t, {"initialized": True})
        self.assertEqual(row2["avail"], "NO_DATA")
        self.assertIsNone(row2["resid"])
        self.assertNotIn("d_fit", row2)
        self.assertEqual(row2["context_last"]["nominal"]["status"], "OK")   # nominal fine; the dashboard says «sin ACM»


@unittest.skipUnless(HAVE_ACM, "pandas/acm_g8 not importable")
class ConnectionAudCad(unittest.TestCase):
    def _roundtrip(self, ccy, ser, session=None):
        """persist → read → evaluate on an EXPLICIT session date over the TARGET calendar (never the last data date).
        session: date; default = last TARGET day ≤ today (UTC)."""
        tmp = tempfile.mkdtemp()
        n = acm_g8.persist_extra(ccy, 2, ser, data_dir=tmp)
        self.assertGreater(n, 0)
        path = os.path.join(tmp, s01b.Y2_FILES[ccy])
        self.assertTrue(os.path.exists(path))
        y2 = s01b.read_series(path)
        self.assertEqual(len(y2), n)
        t = session or date.today()
        while not s01b.is_target_day(t):
            t -= timedelta(days=1)
        cal, d = [], t
        while len(cal) < 400:
            if s01b.is_target_day(d):
                cal.append(d)
            d -= timedelta(days=1)
        cal.reverse()
        pos = 399
        t0 = cal[pos - H]
        acm = acm_series(cal)
        r, f = [0.0] * 400, [0.0] * 400
        row, _ = s01b.evaluate_ccy(ccy, t, pos, cal, acm, [], y2, [], r, f, t, {"initialized": True})
        return row, y2, t, t0

    def _check_live(self, ccy, ser, session=None):
        """Two separate verdicts: (1) download + persist + read + Δ arithmetic; (2) validity of the leg for that session."""
        row, y2, t, t0 = self._roundtrip(ccy, ser, session)
        cl = row["context_last"]["y2"]
        end, start = s01b.asof(y2, t), s01b.asof(y2, t0)
        # (1) arithmetic: whatever the engine computed must equal the rule applied to the file we just wrote
        if end and start and end[0] > start[0]:
            expected = round((end[1] - start[1]) * 100.0, 2)
            self.assertEqual(cl["status"], "OK")
            self.assertIsNotNone(row["d_2y"])
            self.assertAlmostEqual(row["d_2y"], expected, places=2)
            self.assertEqual(row["context_asof"]["y2"], {"t": end[0].isoformat(), "t0": start[0].isoformat()})
            verdict = "VIGENTE para la sesión %s: Δ2Y = %+.2f bp (%s→%s)" % (t, row["d_2y"], start[0], end[0])
        else:
            self.assertIsNone(row["d_2y"])
            self.assertIn(cl["status"], ("STALE", "SHORT"))
            verdict = "descarga y cálculo correctos; VIGENCIA PENDIENTE para la sesión %s (%s: último dato %s, %s d.h.)" % (
                t, cl["status"], cl.get("date"), cl.get("lag_bd"))
        print("\n  LIVE %s 2Y: %d obs, primero %s, último %s = %.3f · %s" % (ccy, len(y2), y2[0][0], y2[-1][0], y2[-1][1], verdict))
        return row

    def test_X1_synthetic_save_read_delta(self):
        for ccy in ("AUD", "CAD"):
            idx = pd.bdate_range(end=date.today(), periods=450)
            ser = pd.Series([3.0 + 0.002 * i for i in range(450)], index=idx)
            row, y2, t, t0 = self._roundtrip(ccy, ser)
            v_t = [v for d, v in y2 if d <= t][-1]
            v_t0 = [v for d, v in y2 if d <= t0][-1]
            self.assertEqual(row["context_last"]["y2"]["status"], "OK")
            self.assertAlmostEqual(row["d_2y"], round((v_t - v_t0) * 100.0, 2), places=2)
            self.assertEqual(row["context_asof"]["y2"]["t"], t.isoformat())

    def test_X2_connection_leaves_detector_untouched(self):
        cal = bcal(400)
        pos, acm = 399, acm_series(bcal(400))
        r, f = [0.0] * 400, [0.0] * 400
        y2 = [(d, 3.0 + 0.001 * i) for i, d in enumerate(cal)]
        a, ea = s01b.evaluate_ccy("CAD", cal[pos], pos, cal, acm, [], [], [], r, f, cal[pos], {"initialized": True})
        b, eb = s01b.evaluate_ccy("CAD", cal[pos], pos, cal, acm, [], y2, [], r, f, cal[pos], {"initialized": True})
        self.assertEqual(ea, eb)
        for k in set(a) | set(b):
            if k in ("d_2y", "context_asof", "context_last"):
                continue
            self.assertEqual(a.get(k), b.get(k), k)

    @unittest.skipUnless(LIVE, "network test: run with --live on the Mac")
    def test_L1_live_aud_rba_f2(self):
        ser = acm_g8.fetch_rba_xls(acm_g8.RBA_F2_DAILY, "FCMYGBAG2D")
        self.assertGreater(len(ser.dropna()), 250)
        self._check_live("AUD", ser, SESSION)

    @unittest.skipUnless(LIVE, "network test: run with --live on the Mac")
    def test_L2_live_cad_valet(self):
        ser = acm_g8.fetch_valet_group("bond_yields_benchmark", "BD.CDN.2YR.DQ.YLD")
        self.assertGreater(len(ser.dropna()), 250)
        self._check_live("CAD", ser, SESSION)


if __name__ == "__main__":
    unittest.main(verbosity=2)

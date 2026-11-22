"""t331u: with NZD_CASH_ON in PROXY, overnight−OCR is NA, never 0.

New tests only. Does not edit older tests. Does not modify spreadSeries.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import feed_proxy as FP
import dashboard_alerts as DA


class NzdProxySpreadNa(unittest.TestCase):
    def test_2026_official_band_from_repo_csvs(self):
        cash = FP.parse_rows((ROOT / "data" / "NZD_CASH_ON.csv").read_text(encoding="utf-8"))
        ocr = []
        import csv, io
        text = (ROOT / "data" / "NZD_OCR.csv").read_text(encoding="utf-8")
        for row in csv.DictReader(io.StringIO(text)):
            d = FP._as_date(row["DATE"])
            ocr.append((d, float(row["CLOSE"])))
        pts = FP.measure_official_spread_bp(cash, ocr, year=2026)
        band = FP.summarize_spread_band(pts)
        self.assertEqual(band["n"], 94)
        self.assertEqual(band["first_date"], "2026-01-08")
        self.assertEqual(band["last_date"], "2026-11-12")
        self.assertAlmostEqual(band["mean"], 7.745, places=3)
        self.assertEqual(band["median"], 7.0)
        self.assertEqual(band["p5"], 3.0)
        self.assertEqual(band["p95"], 13.0)
        self.assertEqual(band["min"], -5.0)
        self.assertEqual(band["max"], 35.0)
        # PROXY rows in 2026 are excluded from the band
        self.assertTrue(any((r.get("status") or "").upper() == "PROXY" and str(r["date"]).startswith("2026") for r in cash))
        payload = json.loads((ROOT / "data" / "_ingest" / "NZD_CASH_ON_proxy_band.json").read_text())
        self.assertEqual(payload["n"], 94)
        self.assertEqual(payload["feed_id"], "NZD_CASH_ON")

    def test_dashboard_alerts_floor_is_na_when_last_cash_is_proxy(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            # official 12-nov 2.80 vs OCR 2.75 = +5 bp; PROXY 13-nov would be 0
            (data / "NZD_CASH_ON.csv").write_text(
                "Date,Value,Status,Source,ProxyDate\n"
                "2026-11-12,2.80,,,\n"
                "2026-11-13,2.75,PROXY,NZD_OCR,2026-11-13\n",
                encoding="utf-8",
            )
            (data / "NZD_OCR.csv").write_text(
                "DATE,OPEN,HIGH,LOW,CLOSE,VOLUME\n"
                "20261113,2.75,2.75,2.75,2.75,0\n",
                encoding="utf-8",
            )
            prev_data, prev_today = DA.DATA, getattr(DA, "TODAY", None)
            DA.DATA = str(data)
            try:
                self.assertEqual(DA.last_row_status("NZD_CASH_ON.csv"), "PROXY")
                st, lines = {}, []
                DA.check_floors(st, lines)
                self.assertEqual(st["floors"]["NZD"]["badge"], "NA")
                self.assertIsNone(st["floors"]["NZD"]["last"])
                self.assertTrue(st["floors"]["NZD"]["proxy"])
                # must not report AMPLE at 0.0 bp
                self.assertFalse(any("0.0 bp" in x or "+0.0" in x for x in lines))
            finally:
                DA.DATA = prev_data

    def test_spreadseries_function_untouched(self):
        html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        i = html.find("function spreadSeries")
        j = html.find("\nfunction ", i + 10)
        body = html[i:j]
        self.assertEqual(
            hashlib.sha256(body.encode()).hexdigest(),
            "173e40abb1bc3cc744ef28813052e4057ec05dcc35a47a7c936b790f2df8ec4b",
        )
        self.assertIn("function blankProxyFloorPoints", html)
        self.assertIn("s = blankProxyFloorPoints(rs[0], s)", html)
        # table cell is NA, never a 0 spread, when last RFR is PROXY
        self.assertIn('title="diferencial overnight−OCR en blanco mientras el overnight es PROXY">NA</span>', html)
        self.assertIn("el diferencial overnight−OCR queda NA, no 0", html)

    def test_proxy_rows_excluded_from_band_fixture(self):
        cash = FP.parse_rows(
            "Date,Value,Status,Source,ProxyDate\n"
            "2026-11-12,2.80,,,\n"
            "2026-11-13,2.75,PROXY,NZD_OCR,2026-11-13\n"
        )
        ocr = [(date(2026, 11, 1), 2.75)]  # step series; official 12-nov still sees 2.75
        pts = FP.measure_official_spread_bp(cash, ocr, year=2026)
        self.assertEqual(len(pts), 1)
        self.assertEqual(pts[0][0], date(2026, 11, 12))
        self.assertAlmostEqual(pts[0][3], 5.0)


if __name__ == "__main__":
    unittest.main()

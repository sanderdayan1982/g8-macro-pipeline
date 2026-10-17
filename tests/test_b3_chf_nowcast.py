"""B3 (SHADOW): nowcast diario del CHF 2Y/5Y. No reescribe el cubo mensual."""
import hashlib
import json
import os
import sys
import unittest
from datetime import date

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import chf_nowcast_shadow as B  # noqa: E402

def _sha(rel):
    with open(os.path.join(ROOT, rel), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _weekdays(day):
    return day.weekday() < 5


class Bridge(unittest.TestCase):
    def test_anchor_plus_tenor_weighted_daily_move(self):
        official = [(date(2026, 9, 30), 1.0)]
        saron = [(date(2026, 9, 30), 0.0), (date(2026, 10, 1), 0.10)]
        nom = [(date(2026, 9, 30), 0.0), (date(2026, 10, 1), 0.0)]
        y2 = B.bridge(official, saron, nom, 2, _weekdays)
        y5 = B.bridge(official, saron, nom, 5, _weekdays)
        self.assertEqual(y2[0]["label"], "OFFICIAL")
        self.assertEqual(y2[0]["value"], 1.0)
        self.assertEqual(y2[1]["date"], date(2026, 10, 1))
        self.assertEqual(y2[1]["label"], "NOWCAST")
        self.assertAlmostEqual(y2[1]["value"], 1.08, places=6)   # 80 % del movimiento del SARON
        self.assertAlmostEqual(y5[1]["value"], 1.05, places=6)   # 50 %
        nom_up = [(date(2026, 9, 30), 0.0), (date(2026, 10, 1), 0.10)]
        flat_saron = [(date(2026, 9, 30), 0.0), (date(2026, 10, 1), 0.0)]
        y2b = B.bridge(official, flat_saron, nom_up, 2, _weekdays)
        y5b = B.bridge(official, flat_saron, nom_up, 5, _weekdays)
        self.assertAlmostEqual(y2b[1]["value"], 1.02, places=6)
        self.assertAlmostEqual(y5b[1]["value"], 1.05, places=6)

    def test_weekend_and_holiday_are_not_emitted(self):
        official = [(date(2026, 9, 30), 1.0)]
        saron = [(date(2026, 9, 30), 0.0), (date(2026, 10, 5), 0.2)]
        nom = [(date(2026, 9, 30), 0.0), (date(2026, 10, 5), 0.0)]
        holiday = {date(2026, 10, 1)}

        def is_bd(day):
            return day.weekday() < 5 and day not in holiday

        rows = B.bridge(official, saron, nom, 2, is_bd)
        dates = [row["date"] for row in rows]
        self.assertNotIn(date(2026, 10, 1), dates)
        self.assertNotIn(date(2026, 10, 3), dates)
        self.assertIn(date(2026, 10, 2), dates)
        carried = next(row for row in rows if row["date"] == date(2026, 10, 2))
        self.assertEqual(carried["saron_date"], date(2026, 9, 30))
        moved = next(row for row in rows if row["date"] == date(2026, 10, 5))
        self.assertAlmostEqual(moved["value"], 1.16, places=6)


class PublishedFiles(unittest.TestCase):
    def test_official_curve_is_not_rewritten_and_shadow_matches(self):
        before = {name: _sha(name) for name in (
            "data/CHF_SPOT_2Y.csv", "data/CHF_SPOT_5Y.csv", "data/CHF_SARON.csv", "data/CHF_NOM_10Y.csv",
            "scripts/s01b.py", "scripts/fetch_chf_snb.py")}
        summary = B.build(ROOT)
        after = {name: _sha(name) for name in before}
        self.assertEqual(before, after)
        self.assertEqual(_sha("scripts/s01b.py"), "0c43d6dd54677974bf4ffde880de049396f4b0d9cdd1c6e39964d796ef132ee5")
        y2 = summary["tenors"]["2Y"]
        y5 = summary["tenors"]["5Y"]
        self.assertEqual(summary["mode"], "SHADOW")
        self.assertEqual(summary["quality"], "NOWCAST")
        self.assertEqual((y2["anchor_date"], y2["official"], y2["w_nom10"]), ("2026-09-30", 0.287, 0.2))
        self.assertEqual((y5["anchor_date"], y5["official"], y5["w_nom10"]), ("2026-09-30", 0.453, 0.5))
        self.assertEqual(y2["last_label"], "NOWCAST")
        self.assertGreater(y2["last_date"], "2026-09-30")
        dates = [row["date"] for row in y2["series"]]
        self.assertIn("2026-10-01", dates)
        self.assertNotIn("2026-10-03", dates)
        with open(os.path.join(ROOT, "data", "CHF_SPOT_2Y_daily_shadow.csv"), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertEqual(lines[0], "DATE,VALUE,LABEL,W_NOM10,SARON,SARON_DATE,NOM10,NOM10_DATE")
        self.assertTrue(lines[1].startswith("2026-09-30,0.287000,OFFICIAL"))
        self.assertTrue(lines[-1].startswith(y2["last_date"]))
        self.assertIn(",NOWCAST,", lines[-1])
        with open(os.path.join(ROOT, "data", B.SHADOW_JSON), encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["tenors"]["2Y"]["last_value"], y2["last_value"])
        self.assertEqual(saved["tenors"]["5Y"]["last_value"], y5["last_value"])
        self.assertNotIn("series", saved["tenors"]["2Y"])


if __name__ == "__main__":
    unittest.main()

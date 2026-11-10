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


def _last_official_row(rel):
    """Última fila de datos del CSV oficial, con el redondeo que el propio CSV imprime."""
    last = None
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        for line in fh:
            text = line.strip()
            if text and not text.startswith("#") and not text.lower().startswith("date"):
                last = text
    date_s, value_s = last.split(",")[:2]
    return date_s[:10], float(value_s)


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
        for tenor, rel, weight in (
            (y2, "data/CHF_SPOT_2Y.csv", 0.2),
            (y5, "data/CHF_SPOT_5Y.csv", 0.5),
        ):
            anchor_date, official = _last_official_row(rel)
            self.assertEqual(tenor["anchor_date"], anchor_date)
            self.assertEqual(tenor["official"], official)
            self.assertEqual(tenor["w_nom10"], weight)
        self.assertEqual(y2["last_label"], "NOWCAST")
        self.assertGreater(y2["last_date"], y2["anchor_date"])
        dates = [row["date"] for row in y2["series"]]
        self.assertEqual(dates[0], y2["anchor_date"])
        self.assertEqual(y2["series"][0]["label"], "OFFICIAL")
        if len(dates) > 1:
            self.assertGreater(dates[1], y2["anchor_date"])
            for prev, nxt in zip(dates, dates[1:]):
                self.assertGreater(nxt, prev)
            for row in y2["series"][1:]:
                self.assertEqual(row["label"], "NOWCAST")
        self.assertNotIn("2026-10-03", dates)
        with open(os.path.join(ROOT, "data", "CHF_SPOT_2Y_daily_shadow.csv"), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertEqual(lines[0], "DATE,VALUE,LABEL,W_NOM10,SARON,SARON_DATE,NOM10,NOM10_DATE")
        self.assertTrue(lines[1].startswith(f'{y2["anchor_date"]},{y2["official"]:.6f},OFFICIAL'))
        self.assertTrue(lines[-1].startswith(y2["last_date"]))
        self.assertIn(",NOWCAST,", lines[-1])
        with open(os.path.join(ROOT, "data", B.SHADOW_JSON), encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["tenors"]["2Y"]["last_value"], y2["last_value"])
        self.assertEqual(saved["tenors"]["5Y"]["last_value"], y5["last_value"])
        self.assertNotIn("series", saved["tenors"]["2Y"])


if __name__ == "__main__":
    unittest.main()

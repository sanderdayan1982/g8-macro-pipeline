"""C1 (SHADOW): CB_EVENT_48H y DTE/ΔOI de opciones. No toca los hashes congelados."""
import hashlib
import json
import os
import sys
import unittest
from datetime import date

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import cb_event_shadow as CB  # noqa: E402
import cme_options_shadow as OPT  # noqa: E402

FROZEN = {
    "scripts/cme_options_collector.py": "61276d17636d921913e383127b47176e946adc5eab59420416f4f3a244a477f9",
    "scripts/build_options_summary.py": "f0fdf61d1047d3d899b5037f3047dc194329b4a4d5de42b59bed1346e304e019",
    "scripts/dashboard_alerts.py": "1d8d24fb0ebc6284cf66ebadbf4151e3ac567b368de947a27236eec8d784befe",
    "scripts/book_risk.py": "78514c2daa319d08454a99dae8225f7c2208da1fcd34cab13eb2547d72ef9d50",
    "scripts/s01b.py": "0c43d6dd54677974bf4ffde880de049396f4b0d9cdd1c6e39964d796ef132ee5",
}


def _sha(rel):
    with open(os.path.join(ROOT, rel), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _weekdays(day):
    return day.weekday() < 5


class BusinessDaysToTheMeeting(unittest.TestCase):
    def test_zero_on_the_day_and_two_business_days_is_the_edge(self):
        meeting = [date(2026, 10, 28)]
        self.assertEqual(CB.days_to_next_cb_meeting(date(2026, 10, 28), meeting, _weekdays), 0)
        self.assertEqual(CB.days_to_next_cb_meeting(date(2026, 10, 27), meeting, _weekdays), 1)
        self.assertEqual(CB.days_to_next_cb_meeting(date(2026, 10, 26), meeting, _weekdays), 2)
        self.assertEqual(CB.days_to_next_cb_meeting(date(2026, 10, 23), meeting, _weekdays), 3)
        self.assertTrue(CB.in_window(2))
        self.assertFalse(CB.in_window(3))
        self.assertFalse(CB.in_window(None))

    def test_weekend_does_not_count_and_a_holiday_meeting_still_counts(self):
        meeting = [date(2026, 10, 26)]  # lunes
        self.assertEqual(CB.days_to_next_cb_meeting(date(2026, 10, 23), meeting, _weekdays), 1)
        holiday = {date(2026, 10, 27)}
        meeting = [date(2026, 10, 28)]

        def is_bd(day):
            return day.weekday() < 5 and day not in holiday

        # Desde el lunes 26: el martes es festivo y el miércoles es la reunión.
        self.assertEqual(CB.days_to_next_cb_meeting(date(2026, 10, 26), meeting, is_bd), 1)

    def test_no_future_meeting_is_none(self):
        self.assertIsNone(CB.days_to_next_cb_meeting(date(2027, 1, 1), [date(2026, 12, 8)], _weekdays))

    def test_a_pair_flags_if_either_leg_is_inside_the_window(self):
        meetings = {
            "US": [date(2026, 10, 28)],
            "EA": [date(2026, 10, 29)],
            "GB": [date(2026, 11, 5)],
            "JP": [],
            "AU": [],
            "NZ": [],
            "CA": [],
            "CH": [],
        }
        doc = CB.evaluate(date(2026, 10, 27), meetings, lambda _cal, day: _weekdays(day), ["EUR/USD", "EUR/GBP", "GBP/USD"])
        self.assertEqual(doc["mode"], "SHADOW")
        self.assertTrue(doc["currencies"]["USD"]["CB_EVENT_48H"])
        self.assertTrue(doc["currencies"]["EUR"]["CB_EVENT_48H"])
        self.assertFalse(doc["currencies"]["GBP"]["CB_EVENT_48H"])
        self.assertTrue(doc["pairs"]["EUR/USD"]["CB_EVENT_48H"])
        self.assertTrue(doc["pairs"]["EUR/USD"]["both_legs_in_window"])
        self.assertTrue(doc["pairs"]["EUR/GBP"]["CB_EVENT_48H"])
        self.assertFalse(doc["pairs"]["EUR/GBP"]["both_legs_in_window"])
        self.assertTrue(doc["pairs"]["GBP/USD"]["CB_EVENT_48H"])
        self.assertFalse(doc["pairs"]["GBP/USD"]["both_legs_in_window"])
        self.assertFalse(doc["currencies"]["JPY"]["CB_EVENT_48H"])
        self.assertIsNone(doc["currencies"]["JPY"]["days_to_next_cb_meeting"])


class OfficialCalendar(unittest.TestCase):
    def test_twenty_eight_pairs_and_the_october_cluster(self):
        doc = CB.build(date(2026, 10, 17), ROOT)
        self.assertEqual(len(doc["pairs"]), 28)
        self.assertTrue(all(not row["CB_EVENT_48H"] for row in doc["currencies"].values()))
        self.assertTrue(all(not row["CB_EVENT_48H"] for row in doc["pairs"].values()))
        self.assertTrue(all(not row["both_legs_in_window"] for row in doc["pairs"].values()))
        # Labour Day NZ (2026-10-26) no cuenta: el viernes 23 NZD ya está a 2 días hábiles y USD no.
        friday = CB.build(date(2026, 10, 23), ROOT)
        self.assertTrue(friday["currencies"]["NZD"]["CB_EVENT_48H"])
        self.assertEqual(friday["currencies"]["NZD"]["days_to_next_cb_meeting"], 2)
        self.assertEqual(friday["currencies"]["USD"]["days_to_next_cb_meeting"], 3)
        self.assertFalse(friday["currencies"]["USD"]["CB_EVENT_48H"])
        nzd_pairs = ["AUD/NZD", "EUR/NZD", "GBP/NZD", "NZD/CAD", "NZD/CHF", "NZD/JPY", "NZD/USD"]
        self.assertEqual(sorted(p for p, row in friday["pairs"].items() if row["CB_EVENT_48H"]), nzd_pairs)
        self.assertTrue(all(not row["both_legs_in_window"] for row in friday["pairs"].values()))
        monday = CB.build(date(2026, 10, 26), ROOT)
        for ccy in ("USD", "CAD", "NZD"):
            self.assertEqual(monday["currencies"][ccy]["days_to_next_cb_meeting"], 2, ccy)
            self.assertTrue(monday["currencies"][ccy]["CB_EVENT_48H"], ccy)
        self.assertFalse(monday["currencies"]["EUR"]["CB_EVENT_48H"])
        self.assertEqual(sorted(p for p, row in monday["pairs"].items() if row["both_legs_in_window"]),
                         ["NZD/CAD", "NZD/USD", "USD/CAD"])
        self.assertTrue(monday["pairs"]["GBP/USD"]["CB_EVENT_48H"])
        self.assertFalse(monday["pairs"]["GBP/USD"]["both_legs_in_window"])
        self.assertFalse(monday["pairs"]["EUR/GBP"]["CB_EVENT_48H"])
        day = CB.build(date(2026, 10, 28), ROOT)
        for ccy in ("USD", "CAD", "NZD"):
            self.assertEqual(day["currencies"][ccy]["days_to_next_cb_meeting"], 0, ccy)
        self.assertEqual(day["currencies"]["EUR"]["days_to_next_cb_meeting"], 1)
        self.assertEqual(day["currencies"]["JPY"]["days_to_next_cb_meeting"], 2)
        self.assertFalse(day["currencies"]["AUD"]["CB_EVENT_48H"])
        self.assertFalse(day["currencies"]["GBP"]["CB_EVENT_48H"])
        self.assertFalse(day["currencies"]["CHF"]["CB_EVENT_48H"])
        doubles = sorted(p for p, row in day["pairs"].items() if row["both_legs_in_window"])
        self.assertEqual(doubles, ["CAD/JPY", "EUR/CAD", "EUR/JPY", "EUR/NZD", "EUR/USD",
                                   "NZD/CAD", "NZD/JPY", "NZD/USD", "USD/CAD", "USD/JPY"])
        self.assertTrue(day["pairs"]["GBP/USD"]["CB_EVENT_48H"])
        self.assertFalse(day["pairs"]["GBP/USD"]["both_legs_in_window"])
        self.assertTrue(day["pairs"]["AUD/USD"]["CB_EVENT_48H"])
        self.assertFalse(day["pairs"]["AUD/USD"]["both_legs_in_window"])
        self.assertFalse(day["pairs"]["GBP/AUD"]["CB_EVENT_48H"])
        after = CB.build(date(2026, 10, 29), ROOT)
        self.assertFalse(after["currencies"]["USD"]["CB_EVENT_48H"])
        self.assertTrue(after["currencies"]["EUR"]["CB_EVENT_48H"])
        self.assertTrue(after["currencies"]["JPY"]["CB_EVENT_48H"])
        self.assertEqual(sorted(p for p, row in after["pairs"].items() if row["both_legs_in_window"]), ["EUR/JPY"])
        self.assertTrue(after["pairs"]["GBP/JPY"]["CB_EVENT_48H"])
        self.assertFalse(after["pairs"]["GBP/JPY"]["both_legs_in_window"])

    def test_snapshot_matches_the_official_files(self):
        with open(os.path.join(ROOT, "data", CB.SHADOW_NAME), encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved, CB.build(date(2026, 10, 17), ROOT))


class OptionWalls(unittest.TestCase):
    def test_dte_and_delta_oi_with_and_without_a_previous_snapshot(self):
        chain = {
            1.11: {"c": 1, "p": 500},
            1.13: {"c": 400, "p": 10},
            1.14: {"c": 100, "p": 0},
        }
        prev = {
            "front": "2026-11-06",
            "oi_front": 1000,
            "chain": {1.11: {"c": 1, "p": 480}, 1.13: {"c": 350, "p": 10}},
        }
        row = OPT.enrich("2026-10-15", "2026-11-06", 1.12, 1111, chain, prev, _weekdays)
        self.assertEqual(row["dte_calendar"], 22)
        self.assertEqual(row["dte_bd"], 16)
        self.assertEqual(row["call_wall"]["k"], 1.13)
        self.assertEqual(row["call_wall"]["oi"], 400)
        self.assertEqual(row["call_wall"]["delta_oi"], 50)
        self.assertEqual(row["put_wall"]["k"], 1.11)
        self.assertEqual(row["put_wall"]["delta_oi"], 20)
        self.assertEqual(row["aggregate"]["delta_oi"], 111)
        bare = OPT.enrich("2026-10-15", "2026-11-06", 1.12, 1111, chain, None, _weekdays)
        self.assertIsNone(bare["call_wall"]["delta_oi"])
        self.assertIsNone(bare["put_wall"]["delta_oi"])
        self.assertIsNone(bare["aggregate"]["delta_oi"])
        self.assertEqual(bare["aggregate"]["oi"], 1111)
        # El strike del call wall no está en la sesión anterior: ΔOI vacío, no cero.
        prev["chain"] = {1.11: {"c": 1, "p": 480}}
        missing = OPT.enrich("2026-10-15", "2026-11-06", 1.12, 1111, chain, prev, _weekdays)
        self.assertIsNone(missing["call_wall"]["delta_oi"])
        self.assertEqual(missing["put_wall"]["delta_oi"], 20)

    def test_tie_keeps_the_lower_strike_and_ignores_thin_strikes(self):
        chain = {1.13: {"c": 200, "p": 0}, 1.15: {"c": 200, "p": 0}, 1.10: {"c": 0, "p": 50}}
        call, put = OPT.walls(chain, 1.12)
        self.assertEqual(call[0], 1.13)
        self.assertIsNone(put)  # 50 de OI total no llega a 100

    def test_published_surface_is_not_rewritten(self):
        before = _sha("data/OPTIONS_SURFACE.json")
        doc = OPT.build(ROOT)
        self.assertEqual(_sha("data/OPTIONS_SURFACE.json"), before)
        self.assertEqual(doc["mode"], "SHADOW")
        self.assertEqual(doc["session"], "2026-10-15")
        eur = doc["currencies"]["EUR"]
        self.assertEqual(eur["dte_calendar"], 22)
        self.assertEqual(eur["dte_bd"], 16)
        self.assertEqual(eur["call_wall"]["k"], 1.135)
        self.assertEqual(eur["call_wall"]["delta_oi"], 25)
        self.assertEqual(eur["put_wall"]["k"], 1.11)
        self.assertEqual(eur["put_wall"]["delta_oi"], -13)
        self.assertEqual(eur["aggregate"]["delta_oi"], 2335)
        self.assertEqual(doc["currencies"]["NZD"]["note"], "sin opciones CME")
        self.assertIsNone(doc["currencies"]["NZD"]["dte_calendar"])
        self.assertIsNone(doc["currencies"]["NZD"]["aggregate"]["delta_oi"])
        with open(os.path.join(ROOT, "data", OPT.SHADOW_NAME), encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved, doc)

    def test_frozen_hashes_and_official_book_stay(self):
        for rel, digest in FROZEN.items():
            self.assertEqual(_sha(rel), digest, rel)
        # Un día de más no puede colarse en el conteo de DTE por usar el calendario de otro país.
        jp_holiday = date(2026, 11, 3)
        self.assertEqual(OPT.dte("2026-10-15", "2026-11-06", _weekdays)[1], 16)
        self.assertTrue(_weekdays(jp_holiday))


if __name__ == "__main__":
    unittest.main()

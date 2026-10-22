"""C2 (SHADOW): distancia, muro cercano, biografía y roll. No toca los hashes congelados."""
import hashlib
import json
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import cme_options_shadow as OPT  # noqa: E402

FROZEN = {
    "scripts/cme_options_collector.py": "61276d17636d921913e383127b47176e946adc5eab59420416f4f3a244a477f9",
    "scripts/build_options_summary.py": "f0fdf61d1047d3d899b5037f3047dc194329b4a4d5de42b59bed1346e304e019",
    ".github/workflows/cme_options.yml": "281dfed01663e6147cd32fdde02c1bc88688ccdee0f6a54789128205e0fab6ab",
    "scripts/dashboard_alerts.py": "0c0aba148a00b8cb7dd950f651f28e708605e0bcc720cd353bb9397370874322",
    "scripts/s01b.py": "0c43d6dd54677974bf4ffde880de049396f4b0d9cdd1c6e39964d796ef132ee5",
    "scripts/usd_factor.py": "d96bbaa366637a5d1238eac2af1e03096561ff63d92fc3bc246919bad804d3d3",
    "scripts/book_risk.py": "78514c2daa319d08454a99dae8225f7c2208da1fcd34cab13eb2547d72ef9d50",
}


def _sha(rel):
    with open(os.path.join(ROOT, rel), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _weekdays(day):
    return day.weekday() < 5


def _snap(session, ccy, front, oi, chain):
    return {
        "session": session,
        "currencies": {ccy: {"front": front, "oi_front": oi, "chain": chain}},
    }


class DistanceAndNearest(unittest.TestCase):
    def test_gbp_call_wall_is_7_73_pct_and_the_nearest_call_is_closer(self):
        chain = {1.43: {"c": 900, "p": 0}, 1.33: {"c": 150, "p": 0}, 1.315: {"c": 0, "p": 400}}
        prev = {"front": "2026-11-06", "oi_front": 1400, "chain": {1.43: {"c": 880, "p": 0}, 1.315: {"c": 0, "p": 390}}}
        gbp = OPT.enrich("2026-10-15", "2026-11-06", 1.3274, 1450, chain, prev, _weekdays, ccy="GBP")
        call = gbp["call_wall"]
        near = gbp["nearest_call_wall"]
        self.assertEqual(call["k"], 1.43)
        self.assertEqual(gbp["ref"], 1.3274)
        self.assertEqual(call["dist_pct"], 7.73)
        self.assertEqual(call["abs_dist_pct"], 7.73)
        self.assertFalse(call["near_pin_05pct"])
        self.assertEqual(near["k"], 1.33)
        self.assertLess(near["abs_dist_pct"], call["abs_dist_pct"])
        self.assertEqual(near["dist_pct"], 0.2)
        self.assertTrue(near["near_pin_05pct"])
        self.assertEqual(gbp["nearest_put_wall"]["k"], 1.315)
        self.assertFalse(gbp["nearest_put_wall"]["near_pin_05pct"])
        self.assertEqual(gbp["map_status"], "READABLE")
        self.assertFalse(gbp["roll_window_7d"])
        # 1.33 no está en la sesión previa: el delta queda vacío, no en cero.
        self.assertIsNone(near["delta_oi"])
        self.assertIsNone(near["delta_oi_1d"])
        self.assertIsNone(near["pct_oi_1d"])
        self.assertIsNone(near["chip_15pct_strike"])
        live_gbp = OPT.build(ROOT)["currencies"]["GBP"]
        self.assertLessEqual(live_gbp["nearest_call_wall"]["abs_dist_pct"], live_gbp["call_wall"]["abs_dist_pct"])

    def test_chf_is_thin_because_the_put_wall_does_not_clear_min_oi(self):
        chf = OPT.enrich(
            "2026-10-15", "2026-11-06", 1.16, 450,
            {1.20: {"c": 400, "p": 0}, 1.14: {"c": 0, "p": 50}},
            None, _weekdays, ccy="CHF",
        )
        self.assertIsNotNone(chf["call_wall"])
        self.assertIsNone(chf["put_wall"])
        self.assertEqual(chf["map_status"], "THIN_ONE_SIDED")
        self.assertIsNone(chf["nearest_put_wall"])

    def test_nzd_has_no_cme_options(self):
        nzd = OPT.build(ROOT)["currencies"]["NZD"]
        self.assertEqual(nzd["map_status"], "NO_CME_OPTIONS")
        self.assertFalse(nzd["roll_window_7d"])
        self.assertEqual(nzd["note"], "sin opciones CME")


class BiographyAndRoll(unittest.TestCase):
    def test_fifteen_percent_and_drain_chips_use_the_signed_cuts(self):
        chain = {1.13: {"c": 115, "p": 0}, 1.10: {"c": 0, "p": 200}}
        prev = {"front": "2026-11-06", "oi_front": 300, "chain": {1.13: {"c": 100, "p": 0}, 1.10: {"c": 0, "p": 300}}}
        row = OPT.enrich("2026-10-15", "2026-11-06", 1.12, 200, chain, prev, _weekdays, ccy="GBP")
        self.assertEqual(row["call_wall"]["pct_oi_1d"], 15.0)
        self.assertTrue(row["call_wall"]["chip_15pct_strike"])
        self.assertEqual(row["put_wall"]["pct_oi_1d"], -33.33)
        self.assertTrue(row["put_wall"]["chip_15pct_strike"])
        self.assertEqual(row["aggregate"]["pct_oi_1d"], -33.33)
        self.assertTrue(row["aggregate"]["chip_15pct_chain"])
        self.assertTrue(row["aggregate"]["drain_1_3"])
        self.assertFalse(row["aggregate"]["drain_2_3"])
        drained = OPT.enrich("2026-10-15", "2026-11-06", 1.12, 100, chain, prev, _weekdays, ccy="GBP")
        self.assertEqual(drained["aggregate"]["pct_oi_1d"], -66.67)
        self.assertTrue(drained["aggregate"]["drain_2_3"])
        quiet = OPT.enrich(
            "2026-10-15", "2026-11-06", 1.12, 114,
            {1.13: {"c": 114, "p": 0}, 1.10: {"c": 0, "p": 100}},
            {"front": "2026-11-06", "oi_front": 100, "chain": {1.13: {"c": 100, "p": 0}, 1.10: {"c": 0, "p": 100}}},
            _weekdays, ccy="GBP",
        )
        self.assertEqual(quiet["call_wall"]["pct_oi_1d"], 14.0)
        self.assertFalse(quiet["call_wall"]["chip_15pct_strike"])
        self.assertFalse(quiet["aggregate"]["drain_1_3"])

    def test_one_week_and_one_month_are_none_until_that_session_exists(self):
        chain = {1.13: {"c": 400, "p": 0}, 1.10: {"c": 0, "p": 500}}
        prev = {"front": "2026-11-06", "oi_front": 800, "chain": chain}
        short = {"sessions": [_snap("2026-10-0%d" % i, "EUR", "2026-11-06", 100, {1.13: {"c": 10, "p": 0}}) for i in range(1, 5)]}
        row = OPT.enrich("2026-10-15", "2026-11-06", 1.12, 900, chain, prev, _weekdays, short, "EUR")
        self.assertIsNone(row["call_wall"]["delta_oi_1w"])
        self.assertEqual(row["call_wall"]["delta_oi_1d"], 0)
        sessions = []
        for i, day in enumerate(["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-05"], start=1):
            sessions.append(_snap(day, "EUR", "2026-11-06", 1000 - i, {1.13: {"c": 100 + i, "p": 0}, 1.10: {"c": 0, "p": 50}}))
        # El quinto hacia atrás es 2026-09-01, pero el strike del call sí está. El put de 1.10 tiene p=0
        # en el filtro de muro; el put wall es 1.10. En el historial p está, así que el delta existe.
        week = {"sessions": sessions}
        row = OPT.enrich("2026-10-15", "2026-11-06", 1.12, 900, chain, prev, _weekdays, week, "EUR")
        self.assertEqual(row["call_wall"]["delta_oi_1w"], 400 - 101)
        self.assertIsNone(row["call_wall"]["delta_oi_1m"])
        self.assertEqual(row["aggregate"]["delta_oi_1w"], 900 - 999)
        gone = {"sessions": [_snap("2026-09-0%d" % i, "EUR", "2026-11-06", 10, {1.5: {"c": 10, "p": 0}}) for i in range(1, 6)]}
        missing = OPT.enrich("2026-10-15", "2026-11-06", 1.12, 900, chain, prev, _weekdays, gone, "EUR")
        self.assertIsNone(missing["call_wall"]["delta_oi_1w"])
        self.assertNotEqual(missing["call_wall"]["delta_oi_1w"], 0)

    def test_roll_window_uses_seven_calendar_days_and_outranks_a_thin_book(self):
        chain = {1.13: {"c": 400, "p": 0}}
        row = OPT.enrich("2026-10-30", "2026-11-06", 1.12, 400, chain, None, _weekdays, ccy="EUR")
        self.assertEqual(row["dte_calendar"], 7)
        self.assertTrue(row["roll_window_7d"])
        self.assertEqual(row["note"], OPT.ROLL_NOTE)
        self.assertEqual(row["map_status"], "EXPIRING_≤7D")
        self.assertIsNone(row["put_wall"])
        later = OPT.enrich("2026-10-29", "2026-11-06", 1.12, 400, {1.13: {"c": 400, "p": 0}, 1.10: {"c": 0, "p": 400}}, None, _weekdays, ccy="EUR")
        self.assertEqual(later["dte_calendar"], 8)
        self.assertFalse(later["roll_window_7d"])
        self.assertEqual(later["map_status"], "READABLE")
        self.assertNotIn("note", later)

    def test_history_rotates_and_replaces_the_same_session(self):
        sessions = [_snap("2026-09-%02d" % i, "EUR", "2026-11-06", i, {}) for i in range(1, 32)]
        kept = OPT.remember({"sessions": sessions}, _snap("2026-10-15", "EUR", "2026-11-06", 1, {}))
        self.assertEqual(len(kept["sessions"]), OPT.HISTORY_KEEP)
        self.assertEqual(kept["sessions"][-1]["session"], "2026-10-15")
        self.assertNotIn("2026-09-01", [s["session"] for s in kept["sessions"]])
        again = OPT.remember(kept, _snap("2026-10-15", "EUR", "2026-11-06", 9, {}))
        self.assertEqual(len(again["sessions"]), OPT.HISTORY_KEEP)
        self.assertEqual(again["sessions"][-1]["currencies"]["EUR"]["oi_front"], 9)

    def test_published_file_matches_the_build_and_frozen_hashes_stay(self):
        before = _sha("data/OPTIONS_SURFACE.json")
        doc = OPT.build(ROOT)
        self.assertEqual(_sha("data/OPTIONS_SURFACE.json"), before)
        self.assertEqual(doc["schema"], "cme-options-c2-shadow-1")
        self.assertEqual(doc["mode"], "SHADOW")
        with open(os.path.join(ROOT, "data", OPT.SHADOW_NAME), encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved, doc)
        for rel, digest in FROZEN.items():
            self.assertEqual(_sha(rel), digest, rel)


if __name__ == "__main__":
    unittest.main()

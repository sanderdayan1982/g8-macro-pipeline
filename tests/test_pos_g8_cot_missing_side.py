"""ORDEN_ENZO_REVISION-TRIPLE-01 Block 3 (14-nov-2026): a missing COT side is null.

A present zero stays 0. Both sides present keep long − short. A non-numeric
STALE marker is missing and is not filled with 0. None serialises as JSON null.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import pos_g8_cot_collector as C  # noqa: E402


def tff_row(**overrides):
    row = {
        "report_date_as_yyyy_mm_dd": "2026-11-03",
        "open_interest_all": "100000",
        "lev_money_positions_long": "120",
        "lev_money_positions_short": "45",
        "asset_mgr_positions_long": "80",
        "asset_mgr_positions_short": "30",
    }
    for key, value in overrides.items():
        if value is _OMIT:
            row.pop(key, None)
        else:
            row[key] = value
    return row


class _Omit:
    pass


_OMIT = _Omit()


class MissingSideNet(unittest.TestCase):
    def test_short_missing_is_null(self):
        for value in (None, "", "STALE", _OMIT):
            out = C.build_ccy("EUR", [tff_row(lev_money_positions_short=value)])
            self.assertIsNone(out["lf_net"], value)
            self.assertIn('"lf_net": null', json.dumps({"lf_net": out["lf_net"]}))
            am = C.build_ccy("EUR", [tff_row(asset_mgr_positions_short=value)])
            self.assertIsNone(am["am_net"], value)
            self.assertIn('"am_net": null', json.dumps({"am_net": am["am_net"]}))

    def test_long_missing_is_null(self):
        for value in (None, "", "STALE", _OMIT):
            out = C.build_ccy("EUR", [tff_row(lev_money_positions_long=value)])
            self.assertIsNone(out["lf_net"], value)
            self.assertIn('"lf_net": null', json.dumps({"lf_net": out["lf_net"]}))
            am = C.build_ccy("EUR", [tff_row(asset_mgr_positions_long=value)])
            self.assertIsNone(am["am_net"], value)
            self.assertIn('"am_net": null', json.dumps({"am_net": am["am_net"]}))

    def test_both_present_unchanged(self):
        # Legacy arithmetic when both sides are present: (long or 0) − (short or 0).
        long_s, short_s = "120", "45"
        legacy = (float(long_s) or 0) - (float(short_s) or 0)
        out = C.build_ccy("EUR", [tff_row(
            lev_money_positions_long=long_s,
            lev_money_positions_short=short_s,
            asset_mgr_positions_long="80",
            asset_mgr_positions_short="30",
        )])
        self.assertEqual(out["lf_net"], legacy)
        self.assertEqual(out["lf_net"], 75.0)
        am_legacy = (float("80") or 0) - (float("30") or 0)
        self.assertEqual(out["am_net"], am_legacy)
        self.assertEqual(out["am_net"], 50.0)

    def test_present_zero_stays_zero(self):
        out = C.build_ccy("EUR", [tff_row(
            lev_money_positions_long="0",
            lev_money_positions_short="0",
            asset_mgr_positions_long="15",
            asset_mgr_positions_short="0",
        )])
        self.assertEqual(out["lf_net"], 0)
        self.assertIsNotNone(out["lf_net"])
        self.assertEqual(out["am_net"], 15.0)
        self.assertNotIn("null", json.dumps({"lf_net": out["lf_net"], "am_net": out["am_net"]}))
        one_sided_zero = C.build_ccy("GBP", [tff_row(
            lev_money_positions_long="0",
            lev_money_positions_short="40",
        )])
        self.assertEqual(one_sided_zero["lf_net"], -40.0)


if __name__ == "__main__":
    unittest.main()

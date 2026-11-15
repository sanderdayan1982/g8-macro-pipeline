"""FAMILIAS-01 module B — dealer fields on the same CFTC TFF row, in shadow.

Synthetic rows only. No network. The golden file is the collector output
from before this module: existing keys must stay identical.
"""
import datetime as dt
import inspect
import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import pos_g8_cot_collector as C  # noqa: E402

GOLDEN = ROOT / "tests" / "fixtures" / "familias01_b" / "existing_keys.json"
TFF_BEFORE = {
    "lf_long": "lev_money_positions_long",
    "lf_short": "lev_money_positions_short",
    "am_long": "asset_mgr_positions_long",
    "am_short": "asset_mgr_positions_short",
}


def synth_tff_rows(n=170):
    """170 weekly prints. Dealer columns are present; the old transform ignores them."""
    start = dt.date(2023, 1, 3)
    rows = []
    for i in range(n):
        day = start + dt.timedelta(weeks=i)
        rows.append({
            "report_date_as_yyyy_mm_dd": day.isoformat() + "T00:00:00.000",
            "cftc_contract_market_code": "099741",
            "open_interest_all": str(100000 + i * 10),
            "lev_money_positions_long": str(1000 + i * 3),
            "lev_money_positions_short": str(400 + (i % 17) * 5),
            "asset_mgr_positions_long": str(2000 + i * 2),
            "asset_mgr_positions_short": str(1500 + (i % 11) * 7),
            "dealer_positions_long_all": str(500 + i),
            "dealer_positions_short_all": str(800 + (i % 9)),
            "dealer_positions_spread_all": str(10 + (i % 4)),
            "traders_dealer_long_all": str(5 + (i % 3)),
            "traders_dealer_short_all": str(4 + (i % 2)),
            "traders_dealer_spread_all": str(1 + (i % 3)),
            "traders_tot_all": str(100 + i),
            "change_in_open_interest_all": str((i % 5) - 2),
        })
    return rows


def synth_metal_rows(n=170):
    start = dt.date(2023, 1, 3)
    rows = []
    for i in range(n):
        day = start + dt.timedelta(weeks=i)
        rows.append({
            "report_date_as_yyyy_mm_dd": day.isoformat() + "T00:00:00.000",
            "cftc_contract_market_code": "088691",
            "open_interest_all": str(50000 + i),
            "m_money_positions_long_all": str(2000 + i),
            "m_money_positions_short_all": str(1000 + (i % 13)),
        })
    return rows


def synth_usd_rows():
    rows = []
    for i in range(7):
        rows.append({
            "_z_lf": -0.2 * (i + 1),
            "_z_am": 0.1 * (i + 1),
            "lf_z": round(-0.2 * (i + 1), 2),
            "am_z": round(0.1 * (i + 1), 2),
            "lf_net": 1000 * (i + 1),
            "dealer_shadow": {"long": 999999, "net": 999999},
        })
    return rows


def _without_shadow(row):
    out = dict(row)
    out.pop("dealer_shadow", None)
    return out


def _http_400(column):
    err = urllib.error.HTTPError(
        "https://publicreporting.cftc.gov/resource/gpe5-46if.json",
        400, "Bad Request", {}, io.BytesIO(b""),
    )
    err.pos_body = (
        "Query coordinator error: query.soql.no-such-column; No such column: %s; position: 1"
        % column
    )
    return err


class DealerShadow(unittest.TestCase):
    def test_tff_column_map_keeps_lf_and_am_without_all_suffix(self):
        self.assertEqual(C.COL["tff"], TFF_BEFORE)
        cols = C.tff_columns()
        self.assertEqual(cols[:4], list(TFF_BEFORE.values()))
        for name in (
            "dealer_positions_long_all",
            "dealer_positions_short_all",
            "dealer_positions_spread_all",
            "traders_dealer_long_all",
            "traders_dealer_short_all",
            "traders_dealer_spread_all",
            "traders_tot_all",
            "change_in_open_interest_all",
        ):
            self.assertIn(name, cols)
        self.assertNotIn("lev_money_positions_long_all", cols)
        self.assertNotIn("asset_mgr_positions_long_all", cols)

    def test_missing_column_stays_null(self):
        shadow = C.dealer_shadow({
            "dealer_positions_long_all": "100",
            "dealer_positions_spread_all": "4",
            "traders_dealer_short_all": "8",
            "change_in_open_interest_all": "",
        })
        self.assertEqual(shadow["long"], 100.0)
        self.assertIsNone(shadow["short"])
        self.assertEqual(shadow["spread"], 4.0)
        self.assertIsNone(shadow["net"])
        self.assertEqual(shadow["net_state"], "UNAVAILABLE")
        self.assertIsNone(shadow["traders"]["long"])
        self.assertEqual(shadow["traders"]["short"], 8.0)
        self.assertIsNone(shadow["traders"]["spread"])
        self.assertIsNone(shadow["traders_total"])
        self.assertIsNone(shadow["oi_change"])
        self.assertEqual(shadow["oi_change_state"], "UNAVAILABLE")
        self.assertNotEqual(shadow["short"], 0)
        self.assertNotEqual(shadow["net"], 0)
        self.assertNotEqual(shadow["net"], 100)

    def test_net_only_when_both_sides_present(self):
        both = C.dealer_shadow({
            "dealer_positions_long_all": "10",
            "dealer_positions_short_all": "4",
            "dealer_positions_spread_all": "100",
        })
        self.assertEqual(both["net"], 6.0)
        self.assertIsNone(both["net_state"])
        self.assertEqual(both["spread"], 100.0)

        missing_long = C.dealer_shadow({"dealer_positions_short_all": "4"})
        self.assertIsNone(missing_long["net"])
        self.assertEqual(missing_long["net_state"], "UNAVAILABLE")

        missing_both = C.dealer_shadow({})
        self.assertIsNone(missing_both["long"])
        self.assertIsNone(missing_both["short"])
        self.assertIsNone(missing_both["spread"])
        self.assertIsNone(missing_both["net"])
        self.assertEqual(missing_both["net_state"], "UNAVAILABLE")

    def test_present_zero_is_kept(self):
        shadow = C.dealer_shadow({
            "dealer_positions_long_all": "0",
            "dealer_positions_short_all": "0",
            "dealer_positions_spread_all": "0",
            "traders_dealer_long_all": "0",
            "traders_dealer_short_all": "0",
            "traders_dealer_spread_all": "0",
            "traders_tot_all": "0",
            "change_in_open_interest_all": "0",
        })
        self.assertEqual(shadow["long"], 0.0)
        self.assertEqual(shadow["short"], 0.0)
        self.assertEqual(shadow["net"], 0.0)
        self.assertIsNone(shadow["net_state"])
        self.assertEqual(shadow["traders"]["long"], 0.0)
        self.assertEqual(shadow["traders"]["spread"], 0.0)
        self.assertEqual(shadow["traders_total"], 0.0)
        self.assertEqual(shadow["oi_change"], 0.0)
        self.assertEqual(shadow["oi_change_state"], "FLAT")

    def test_oi_change_states_including_unavailable(self):
        self.assertEqual(C.oi_change_state(5), "UP")
        self.assertEqual(C.oi_change_state(0.1), "UP")
        self.assertEqual(C.oi_change_state(-2), "DOWN")
        self.assertEqual(C.oi_change_state(-0.5), "DOWN")
        self.assertEqual(C.oi_change_state(0), "FLAT")
        self.assertEqual(C.oi_change_state(0.0), "FLAT")
        self.assertEqual(C.oi_change_state(None), "UNAVAILABLE")
        self.assertEqual(
            C.dealer_shadow({"change_in_open_interest_all": "15"})["oi_change_state"],
            "UP",
        )
        self.assertEqual(
            C.dealer_shadow({"change_in_open_interest_all": "-3"})["oi_change_state"],
            "DOWN",
        )
        self.assertEqual(C.dealer_shadow({})["oi_change_state"], "UNAVAILABLE")
        self.assertIsNone(C.dealer_shadow({})["oi_change"])

    def test_new_fields_do_not_use_or_0(self):
        for fn in (C.dealer_shadow, C.dealer_net, C.oi_change_state):
            src = inspect.getsource(fn)
            self.assertNotRegex(src, r"or\s+0(?:\.0)?\b", fn.__name__)
        shadow = C.dealer_shadow({"dealer_positions_long_all": "12"})
        self.assertIsNone(shadow["net"])
        self.assertNotEqual(shadow["net"], 12)
        self.assertNotEqual(shadow["net"], 0)

    def test_rejected_column_retry_renames_only_that_column(self):
        selects = []

        def fake_get(dataset, params):
            select = params["$select"]
            selects.append(select)
            names = select.split(",")
            if "dealer_positions_long_all" in names:
                raise _http_400("dealer_positions_long_all")
            return [{
                "report_date_as_yyyy_mm_dd": "2026-11-03T00:00:00.000",
                "cftc_contract_market_code": "099741",
                "open_interest_all": "1",
                "dealer_positions_long": "5",
            }]

        with patch.object(C, "_get", fake_get):
            out = C.fetch(C.DS_TFF, ["099741"], C.tff_columns())
        self.assertEqual(len(selects), 2)
        first = selects[0].split(",")
        second = selects[1].split(",")
        self.assertEqual(
            [c for c in first if c != "dealer_positions_long_all"],
            [c for c in second if c != "dealer_positions_long"],
        )
        self.assertIn("dealer_positions_long", second)
        self.assertNotIn("dealer_positions_long_all", second)
        self.assertIn("lev_money_positions_long", second)
        self.assertNotIn("lev_money_positions_long_all", second)
        self.assertIn("asset_mgr_positions_short", second)
        self.assertNotIn("asset_mgr_positions_short_all", second)
        self.assertIn("dealer_positions_short_all", second)
        self.assertIn("traders_dealer_spread_all", second)
        self.assertIn("099741", out)

    def test_rejected_column_twice_drops_only_that_column(self):
        selects = []
        rejected = {"traders_dealer_spread_all", "traders_dealer_spread"}

        def fake_get(dataset, params):
            select = params["$select"]
            selects.append(select)
            names = select.split(",")
            hit = rejected.intersection(names)
            if hit:
                raise _http_400(next(iter(hit)))
            return [{
                "report_date_as_yyyy_mm_dd": "2026-11-03T00:00:00.000",
                "cftc_contract_market_code": "099741",
                "open_interest_all": "1",
            }]

        with patch.object(C, "_get", fake_get):
            C.fetch(C.DS_TFF, ["099741"], C.tff_columns())
        self.assertEqual(len(selects), 3)
        first = selects[0].split(",")
        last = selects[2].split(",")
        self.assertNotIn("traders_dealer_spread_all", last)
        self.assertNotIn("traders_dealer_spread", last)
        self.assertEqual([c for c in first if c not in rejected], last)
        self.assertIn("lev_money_positions_long", last)
        self.assertIn("dealer_positions_long_all", last)
        self.assertIn("traders_dealer_long_all", last)
        self.assertIn("change_in_open_interest_all", last)

    def test_opaque_400_does_not_rename_the_other_columns(self):
        calls = []

        def fake_get(dataset, params):
            calls.append(params["$select"])
            err = urllib.error.HTTPError(
                "https://publicreporting.cftc.gov/resource/gpe5-46if.json",
                400, "Bad Request", {}, io.BytesIO(b"nope"),
            )
            err.pos_body = "nope"
            raise err

        with patch.object(C, "_get", fake_get):
            with self.assertRaises(urllib.error.HTTPError):
                C.fetch(C.DS_TFF, ["099741"], C.tff_columns())
        self.assertEqual(len(calls), 1)
        self.assertIn("lev_money_positions_long", calls[0])
        self.assertNotIn("lev_money_positions_long_all", calls[0])

    def test_existing_keys_match_the_prechange_snapshot(self):
        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        got = _without_shadow(C.build_ccy("EUR", synth_tff_rows()))
        self.assertEqual(got, golden["ccy"])
        self.assertEqual(json.dumps(got, sort_keys=True), json.dumps(golden["ccy"], sort_keys=True))
        metal = C.build_metal("XAU", synth_metal_rows())
        self.assertEqual(metal, golden["metal"])
        self.assertNotIn("dealer_shadow", metal)
        usd = C.usd_proxy(synth_usd_rows())
        for row in synth_usd_rows():
            row.pop("_z_lf", None)
            row.pop("_z_am", None)
        self.assertEqual(usd, golden["usd"])
        self.assertNotIn("dealer_shadow", usd)

    def test_shadow_is_additive_on_the_currency_row_only(self):
        row = C.build_ccy("EUR", synth_tff_rows())
        shadow = row["dealer_shadow"]
        i = len(synth_tff_rows()) - 1
        self.assertEqual(shadow["long"], float(500 + i))
        self.assertEqual(shadow["short"], float(800 + (i % 9)))
        self.assertEqual(shadow["spread"], float(10 + (i % 4)))
        self.assertEqual(shadow["net"], shadow["long"] - shadow["short"])
        self.assertIsNone(shadow["net_state"])
        self.assertEqual(shadow["traders"]["long"], float(5 + (i % 3)))
        self.assertEqual(shadow["traders"]["short"], float(4 + (i % 2)))
        self.assertEqual(shadow["traders"]["spread"], float(1 + (i % 3)))
        self.assertEqual(shadow["traders_total"], float(100 + i))
        self.assertEqual(shadow["oi_change"], float((i % 5) - 2))
        self.assertEqual(shadow["oi_change"], 2.0)
        self.assertEqual(shadow["oi_change_state"], "UP")
        self.assertNotIn("dealer_shadow", C.build_metal("XAU", synth_metal_rows()))

    def test_published_scores_do_not_read_the_shadow(self):
        for rel in ("scripts/generators/pos_g8.py", "scripts/dashboard_alerts.py"):
            src = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn("dealer_shadow", src, rel)
        src = (ROOT / "scripts" / "pos_g8_cot_collector.py").read_text(encoding="utf-8")
        self.assertIn('"schema": "pos_g8_cot/1.2.0"', src)
        main_src = inspect.getsource(C.main)
        self.assertEqual(main_src.count("fetch("), 2)
        self.assertIn("tff_columns()", main_src)
        self.assertIn('list(COL["disagg"].values())', main_src)


if __name__ == "__main__":
    unittest.main()

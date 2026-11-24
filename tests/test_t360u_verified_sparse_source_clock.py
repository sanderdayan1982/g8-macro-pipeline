"""orden Sander 24-nov t360u: las series VERIFIED_SPARSE usan el reloj de su fuente
(no un reloj diario sobre la última impresión de la columna dispersa)."""
import csv
import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import health_monitor as HM  # noqa: E402
from g8common import schedule as SC  # noqa: E402


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class SparseOverdueUsesSourceClock(unittest.TestCase):
    def setUp(self):
        self.cals = SC.load_calendars(ROOT)
        with open(os.path.join(ROOT, "sources", "schedules.csv"), encoding="utf-8") as fh:
            self.passes = list(csv.DictReader(fh))
        with open(os.path.join(ROOT, "sources", "freshness_rules.csv"), encoding="utf-8") as fh:
            self.rule_list = list(csv.DictReader(fh))
        self.rules = {r["rule_id"]: r for r in self.rule_list}
        self.outputs = HM.load_freshness_outputs(ROOT)
        self.params = HM.load_freshness_params(ROOT)

    def test_nz_cash_model_none_uses_b2_source_rule(self):
        src = HM.source_clock_rule(self.rules["NZ_CASH"], self.rule_list, self.params)
        self.assertEqual(src["rule_id"], "NZ_B2")
        self.assertTrue(HM.uses_source_table_clock(self.rules["NZ_CASH"], self.params))

    def test_have_max_20_nov_is_not_overdue_on_24_nov_morning(self):
        # Same snapshot as health.json 24-nov 06:58Z: last official cash 20-nov,
        # last B2 fetch 23-nov 20:02Z. Next table pub is 24-nov; first MAC query
        # is 19:30 WAT (18:30Z). UNKNOWN may still be set by cash(), but it is
        # not UNKNOWN_OVERDUE that morning.
        now = ts("2026-11-24T06:58:53Z")
        items = [{
            "file": "NZD_CASH_ON.csv",
            "status": "UNKNOWN",
            "state": "UNKNOWN_SCHEDULE",
            "flags": [],
            "have_max": "2026-11-20",
            "verified_download_utc": "2026-11-23T20:02:55Z",
            "reason": "Falta comprobar la última publicación B2 o su recepción íntegra.",
        }]
        HM.mark_unknown_overdue(items, now, self.rule_list, self.passes, self.cals, self.outputs, self.params)
        self.assertNotIn("UNKNOWN_OVERDUE", items[0]["flags"])
        self.assertFalse(HM.is_counted_issue(items[0]))
        pub_day = HM.next_source_pub_day_after(self.rules["NZ_B2"], self.cals, ts("2026-11-23T20:02:55Z"))
        self.assertEqual(pub_day, date(2026, 11, 24))
        resol = HM.expected_resolution_utc(self.rules["NZ_B2"], self.passes, self.cals, pub_day, self.rule_list, self.outputs)
        self.assertEqual(items[0].get("expected_resolution_utc"), resol.isoformat().replace("+00:00", "Z"))

    def test_verified_sparse_current_is_never_unknown_overdue(self):
        now = ts("2026-11-24T06:58:53Z")
        items = [{
            "file": "NZD_CASH_ON.csv",
            "status": "CURRENT",
            "state": "VERIFIED_SPARSE",
            "flags": [],
            "have_max": "2026-11-20",
            "reason": "x",
        }]
        HM.mark_unknown_overdue(items, now, self.rule_list, self.passes, self.cals, self.outputs, self.params)
        self.assertNotIn("UNKNOWN_OVERDUE", items[0].get("flags") or [])
        self.assertFalse(HM.is_counted_issue(items[0]))

    def test_unknown_overdue_follows_source_query_plus_24h(self):
        # Source pub 24-nov, first MAC 18:30Z; overdue only after +24 h.
        # Anchored to the fetch of 23-nov 20:02Z, so the clock does not slide.
        resol = HM.expected_resolution_utc(
            self.rules["NZ_B2"], self.passes, self.cals, date(2026, 11, 24), self.rule_list, self.outputs,
        )
        self.assertIsNotNone(resol)
        items = [{
            "file": "NZD_CASH_ON.csv",
            "status": "UNKNOWN",
            "state": "UNKNOWN_SCHEDULE",
            "flags": [],
            "have_max": "2026-11-20",
            "verified_download_utc": "2026-11-23T20:02:55Z",
            "reason": "x",
        }]
        HM.mark_unknown_overdue(items, resol + timedelta(hours=24, seconds=1), self.rule_list, self.passes, self.cals, self.outputs, self.params)
        self.assertIn("UNKNOWN_OVERDUE", items[0]["flags"])
        self.assertTrue(HM.is_counted_issue(items[0]))
        items2 = [{
            "file": "NZD_CASH_ON.csv",
            "status": "UNKNOWN",
            "state": "UNKNOWN_SCHEDULE",
            "flags": [],
            "have_max": "2026-11-20",
            "verified_download_utc": "2026-11-23T20:02:55Z",
            "reason": "x",
        }]
        HM.mark_unknown_overdue(items2, resol + timedelta(hours=24) - timedelta(seconds=1), self.rule_list, self.passes, self.cals, self.outputs, self.params)
        self.assertNotIn("UNKNOWN_OVERDUE", items2[0]["flags"])


if __name__ == "__main__":
    unittest.main()

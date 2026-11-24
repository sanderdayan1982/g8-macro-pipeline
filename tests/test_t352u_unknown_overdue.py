"""t352u, orden expresa Sander 23-nov: incidencias = issues[]; UNKNOWN en línea aparte;
un UNKNOWN que sigue así pasada su hora prevista de resolución + 24 h cuenta como incidencia."""
import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import health_monitor as HM  # noqa: E402
from g8common import schedule as SC  # noqa: E402


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


class CountedIssues(unittest.TestCase):
    def test_plain_unknown_is_not_an_incidencia(self):
        self.assertFalse(HM.is_counted_issue({"status": "UNKNOWN", "flags": []}))

    def test_unknown_overdue_is_an_incidencia(self):
        self.assertTrue(HM.is_counted_issue({"status": "UNKNOWN", "flags": ["UNKNOWN_OVERDUE"]}))

    def test_late_still_counts(self):
        self.assertTrue(HM.is_counted_issue({"status": "LATE", "flags": []}))


class ResolutionClock(unittest.TestCase):
    def setUp(self):
        self.cals = SC.load_calendars(ROOT)
        self.passes = list(__import__("csv").DictReader(open(os.path.join(ROOT, "sources", "schedules.csv"))))
        self.rule_list = list(__import__("csv").DictReader(open(os.path.join(ROOT, "sources", "freshness_rules.csv"))))
        self.rules = {r["rule_id"]: r for r in self.rule_list}
        self.outputs = HM.load_freshness_outputs(ROOT)

    def test_resolution_is_first_query_after_pub_local(self):
        # NZ_CASH pub 16:04 Pacific/Auckland, query_groups=MAC (MAC1 19:30, MAC2 21:00 WAT).
        rule = self.rules["NZ_CASH"]
        # A dated NZ business day in Nov 2026: Friday 20-nov
        resol = HM.expected_resolution_utc(rule, self.passes, self.cals, date(2026, 11, 20))
        self.assertIsNotNone(resol)
        pub = SC.publication_utc(rule, self.cals, date(2026, 11, 20))
        self.assertGreater(resol, pub)

    def test_no_clock_without_pub_local(self):
        # A non-derived rule without pub_local still has no clock (ACM_TP).
        # orden Sander 24-nov t364u: ejemplo actualizado, ES_10Y ya tiene pub_local
        rule = self.rules["ACM_TP"]
        self.assertFalse((rule.get("pub_local") or "").strip())
        self.assertNotEqual(rule.get("frequency"), "derived")
        self.assertIsNone(HM.expected_resolution_utc(rule, self.passes, self.cals, date(2026, 11, 20), self.rule_list, self.outputs))

    def test_derived_clock_is_latest_of_inputs(self):
        # t357u: el reloj de una serie derivada es la hora prevista más tardía de sus entradas.
        # orden Sander 24-nov t364u: ejemplo actualizado, ES_10Y ya tiene pub_local
        spread = self.rules["ES_DE_10Y_SPREAD"]
        de = self.rules["DE10Y_PAR"]
        es = self.rules["ES10Y_DAILY"]
        day = date(2026, 11, 20)
        de_clock = HM.expected_resolution_utc(de, self.passes, self.cals, day, self.rule_list, self.outputs)
        es_clock = HM.expected_resolution_utc(es, self.passes, self.cals, day, self.rule_list, self.outputs)
        spread_clock = HM.expected_resolution_utc(spread, self.passes, self.cals, day, self.rule_list, self.outputs)
        self.assertIsNotNone(de_clock)
        self.assertIsNotNone(es_clock)
        self.assertEqual(es_clock, max(de_clock, es_clock))
        self.assertEqual(spread_clock, es_clock)

    def test_spread_can_become_unknown_overdue(self):
        day = date(2026, 11, 20)
        resol = HM.expected_resolution_utc(self.rules["ES_DE_10Y_SPREAD"], self.passes, self.cals, day, self.rule_list, self.outputs)
        self.assertIsNotNone(resol)
        items = [{"file": "ES_DE_10Y_SPREAD_DAILY.csv", "status": "UNKNOWN", "flags": [], "have_max": "2026-11-20", "reason": "x"}]
        HM.mark_unknown_overdue(items, resol + timedelta(hours=24, seconds=1), self.rule_list, self.passes, self.cals, self.outputs)
        self.assertIn("UNKNOWN_OVERDUE", items[0]["flags"])
        self.assertTrue(HM.is_counted_issue(items[0]))
        items2 = [{"file": "ES_DE_10Y_SPREAD_DAILY.csv", "status": "UNKNOWN", "flags": [], "have_max": "2026-11-20", "reason": "x"}]
        HM.mark_unknown_overdue(items2, resol + timedelta(hours=24) - timedelta(seconds=1), self.rule_list, self.passes, self.cals, self.outputs)
        self.assertNotIn("UNKNOWN_OVERDUE", items2[0]["flags"])

    def test_mark_overdue_after_resolution_plus_24h(self):
        rule = self.rules["NZ_CASH"]
        resol = HM.expected_resolution_utc(rule, self.passes, self.cals, date(2026, 11, 20))
        items = [{"file": "NZD_CASH_ON.csv", "status": "UNKNOWN", "flags": [], "have_max": "2026-11-20", "reason": "x"}]
        HM.mark_unknown_overdue(items, resol + timedelta(hours=24, seconds=1), self.rule_list, self.passes, self.cals, self.outputs)
        self.assertIn("UNKNOWN_OVERDUE", items[0]["flags"])
        self.assertTrue(HM.is_counted_issue(items[0]))
        # 1 second before the deadline: not yet
        items2 = [{"file": "NZD_CASH_ON.csv", "status": "UNKNOWN", "flags": [], "have_max": "2026-11-20", "reason": "x"}]
        HM.mark_unknown_overdue(items2, resol + timedelta(hours=24) - timedelta(seconds=1), self.rule_list, self.passes, self.cals, self.outputs)
        self.assertNotIn("UNKNOWN_OVERDUE", items2[0]["flags"])
        self.assertFalse(HM.is_counted_issue(items2[0]))


if __name__ == "__main__":
    unittest.main()

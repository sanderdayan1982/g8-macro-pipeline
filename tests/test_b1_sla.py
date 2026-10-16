"""B1 (2026-10-16, consenso Claude + Argon 4): la espera de una observación exigible tiene límite.

OVERDUE con causa SIN_PASADA_PROGRAMADA es PENDING solo durante 1 día hábil del calendario del feed desde
overdue_since; después es LATE (issue → needs_repair → agente + Telegram). Una verificación de tipo oficial
caducada es LATE (state EVIDENCE_EXPIRED), no UNKNOWN.
"""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import health_monitor as HM  # noqa: E402


def row(since, cal="TARGET", file="zz_b1.csv"):
    return {"file": file, "state": "OVERDUE", "cause": "SIN_PASADA_PROGRAMADA", "have_max": "2026-10-14",
            "expected_obs": "2026-10-15", "calendar": cal, "overdue_since": since, "facts": {}}


def status(now, r):
    out = HM.build(ROOT, now, {"outputs": [r]})
    item = next(x for x in out["feeds"] if x["file"] == r["file"])
    return item, any(x["file"] == r["file"] for x in out["issues"]), out


class SLA(unittest.TestCase):
    def test_within_one_business_day_is_pending(self):
        item, issue, _ = status(datetime(2026, 10, 16, 20, tzinfo=timezone.utc), row("2026-10-16T08:00:00Z"))
        self.assertEqual(item["status"], "PENDING")
        self.assertEqual(item["sla_deadline_utc"], "2026-10-19T08:00:00Z")   # viernes → lunes (fin de semana)
        self.assertFalse(issue)

    def test_after_one_business_day_is_late_and_needs_repair(self):
        item, issue, out = status(datetime(2026, 10, 19, 8, 1, tzinfo=timezone.utc), row("2026-10-16T08:00:00Z"))
        self.assertEqual(item["status"], "LATE")
        self.assertTrue(issue)
        self.assertTrue(out["needs_repair"])
        self.assertIn("SLA superado", item["reason"])

    def test_holiday_extends_the_grace(self):
        cals = HM.SC.load_calendars(str(ROOT))
        d = HM.sla_deadline("2026-12-24T08:00:00Z", "TARGET", cals)
        if "2026-12-25" in cals.get("TARGET", set()):
            self.assertEqual(d.date().isoformat(), "2026-12-28")   # 25 festivo TARGET + fin de semana

    def test_unknown_since_keeps_pending(self):
        r = row(None)
        item, issue, _ = status(datetime(2026, 10, 20, tzinfo=timezone.utc), r)
        self.assertEqual(item["status"], "PENDING")
        self.assertFalse(issue)

    def test_naive_timestamp_is_utc_and_comparable(self):
        item, issue, _ = status(datetime(2026, 10, 19, 8, 1, tzinfo=timezone.utc), row("2026-10-16T08:00:00"))
        self.assertEqual(item["status"], "LATE")
        self.assertEqual(item["sla_deadline_utc"], "2026-10-19T08:00:00Z")
        d = HM.sla_deadline("2026-10-16", "TARGET", {})
        self.assertEqual(d.isoformat(), "2026-10-19T00:00:00+00:00")

    def test_bad_timestamp_is_not_a_crash(self):
        self.assertIsNone(HM.sla_deadline("no-date", "TARGET", {}))


if __name__ == "__main__":
    unittest.main()

"""t348u: el timer SWPT del VPS cubre el día hábil siguiente a un jueves festivo US."""
import os
import sys
import unittest
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "vps"))
import swpt_weekly as S  # noqa: E402


def Z(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)


class SwptWeeklyPubDay(unittest.TestCase):
    def test_regular_thursday_runs_friday_does_not(self):
        # jue 19-nov-2026 hábil US: día oficial. El viernes normal no.
        self.assertTrue(S.is_official_pub_day(Z("2026-11-19T21:00Z"), ROOT))
        self.assertFalse(S.is_official_pub_day(Z("2026-11-20T21:00Z"), ROOT))

    def test_thanksgiving_2026_moves_to_friday(self):
        # jue 26-nov-2026 Thanksgiving: no publica. vie 27 sí (16:30 ET).
        self.assertFalse(S.is_official_pub_day(Z("2026-11-26T21:00Z"), ROOT))
        self.assertTrue(S.is_official_pub_day(Z("2026-11-27T21:00Z"), ROOT))

    def test_timer_mirrors_thursday_and_friday(self):
        with open(os.path.join(ROOT, "vps", "g8-swpt-weekly.timer"), encoding="utf-8") as fh:
            timer = fh.read()
        self.assertIn("OnCalendar=Thu 21:00:00", timer)
        self.assertIn("OnCalendar=Fri 21:00:00", timer)
        with open(os.path.join(ROOT, "vps", "g8-swpt-weekly.service"), encoding="utf-8") as fh:
            svc = fh.read()
        self.assertIn("ProtectHome=yes", svc)
        self.assertIn("G8_VPS_JOB=swpt", svc)
        self.assertNotRegex(svc, r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

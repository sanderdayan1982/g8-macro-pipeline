"""t334u / t348u: el workflow SWPT solo toca data/SWPT.csv, tiene dispatch y, tras t348u, ningún cron."""
import os
import re
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WF = os.path.join(ROOT, ".github", "workflows", "swpt_update.yml")
TIMER = os.path.join(ROOT, "vps", "g8-swpt-weekly.timer")


class SwptWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(WF, encoding="utf-8") as fh:
            cls.text = fh.read()

    def test_workflow_exists_and_has_dispatch_and_thursday_cron(self):
        # orden Sander t348u: cron swpt migrado al VPS, workflow solo manual
        self.assertTrue(os.path.isfile(WF))
        self.assertIn("workflow_dispatch:", self.text)
        self.assertNotIn("schedule:", self.text)
        self.assertNotRegex(self.text, r"^\s*- cron:", re.M)
        self.assertTrue(os.path.isfile(TIMER))
        with open(TIMER, encoding="utf-8") as fh:
            timer = fh.read()
        # orden Sander 25-nov t378u: swpt a las 22:00 UTC
        self.assertIn("OnCalendar=Thu 22:00:00", timer)
        self.assertIn("OnCalendar=Fri 22:00:00", timer)

    def test_only_adds_swpt_csv(self):
        adds = re.findall(r"git add (.+)", self.text)
        self.assertEqual(adds, ["data/SWPT.csv"])
        self.assertNotRegex(self.text, r"git add\s+data/\*")
        self.assertIn("[skip netlify]", self.text)

    def test_fred_key_by_name_never_hardcoded(self):
        self.assertIn("secrets.FRED_API_KEY", self.text)
        self.assertNotRegex(self.text, r"FRED_API_KEY\s*:\s*['\"]?[A-Za-z0-9]{8,}")

    def test_uses_fetch_swpt_and_git_push_retry(self):
        self.assertIn("scripts/fetch_swpt.py", self.text)
        self.assertIn("scripts/tools/git_push_retry.sh", self.text)

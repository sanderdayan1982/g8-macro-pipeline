"""t339u/t340u: el workflow DE/ES 10Y diario solo toca esos tres CSV, tiene dispatch y cron L–V 14:00 WAT."""
import os
import re
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WF = os.path.join(ROOT, ".github", "workflows", "sovereign_10y_daily.yml")
FILES = (
    "data/DE_10Y_PAR_DAILY.csv",
    "data/ES_10Y_DAILY.csv",
    "data/ES_DE_10Y_SPREAD_DAILY.csv",
)


class Sovereign10yWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(WF, encoding="utf-8") as fh:
            cls.text = fh.read()

    def test_workflow_exists_and_has_dispatch_and_weekday_cron(self):
        # t339u, orden expresa Sander 23-nov: nuevo workflow soberano 10Y
        # cron L–V 14:00 WAT = 13:00 UTC (WAT = UTC+1 todo el año).
        self.assertTrue(os.path.isfile(WF))
        self.assertIn("workflow_dispatch:", self.text)
        self.assertRegex(self.text, r"cron:\s*'0 13 \* \* 1-5'")

    def test_only_adds_the_three_daily_files(self):
        adds = re.findall(r"git add (.+)", self.text)
        self.assertEqual(len(adds), 1)
        added = adds[0].split()
        self.assertEqual(added, list(FILES))
        self.assertNotRegex(self.text, r"git add\s+data/\*")
        self.assertIn("[skip netlify]", self.text)

    def test_uses_fetch_script_and_git_push_retry(self):
        self.assertIn("scripts/fetch_sovereign_10y_daily.py", self.text)
        self.assertIn("scripts/tools/git_push_retry.sh", self.text)
        self.assertNotIn("cme_options", self.text)
        self.assertNotIn("g8_port_run", self.text)

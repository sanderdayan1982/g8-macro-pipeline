"""t339u/t340u/t346u: el workflow DE/ES 10Y diario solo toca esos tres CSV y queda como respaldo manual (dispatch, sin cron)."""
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
        # t346u, orden expresa Sander 23-nov: cron movido al VPS
        self.assertTrue(os.path.isfile(WF))
        self.assertIn("workflow_dispatch:", self.text)
        self.assertNotRegex(self.text, r"^on:\s*$\n(?:.*\n)*?\s*schedule:", re.M)
        self.assertNotRegex(self.text, r"^\s*- cron:", re.M)
        # El timer vive en infra/vps del repo privado; la copia revisable en este repo es vps/.
        timer = os.path.join(ROOT, "vps", "g8-sovereign-10y-daily.timer")
        self.assertTrue(os.path.isfile(timer), "falta el espejo público del timer VPS")
        with open(timer, encoding="utf-8") as fh:
            ttxt = fh.read()
        self.assertIn("OnCalendar=Mon..Fri 13:00:00", ttxt)
        self.assertIn("g8-sovereign-10y-daily.service", ttxt)

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

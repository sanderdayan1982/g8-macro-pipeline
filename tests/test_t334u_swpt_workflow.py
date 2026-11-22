"""t334u: el workflow SWPT solo toca data/SWPT.csv, tiene dispatch y usa FRED_API_KEY por nombre."""
import os
import re
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WF = os.path.join(ROOT, ".github", "workflows", "swpt_update.yml")


class SwptWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(WF, encoding="utf-8") as fh:
            cls.text = fh.read()

    def test_workflow_exists_and_has_dispatch_and_thursday_cron(self):
        self.assertTrue(os.path.isfile(WF))
        self.assertIn("workflow_dispatch:", self.text)
        self.assertRegex(self.text, r"cron:\s*'0 21 \* \* 4'")

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

"""t384u: el workflow ligero del guardián CME corre en todo push a main y en PR.

Tests nuevos. No modifican tests/test_cme_public_guardian.py ni scripts congelados.
"""
import os
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WF = os.path.join(ROOT, ".github", "workflows", "cme_public_guardian.yml")
GUARDIAN = os.path.join(ROOT, "tests", "test_cme_public_guardian.py")


class CmePublicGuardianWorkflow(unittest.TestCase):
    def setUp(self):
        with open(WF, encoding="utf-8") as fh:
            self.src = fh.read()
        self.pre = self.src.split("jobs:", 1)[0]

    def test_workflow_exists(self):
        self.assertTrue(os.path.isfile(WF))
        self.assertTrue(os.path.isfile(GUARDIAN))

    def test_push_to_main_and_pull_request_without_path_filter(self):
        self.assertIn("\n  push:\n", self.pre)
        self.assertIn("branches: [main]", self.pre)
        self.assertIn("\n  pull_request:\n", self.pre)
        self.assertNotIn("paths:", self.pre)
        self.assertNotIn("paths-ignore:", self.pre)

    def test_runs_only_the_existing_guardian_test(self):
        self.assertIn("python3 -m unittest tests.test_cme_public_guardian", self.src)
        self.assertNotIn("validate_smoke.sh", self.src)
        self.assertNotIn("private_data.py", self.src)

    def test_failure_uses_existing_telegram_secrets(self):
        self.assertIn("if: failure()", self.src)
        self.assertIn("secrets.TELEGRAM_BOT_TOKEN", self.src)
        self.assertIn("secrets.TELEGRAM_CHAT_ID", self.src)
        self.assertIn("scripts/notify_telegram.py --fail", self.src)
        # solo esos dos secretos, ya usados por validate.yml
        import re
        names = set(re.findall(r"secrets\.([A-Z0-9_]+)", self.src))
        self.assertEqual(names, {"TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"})

    def test_does_not_touch_netlify_or_index(self):
        self.assertNotIn("docs/index.html", self.src)
        self.assertNotIn("netlify", self.src.lower())


if __name__ == "__main__":
    unittest.main()

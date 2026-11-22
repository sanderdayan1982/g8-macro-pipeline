"""metals_update.yml and freshness_snapshot.yml must Telegram a job failure (t330u)."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = (
    ROOT / ".github" / "workflows" / "metals_update.yml",
    ROOT / ".github" / "workflows" / "freshness_snapshot.yml",
)


def _alert_block(text: str) -> str:
    parts = re.split(r"(?m)^      - name: Alert on failure\s*$", text)
    if len(parts) < 2:
        return ""
    # body until the next same-indent step or EOF
    body = parts[1]
    nxt = re.search(r"(?m)^      - name: ", body)
    return body if nxt is None else body[: nxt.start()]


class TelegramFailAlertMetalsFreshness(unittest.TestCase):
    def test_both_workflows_alert_on_failure_after_the_public_push(self):
        for wf in WORKFLOWS:
            with self.subTest(workflow=wf.name):
                text = wf.read_text(encoding="utf-8")
                self.assertIn("bash scripts/tools/git_push_retry.sh", text)
                push_at = text.rfind("bash scripts/tools/git_push_retry.sh")
                alert_at = text.find("- name: Alert on failure")
                self.assertGreater(alert_at, push_at, "the fail alert must run after the public push")
                block = _alert_block(text)
                self.assertTrue(block, "missing Alert on failure step")
                self.assertIn("if: failure()", block)
                self.assertIn("scripts/notify_telegram.py --fail", block)
                self.assertIn("TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}", block)
                self.assertIn("TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}", block)
                self.assertIn('workflow=%s run=${{ github.run_number }}' % wf.stem, block)

    def test_does_not_remove_existing_metals_private_state_stash(self):
        # same contracts as test_metals_private_state_stash.py — this file is new, that one is untouched
        text = (ROOT / ".github" / "workflows" / "metals_update.yml").read_text(encoding="utf-8")
        self.assertIn("git stash push", text)
        self.assertIn("data/alerts/state.json", text)
        self.assertIn("git stash pop", text)


if __name__ == "__main__":
    unittest.main()

"""metals_update.yml: the private pull of state.json must not block the public push (t273u)."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows" / "metals_update.yml"


class MetalsPrivateStateStash(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WF.read_text(encoding="utf-8")

    def test_pulls_private_brief_and_state(self):
        self.assertIn('private_data.py pull "$RUNNER_TEMP/g8data" --rows 6,11,12', self.text)

    def test_does_not_stage_state_json(self):
        self.assertNotRegex(self.text, r"git add(?:\s+--)?(?:[^\n]*\s)?data/alerts/state\.json")

    def test_stashes_state_json_around_public_push(self):
        self.assertIn("bash scripts/tools/git_push_retry.sh", self.text)
        commit = self.text.split("git commit -m")[1]
        helper_at = commit.find("bash scripts/tools/git_push_retry.sh")
        self.assertGreater(helper_at, 0)
        before, after = commit[:helper_at], commit[helper_at:]
        self.assertIn("git stash push", before)
        self.assertIn("data/alerts/state.json", before)
        self.assertIn("git stash pop", after)

    def test_private_push_still_includes_row_12(self):
        self.assertIn("--rows 11,12", self.text.split("Private data push")[1])


if __name__ == "__main__":
    unittest.main()

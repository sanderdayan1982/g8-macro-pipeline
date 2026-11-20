"""t283u: el workflow de mantenimiento termina en rojo si el agente falla, aunque haya informe."""
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(yaml, "sin pyyaml")
class MaintFailureStaysRed(unittest.TestCase):
    def setUp(self):
        self.wf = yaml.safe_load((ROOT / ".github/workflows/maintenance_agent.yml").read_text())
        self.src = (ROOT / ".github/workflows/maintenance_agent.yml").read_text()

    def _step(self, job, pred):
        hits = [s for s in self.wf["jobs"][job]["steps"] if pred(s)]
        self.assertEqual(len(hits), 1)
        return hits[0]

    def test_claude_step_has_no_continue_on_error(self):
        cl = self._step("agent", lambda s: str(s.get("uses", "")).startswith("anthropics/claude-code-action"))
        self.assertEqual(cl.get("id"), "claude")
        self.assertNotEqual(cl.get("continue-on-error"), True)
        self.assertNotIn("continue-on-error: true", self.src.split("id: claude", 1)[1].split("Agent Doctor fallback", 1)[0])

    def test_fallback_keeps_report_but_not_success(self):
        fb = self._step("agent", lambda s: s.get("id") == "fallback")
        self.assertEqual(fb.get("if"), "always()")
        run = fb["run"]
        self.assertIn("diagnosis.md", run)
        self.assertIn(".agent/report.md", run)
        self.assertNotIn('|| [ -f .agent/report.md ]', run)
        self.assertIn('if [ "$CLAUDE_RAW" = "success" ]; then', run)
        self.assertIn('echo "outcome=${CLAUDE_RAW:-failure}"', run)

    def test_packaging_runs_after_agent_failure(self):
        pkg = self._step("agent", lambda s: s.get("name") == "Package work")
        self.assertEqual(pkg.get("if"), "always()")
        up = self._step("agent", lambda s: str(s.get("uses", "")).startswith("actions/upload-artifact"))
        self.assertEqual(up.get("if"), "always()")
        self.assertEqual(self.wf["jobs"]["verify"]["if"], "always() && needs.agent.result != 'skipped'")
        self.assertEqual(self.wf["jobs"]["publish"]["if"], "always() && needs.agent.result != 'skipped'")


if __name__ == "__main__":
    unittest.main()

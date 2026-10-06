"""Acta P-6 · compuerta del agente de mantenimiento y forma del workflow.

C1 solo descargadores / registro / decisiones / proxy / actas y tests del agente → AUTO
C2 cualquier fichero de metodología, motor de alertas, huellas, workflows o dashboard → OWNER
C3 sin cambios → NADA
C4 integración con git real: rc 0 / 3 / 4 según los commits
W1 el job del agente es de solo lectura y sin credenciales de git; solo la compuerta escribe
W2 horario: lunes, miércoles y viernes 19:00 UTC (= 20:00 Bata); autenticación con la suscripción (OAuth)
W3 validate.yml y la compuerta usan el mismo scripts/tools/validate_smoke.sh
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts", "tools"))
import agent_gate as G  # noqa: E402

try:
    import yaml
except ImportError:                                                     # pragma: no cover
    yaml = None


class Classify(unittest.TestCase):
    def test_C1_auto(self):
        r = G.classify(["scripts/fetch_aonia.py", "sources/registry.csv", "data/manual/policy_decisions.csv",
                        "docs/_redirects", "docs/actas/ACTA_AGENTE_20261005.md", "tests/test_agent_rba.py",
                        "tests/fixtures/agent/rba/f1.csv", "scripts/g8common/cb_direct.py"])
        self.assertEqual((r["decision"], r["outside"]), ("AUTO", []))

    def test_C2_owner(self):
        for p in ("scripts/acm_g8.py", "scripts/s01b.py", "scripts/dashboard_alerts.py", "scripts/usd_factor.py",
                  "sources/freshness_rules.csv", "tests/test_exclusions.py", ".github/workflows/daily_update.yml",
                  "docs/js/health.js", "scripts/g8common/ingest.py", "data/AUD_POLICY.csv", "CLAUDE.md"):
            r = G.classify(["scripts/fetch_aonia.py", p])
            self.assertEqual((r["decision"], r["outside"]), ("OWNER", [p]), p)

    def test_C3_nothing(self):
        self.assertEqual(G.classify(["", ""])["decision"], "NADA")


@unittest.skipUnless(shutil.which("git"), "sin git")
class GitIntegration(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.git("init", "-q")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        self.write("README.md", "x")
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD").strip()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def git(self, *a):
        return subprocess.run(["git"] + list(a), cwd=self.d, capture_output=True, text=True, check=True).stdout

    def write(self, rel, text):
        p = os.path.join(self.d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(text)

    def gate(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "tools", "agent_gate.py"), "--base", self.base],
                           cwd=self.d, capture_output=True, text=True)
        return r.returncode, json.loads(r.stdout)

    def test_C4_rc(self):
        self.assertEqual(self.gate()[0], 4)
        self.write("scripts/fetch_x.py", "print(1)")
        self.write("tests/test_agent_x.py", "# regression")
        self.write("docs/actas/ACTA_AGENTE_x.md", "Source evidence")
        self.git("add", ".")
        self.git("commit", "-qm", "fix")
        self.assertEqual(self.gate()[0], 0)
        self.write("scripts/s01b.py", "print(2)")
        self.git("add", ".")
        self.git("commit", "-qm", "meth")
        rc, out = self.gate()
        self.assertEqual((rc, out["outside"]), (3, ["scripts/s01b.py"]))


@unittest.skipUnless(yaml, "sin pyyaml")
class Workflow(unittest.TestCase):
    def load(self, name):
        with open(os.path.join(ROOT, ".github", "workflows", name), encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    def test_W1_agent_job_cannot_write(self):
        wf = self.load("maintenance_agent.yml")
        self.assertEqual(wf["permissions"], {"contents": "read"})
        agent = wf["jobs"]["agent"]
        self.assertEqual(agent["permissions"], {"contents": "read", "actions": "read"})
        co = [s for s in agent["steps"] if str(s.get("uses", "")).startswith("actions/checkout")][0]
        self.assertIs(co["with"]["persist-credentials"], False)
        cl = [s for s in agent["steps"] if str(s.get("uses", "")).startswith("anthropics/claude-code-action")][0]
        self.assertIn("Bash(git push:*)", cl["with"]["claude_args"].split("--disallowedTools")[1])
        self.assertNotIn("Bash(git push", cl["with"]["claude_args"].split("--disallowedTools")[0])
        gate = wf["jobs"]["publish"]
        self.assertEqual(gate["permissions"], {"contents": "write", "pull-requests": "write", "actions": "write"})
        self.assertFalse([s for s in gate["steps"] if "claude-code-action" in str(s.get("uses", ""))])
        self.assertNotIn("secrets.CLAUDE_CODE_OAUTH_TOKEN", json.dumps(gate))       # el agente no corre donde se escribe

    def test_W2_schedule_and_auth(self):
        wf = self.load("maintenance_agent.yml")
        on = wf.get("on", wf.get(True))
        self.assertEqual(on["schedule"], [{"cron": "17 17 * * *"}])
        self.assertIn("workflow_dispatch", on)
        cl = [s for s in wf["jobs"]["agent"]["steps"] if str(s.get("uses", "")).startswith("anthropics/claude-code-action")][0]
        self.assertEqual(cl["uses"], "anthropics/claude-code-action@v1")
        self.assertEqual(cl["with"]["claude_code_oauth_token"], "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}")
        self.assertNotIn("anthropic_api_key", cl["with"])

    def test_W3_shared_smoke(self):
        v = json.dumps(self.load("validate.yml"))
        m = json.dumps(self.load("maintenance_agent.yml"))
        self.assertIn("bash scripts/tools/validate_smoke.sh", v)
        self.assertIn("bash scripts/tools/validate_smoke.sh", m)
        self.assertIn("agent_gate.py", m)


if __name__ == "__main__":
    unittest.main()

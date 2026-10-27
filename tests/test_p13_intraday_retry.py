"""Acta P-13 · intradía: un fallo pasajero de ACM/ECB no tumba el job ni deja CAD/AUD sin intentar.

Evidencia 2026-10-27 run 40436083046 (commit 7db9be0, 02:02–02:06Z): acm_g8 FAILED rc=1 en 164,8 s de
--cap 180; EUR (BCE) FAIL_TRANSIENT en el 1.er intento y OK en el 2.º; CAD/AUD DEFERRED sin red
(presupuesto agotado). El 22-oct (run 39705899708) fallaron TAMBIÉN us_bills/floors/real_yields/USD:
el reintento debe ser del grupo entero, no solo de acm_g8, para no ocultar otras fuentes.
"""
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts", "tools"))
import refresh_feeds as RF  # noqa: E402

try:
    import yaml
except ImportError:
    yaml = None

WF = os.path.join(ROOT, ".github", "workflows", "intraday_fetch.yml")


def _caps(mock_call):
    """[(name, cap), ...] de las invocaciones a g8step (excluye --ledger)."""
    out = []
    for args, *_kw in mock_call.call_args_list:
        cmd = args[0]
        if "--ledger" in cmd or "--cap" not in cmd:
            continue
        out.append((cmd[cmd.index("--name") + 1], cmd[cmd.index("--cap") + 1]))
    return out


class RefreshFeedsCap(unittest.TestCase):
    def test_default_step_cap_stays_180(self):
        with patch.object(RF.subprocess, "call", return_value=0) as call:
            with patch.object(sys, "argv", ["refresh_feeds.py", "ASIA"]):
                self.assertEqual(RF.main(), 0)
        caps = _caps(call)
        self.assertTrue(caps)
        self.assertTrue(all(cap == "180" for _, cap in caps), caps)

    def test_cli_cap_overrides_every_step_including_acm_g8(self):
        with patch.object(RF.subprocess, "call", return_value=0) as call:
            with patch.object(sys, "argv", ["refresh_feeds.py", "LATE", "--cap", "360"]):
                self.assertEqual(RF.main(), 0)
        caps = dict(_caps(call))
        self.assertEqual(caps.get("acm_g8"), "360")
        self.assertTrue(caps)
        self.assertTrue(all(cap == "360" for cap in caps.values()), caps)

    def test_nonzero_collector_is_still_a_failure(self):
        with patch.object(RF.subprocess, "call", side_effect=[1, 0, 0, 0, 0, 0, 0, 0]) as call:
            with patch.object(sys, "argv", ["refresh_feeds.py", "LATE", "--cap", "360"]):
                self.assertEqual(RF.main(), 1)
        self.assertGreaterEqual(call.call_count, 2)


@unittest.skipIf(yaml is None, "pyyaml no disponible")
class IntradayWorkflowRetry(unittest.TestCase):
    def setUp(self):
        with open(WF, encoding="utf-8") as fh:
            self.wf = yaml.safe_load(fh)
        self.steps = self.wf["jobs"]["refresh"]["steps"]

    def _by_id(self, step_id):
        return next(s for s in self.steps if s.get("id") == step_id)

    def test_retry_follows_feeds_with_larger_cap_and_same_group(self):
        ids = [s.get("id") for s in self.steps]
        self.assertIn("feeds_retry", ids)
        self.assertEqual(ids.index("feeds_retry"), ids.index("feeds") + 1)
        feeds = self._by_id("feeds")
        retry = self._by_id("feeds_retry")
        self.assertTrue(feeds["continue-on-error"])
        self.assertTrue(retry["continue-on-error"])
        self.assertEqual(retry.get("if"), "steps.feeds.outcome == 'failure'")
        self.assertIn("refresh_feeds.py", retry["run"])
        self.assertIn("--cap 360", retry["run"])
        self.assertNotIn("--cap 360", feeds["run"])
        self.assertIn('echo "group=$group" >> "$GITHUB_OUTPUT"', feeds["run"])
        self.assertIn("steps.feeds.outputs.group", retry["run"])

    def test_retry_runs_before_publish_so_recovered_files_are_committed(self):
        retry_i = next(i for i, s in enumerate(self.steps) if s.get("id") == "feeds_retry")
        pub_i = next(i for i, s in enumerate(self.steps) if str(s.get("name", "")).startswith("Publish"))
        self.assertLess(retry_i, pub_i)

    def test_surface_fails_only_when_retry_did_not_recover(self):
        surface = next(s for s in self.steps if s.get("name") == "Surface collection failure")
        cond = surface["if"]
        self.assertTrue(cond.startswith("always()"))
        self.assertIn("steps.feeds.outcome == 'failure'", cond)
        self.assertIn("steps.feeds_retry.outcome != 'success'", cond)
        self.assertIn("exit 1", surface["run"])


class WorkflowText(unittest.TestCase):
    def test_surface_condition_requires_both_outcomes(self):
        with open(WF, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("if: always() && steps.feeds.outcome == 'failure' && steps.feeds_retry.outcome != 'success'", text)
        self.assertNotIn("if: always() && steps.feeds.outcome == 'failure'\n        run: exit 1", text)


if __name__ == "__main__":
    unittest.main()

"""t192u follow-up: volume-plan must list months without requests, and a
python failure must fail the step instead of looking like an empty window.

Run 43729709853 printed 'FATAL: requests not installed' and then
'volume window has no month' because the collector import exits and the
process substitution dropped that status. Synthetic only. No Databento.
"""
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WF = os.path.join(ROOT, ".github", "workflows", "cme_options_backfill.yml")
SCRIPT = os.path.join(ROOT, "scripts", "cme_options_backfill.py")


def _plan_run():
    with open(WF, encoding="utf-8") as fh:
        wf = yaml.safe_load(fh)
    step = next(s for s in wf["jobs"]["volume-plan"]["steps"]
                if s.get("name") == "List volume months")
    return step["run"]


class ListVolumeMonthsWithoutRequests(unittest.TestCase):
    def test_cli_lists_months_when_requests_cannot_be_imported(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", tmp], check=False))
        with open(os.path.join(tmp, "requests.py"), "w", encoding="utf-8") as fh:
            fh.write("raise ImportError('requests blocked')\n")
        env = os.environ.copy()
        env["PYTHONPATH"] = tmp + os.pathsep + env.get("PYTHONPATH", "")
        env.pop("DATABENTO_API_KEY", None)
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--list-volume-months",
             "--volume-start", "2026-07-01", "--volume-end", "2026-11-15"],
            text=True, capture_output=True, check=False, env=env, cwd=ROOT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.splitlines(), [
            "2026-07 2026-07-01 2026-08-01",
            "2026-08 2026-08-01 2026-09-01",
            "2026-09 2026-09-01 2026-10-01",
            "2026-10 2026-10-01 2026-11-01",
            "2026-11 2026-11-01 2026-11-15",
        ])
        self.assertNotIn("FATAL", proc.stderr)
        self.assertNotIn("requests", proc.stderr)


class PlanStepFailsLoudly(unittest.TestCase):
    def setUp(self):
        self.run = _plan_run()
        self.assertNotIn("done < <(python", self.run)
        self.assertIn("py_rc=$?", self.run)
        self.assertIn("list-volume-months failed with rc=", self.run)
        self.assertIn('exit "$py_rc"', self.run)
        self.assertIn("--list-volume-months", self.run)

    def _shell(self, python_body):
        script = self.run
        self.assertNotIn("${{", script)
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", tmp], check=False))
        bindir = os.path.join(tmp, "bin")
        os.makedirs(bindir)
        log = os.path.join(tmp, "calls")
        path = os.path.join(bindir, "python")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(python_body)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        out = os.path.join(tmp, "github_output")
        env = os.environ.copy()
        env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
        env["CALLLOG"] = log
        env["VOLUME_START"] = "2026-07-01"
        env["VOLUME_END"] = "2026-11-15"
        env["GITHUB_OUTPUT"] = out
        env.pop("DATABENTO_API_KEY", None)
        proc = subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail"],
            input=script, text=True, capture_output=True, check=False, env=env, cwd=tmp)
        calls = []
        if os.path.exists(log):
            with open(log, encoding="utf-8") as fh:
                calls = [line.rstrip("\n") for line in fh]
        output = ""
        if os.path.exists(out):
            with open(out, encoding="utf-8") as fh:
                output = fh.read()
        return proc.returncode, proc.stderr, calls, output

    def test_python_failure_is_the_step_status_and_is_not_an_empty_window(self):
        body = textwrap.dedent("""\
            #!/bin/sh
            echo "FATAL: requests not installed" >&2
            exit 7
            """)
        rc, err, calls, output = self._shell(body)
        self.assertEqual(rc, 7, err)
        self.assertIn("FATAL: requests not installed", err)
        self.assertIn("list-volume-months failed with rc=7", err)
        self.assertNotIn("volume window has no month", err)
        self.assertEqual(output, "")
        self.assertEqual(calls, [])

    def test_python_months_become_the_matrix_json(self):
        body = textwrap.dedent("""\
            #!/bin/sh
            printf '%s\\n' "python $*" >> "$CALLLOG"
            printf '%s\\n' "2026-07 2026-07-01 2026-08-01" "2026-11 2026-11-01 2026-11-15"
            exit 0
            """)
        rc, err, calls, output = self._shell(body)
        self.assertEqual(rc, 0, err)
        self.assertNotIn("volume window has no month", err)
        self.assertNotIn("list-volume-months failed", err)
        self.assertEqual(calls, [
            "python scripts/cme_options_backfill.py --list-volume-months "
            "--volume-start 2026-07-01 --volume-end 2026-11-15",
        ])
        self.assertIn(
            'months=[{"id":"2026-07","start":"2026-07-01","end":"2026-08-01"},'
            '{"id":"2026-11","start":"2026-11-01","end":"2026-11-15"}]',
            output)


if __name__ == "__main__":
    unittest.main()

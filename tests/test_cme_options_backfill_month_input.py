"""Optional workflow_dispatch month on the CME options backfill workflow.

Parses .github/workflows/cme_options_backfill.yml. Does not edit the backfill
script, the collector, or any existing test, and does not call Databento.
PyYAML 1.1 loads the bare key `on` as True; the trigger is read from that key.
"""
import os
import stat
import subprocess
import tempfile
import textwrap
import unittest

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WF = os.path.join(ROOT, ".github", "workflows", "cme_options_backfill.yml")
DESCRIPTION = "YYYY-MM: process only this chunk. Empty = all 24 chunks as before."
MONTH_RE = "^[0-9]{4}-[0-9]{2}$"
STEP_NAME = "Backfill one calendar month at a time"

# The empty-input loop, dedented as PyYAML returns the run block. Same bytes as
# the loop before the month input (orden Sander 14-nov-2026 16:36 WAT).
EMPTY_LOOP = "\n".join([
    "for month in $(python scripts/cme_options_backfill.py --list-months); do",
    "  elapsed=$(( $(date +%s) - start_s ))",
    '  if [ "$elapsed" -ge "$budget_s" ]; then',
    '    python scripts/cme_options_backfill.py --record-stop "time budget: not starting new chunks after 320 min (job timeout is 360). Relaunch resumes."',
    '    commit_month "time-budget"',
    '    echo "time budget reached after ${elapsed}s; relaunch resumes"',
    "    break",
    "  fi",
    "  set +e",
    '  python scripts/cme_options_backfill.py --month "$month" $mode',
    "  rc=$?",
    "  set -e",
    "  # Push this chunk (or the cap report, which has no new session) before",
    "  # looking at rc. Months that already finished were pushed on earlier",
    "  # iterations and are not rewritten.",
    '  commit_month "$month"',
    '  if [ "$rc" -ne 0 ]; then',
    '    echo "chunk ${month} stopped with rc=${rc}; earlier chunks stay pushed; job fails so the stop is visible"',
    '    exit "$rc"',
    "  fi",
    "done",
])


def _load():
    with open(WF, encoding="utf-8") as fh:
        wf = yaml.safe_load(fh)
    trigger = wf[True] if True in wf else wf["on"]
    step = next(s for s in wf["jobs"]["backfill"]["steps"] if s.get("name") == STEP_NAME)
    return wf, trigger, step


def _branches(run):
    marker = 'if [ -z "${BACKFILL_MONTH}" ]; then\n'
    tail = run[run.index(marker) + len(marker):]
    else_at = tail.index("\nelse\n")
    empty = tail[:else_at]
    rest = tail[else_at + len("\nelse\n"):]
    if not rest.endswith("\nfi\n"):
        raise AssertionError("month branch is not closed by an unindented fi")
    return empty, rest[:-len("\nfi\n")]


class MonthInput(unittest.TestCase):
    def setUp(self):
        self.wf, self.trigger, self.step = _load()
        self.run = self.step["run"]
        self.empty, self.single = _branches(self.run)
        self.inputs = self.trigger["workflow_dispatch"]["inputs"]

    def test_month_input_is_optional_string_default_empty(self):
        self.assertEqual(list(self.trigger), ["workflow_dispatch"])
        self.assertNotIn("schedule", self.trigger)
        month = self.inputs["month"]
        self.assertEqual(month["description"], DESCRIPTION)
        self.assertEqual(month["type"], "string")
        self.assertEqual(month["default"], "")
        self.assertIs(month["required"], False)
        dry = self.inputs["dry_run"]
        self.assertEqual(dry["type"], "boolean")
        self.assertIs(dry["default"], True)

    def test_month_reaches_the_shell_only_through_the_env(self):
        self.assertEqual(self.step["env"]["BACKFILL_MONTH"], "${{ inputs.month }}")
        self.assertEqual(self.step["env"]["DATABENTO_API_KEY"], "${{ secrets.DATABENTO_API_KEY }}")
        self.assertNotIn("inputs.month", self.run)
        self.assertNotIn("${{ inputs.month }}", self.run)
        leftover = self.run.replace("${{ inputs.dry_run }}", "")
        self.assertNotIn("${{", leftover)

    def test_empty_path_is_the_list_months_loop(self):
        self.assertEqual(self.empty, EMPTY_LOOP)
        self.assertIn("for month in $(python scripts/cme_options_backfill.py --list-months); do", self.empty)
        self.assertNotIn(MONTH_RE, self.empty)
        self.assertLess(self.empty.index('commit_month "$month"'), self.empty.index('exit "$rc"'))

    def test_set_path_validates_then_runs_only_that_month(self):
        single = self.single
        self.assertIn(MONTH_RE, single)
        self.assertNotIn("for month", single)
        self.assertNotIn("--record-stop", single)
        self.assertEqual(single.count("python scripts/cme_options_backfill.py --list-months"), 1)
        self.assertEqual(single.count('python scripts/cme_options_backfill.py --month "$month" $mode'), 1)
        regex_at = single.index(MONTH_RE)
        list_at = single.index("python scripts/cme_options_backfill.py --list-months")
        run_at = single.index('python scripts/cme_options_backfill.py --month "$month" $mode')
        commit_at = single.index('commit_month "$month"')
        exit_at = single.index('exit "$rc"')
        self.assertLess(regex_at, list_at)
        self.assertLess(list_at, run_at)
        self.assertLess(run_at, commit_at)
        self.assertLess(commit_at, exit_at)
        self.assertEqual(single[:run_at].count("exit 1"), 2)
        self.assertNotIn("exit 0", single)

    def test_shell_parses_after_actions_substitutes_dry_run(self):
        script = self.run.replace("${{ inputs.dry_run }}", "true")
        self.assertNotIn("${{", script)
        proc = subprocess.run(
            ["bash", "--noprofile", "--norc", "-n"],
            input=script, text=True, capture_output=True, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def _run(self, month, py_rc="0"):
        script = self.run.replace("${{ inputs.dry_run }}", "true")
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", tmp], check=False))
        bindir = os.path.join(tmp, "bin")
        os.makedirs(bindir)
        log = os.path.join(tmp, "calls")
        python_stub = textwrap.dedent("""\
            #!/bin/sh
            printf '%s\\n' "python $*" >> "$CALLLOG"
            if [ "$2" = "--list-months" ]; then
              printf '%s\\n' 2024-06 2024-07 2026-05
              exit 0
            fi
            if [ "$2" = "--month" ]; then
              exit "$PY_RC"
            fi
            if [ "$2" = "--record-stop" ]; then
              exit 0
            fi
            echo "unexpected python $*" >&2
            exit 9
            """)
        git_stub = textwrap.dedent("""\
            #!/bin/sh
            printf '%s\\n' "git $*" >> "$CALLLOG"
            exit 0
            """)
        for name, body in (("python", python_stub), ("git", git_stub)):
            path = os.path.join(bindir, name)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(body)
            os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        env = os.environ.copy()
        env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
        env["CALLLOG"] = log
        env["PY_RC"] = str(py_rc)
        env["BACKFILL_MONTH"] = month
        env.pop("DATABENTO_API_KEY", None)
        proc = subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail"],
            input=script, text=True, capture_output=True, check=False,
            env=env, cwd=tmp,
        )
        calls = []
        if os.path.exists(log):
            with open(log, encoding="utf-8") as fh:
                calls = [line.rstrip("\n") for line in fh]
        return proc.returncode, calls, proc.stderr

    def test_empty_month_loops_list_months_and_does_not_validate(self):
        rc, calls, err = self._run("")
        self.assertEqual(rc, 0, err)
        py = [c for c in calls if c.startswith("python ")]
        self.assertEqual(py, [
            "python scripts/cme_options_backfill.py --list-months",
            "python scripts/cme_options_backfill.py --month 2024-06 --dry-run",
            "python scripts/cme_options_backfill.py --month 2024-07 --dry-run",
            "python scripts/cme_options_backfill.py --month 2026-05 --dry-run",
        ])
        self.assertNotIn("git commit", "\n".join(calls))

    def test_set_month_rejects_before_any_backfill_call(self):
        for bad in ("2024-6", "202406", "2024-06-01", "2024-06 ", "nope", "2024-06;touch /tmp/x"):
            rc, calls, err = self._run(bad)
            self.assertEqual(rc, 1, (bad, err, calls))
            self.assertEqual([c for c in calls if c.startswith("python ")], [], bad)
        rc, calls, err = self._run("2024-13")
        self.assertEqual(rc, 1, err)
        self.assertEqual(
            [c for c in calls if c.startswith("python ")],
            ["python scripts/cme_options_backfill.py --list-months"],
        )
        rc, calls, err = self._run("2026-06")
        self.assertEqual(rc, 1, err)
        self.assertEqual(
            [c for c in calls if c.startswith("python ")],
            ["python scripts/cme_options_backfill.py --list-months"],
        )

    def test_set_month_runs_only_that_chunk_and_keeps_rc(self):
        rc, calls, err = self._run("2024-07")
        self.assertEqual(rc, 0, err)
        py = [c for c in calls if c.startswith("python ")]
        self.assertEqual(py, [
            "python scripts/cme_options_backfill.py --list-months",
            "python scripts/cme_options_backfill.py --month 2024-07 --dry-run",
        ])
        rc, calls, err = self._run("2024-06", py_rc="3")
        self.assertEqual(rc, 3, err)
        self.assertEqual(
            [c for c in calls if c.startswith("python ")],
            [
                "python scripts/cme_options_backfill.py --list-months",
                "python scripts/cme_options_backfill.py --month 2024-06 --dry-run",
            ],
        )
        month_at = next(i for i, c in enumerate(calls) if c.startswith("python ") and "--month" in c)
        self.assertTrue(any(c.startswith("git ") for c in calls[month_at + 1:]))


if __name__ == "__main__":
    unittest.main()

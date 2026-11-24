"""Pieza 1 de la migración de daily_update al VPS. Pruebas sin red."""
import contextlib
import inspect
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vps"))
import daily_update_vps as D


class DailyUpdateVps(unittest.TestCase):
    def test_timer_schedule(self):
        timer = (ROOT / "vps/g8-daily-update.timer").read_text()
        self.assertEqual(re.findall(r"^OnCalendar=(.*)$", timer, re.M), ["Mon..Fri 21:30:00"])
        self.assertIn("Unit=g8-daily-update.service", timer)

    def test_own_clone_lock_and_protecthome(self):
        service = (ROOT / "vps/g8-daily-update.service").read_text()
        self.assertEqual(D.WORKDIR, "/srv/g8/prod/work/daily-update")
        self.assertEqual(D.LOCK, "/srv/g8/prod/state/locks/daily-update.lock")
        for setting in (f"G8_DAILY_WORKDIR={D.WORKDIR}", f"G8_DAILY_LOCK={D.LOCK}",
                        "ProtectHome=yes", "TimeoutStartSec=2100", "OnFailure=g8-alarm@%n.service"):
            self.assertIn(setting, service)

        events = []

        @contextlib.contextmanager
        def lock(path):
            events.append(("enter", path))
            yield
            events.append(("exit", path))

        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            state = Path(tmp, "state.json")
            with patch.dict(os.environ, self.credentials(), clear=True), \
                 patch.object(D.job_flock, "exclusive", lock), patch.object(D.C, "wait_exclusive", lock), \
                 patch.object(D, "run", return_value={}) as run:
                self.assertEqual(D.main(["--state", str(state)]), 0)
                run.assert_called_once_with(D.WORKDIR)
        self.assertEqual(events, [
            ("enter", D.LOCK), ("enter", D.C.SHARED_ALERTS_LOCK),
            ("exit", D.C.SHARED_ALERTS_LOCK), ("exit", D.LOCK),
        ])

    def test_publishable_allowlist_excludes_licensed_paths(self):
        for path in (
            "data/OPTIONS_SURFACE.json", "data/futures/canonical/6E.csv",
            "data/futures/other.csv", "data/options/shadow_history.json",
            "data/options/fxvol_history.csv", "data/options/canonical/payload.csv",
            "data/CME_OPTIONS_C1_shadow.json", "data/CME_FXVOL_shadow.json",
            "data/alerts/private/brief.json", "scripts/argos_rates.py",
        ):
            self.assertFalse(D.publishable(path), path)
        for path in (
            "data/options/state.json", "data/options/canonical/.gitkeep",
            "data/alerts/brief.json", "data/public/CME_OPTIONS_SURFACE_public.json",
            "data/_ingest/latest/actions__job_daily.json", "data/S01B_shadow_a3.json",
        ):
            self.assertTrue(D.publishable(path), path)

    @staticmethod
    def credentials():
        return {name: "test-" + name for name in D.SECRET_NAMES}

    def test_missing_secrets_fail_by_name(self):
        for name in ("G8_FULL", "G8_DATA_READ_CI", "G8_DATA_WRITE",
                     "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                credentials = self.credentials()
                credentials.pop(name)
                state = Path(tmp, "state.json")
                with patch.dict(os.environ, credentials, clear=True), \
                     contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
                     patch.object(D, "run") as run:
                    self.assertEqual(D.main(["--state", str(state)]), 1)
                    run.assert_not_called()
                self.assertEqual(json.loads(state.read_text())["error"], "secretos ausentes: " + name)

    def exercise_run(self, fail_pull=False):
        commands = []
        environments = []
        timeouts = []
        clones = []
        deadline = None

        def step(cmd, workdir, env, timeout, secrets=(), log=None):
            nonlocal deadline
            commands.append(tuple(cmd))
            timeouts.append(timeout)
            environments.append((tuple(cmd), env))
            deadline = env["G8_JOB_DEADLINE_EPOCH"]
            if D.PRIVATE_TOOL in cmd and "pull" in cmd:
                clones.append(Path(cmd[cmd.index("pull") + 1]))
                if fail_pull:
                    return 1
            if "scripts/ingest_watch.py" in cmd:
                Path(cmd[cmd.index("--result") + 1]).write_text('{"delivery": "NOTHING"}')
            return 0

        def publish(workdir, py, env, job, args):
            commands.append((py, D.ALARM_TOOL, "publish", job, *args))
            timeouts.append(120)  # Límite del paso compuesto de verify.
            return 0, True

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, self.credentials(), clear=True), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
             patch.object(D, "step", side_effect=step), patch.object(D.C, "start_clean", return_value=[]), \
             patch.object(D.P, "prepare_git_home", return_value=(tmp, str(Path(tmp, "gitconfig")))), \
             patch.object(D.C, "unstage_all"), patch.object(D.C, "restore_paths"), \
             patch.object(D, "public_changes", return_value=()), patch.object(D.P, "commit_and_push", return_value={}), \
             patch.object(D, "private_push"), patch.object(D.C, "publish_record", side_effect=publish), \
             patch.object(D, "remove_licensed"):
            result = D.run(tmp)
        self.assertEqual(clones[0].name, "g8data")
        self.assertFalse(clones[0].parent.exists())
        self.assertTrue(deadline.isdecimal())
        for cmd, env in environments:
            allowed = set()
            name = cmd[cmd.index("--name") + 1] if "--name" in cmd else None
            if D.PRIVATE_TOOL in cmd and "pull" in cmd:
                allowed = {"G8_DATA_TOKEN"}
                self.assertEqual(env["G8_DATA_TOKEN"], self.credentials()["G8_DATA_READ_CI"])
            elif name in ("floors", "acm_g8", "real_yields"):
                allowed = {"FRED_API_KEY"}
            elif name in ("dashboard_alerts", "ingest_watch"):
                allowed = {"TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"}
            self.assertEqual(env.keys() & set(D.SECRET_NAMES), allowed)
            self.assertEqual(Path(env["G8_STEP_LOG"]).parent, clones[0].parent)
        return commands, timeouts, result

    def test_python_script_order_matches_workflow(self):
        workflow = (ROOT / ".github/workflows/daily_update.yml").read_text()
        steps = re.split(r"^      - name: ", workflow, flags=re.M)[1:]
        expected = []
        pattern = r"\bpython3? (scripts/[\w/]+\.py)"
        for block in steps:
            if block.startswith(("Alert on failure", "Private data push")):
                # OnFailure y los helpers de git sustituyen estos dos comandos.
                continue
            run = re.split(r"^        run: *", block, maxsplit=1, flags=re.M)
            if len(run) == 2:
                for script in re.findall(pattern, run[1]):
                    timeout = int(re.search(r"timeout-minutes: (\d+)", run[0])[1]) * 60
                    expected.append((script, timeout))
        commands, timeouts, result = self.exercise_run()
        actual = [(name, timeout) for cmd, timeout in zip(commands, timeouts)
                  for name in cmd if re.fullmatch(r"scripts/[\w/]+\.py", name)]
        self.assertEqual(actual, expected)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["delivery"], "NOTHING")
        self.assertNotIn("--commit", [arg for cmd in commands for arg in cmd])
        self.assertNotIn('"--commit"', inspect.getsource(D.run) + inspect.getsource(D.run_steps))

    def test_failed_pull_skips_scrapers_but_runs_always_steps(self):
        commands, _, result = self.exercise_run(fail_pull=True)
        self.assertEqual(result["errors"], ["private pull: rc=1"])
        self.assertEqual(set(result["scrapers"].values()), {"skipped"})
        self.assertNotIn("scripts/acm_g8.py", [arg for cmd in commands for arg in cmd])
        self.assertIn("scripts/tools/finalize_session.py", [arg for cmd in commands for arg in cmd])
        self.assertIn("verify", [arg for cmd in commands for arg in cmd])

    def test_cleanup_removes_licensed_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True, capture_output=True)
            remove = (
                "data/OPTIONS_SURFACE.json", "data/futures/canonical/6E.csv",
                "data/CME_FXVOL_shadow.json", "data/options/canonical/imported.csv",
                "data/options/raw/ignored.csv",
            )
            keep = ("data/options/state.json", "data/options/canonical/.gitkeep")
            for path in remove + keep:
                file = Path(tmp, path)
                file.parent.mkdir(parents=True, exist_ok=True)
                file.touch()
            Path(tmp, ".gitignore").write_text("data/options/raw/\n")
            subprocess.run(["git", "add", "--", *keep], cwd=tmp, check=True, capture_output=True)
            D.remove_licensed(tmp)
            for path in remove:
                self.assertFalse(Path(tmp, path).exists(), path)
            for path in keep:
                self.assertTrue(Path(tmp, path).is_file(), path)

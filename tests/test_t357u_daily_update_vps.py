"""Migración de daily_update al VPS. Pruebas sin red."""
import contextlib
import hashlib
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
import compare_daily_dryrun as Compare


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

    @staticmethod
    def git(cwd, *args):
        env = {"PATH": os.defpath, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        return subprocess.check_output(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args],
            cwd=cwd, env=env, stderr=subprocess.PIPE,
        ).decode().strip()

    @staticmethod
    def put(repo, path, text):
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def init_main(self, repo):
        repo.mkdir()
        self.git(repo, "init", "-q", "-b", "main")
        self.put(repo, "data/base.txt", "baseline")
        self.git(repo, "add", "data")
        self.git(repo, "commit", "-qm", "base")
        return self.git(repo, "rev-parse", "HEAD")

    def test_dry_run_refuses_production_and_requires_explicit_workdir(self):
        with patch.dict(os.environ, {"G8_DAILY_WORKDIR": D.WORKDIR}, clear=True), \
             patch.object(D, "dryrun_git") as git, contextlib.redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit) as missing:
                D.main(["--dry-run"])
            self.assertEqual(missing.exception.code, 2)
            self.assertEqual(D.main(["--dry-run", "--workdir", D.WORKDIR]), 1)
            self.assertIn("producción", err.getvalue())
            git.assert_not_called()

    def test_dry_run_manifest_secrets_and_cleanup(self):
        for with_unused_secrets in (False, True):
            with self.subTest(with_unused_secrets=with_unused_secrets), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                remote, scratch = root / "remote", root / "scratch"
                self.init_main(remote)
                self.git(root, "clone", "-q", str(remote), str(scratch))
                # The fetch must pick up a commit that was absent at clone time.
                self.put(remote, "data/base.txt", "new baseline")
                self.git(remote, "commit", "-qam", "new base")
                base = self.git(remote, "rev-parse", "HEAD")
                manifest = root / "manifest.json"
                read_token = self.credentials()["G8_DATA_READ_CI"]

                def step(cmd, workdir, env, timeout, secrets=(), log=None):
                    self.assertEqual(env["G8_NO_SEND"], "1")
                    self.assertFalse(any(key.startswith("TELEGRAM_") for key in env))
                    self.assertFalse({"G8_FULL", "G8_DATA_WRITE", "G8_DATA_READ_CI"} & env.keys())
                    if D.PRIVATE_TOOL in cmd and "pull" in cmd:
                        self.assertEqual(env["G8_DATA_TOKEN"], read_token)
                    else:
                        self.assertNotIn("G8_DATA_TOKEN", env)
                    if "scripts/ingest_watch.py" in cmd:
                        Path(cmd[cmd.index("--result") + 1]).write_text('{"delivery":"DRY"}')
                        for path in ("data/base.txt", "data/options/state.json", "data/OPTIONS_SURFACE.json",
                                     "data/futures/canonical/6E.csv", "data/options/shadow_history.json",
                                     "data/alerts/private/brief.json"):
                            self.put(workdir, path, "fixture content")
                        self.put(workdir, "data/alerts/brief.json", '{"walls":["private"],"public":true}')
                        self.git(workdir, "add", "--", "data/base.txt")
                    return 0

                env = dict(self.credentials(), TELEGRAM_EXTRA="unused") if with_unused_secrets else {
                    "G8_DATA_READ_CI": read_token
                }
                with patch.dict(os.environ, env, clear=True), patch.object(D, "step", side_effect=step), \
                     patch.object(D.C, "start_clean") as start_clean, \
                     patch.object(D.P, "commit_and_push") as push, patch.object(D, "private_push") as private, \
                     patch.object(D.C, "publish_record", return_value=(0, True)), \
                     contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(D.main(["--dry-run", "--workdir", str(scratch),
                                             "--manifest", str(manifest)]), 0)
                    start_clean.assert_not_called()
                    push.assert_not_called()
                    private.assert_not_called()
                    self.assertEqual(out.getvalue().count("skipped: dry-run"), 3)
                    self.assertNotIn("fixture content", out.getvalue())

                doc = json.loads(manifest.read_text())
                self.assertEqual(doc["schema"], "G8_DAILY_DRYRUN/1")
                self.assertEqual(doc["base_sha"], base)
                self.assertTrue(doc["started_utc"].endswith("+00:00"))
                self.assertTrue(any(row["name"] == "ingest_watch" and row["rc"] == 0
                                    and row["seconds"] >= 0 for row in doc["steps"]))
                fixture_hash = hashlib.sha256(b"fixture content").hexdigest()
                brief_hash = hashlib.sha256(b'{\n "public": true\n}').hexdigest()
                self.assertEqual(doc["files"], [
                    {"path": "data/alerts/brief.json", "sha256": brief_hash},
                    {"path": "data/base.txt", "sha256": fixture_hash},
                    {"path": "data/options/state.json", "sha256": fixture_hash},
                ])
                self.assertEqual(self.git(scratch, "status", "--porcelain", "--ignored"), "")
                self.assertEqual(self.git(scratch, "rev-parse", "HEAD"), base)
                self.assertEqual(self.git(scratch, "rev-parse", "--abbrev-ref", "HEAD"), "HEAD")

    def test_compare_counts_and_timing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            base = self.init_main(repo)
            for path in ("data/same", "data/different", "data/actions only", "data/_ingest/clock",
                         "data/ledger.json", "data/options/secret", "data/alerts/private/brief.json"):
                self.put(repo, path, "Actions content")
            self.git(repo, "add", "data")
            self.git(repo, "commit", "-qm", "Actions")
            sha = self.git(repo, "rev-parse", "HEAD")
            manifest = root / "manifest.json"
            doc = {"schema": D.DRYRUN_SCHEMA, "base_sha": base, "started_utc": "", "steps": []}

            def entry(path, content=b"VPS content"):
                return {"path": path, "sha256": hashlib.sha256(content).hexdigest()}

            doc["files"] = [
                entry("data/same", b"Actions content"), entry("data/different"), entry("data/vps only"),
                entry("data/_ingest/clock"), entry("data/health.json"),
            ]
            manifest.write_text(json.dumps(doc))
            args = ["--manifest", str(manifest), "--repo", str(repo), "--actions-sha", sha]
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(Compare.main(args), 1)
            self.assertEqual(out.getvalue().splitlines(), [
                "SAME 1", "  data/same", "DIFF 1", "  data/different", "ONLY_VPS 1", "  data/vps only",
                "ONLY_ACTIONS 1", "  data/actions only", "TIMING 3", "  data/_ingest/clock",
                "  data/health.json", "  data/ledger.json",
            ])
            doc["files"] = [entry(path, b"Actions content")
                            for path in ("data/same", "data/different", "data/actions only")]
            manifest.write_text(json.dumps(doc))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(Compare.main(args), 0)

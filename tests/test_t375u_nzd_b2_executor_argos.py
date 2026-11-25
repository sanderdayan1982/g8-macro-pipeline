"""t375u (orden Sander 25-nov): nzd-b2 sin choques con los registros de Actions y una sola publicación de argos.

1. g8common.ingest lee el ejecutor de G8_EXECUTOR_ID (vps-prod en el VPS); sin variable, "actions".
2. fetch_ff_only comprueba el resultado del stash pop: un conflicto ya no deja el índice sin fusionar
   (el «needs merge» que tumbó nzd-b2 el 24-nov) y un índice heredado sin fusionar se cura al empezar.
3. El VPS ya no publica argos_rates.json; el workflow de Actions publica una vez por turno.
4. El workflow ya no lleva «mac» en el nombre ni en el mensaje del commit.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "scripts", "tools"))
sys.path.insert(0, os.path.join(ROOT, "vps"))

from g8common import ingest as ING  # noqa: E402
import vps_git_push as P  # noqa: E402
import nzd_b2_daily as N  # noqa: E402
import turn_debounce as TD  # noqa: E402

WF = os.path.join(ROOT, ".github", "workflows", "mac_nzd_chf_shadow.yml")
TONA_LATEST = "data/_ingest/latest/actions__fetch_tona.json"


def _env():
    e = dict(os.environ)
    e.update(GIT_AUTHOR_NAME="t", GIT_COMMITTER_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_EMAIL="t@t")
    return e


def _git(cwd, *args, check=True):
    r = subprocess.run(["git", *args], cwd=cwd, env=_env(), capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError("git %s: %s" % (args, r.stderr))
    return r


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class ExecutorFromEnv(unittest.TestCase):
    def test_default_is_actions(self):
        self.assertEqual(ING.executor_id({}), "actions")
        self.assertEqual(ING.executor_id({"G8_EXECUTOR_ID": "  "}), "actions")

    def test_vps_prod_from_env(self):
        self.assertEqual(ING.executor_id({"G8_EXECUTOR_ID": "vps-prod"}), "vps-prod")

    def test_invalid_id_is_an_error(self):
        with self.assertRaises(ValueError):
            ING.executor_id({"G8_EXECUTOR_ID": "../actions"})

    def _finish(self, env_value):
        root = tempfile.mkdtemp()
        env = {k: v for k, v in os.environ.items() if k != "G8_EXECUTOR_ID"}
        if env_value is not None:
            env["G8_EXECUTOR_ID"] = env_value
        with mock.patch.dict(os.environ, env, clear=True):
            ctx = ING.Ingest("fetch_tona", root=root, env={})
            ctx.finish(0)
        return root, ctx

    def test_vps_writes_its_own_ledger_not_the_actions_one(self):
        root, ctx = self._finish("vps-prod")
        self.assertEqual(ctx.executor, "vps-prod")
        self.assertIn("_vps-prod_", ctx.run_id)
        latest = Path(root, "data", "_ingest", "latest")
        self.assertTrue((latest / "vps-prod__fetch_tona.json").is_file())
        self.assertFalse((latest / "actions__fetch_tona.json").exists())
        self.assertTrue(Path(root, "data", "_ingest", "runs", "vps-prod", "fetch_tona").is_dir())
        self.assertFalse(Path(root, "data", "_ingest", "runs", "actions").exists())

    def test_actions_unchanged_without_env(self):
        root, ctx = self._finish(None)
        self.assertEqual(ctx.executor, "actions")
        self.assertTrue(Path(root, "data", "_ingest", "latest", "actions__fetch_tona.json").is_file())

    def test_vps_unit_sets_vps_prod(self):
        unit = Path(ROOT, "vps", "g8-nzd-b2-daily.service").read_text(encoding="utf-8")
        self.assertIn("Environment=G8_EXECUTOR_ID=vps-prod", unit)

    def test_vps_ledger_is_owned_by_nzd_b2(self):
        # runs/vps-prod/ ya es del clon nzd-b2: el sobrante no aborta commit_and_push
        self.assertTrue(P.path_owned("data/_ingest/runs/vps-prod/fetch_tona/2026-11.jsonl", P.NZD_B2_OWNED))


class FetchFfOnlyStashPop(unittest.TestCase):
    """Reproduce el 24-nov: sobrante del VPS y Actions tocan el mismo puntero de TONA."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.remote = os.path.join(self.tmp, "remote.git")
        self.work = os.path.join(self.tmp, "work")
        self.other = os.path.join(self.tmp, "other")
        subprocess.check_call(["git", "init", "-q", "--bare", "--initial-branch=main", self.remote])
        subprocess.check_call(["git", "clone", "-q", self.remote, self.work], stderr=subprocess.DEVNULL)
        _git(self.work, "checkout", "-q", "-b", "main")
        _write(os.path.join(self.work, TONA_LATEST), '{"run": "a"}\n')
        _write(os.path.join(self.work, "data", "TONA.csv"), "DATE,CLOSE\n20261120,1\n")
        _git(self.work, "add", "data")
        _git(self.work, "commit", "-qm", "init")
        _git(self.work, "push", "-q", "origin", "HEAD:main")
        subprocess.check_call(["git", "clone", "-q", self.remote, self.other], stderr=subprocess.DEVNULL)
        self.alerts = os.path.join(self.tmp, "ALERTAS.log")
        self._env = mock.patch.dict(os.environ, {"G8_ALERTAS_LOG": self.alerts, "G8_VPS_JOB": "nzd-b2"})
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def _remote_moves(self, text):
        _write(os.path.join(self.other, TONA_LATEST), text)
        _git(self.other, "commit", "-qam", "actions tona")
        _git(self.other, "push", "-q", "origin", "HEAD:main")

    def _stashes(self):
        return [l for l in _git(self.work, "stash", "list").stdout.splitlines() if l.strip()]

    def test_pop_conflict_leaves_clone_mergeable_and_keeps_stash(self):
        self._remote_moves('{"run": "actions"}\n')
        _write(os.path.join(self.work, TONA_LATEST), '{"run": "vps"}\n')
        P.fetch_ff_only(self.work, "x", allowed=P.NZD_B2_OWNED)
        self.assertEqual(P._unmerged_paths(self.work), [])
        self.assertEqual(_git(self.work, "rev-parse", "HEAD").stdout, _git(self.work, "rev-parse", "origin/main").stdout)
        self.assertEqual(Path(self.work, TONA_LATEST).read_text(), '{"run": "actions"}\n')
        self.assertEqual(len(self._stashes()), 1, "el stash del pop fallido se conserva como evidencia")
        self.assertIn("INCIDENCIA DIRTY_RESET nzd-b2 " + TONA_LATEST, Path(self.alerts).read_text())
        # el turno siguiente ya no muere con «needs merge»
        self._remote_moves('{"run": "actions-2"}\n')
        _write(os.path.join(self.work, TONA_LATEST), '{"run": "vps-2"}\n')
        P.fetch_ff_only(self.work, "x", allowed=P.NZD_B2_OWNED)
        self.assertEqual(P._unmerged_paths(self.work), [])
        self.assertEqual(Path(self.work, TONA_LATEST).read_text(), '{"run": "actions-2"}\n')

    def test_inherited_unmerged_index_is_healed_before_stash_push(self):
        # estado del 24-nov: un pop anterior dejó el puntero sin fusionar
        _write(os.path.join(self.work, TONA_LATEST), '{"run": "vps"}\n')
        _git(self.work, "stash", "push", "-q", "-m", "vps-ff-autostash")
        self._remote_moves('{"run": "actions"}\n')
        _git(self.work, "fetch", "-q", "origin", "main")
        _git(self.work, "merge", "-q", "--ff-only", "origin/main")
        self.assertNotEqual(_git(self.work, "stash", "pop", "-q", check=False).returncode, 0)
        self.assertEqual(P._unmerged_paths(self.work), [TONA_LATEST])
        P.fetch_ff_only(self.work, "x", allowed=P.NZD_B2_OWNED)
        self.assertEqual(P._unmerged_paths(self.work), [])
        self.assertIn("INCIDENCIA DIRTY_RESET nzd-b2 " + TONA_LATEST, Path(self.alerts).read_text())
        self.assertEqual(Path(self.work, TONA_LATEST).read_text(), '{"run": "actions"}\n')

    def test_clean_pop_keeps_same_job_leftover(self):
        _write(os.path.join(self.work, "data", "TONA.csv"), "DATE,CLOSE\n20261120,1\nDIRTY\n")
        self._remote_moves('{"run": "actions"}\n')
        P.fetch_ff_only(self.work, "x", allowed=P.NZD_B2_OWNED)
        self.assertIn("DIRTY", Path(self.work, "data", "TONA.csv").read_text())
        self.assertEqual(self._stashes(), [])

    def test_pop_failure_without_conflict_is_loud(self):
        r = mock.Mock(returncode=1, stdout="", stderr="boom")
        with mock.patch.object(P.subprocess, "run", return_value=r), \
                mock.patch.object(P, "_unmerged_paths", return_value=[]):
            with self.assertRaises(P.PushError) as cm:
                P._stash_pop_checked(self.work, {})
        self.assertIn("stash pop rc=1", str(cm.exception))


class VpsDoesNotPublishArgos(unittest.TestCase):
    def test_source_has_no_argos_refresh(self):
        src = Path(ROOT, "vps", "nzd_b2_daily.py").read_text(encoding="utf-8")
        self.assertNotIn("import argos_rates", src)
        self.assertNotIn("refresh_if_inputs_changed", src)
        self.assertNotIn('"data/argos_rates.json"', src)

    def test_drop_mac_latest_never_commits_argos(self):
        calls = []
        with tempfile.TemporaryDirectory() as work:
            _write(os.path.join(work, "data", "argos_rates.json"), "{}\n")
            with mock.patch.object(P, "commit_and_push", side_effect=lambda *a, **k: calls.append(a)):
                out = N.drop_mac_latest(work, "x")
            self.assertEqual(calls, [])
            self.assertIsNone(out["push"])
            _write(os.path.join(work, "data", "_ingest", "latest", "mac-primary__nzchf-tona.json"), "{}\n")
            with mock.patch.object(P, "commit_and_push", side_effect=lambda *a, **k: calls.append(a) or {}):
                N.drop_mac_latest(work, "x")
        self.assertEqual(len(calls), 1)
        allowed, msg = calls[0][1], calls[0][2]
        self.assertNotIn("data/argos_rates.json", allowed)
        self.assertTrue(msg.startswith("[vps-push]"))
        self.assertIn("[skip netlify]", msg)


class ShadowWorkflowOncePerTurn(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = Path(WF).read_text(encoding="utf-8")
        cls.doc = yaml.safe_load(cls.text)

    def test_no_mac_in_name_or_commit(self):
        self.assertNotIn("mac", self.doc["name"].lower())
        self.assertNotIn("data: mac ", self.text)
        self.assertIn("git commit -m 'data: nzd chf tona shadow [skip netlify]'", self.text)

    def test_publish_steps_gated_by_turn_debounce(self):
        steps = self.doc["jobs"]["refresh"]["steps"]
        names = [s.get("name") for s in steps]
        i_turn = names.index("Una publicación por turno")
        self.assertEqual(steps[i_turn]["id"], "turn")
        self.assertIn("turn_debounce.py", steps[i_turn]["run"])
        self.assertIn("${{ github.sha }}", steps[i_turn]["run"])
        for name in ("Shadow CHF nowcast", "Publish argos rates"):
            i = names.index(name)
            self.assertGreater(i, i_turn)
            self.assertEqual(steps[i]["if"], "steps.turn.outputs.skip != 'true'")


class TurnDebounce(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        _git(self.repo, "init", "-q", "--initial-branch=main")
        _write(os.path.join(self.repo, "data", "NZD_BOND_2Y.csv"), "a\n")
        _git(self.repo, "add", "data")
        _git(self.repo, "commit", "-qm", "nz")
        self.first = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self.paths = TD.push_paths(WF)

    def _commit(self, rel):
        _write(os.path.join(self.repo, rel), "x%s\n" % os.urandom(2).hex())
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", rel)

    def test_later_family_push_skips_this_run(self):
        self._commit("data/CHF_SARON.csv")
        hits = TD.newer_commits_touching(self.first, "HEAD", self.paths, cwd=self.repo)
        self.assertEqual(len(hits), 1)

    def test_unrelated_later_commit_does_not_skip(self):
        self._commit("data/_ingest/latest/vps-prod__nzchf-tona.json")
        self._commit("data/argos_rates.json")
        self.assertEqual(TD.newer_commits_touching(self.first, "HEAD", self.paths, cwd=self.repo), [])

    def test_last_run_of_turn_publishes(self):
        self.assertEqual(TD.newer_commits_touching(self.first, "HEAD", self.paths, cwd=self.repo), [])

    def test_paths_come_from_the_workflow(self):
        self.assertIn("data/TONA.csv", self.paths)
        self.assertIn("data/NZD_BOND_*.csv", self.paths)


if __name__ == "__main__":
    unittest.main()

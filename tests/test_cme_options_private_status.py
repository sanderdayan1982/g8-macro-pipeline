"""t299u (opción B) — el estado de cme_options va al repo PRIVADO y ingest_watch avisa si falla o si no llega.

cme_options.yml sigue con `permissions: contents: read` en el público. Su paso final escribe
g8-macro-data/data/_status/cme_options.json con G8_DATA_WRITE; ingest_watch lo lee del clon privado de
daily_update y usd_factor (G8_PRIVATE_CLONE). Sin números CME: solo rc, resultados de paso, hora, sesión, motivo.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts", "tools"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import cme_run_status as S  # noqa: E402
import ingest_watch  # noqa: E402

try:
    import yaml
except ImportError:
    yaml = None

NOW = datetime(2026, 11, 21, 18, 0, tzinfo=timezone.utc)
ALL_OK = ["summary=success", "dashboard_alerts=success", "private_push=success", "private_verify=success"]
KEYS = {"workflow", "run_id", "run_attempt", "event", "written_utc", "session", "collector_rc", "steps",
        "status", "reason"}


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main"]
                          + list(args), cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True).stdout


class Build(unittest.TestCase):
    def test_ok_and_only_allowed_fields(self):
        d = S.build("2026-11-21", "0", ALL_OK, env={"GITHUB_RUN_ID": "5"}, now=NOW)
        self.assertEqual(set(d), KEYS)
        self.assertEqual(d["status"], "OK")
        self.assertEqual(d["reason"], "")
        self.assertEqual(d["written_utc"], "2026-11-21T18:00:00Z")
        self.assertEqual(set(d["steps"]), set(S.STEPS))
        for v in d["steps"].values():
            self.assertIn(v, S.OUTCOMES + ("missing",))

    def test_failures_give_reason(self):
        d = S.build("2026-11-21", "3", ["summary=skipped", "dashboard_alerts=failure", "private_push=success",
                                        "private_verify=failure"], env={}, now=NOW)
        self.assertEqual(d["status"], "FAILED")
        self.assertEqual(d["reason"], "colector rc=3; dashboard_alerts failure; private_verify failure")

    def test_missing_rc_and_step_fail(self):
        d = S.build("2026-11-21", "", ["summary=success"], env={}, now=NOW)
        self.assertEqual(d["status"], "FAILED")
        self.assertIn("colector sin rc", d["reason"])
        self.assertIn("private_push missing", d["reason"])

    def test_rejects_unknown_step_and_bad_session(self):
        with self.assertRaises(ValueError):
            S.build("2026-11-21", "0", ["oi=123"], env={})
        with self.assertRaises(ValueError):
            S.build("21-11-2026", "0", ALL_OK, env={})


class WriteToPrivateClone(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.origin = os.path.join(self.tmp, "data.git")
        git(self.tmp, "init", "-q", "--bare", "-b", "main", self.origin)
        seed = os.path.join(self.tmp, "seed")
        git(self.tmp, "clone", "-q", self.origin, seed)
        with open(os.path.join(seed, "README"), "w") as fh:
            fh.write("x\n")
        git(seed, "add", "-A")
        git(seed, "commit", "-q", "-m", "seed")
        git(seed, "push", "-q", "origin", "HEAD:main")
        self.clone = os.path.join(self.tmp, "g8data")
        git(self.tmp, "clone", "-q", self.origin, self.clone)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_commits_and_pushes_only_the_status_file(self):
        doc = S.build("2026-11-21", "0", ALL_OK, env={}, now=NOW)
        with mock.patch.dict(os.environ, {"G8_DATA_TOKEN": ""}):
            rc = S.write(self.clone, doc, repo=self.origin, pause=0)
        self.assertEqual(rc, 0)
        out = os.path.join(self.tmp, "check")
        git(self.tmp, "clone", "-q", self.origin, out)
        self.assertEqual(git(out, "log", "-1", "--format=%s"), "data: cme_options status 2026-11-21 [skip netlify]\n")
        self.assertEqual(git(out, "show", "--name-only", "--format=", "HEAD").split(), [S.REL])
        with open(os.path.join(out, S.REL)) as fh:
            self.assertEqual(json.load(fh)["status"], "OK")


class IngestWatchReadsPrivateStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _status(self, **over):
        doc = S.build("2026-11-21", "0", ALL_OK, env={}, now=NOW - timedelta(hours=2))
        doc.update(over)
        p = os.path.join(self.tmp, "data", "_status", "cme_options.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            json.dump(doc, fh)

    def _check(self, clone=None, prev=None):
        alerts, info = {}, []
        ingest_watch.check_private_status(self.tmp if clone is None else clone, NOW, alerts, info, prev_active=prev)
        return alerts

    def test_fresh_ok_is_quiet(self):
        self._status()
        self.assertEqual(self._check(), {})

    def test_failed_run_alerts_with_reason(self):
        self._status(status="FAILED", reason="private_verify failure")
        a = self._check()
        self.assertEqual(list(a), ["cme_options:fail"])
        self.assertIn("private_verify failure", a["cme_options:fail"])

    def test_stale_alerts_after_36h(self):
        self._status(written_utc=(NOW - timedelta(hours=37)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.assertIn("cme_options:silence", self._check())
        self._status(written_utc=(NOW - timedelta(hours=35)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.assertEqual(self._check(), {})
        self.assertEqual(ingest_watch.CME_STATUS_MAX_AGE_H, 36.0)

    def test_missing_file_and_missing_clone_alert(self):
        self.assertIn("cme_options:missing", self._check())
        self.assertIn("cme_options:no_clone", self._check(clone=os.path.join(self.tmp, "nope")))

    def test_without_clone_keeps_open_alert_unchanged(self):
        prev = {"cme_options:fail": {"text": "cme_options: run con fallo — x", "sent_ts": 1},
                "actions:x": {"text": "otro", "sent_ts": 1}}
        self.assertEqual(self._check(clone="", prev=prev), {"cme_options:fail": "cme_options: run con fallo — x"})
        self.assertEqual(self._check(clone="", prev={}), {})

    def test_main_uses_g8_private_clone(self):
        self._status(status="FAILED", reason="colector rc=1")
        root = os.path.join(self.tmp, "root")
        os.makedirs(os.path.join(root, "sources"))
        shutil.copy(os.path.join(ROOT, "sources", "actions_jobs.csv"), os.path.join(root, "sources"))
        res = os.path.join(self.tmp, "res.json")
        with mock.patch.dict(os.environ, {"G8_PRIVATE_CLONE": self.tmp, "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}):
            ingest_watch.main(["--dry-run", "--result", res], now=lambda: NOW.timestamp(), root=root)
        with open(res) as fh:
            self.assertIn("cme_options:fail", json.load(fh)["alerts"])


@unittest.skipIf(yaml is None, "pyyaml no disponible")
class Workflows(unittest.TestCase):
    def _wf(self, name):
        with open(os.path.join(ROOT, ".github", "workflows", name), encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    def test_cme_options_stays_read_only_and_writes_status(self):
        wf = self._wf("cme_options.yml")
        self.assertEqual(wf["permissions"], {"contents": "read"})
        steps = wf["jobs"]["collect"]["steps"]
        names = [s.get("name") for s in steps]
        i = names.index("Estado del run al repo privado (alarma ingest_watch)")
        st = steps[i]
        self.assertEqual(st["if"], "always()")
        self.assertTrue(st["continue-on-error"])
        self.assertEqual(st["env"]["G8_DATA_TOKEN"], "${{ secrets.G8_DATA_WRITE }}")
        self.assertEqual(st["env"]["COLLECTOR_RC"], "${{ steps.collector.outputs.rc }}")
        ids = {s.get("id"): s.get("name") for s in steps}
        for env, sid in (("OUT_SUMMARY", "summary"), ("OUT_ALERTS", "alerts"), ("OUT_PUSH", "push"),
                         ("OUT_VERIFY", "verify")):
            self.assertEqual(st["env"][env], "${{ steps.%s.outcome }}" % sid)
            self.assertLess(names.index(ids[sid]), i)
        self.assertIn("scripts/tools/cme_run_status.py write", st["run"])
        self.assertNotIn("git push", st["run"])
        self.assertLess(i, names.index("Collector result"))

    def test_ingest_watch_gets_the_private_clone(self):
        for name, job in (("daily_update.yml", "fetch-data"), ("usd_factor.yml", "factor")):
            steps = self._wf(name)["jobs"][job]["steps"]
            names = [s.get("name") for s in steps]
            iw = steps[names.index("Ingest watch (latido del Mac + credenciales)")]
            self.assertEqual(iw["env"]["G8_PRIVATE_CLONE"], "${{ runner.temp }}/g8data")
            self.assertLess(names.index("Private data pull"), names.index(iw["name"]))


if __name__ == "__main__":
    unittest.main()

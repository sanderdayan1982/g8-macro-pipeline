"""t297u (b) — «Private data verify» con diferencias (differ>0) o fallo sale como incidencia que ve ingest_watch.

El paso va después del commit del job y sigue con continue-on-error. step_alarm.py registra su rc y sube solo
data/_ingest/latest/actions__job_verify_<wf>.json desde un worktree aparte, con [skip netlify].
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts", "tools"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import step_alarm  # noqa: E402
import ingest_watch  # noqa: E402

try:
    import yaml
except ImportError:
    yaml = None

WFS = {"daily_update": "daily", "freshness_snapshot": "freshness", "g8_port_run": "g8_port",
       "metals_update": "metals", "usd_factor": "usd_factor"}


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main"]
                          + list(args), cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True).stdout


class Repo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.origin = os.path.join(self.tmp, "origin.git")
        self.clone = os.path.join(self.tmp, "clone")
        git(self.tmp, "init", "-q", "--bare", "-b", "main", self.origin)
        seed = os.path.join(self.tmp, "seed")
        git(self.tmp, "clone", "-q", self.origin, seed)
        os.makedirs(os.path.join(seed, "data", "alerts"))
        with open(os.path.join(seed, "data", "alerts", "brief.json"), "w") as fh:
            fh.write("{}\n")
        git(seed, "add", "-A")
        git(seed, "commit", "-q", "-m", "seed")
        git(seed, "push", "-q", "origin", "HEAD:main")
        git(self.tmp, "clone", "-q", self.origin, self.clone)
        self.seed = seed
        self.env = {"G8_STEP_LOG": os.path.join(self.tmp, "steps.jsonl"), "GITHUB_RUN_ID": "9",
                    "STEP_ALARM_NO_SLEEP": "1"}

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _origin_file(self, job):
        try:
            return json.loads(git(self.clone, "show", "origin/main:data/_ingest/latest/actions__job_%s.json" % job))
        except subprocess.CalledProcessError:
            return None

    def _publish(self, job, name):
        step_alarm.publish(job, [name], commit=True, env=self.env, root=self.clone)
        git(self.clone, "fetch", "-q", "origin")

    def test_differ_is_failed_and_reaches_ingest_watch(self):
        step_alarm.record("private_verify_x", "1", "private_data verify rc=1", env=self.env)
        self._publish("verify_x", "private_verify_x")
        led = self._origin_file("verify_x")
        self.assertEqual(led["failed"], [{"name": "private_verify_x", "rc": 1, "status": "FAILED"}])
        msg = git(self.clone, "log", "-1", "--format=%s", "origin/main")
        self.assertIn("[skip netlify]", msg)
        self.assertEqual(git(self.clone, "show", "--name-only", "--format=", "origin/main").split(),
                         ["data/_ingest/latest/actions__job_verify_x.json"])
        # ingest_watch sobre lo subido
        view = os.path.join(self.tmp, "view")
        git(self.tmp, "clone", "-q", self.origin, view)
        os.makedirs(os.path.join(view, "sources"))
        shutil.copy(os.path.join(ROOT, "sources", "actions_jobs.csv"), os.path.join(view, "sources"))
        alerts = {}
        ingest_watch.check_actions(view, datetime.now(timezone.utc), alerts, {})
        self.assertIn("actions:job:verify_x:step:private_verify_x", alerts)

    def test_ok_has_no_incident_and_unchanged_does_not_commit(self):
        step_alarm.record("private_verify_x", "0", env=self.env)
        self._publish("verify_x", "private_verify_x")
        self.assertEqual(self._origin_file("verify_x")["failed"], [])
        head = git(self.clone, "rev-parse", "origin/main")
        open(self.env["G8_STEP_LOG"], "w").close()
        step_alarm.record("private_verify_x", "0", env=self.env)
        self._publish("verify_x", "private_verify_x")
        self.assertEqual(git(self.clone, "rev-parse", "origin/main"), head)

    def test_missing_record_is_failed(self):
        self._publish("verify_x", "private_verify_x")
        self.assertEqual(self._origin_file("verify_x")["failed"],
                         [{"name": "private_verify_x", "rc": None, "status": "FAILED"}])

    def test_dirty_work_tree_is_not_touched_or_pushed(self):
        p = os.path.join(self.clone, "data", "alerts", "brief.json")
        with open(p, "w") as fh:
            fh.write('{"walls": "privado"}\n')
        step_alarm.record("private_verify_x", "1", env=self.env)
        self._publish("verify_x", "private_verify_x")
        with open(p) as fh:
            self.assertIn("walls", fh.read())
        self.assertEqual(git(self.clone, "show", "origin/main:data/alerts/brief.json"), "{}\n")
        self.assertFalse(os.path.exists(os.path.join(self.clone, "data", "_ingest")))
        self.assertEqual(git(self.clone, "worktree", "list").count("\n"), 1)

    def test_push_race_regenerates_on_new_main(self):
        # otro proceso sube entre medias: el primer push se rechaza y se regenera
        real = step_alarm._write
        state = {"n": 0}

        def racing(job, only, env, root):
            state["n"] += 1
            if state["n"] == 1:
                with open(os.path.join(self.seed, "otro.txt"), "w") as fh:
                    fh.write("x\n")
                git(self.seed, "add", "-A")
                git(self.seed, "commit", "-q", "-m", "otro")
                git(self.seed, "push", "-q", "origin", "HEAD:main")
            return real(job, only, env, root)

        step_alarm._write = racing
        try:
            step_alarm.record("private_verify_x", "1", env=self.env)
            self._publish("verify_x", "private_verify_x")
        finally:
            step_alarm._write = real
        self.assertEqual(state["n"], 2)
        self.assertIsNotNone(self._origin_file("verify_x"))
        self.assertIn("otro.txt", git(self.clone, "ls-tree", "--name-only", "origin/main").split())


class Record(unittest.TestCase):
    def test_rc_mapping(self):
        tmp = tempfile.mkdtemp()
        try:
            env = {"RUNNER_TEMP": tmp}
            for rc, st in (("0", "OK"), ("1", "FAILED"), ("", "FAILED"), ("124", "FAILED")):
                step_alarm.record("s", rc, env=env)
                with open(os.path.join(tmp, "g8_alarm_steps.jsonl")) as fh:
                    self.assertEqual(json.loads(fh.read().splitlines()[-1])["status"], st)
        finally:
            shutil.rmtree(tmp)

    def test_job_rows(self):
        jobs = ingest_watch.load_jobs(ROOT)
        for x in WFS.values():
            self.assertEqual(jobs["job_verify_" + x]["status"], "ACTIVE")


@unittest.skipIf(yaml is None, "pyyaml no disponible")
class Workflows(unittest.TestCase):
    def test_verify_records_and_keeps_continue_on_error(self):
        for wf, x in WFS.items():
            with self.subTest(wf=wf):
                with open(os.path.join(ROOT, ".github", "workflows", wf + ".yml"), encoding="utf-8") as fh:
                    doc = yaml.safe_load(fh)
                steps = [s for j in doc["jobs"].values() for s in j["steps"] if s.get("name") == "Private data verify"]
                self.assertEqual(len(steps), 1)
                st = steps[0]
                self.assertTrue(st["continue-on-error"])
                self.assertEqual(st["if"], "always()")
                run = st["run"]
                rec = run.index('step_alarm.py record private_verify_%s "$rc"' % x)
                pub = run.index("step_alarm.py publish verify_%s --only private_verify_%s --commit" % (x, x))
                self.assertLess(run.index("private_data.py verify"), rec)
                self.assertLess(rec, pub)
                self.assertLess(pub, run.rindex('exit "$rc"'))


if __name__ == "__main__":
    unittest.main()

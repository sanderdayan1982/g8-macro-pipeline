"""t343u: worktree/flock por job; DIRTY_RESET es incidencia; desfase de código >1 h."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "vps"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import job_flock  # noqa: E402
import vps_git_push as P  # noqa: E402
import watch_vps_jobs as W  # noqa: E402
import health_monitor as H  # noqa: E402


def _git(cwd, *args):
    e = dict(os.environ)
    e.update(GIT_AUTHOR_NAME="t", GIT_COMMITTER_NAME="t",
             GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_EMAIL="t@t")
    subprocess.check_call(["git", *args], cwd=cwd, env=e, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL)


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


NOW = datetime(2026, 11, 23, 15, 0, tzinfo=timezone.utc)
NOW_UTC = "2026-11-23T15:00:00Z"


class DirtyResetAlert(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log = os.path.join(self.tmp, "ALERTAS.log")
        self.work = os.path.join(self.tmp, "work")
        self.remote = os.path.join(self.tmp, "remote.git")
        subprocess.check_call(["git", "init", "-q", "--bare", "--initial-branch=main", self.remote],
                              stdout=subprocess.DEVNULL)
        subprocess.check_call(["git", "clone", "-q", self.remote, self.work], stdout=subprocess.DEVNULL)
        _git(self.work, "checkout", "-q", "-b", "main")
        _write(os.path.join(self.work, "data", "CHF_SPOT_1Y_DAILY.csv"),
               "Date,Value,Source,PublishingDate\n2026-11-19,0.4,SNB1A,2026-11-20T08:31:00\n")
        _write(os.path.join(self.work, "data", "TONA.csv"), "DATE,OPEN,HIGH,LOW,CLOSE,VOLUME\n20261118,1,1,1,1,0\n")
        _write(os.path.join(self.work, "README"), "x\n")
        _git(self.work, "add", "README", "data/CHF_SPOT_1Y_DAILY.csv", "data/TONA.csv")
        _git(self.work, "commit", "-qm", "init")
        _git(self.work, "push", "-q", "origin", "HEAD:main")
        self._env = {"G8_ALERTAS_LOG": self.log, "G8_VPS_JOB": "snb1a"}

    def _add_remote_commit(self):
        other = os.path.join(self.tmp, "other")
        subprocess.check_call(["git", "clone", "-q", "--branch", "main", self.remote, other],
                              stdout=subprocess.DEVNULL)
        _write(os.path.join(other, "data", "CHF_SPOT_1Y_DAILY.csv"),
               "Date,Value,Source,PublishingDate\n2026-11-19,0.4,SNB1A,2026-11-20T08:31:00\n"
               "2026-11-20,0.5,SNB1A,2026-11-23T08:31:00\n")
        _git(other, "add", "data/CHF_SPOT_1Y_DAILY.csv")
        _git(other, "commit", "-qm", "remote snb")
        _git(other, "push", "-q", "origin", "HEAD:main")

    def test_dirty_reset_writes_incidencia_to_alertas(self):
        self._add_remote_commit()
        _write(os.path.join(self.work, "data", "TONA.csv"), "DATE,OPEN,HIGH,LOW,CLOSE,VOLUME\ntruncated\n")
        with _Env(self._env):
            P.fetch_ff_only(self.work, token="x", allowed=P.SNB_ALLOWED)
        text = Path(self.log).read_text(encoding="utf-8")
        self.assertIn("INCIDENCIA DIRTY_RESET snb1a", text)
        self.assertIn("data/TONA.csv", text)
        self.assertNotIn("/srv/", text)
        tona = Path(self.work, "data", "TONA.csv").read_text()
        self.assertIn("20261118", tona)
        self.assertNotIn("truncated", tona)

    def test_no_alert_when_no_foreign_reset(self):
        self._add_remote_commit()
        with _Env(self._env):
            P.fetch_ff_only(self.work, token="x", allowed=P.SNB_ALLOWED)
        self.assertFalse(os.path.exists(self.log))

    def test_safe_paths_strip_absolute_and_ip(self):
        out = P._safe_reset_paths([
            "/srv/g8/prod/work/pipeline/data/TONA.csv",
            "data/CHF_NOM_10Y.csv",
            "host 10.1.2.3/data/x.csv",
        ])
        self.assertEqual(out[0], "data/TONA.csv")
        self.assertEqual(out[1], "data/CHF_NOM_10Y.csv")
        self.assertNotIn("10.1.2.3", " ".join(out))
        self.assertIn("<ip>", out[2])


class _Env:
    def __init__(self, mapping):
        self.mapping = mapping
        self.prev = {}

    def __enter__(self):
        for k, v in self.mapping.items():
            self.prev[k] = os.environ.get(k)
            os.environ[k] = v
        return self

    def __exit__(self, *exc):
        for k, old in self.prev.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old


class FlockPerJob(unittest.TestCase):
    def test_second_holder_fails_immediately(self):
        tmp = tempfile.mkdtemp()
        lock = os.path.join(tmp, "snb.lock")
        held = threading.Event()
        done = threading.Event()

        def holder():
            with job_flock.exclusive(lock):
                held.set()
                done.wait(2)

        t = threading.Thread(target=holder)
        t.start()
        self.assertTrue(held.wait(2))
        with self.assertRaises(job_flock.LockError) as cm:
            with job_flock.exclusive(lock):
                pass
        self.assertIn("snb.lock", str(cm.exception))
        self.assertNotIn("/srv/", str(cm.exception))
        done.set()
        t.join(2)

    def test_sequential_ok(self):
        tmp = tempfile.mkdtemp()
        lock = os.path.join(tmp, "nzd-b2.lock")
        with job_flock.exclusive(lock):
            pass
        with job_flock.exclusive(lock):
            pass


class ParseAndCountIncidencia(unittest.TestCase):
    def test_parse_dirty_reset_line(self):
        text = (
            "2026-11-23T08:29:36Z 🔴 INCIDENCIA DIRTY_RESET snb1a data/CHF_NOM_10Y.csv data/_ingest/latest/actions__fetch_tona.json\n"
            "2026-11-23T07:45:10Z 🔴 VPS: la unidad g8-snb-daily.service FALLÓ (result=exit-code rc=1)\n"
        )
        lines = W.parse_fallo_lines(text, NOW_UTC, keep_sec=48 * 3600)
        units = {x["unit"] for x in lines}
        self.assertIn("dirty_reset:snb1a", units)
        self.assertIn("g8-snb-daily.service", units)
        dirty = next(x for x in lines if x["unit"].startswith("dirty_reset:"))
        self.assertIn("DIRTY_RESET", dirty["text"])
        self.assertIn("data/CHF_NOM_10Y.csv", dirty["text"])

    def test_ingest_watch_counts_dirty_reset_since_cut(self):
        status = W.build_status(
            [],
            [{"utc": "2026-11-23T08:29:36Z", "unit": "dirty_reset:snb1a",
              "text": "INCIDENCIA DIRTY_RESET snb1a data/CHF_NOM_10Y.csv"},
             {"utc": "2026-11-22T19:45:10Z", "unit": "dirty_reset:nzd-b2",
              "text": "INCIDENCIA DIRTY_RESET nzd-b2 data/x.csv"}],
            "2026-11-23T15:00:00Z",
        )
        items = H.vps_unit_items(status, NOW, last_cut_utc="2026-11-23T08:00:00Z", require=True)
        self.assertEqual([x["file"] for x in items], ["vps/dirty_reset:snb1a"])
        self.assertEqual(items[0]["state"], "DIRTY_RESET")
        self.assertTrue(H.is_counted_issue(items[0]))

    def test_code_lag_over_one_hour_is_incidencia(self):
        status = W.build_status(
            [], [], "2026-11-23T15:00:00Z",
            code_lag=[{"name": "snb_daily.py", "first_diff_utc": "2026-11-23T13:00:00Z", "age_sec": 7200}],
        )
        items = H.vps_unit_items(status, NOW, require=True)
        self.assertEqual(items[0]["state"], "CODE_LAG")
        self.assertEqual(items[0]["file"], "vps/code_lag:snb_daily.py")
        self.assertTrue(H.is_counted_issue(items[0]))
        self.assertNotIn("/srv/", json.dumps(items))

    def test_code_lag_under_one_hour_not_listed(self):
        # evaluate_code_lag only emits incidencias after the hour
        lag, state = W.evaluate_code_lag(
            {"snb_daily.py": "aaa"},
            {"snb_daily.py": "bbb"},
            {},
            "2026-11-23T13:30:00Z",
            max_sec=3600,
        )
        self.assertEqual(lag, [])
        self.assertEqual(state["snb_daily.py"]["first_diff_utc"], "2026-11-23T13:30:00Z")
        lag2, state2 = W.evaluate_code_lag(
            {"snb_daily.py": "aaa"},
            {"snb_daily.py": "bbb"},
            state,
            "2026-11-23T15:00:00Z",
            max_sec=3600,
        )
        self.assertEqual(len(lag2), 1)
        self.assertEqual(lag2[0]["name"], "snb_daily.py")
        self.assertEqual(lag2[0]["first_diff_utc"], "2026-11-23T13:30:00Z")
        self.assertGreater(lag2[0]["age_sec"], 3600)
        # match clears state
        lag3, state3 = W.evaluate_code_lag(
            {"snb_daily.py": "bbb"},
            {"snb_daily.py": "bbb"},
            state2,
            "2026-11-23T15:05:00Z",
            max_sec=3600,
        )
        self.assertEqual(lag3, [])
        self.assertEqual(state3, {})

    def test_status_json_has_no_paths_or_ip(self):
        lag, _ = W.evaluate_code_lag(
            {"dispatch_prod.py": "x"},
            {"dispatch_prod.py": "y"},
            {"dispatch_prod.py": {"bin_sha": "x", "main_sha": "y",
                                  "first_diff_utc": "2026-11-23T12:00:00Z"}},
            NOW_UTC,
            max_sec=3600,
        )
        status = W.build_status([], [], NOW_UTC, code_lag=lag)
        blob = json.dumps(status)
        self.assertNotIn("/srv/", blob)
        self.assertNotRegex(blob, r"(?:\d{1,3}\.){3}\d{1,3}")
        self.assertEqual(status["code_lag"][0]["name"], "dispatch_prod.py")

    def test_should_push_when_code_lag_changes(self):
        a = W.build_status([], [], "2026-11-23T14:00:00Z")
        b = W.build_status(
            [], [], "2026-11-23T15:00:00Z",
            code_lag=[{"name": "snb_daily.py", "first_diff_utc": "2026-11-23T13:00:00Z", "age_sec": 7200}],
        )
        now = datetime(2026, 11, 23, 15, 0, tzinfo=timezone.utc)
        do, why = W.should_push(b, a, now, heartbeat_sec=3 * 3600)
        self.assertTrue(do)
        self.assertEqual(why, "changed")


if __name__ == "__main__":
    unittest.main()

"""t342u: dirty leftover no bloquea ff-only; ingest_watch cuenta timers en fallo; errores reales no se callan."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "vps"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import vps_git_push as P  # noqa: E402
import watch_vps_jobs as W  # noqa: E402
import health_monitor as H  # noqa: E402


def _git(cwd, *args, env=None):
    e = dict(os.environ)
    e.update(GIT_AUTHOR_NAME="t", GIT_COMMITTER_NAME="t",
             GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_EMAIL="t@t")
    if env:
        e.update(env)
    subprocess.check_call(["git", *args], cwd=cwd, env=e, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL)


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


NOW = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)
NOW_UTC = "2026-11-23T09:00:00Z"


class FetchFfOnlyDirty(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.work = os.path.join(self.tmp, "work")
        self.remote = os.path.join(self.tmp, "remote.git")
        subprocess.check_call(["git", "init", "-q", "--bare", "--initial-branch=main", self.remote], stdout=subprocess.DEVNULL)
        subprocess.check_call(["git", "clone", "-q", self.remote, self.work], stdout=subprocess.DEVNULL)
        _git(self.work, "checkout", "-q", "-b", "main")
        _write(os.path.join(self.work, "data", "CHF_SPOT_1Y_DAILY.csv"),
               "Date,Value,Source,PublishingDate\n2026-11-19,0.4,SNB1A,2026-11-20T08:31:00\n")
        _write(os.path.join(self.work, "data", "CHF_SPOT_2Y_DAILY.csv"),
               "Date,Value,Source,PublishingDate\n2026-11-19,0.3,SNB1A,2026-11-20T08:31:00\n")
        _write(os.path.join(self.work, "data", "TONA.csv"), "DATE,OPEN,HIGH,LOW,CLOSE,VOLUME\n20261118,1,1,1,1,0\n")
        _write(os.path.join(self.work, "README"), "x\n")
        _git(self.work, "add", "README", "data/CHF_SPOT_1Y_DAILY.csv", "data/CHF_SPOT_2Y_DAILY.csv", "data/TONA.csv")
        _git(self.work, "commit", "-qm", "init")
        _git(self.work, "push", "-q", "origin", "HEAD:main")

    def _add_remote_commit(self):
        other = os.path.join(self.tmp, "other")
        subprocess.check_call(["git", "clone", "-q", "--branch", "main", self.remote, other], stdout=subprocess.DEVNULL)
        _write(os.path.join(other, "data", "CHF_SPOT_1Y_DAILY.csv"),
               "Date,Value,Source,PublishingDate\n2026-11-19,0.4,SNB1A,2026-11-20T08:31:00\n2026-11-20,0.5,SNB1A,2026-11-23T08:31:00\n")
        _git(other, "add", "data/CHF_SPOT_1Y_DAILY.csv")
        _git(other, "commit", "-qm", "remote snb")
        _git(other, "push", "-q", "origin", "HEAD:main")

    def test_foreign_dirty_is_reset_and_merge_proceeds(self):
        self._add_remote_commit()
        _write(os.path.join(self.work, "data", "TONA.csv"), "DATE,OPEN,HIGH,LOW,CLOSE,VOLUME\ntruncated\n")
        P.fetch_ff_only(self.work, token="x", allowed=P.SNB_ALLOWED)
        tona = Path(self.work, "data", "TONA.csv").read_text()
        self.assertIn("20261118", tona)
        self.assertNotIn("truncated", tona)
        daily = Path(self.work, "data", "CHF_SPOT_1Y_DAILY.csv").read_text()
        self.assertIn("2026-11-20", daily)

    def test_owned_dirty_is_kept(self):
        self._add_remote_commit()
        _write(os.path.join(self.work, "data", "CHF_SPOT_2Y_DAILY.csv"),
               "Date,Value,Source,PublishingDate\nLOCAL,0.9,SNB1A,2026-11-23\n")
        P.fetch_ff_only(self.work, token="x", allowed=P.SNB_ALLOWED)
        daily = Path(self.work, "data", "CHF_SPOT_2Y_DAILY.csv").read_text()
        self.assertIn("LOCAL", daily)
        # the unrelated remote update still arrived
        self.assertIn("2026-11-20", Path(self.work, "data", "CHF_SPOT_1Y_DAILY.csv").read_text())

    def test_diverged_history_still_raises(self):
        # non-ff: local unique commit vs remote unique commit
        _write(os.path.join(self.work, "README"), "local\n")
        _git(self.work, "add", "README")
        _git(self.work, "commit", "-qm", "local only")
        self._add_remote_commit()
        with self.assertRaises(P.PushError) as cm:
            P.fetch_ff_only(self.work, token="x", allowed=P.SNB_ALLOWED)
        self.assertIn("merge", str(cm.exception).lower())

    def test_fetch_failure_still_raises(self):
        with self.assertRaises(P.PushError):
            P.fetch_ff_only(self.work, token="x", remote="does-not-exist")

    def test_untracked_outside_allowlist_does_not_abort_commit(self):
        cache = os.path.join(self.work, "data", "_cache", "x.bin")
        _write(cache, "bin")
        # file in allowlist staged? make an allowed change
        _write(os.path.join(self.work, "data", "CHF_SPOT_1Y_DAILY.csv"),
               "Date,Value,Source,PublishingDate\n2026-11-19,0.4,SNB1A,2026-11-20T08:31:00\n2026-11-20,0.5,SNB1A,2026-11-23\n")
        out = P.commit_and_push(self.work, P.SNB_ALLOWED,
                                "[vps-push] snb1a last=2026-11-20 [skip netlify]", token="x")
        self.assertTrue(out["changed"])
        self.assertTrue(os.path.exists(cache))


class VpsUnitIssues(unittest.TestCase):
    def test_missing_is_incidencia_when_required(self):
        items = H.vps_unit_items(None, NOW, require=True)
        self.assertEqual(items[0]["status"], "MISSING")
        self.assertTrue(H.is_counted_issue(items[0]))
        self.assertEqual(H.vps_unit_items(None, NOW, require=False), [])

    def test_stale_is_incidencia(self):
        status = {"schema": "G8_VPS_UNITS/1", "generated_utc": "2026-11-23T01:00:00Z",
                  "failed_units": [], "fallo_lines": []}
        items = H.vps_unit_items(status, NOW, require=True)
        self.assertEqual(items[0]["state"], "STALE")
        self.assertTrue(H.is_counted_issue(items[0]))

    def test_fresh_clean_is_not_incidencia(self):
        status = {"schema": "G8_VPS_UNITS/1", "generated_utc": "2026-11-23T08:50:00Z",
                  "failed_units": [], "fallo_lines": []}
        self.assertEqual(H.vps_unit_items(status, NOW, require=True), [])

    def test_failed_unit_is_incidencia_even_if_file_would_be_current(self):
        status = {"schema": "G8_VPS_UNITS/1", "generated_utc": "2026-11-23T08:50:00Z",
                  "failed_units": [{"unit": "g8-snb-daily.service", "result": "exit-code", "rc": 1}],
                  "fallo_lines": [{"utc": "2026-11-23T07:45:10Z", "unit": "g8-snb-daily.service",
                                   "text": "la unidad g8-snb-daily.service FALLÓ (result=exit-code rc=1)"}]}
        items = H.vps_unit_items(status, NOW, last_cut_utc="2026-11-23T07:00:00Z", require=True)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["file"], "vps/g8-snb-daily.service")
        self.assertEqual(items[0]["status"], "ERROR")
        self.assertTrue(H.is_counted_issue(items[0]))

    def test_fallo_since_last_cut_counts_recovered_unit(self):
        status = {"schema": "G8_VPS_UNITS/1", "generated_utc": "2026-11-23T08:50:00Z",
                  "failed_units": [],
                  "fallo_lines": [
                      {"utc": "2026-11-22T19:45:10Z", "unit": "g8-snb-daily.service", "text": "FALLÓ domingo"},
                      {"utc": "2026-11-23T08:10:00Z", "unit": "g8-nzd-b2-daily.service", "text": "FALLÓ después del corte"},
                  ]}
        items = H.vps_unit_items(status, NOW, last_cut_utc="2026-11-23T07:51:35Z", require=True)
        self.assertEqual([x["file"] for x in items], ["vps/g8-nzd-b2-daily.service"])
        self.assertEqual(items[0]["state"], "FALLO_LOG")

    def test_ilegible_is_incidencia_when_required(self):
        items = H.vps_unit_items("bad", NOW, require=True)
        self.assertEqual(items[0]["status"], "ERROR")
        self.assertIn("ilegible", items[0]["reason"])


class WatchStatusHelpers(unittest.TestCase):
    def test_list_failed_units_from_runner(self):
        def runner(cmd):
            unit = cmd[-1]
            if cmd[1] == "is-failed":
                if unit == "g8-snb-daily.service":
                    return 1, "failed\n", ""
                return 0, "inactive\n", ""
            if "ActiveState" in cmd:
                return 0, "dead\n", ""
            if "ExecMainStatus" in "".join(cmd) or "Result" in "".join(cmd):
                return 0, "Result=exit-code\nExecMainStatus=1\n", ""
            return 0, "", ""
        failed = W.list_failed_units(runner=runner)
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["unit"], "g8-snb-daily.service")
        self.assertEqual(failed[0]["rc"], 1)
        self.assertEqual(failed[0]["result"], "exit-code")

    def test_parse_fallo_lines_keeps_recent_and_strips_ip(self):
        text = (
            "2026-11-21T16:08:35Z 🔴 VPS shadow: la unidad g8-alarmtest-fasec.service FALLÓ (result=exit-code rc=1)\n"
            "2026-11-23T07:45:10Z 🔴 VPS: la unidad g8-snb-daily.service FALLÓ (result=exit-code rc=1) — ver journalctl -u g8-snb-daily.service host 10.1.2.3\n"
            "2026-11-23T08:00:00Z 🟢 resuelto: algo\n"
        )
        lines = W.parse_fallo_lines(text, NOW_UTC, keep_sec=48 * 3600)
        units = {x["unit"] for x in lines}
        self.assertIn("g8-snb-daily.service", units)
        self.assertNotIn("10.1.2.3", json.dumps(lines))
        self.assertTrue(all("<ip>" in x["text"] or "10." not in x["text"] for x in lines if "host" in x["text"] or "<ip>" in x["text"] or True))
        snb = next(x for x in lines if x["unit"] == "g8-snb-daily.service")
        self.assertEqual(snb["utc"], "2026-11-23T07:45:10Z")
        self.assertNotIn("10.1.2.3", snb["text"])

    def test_should_push_changed_heartbeat_and_unchanged(self):
        a = W.build_status([], [], "2026-11-23T08:00:00Z")
        b = W.build_status([], [], "2026-11-23T08:10:00Z")
        now = datetime(2026, 11, 23, 8, 10, tzinfo=timezone.utc)
        do, why = W.should_push(b, a, now, heartbeat_sec=3 * 3600)
        self.assertFalse(do)
        self.assertEqual(why, "unchanged")
        later = datetime(2026, 11, 23, 11, 10, tzinfo=timezone.utc)
        do, why = W.should_push(b, a, later, heartbeat_sec=3 * 3600)
        self.assertTrue(do)
        self.assertEqual(why, "heartbeat")
        c = W.build_status([{"unit": "g8-snb-daily.service"}], [], "2026-11-23T08:10:00Z")
        do, why = W.should_push(c, a, now, heartbeat_sec=3 * 3600)
        self.assertTrue(do)
        self.assertEqual(why, "changed")
        do, why = W.should_push(a, None, now)
        self.assertTrue(do)
        self.assertEqual(why, "missing")

    def test_push_status_api_skips_when_unchanged(self):
        status = W.build_status([], [], "2026-11-23T08:10:00Z")
        prev = W.build_status([], [], "2026-11-23T08:00:00Z")
        calls = []

        def transport(method, path, data):
            calls.append(method)
            return 200, {}

        out = W.push_status_api(status, token="t", prev=prev, transport=transport)
        self.assertFalse(out["pushed"])
        self.assertEqual(out["reason"], "unchanged")
        self.assertEqual(calls, [])

    def test_push_status_api_puts_on_change(self):
        status = W.build_status([{"unit": "g8-snb-daily.service", "rc": 1}], [], "2026-11-23T08:10:00Z")

        def transport(method, path, data):
            if method == "GET":
                return 404, {"message": "Not Found"}
            self.assertEqual(method, "PUT")
            payload = json.loads(data.decode())
            self.assertTrue(payload["message"].startswith("[vps-push]"))
            self.assertIn("[skip netlify]", payload["message"])
            # the token is only used in the Authorization header (not passed to transport)
            self.assertNotIn("token", payload)
            self.assertNotIn("Authorization", payload)
            return 201, {"commit": {"sha": "abc"}, "content": {"sha": "blob"}}

        out = W.push_status_api(status, token="t", prev=None, transport=transport)
        self.assertTrue(out["pushed"])
        self.assertEqual(out["sha"], "abc")


if __name__ == "__main__":
    unittest.main()

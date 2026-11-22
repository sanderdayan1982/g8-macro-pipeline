"""t334u: job VPS (push acotado, mensaje [vps-push], alerta Telegram a 2 fallos, proxy RBNZ)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "vps"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import vps_git_push as P  # noqa: E402
import watch_vps_jobs as W  # noqa: E402
import fetch_nzd_b2 as B2  # noqa: E402


class RbnzProxy(unittest.TestCase):
    def test_default_is_official_origin(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("G8_RBNZ_PROXY_BASE", None)
            u, h = B2.resolved_urls()
        self.assertTrue(u.startswith("https://www.rbnz.govt.nz/"))
        self.assertIn("hb2-daily-close.xlsx", u)
        self.assertIn("1985-2017", h)

    def test_g8net_proxy_rewrites_origin_only(self):
        base = "https://g8-institutional.netlify.app/proxy/rbnz"
        with mock.patch.dict(os.environ, {"G8_RBNZ_PROXY_BASE": base}):
            u, h = B2.resolved_urls()
        self.assertTrue(u.startswith(base + "/-/media/"))
        self.assertNotIn("www.rbnz.govt.nz", u)
        self.assertTrue(h.startswith(base + "/"))


class VpsGitPush(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.work = os.path.join(self.tmp, "work")
        self.remote = os.path.join(self.tmp, "remote.git")
        subprocess.check_call(["git", "init", "--bare", self.remote], stdout=subprocess.DEVNULL)
        subprocess.check_call(["git", "clone", "-q", self.remote, self.work], stdout=subprocess.DEVNULL)
        subprocess.check_call(["git", "checkout", "-q", "-b", "main"], cwd=self.work)
        os.makedirs(os.path.join(self.work, "data"))
        with open(os.path.join(self.work, "data", "CHF_SPOT_1Y_DAILY.csv"), "w") as fh:
            fh.write("Date,Value,Source,PublishingDate\n2026-11-19,0.4,SNB1A,2026-11-20T08:31:00\n")
        with open(os.path.join(self.work, "README"), "w") as fh:
            fh.write("x\n")
        subprocess.check_call(["git", "add", "README"], cwd=self.work)
        env = dict(os.environ)
        env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = "t"
        env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = "t@t"
        subprocess.check_call(["git", "commit", "-qm", "init"], cwd=self.work, env=env)
        subprocess.check_call(["git", "push", "-q", "origin", "HEAD:main"], cwd=self.work)
        subprocess.check_call(["git", "add", "data/CHF_SPOT_1Y_DAILY.csv"], cwd=self.work)

    def test_rejects_message_without_prefix(self):
        with self.assertRaises(P.PushError):
            P.commit_and_push(self.work, P.SNB_ALLOWED, "snb1a last=x [skip netlify]", token="x")

    def test_rejects_dirty_path_outside_allowlist(self):
        with open(os.path.join(self.work, "README"), "a") as fh:
            fh.write("y\n")
        with self.assertRaises(P.PushError) as cm:
            P.commit_and_push(self.work, P.SNB_ALLOWED, "[vps-push] snb1a last=x [skip netlify]", token="x")
        self.assertIn("no permitidas", str(cm.exception))

    def test_commit_msg_helper_default_unchanged(self):
        # push_nzd hook: sin env, el cuerpo queda igual
        sys.path.insert(0, os.path.join(ROOT, "mac"))
        import push_nzd_to_github as N  # noqa: WPS433
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("G8_COMMIT_PREFIX", None)
            os.environ.pop("G8_COMMIT_SUFFIX", None)
            self.assertEqual(N.commit_msg("mac-primary NZ-B2 rid"), "mac-primary NZ-B2 rid")
            os.environ["G8_COMMIT_PREFIX"] = "[vps-push]"
            os.environ["G8_COMMIT_SUFFIX"] = "[skip netlify]"
            self.assertEqual(N.commit_msg("vps-prod NZ-B2 rid"), "[vps-push] vps-prod NZ-B2 rid [skip netlify]")


class WatchTwoFails(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _write(self, job, **rec):
        p = os.path.join(self.tmp, "%s.json" % job)
        Path(p).write_text(json.dumps(rec), encoding="utf-8")

    def test_alerts_only_on_second_consecutive_failure(self):
        self._write("snb1a", ok=False, consecutive_fail=1, utc="2026-11-22T18:00:00Z")
        self._write("nzd-b2", ok=True, consecutive_fail=0, utc="2026-11-22T18:00:00Z")
        nuevos, alerts = W.evaluate(self.tmp, {})
        self.assertEqual(nuevos, [])
        self._write("snb1a", ok=False, consecutive_fail=2, utc="2026-11-22T19:00:00Z", error="HTTP 500")
        nuevos, alerts = W.evaluate(self.tmp, alerts)
        self.assertEqual(len(nuevos), 1)
        self.assertEqual(nuevos[0][0], "snb1a")
        self.assertIn("2 veces seguidas", nuevos[0][1])
        # no re-alerta mientras sigue en fallo
        nuevos2, alerts = W.evaluate(self.tmp, alerts)
        self.assertEqual(nuevos2, [])
        # recuperación limpia el flag
        self._write("snb1a", ok=True, consecutive_fail=0, utc="2026-11-22T20:00:00Z")
        nuevos3, alerts = W.evaluate(self.tmp, alerts)
        self.assertEqual(nuevos3, [])
        self.assertNotIn("fail2:snb1a", alerts)


class UnitsAndNoDispatcher(unittest.TestCase):
    def test_units_are_not_dispatcher_slots(self):
        for name in ("g8-snb-daily.service", "g8-nzd-b2-daily.service"):
            text = Path(os.path.join(ROOT, "vps", name)).read_text()
            self.assertIn("no es un turno del dispatcher", text)
            self.assertIn("EnvironmentFile=/etc/g8/secrets.env", text)
            self.assertNotIn("dispatch_prod", text)
        t = Path(os.path.join(ROOT, "vps", "g8-snb-daily.timer")).read_text()
        self.assertIn("07:45:00", t)

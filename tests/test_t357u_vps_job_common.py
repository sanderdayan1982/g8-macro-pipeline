"""t354u/t357u: piezas comunes de los jobs VPS migrados (cola compartida, saneado, arranque limpio). Sin red."""
import base64
import fcntl
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vps"))
import vps_job_common as C


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                   capture_output=True)


class Sanitize(unittest.TestCase):
    def test_no_secret_plain_or_basic_header(self):
        tok = "ghp_FAKEFAKEFAKE123"
        b64 = base64.b64encode(("x-access-token:" + tok).encode()).decode()
        text = "fatal %s\n> Authorization: Basic %s\nother %s" % (tok, b64, b64)
        out = C.sanitize(text, (tok,))
        self.assertNotIn(tok, out)
        self.assertNotIn(b64, out)

    def test_trace_vars_removed(self):
        env = {"GIT_TRACE": "1", "GIT_CURL_VERBOSE": "1", "PATH": "/bin"}
        C.scrub_git_trace_env(env)
        self.assertEqual(env, {"PATH": "/bin"})


class SharedLock(unittest.TestCase):
    def test_waits_then_times_out_without_cancel(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "g8-shared-data-alerts.lock")
            holder = open(path, "a+")
            fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
            clock = iter(range(0, 100, 10))
            slept = []
            with self.assertRaises(C.LockTimeout):
                with C.wait_exclusive(path, timeout=30, poll=1, sleep=slept.append, clock=lambda: next(clock)):
                    pass
            self.assertTrue(slept)
            holder.close()
            with C.wait_exclusive(path, timeout=1):
                pass

    def test_both_jobs_take_the_shared_queue(self):
        for f in ("metals_vps.py", "usd_factor_vps.py"):
            if (ROOT / "vps" / f).exists():
                self.assertIn("C.wait_exclusive(", (ROOT / "vps" / f).read_text(), f)


class StartClean(unittest.TestCase):
    def test_stranded_local_commit_aborts_without_discarding(self):
        with tempfile.TemporaryDirectory() as tmp:
            remote, clone = os.path.join(tmp, "r.git"), os.path.join(tmp, "c")
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", remote], check=True)
            git(tmp, "clone", "-q", remote, clone)
            Path(clone, "a").write_text("1")
            git(clone, "add", "a")
            git(clone, "commit", "-qm", "base")
            git(clone, "push", "-q", "origin", "HEAD:main")
            Path(clone, "a").write_text("2")
            git(clone, "commit", "-qam", "varado")
            Path(clone, "a").write_text("3")
            git(clone, "add", "a")

            class P:
                @staticmethod
                def fetch_ff_only(cwd, token, remote="origin", branch="main", allowed=None):
                    subprocess.run(["git", "fetch", "-q", remote, branch], cwd=cwd, check=True)

            log = os.path.join(tmp, "ALERTAS.log")
            os.environ["G8_ALERTAS_LOG"] = log
            self.addCleanup(os.environ.pop, "G8_ALERTAS_LOG", None)
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=clone, capture_output=True, text=True).stdout
            with self.assertRaises(C.StrandedCommits):
                C.start_clean(clone, P, "tok", allowed=("a",))
            # nada se descarta: el commit varado sigue en HEAD y la alerta queda registrada
            self.assertEqual(subprocess.run(["git", "rev-parse", "HEAD"], cwd=clone, capture_output=True,
                                            text=True).stdout, head)
            self.assertEqual(Path(clone, "a").read_text(), "3")
            self.assertIn("INCIDENCIA STRANDED", Path(log).read_text())

    def test_clean_clone_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            remote, clone = os.path.join(tmp, "r.git"), os.path.join(tmp, "c")
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", remote], check=True)
            git(tmp, "clone", "-q", remote, clone)
            Path(clone, "a").write_text("1")
            git(clone, "add", "a")
            git(clone, "commit", "-qm", "base")
            git(clone, "push", "-q", "origin", "HEAD:main")

            class P:
                @staticmethod
                def fetch_ff_only(cwd, token, remote="origin", branch="main", allowed=None):
                    subprocess.run(["git", "fetch", "-q", remote, branch], cwd=cwd, check=True)

            self.assertEqual(C.start_clean(clone, P, "tok", allowed=("a",)), [])


class Wrappers(unittest.TestCase):
    def test_private_clone_in_subdir_and_private_copies_removed(self):
        for f in ("metals_vps.py", "usd_factor_vps.py"):
            if not (ROOT / "vps" / f).exists():
                continue
            t = (ROOT / "vps" / f).read_text()
            self.assertIn('os.path.join(tmp, "g8data")', t, f)
            self.assertIn("remove_private_copies", t, f)
            self.assertIn("C.publish_record(", t, f)
            self.assertNotIn("traceback.print_exc", t, f)

    def test_private_alert_copies_never_public_on_failure(self):
        m = (ROOT / "vps/metals_vps.py").read_text()
        self.assertIn('post.get("refresh_brief") != 0', m)
        if (ROOT / "vps/usd_factor_vps.py").exists():
            u = (ROOT / "vps/usd_factor_vps.py").read_text()
            self.assertIn('if dash_rc != 0:', u)
            self.assertIn('"data/alerts/state.json", "data/alerts/brief.json"', u)

"""PR-A — private_data.py contra repositorios git locales.

pull, push, redact y verify. El token no entra en argv ni en la salida.
No llama a GitHub ni a Databento.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(ROOT, "scripts", "tools", "private_data.py")
TOKEN = "g8-token-SENTINEL-not-a-real-secret"
FIGURES = ("1.2345", "OI-99991", "77.77", "IV-5.96", "STRIKE-9.875")


def git(cwd, *args, check=True):
    proc = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    return proc


class RepoCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(subprocess.run, ["rm", "-rf", self.tmp], check=False)
        self.remote = os.path.join(self.tmp, "remote.git")
        git(self.tmp, "init", "-q", "--bare", "-b", "main", self.remote)
        self.seed = os.path.join(self.tmp, "seed")
        git(self.tmp, "clone", "-q", self.remote, self.seed)
        self.config(self.seed)
        self.write(self.seed, "data/options/canonical/a.csv", "from-private\n")
        self.write(self.seed, "data/alerts/brief.json", '{"walls":[]}\n')
        self.write(self.seed, "data/alerts/state.json", "{}\n")
        self.write(self.seed, "other.txt", "keep\n")
        git(self.seed, "add", ".")
        git(self.seed, "commit", "-qm", "seed")
        git(self.seed, "push", "-q", "origin", "HEAD:main")
        self.ws = os.path.join(self.tmp, "ws")
        os.makedirs(self.ws)
        self.clone = os.path.join(self.tmp, "g8data")

    def config(self, path):
        git(path, "config", "user.email", "t@t")
        git(path, "config", "user.name", "t")

    def write(self, root, rel, text):
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def read(self, root, rel):
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            return fh.read()

    def run_pd(self, args, env=None):
        proc = subprocess.run(
            [sys.executable, SCRIPT] + args,
            cwd=self.ws, env=env, capture_output=True, text=True,
        )
        return proc

    def base_env(self, token=""):
        env = os.environ.copy()
        env.pop("G8_DATA_TOKEN", None)
        if token:
            env["G8_DATA_TOKEN"] = token
        return env


class PullPushVerify(RepoCase):
    def test_pull_copies_rows_and_push_retries_then_noops(self):
        self.write(self.ws, "data/alerts/brief.json", '{"walls":[{"pair":"OLD"}]}\n')
        proc = self.run_pd([
            "pull", self.clone, "--repo", self.remote, "--workspace", self.ws, "--rows", "1,11",
        ], self.base_env())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("copied=2", proc.stdout)
        self.assertEqual(self.read(self.ws, "data/options/canonical/a.csv"), "from-private\n")
        self.assertIn("missing=0", proc.stdout)

        self.write(self.ws, "data/alerts/brief.json", '{"walls":[{"pair":"RUN"}]}\n')
        self.write(self.ws, "other.txt", "local-only\n")
        proc = self.run_pd([
            "push", self.clone, "--repo", self.remote, "--workspace", self.ws,
            "--paths", "data/alerts/brief.json",
            "--workflow", "daily_update", "--session", "2026-11-16",
            "--retries", "5", "--retry-sleep", "0",
        ], self.base_env())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("push correcto", proc.stdout)
        git(self.seed, "fetch", "-q", "origin", "main")
        subject = git(self.seed, "log", "-1", "--format=%s", "origin/main").stdout.strip()
        self.assertEqual(subject, "data: daily_update 2026-11-16 [skip netlify]")
        git(self.seed, "merge", "-q", "--ff-only", "origin/main")
        self.assertEqual(self.read(self.seed, "data/alerts/brief.json"), '{"walls":[{"pair":"RUN"}]}\n')
        self.assertEqual(self.read(self.seed, "other.txt"), "keep\n")

        again = self.run_pd([
            "push", self.clone, "--repo", self.remote, "--workspace", self.ws,
            "--paths", "data/alerts/brief.json",
            "--workflow", "daily_update", "--session", "2026-11-16",
            "--retry-sleep", "0",
        ], self.base_env())
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("no-op", again.stdout)
        count = git(self.seed, "rev-list", "--count", "origin/main").stdout.strip()
        self.assertEqual(count, "2")

    def test_push_rebase_keeps_this_run_and_the_other_file(self):
        proc = self.run_pd([
            "pull", self.clone, "--repo", self.remote, "--workspace", self.ws, "--rows", "11",
        ], self.base_env())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        other = os.path.join(self.tmp, "other")
        git(self.tmp, "clone", "-q", self.remote, other)
        self.config(other)
        self.write(other, "data/alerts/brief.json", "from-other\n")
        self.write(other, "other.txt", "other-moved\n")
        git(other, "add", ".")
        git(other, "commit", "-qm", "other")
        git(other, "push", "-q", "origin", "HEAD:main")

        self.write(self.ws, "data/alerts/brief.json", "from-this-run\n")
        proc = self.run_pd([
            "push", self.clone, "--repo", self.remote, "--workspace", self.ws,
            "--rows", "11", "--workflow", "cme_options", "--session", "2026-11-16",
            "--retries", "5", "--retry-sleep", "0",
        ], self.base_env())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("push correcto", proc.stdout)
        git(self.seed, "fetch", "-q", "origin", "main")
        brief = git(self.seed, "show", "origin/main:data/alerts/brief.json").stdout
        other_txt = git(self.seed, "show", "origin/main:other.txt").stdout
        self.assertEqual(brief, "from-this-run\n")
        self.assertEqual(other_txt, "other-moved\n")

    def test_verify_reports_only_counts_and_paths(self):
        proc = self.run_pd([
            "pull", self.clone, "--repo", self.remote, "--workspace", self.ws, "--rows", "1,11",
        ], self.base_env())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        ok = self.run_pd([
            "verify", self.clone, "--workspace", self.ws, "--rows", "1,11",
        ], self.base_env())
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn("differ=0", ok.stdout)
        self.assertNotIn("from-private", ok.stdout)
        self.write(self.ws, "data/alerts/brief.json", "SENTINEL_VALUE_42\n")
        bad = self.run_pd([
            "verify", self.clone, "--workspace", self.ws, "--paths", "data/alerts/brief.json",
        ], self.base_env())
        self.assertEqual(bad.returncode, 1, bad.stdout)
        self.assertIn("differ=1", bad.stdout)
        self.assertIn("data/alerts/brief.json", bad.stdout)
        self.assertNotIn("SENTINEL_VALUE_42", bad.stdout)
        self.assertNotIn("SENTINEL_VALUE_42", bad.stderr)

    def test_credit_push_only_when_the_file_exists(self):
        proc = self.run_pd([
            "pull", self.clone, "--repo", self.remote, "--workspace", self.ws, "--rows", "11",
        ], self.base_env())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.write(self.ws, "data/alerts/brief.json", "{}\n")
        self.write(self.ws, "data/credit/CRD_shadow.json", '{"oas": 1}\n')
        proc = self.run_pd([
            "push", self.clone, "--repo", self.remote, "--workspace", self.ws,
            "--rows", "11", "--credit-if-present",
            "--workflow", "daily_update", "--session", "2026-11-16",
            "--retry-sleep", "0",
        ], self.base_env())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("credit=1", proc.stdout)
        git(self.seed, "fetch", "-q", "origin", "main")
        crd = git(self.seed, "show", "origin/main:data/credit/CRD_shadow.json").stdout
        self.assertEqual(crd, '{"oas": 1}\n')
        missing = git(self.seed, "cat-file", "-e", "origin/main:data/credit/oas_vintage.csv", check=False)
        self.assertNotEqual(missing.returncode, 0)


class Redact(unittest.TestCase):
    def test_redact_keeps_dates_and_drops_cme_figures(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(subprocess.run, ["rm", "-rf", tmp], check=False)
        brief = {
            "as_of": {"options": "2026-11-12", "rates": "2026-11-16"},
            "walls": [{
                "pair": "EUR/USD", "gate0": "CLEAN", "spot": "1.2345",
                "front_oi": "OI-99991", "top": [{"level": "STRIKE-9.875"}],
            }],
            "bullets": [{"sec": "§02", "text": "sin figura cme"}],
        }
        state = {
            "walls": {
                "EUR": {"front_metrics": {"pin_k": "1.2345"}, "front_oi": "OI-99991"},
                "session": "2026-11-12",
            },
            "dqm": {"OPTIONS_SURFACE": {"status": "LIVE", "pata_pct": "77.77"}, "SOFR": "LIVE"},
        }
        surface = {
            "latest_session": "2026-11-12", "generated_utc": "2026-11-15T21:29:40Z",
            "gate0": {"EUR": {"verdict": "CLEAN", "pata_pct": "77.77"}},
        }
        c1 = {"session": "2026-11-12", "mode": "SHADOW", "currencies": {"EUR": {"ref": "1.2345"}}}
        fx = {
            "session": "2026-11-12", "status": "SHADOW",
            "generated_utc": "2026-11-14T01:38:17Z",
            "fut_roll_window": {"start": "2026-12-07", "end": "2026-12-19", "active": False, "imm": "2026-12-16"},
            "currencies": {
                "EUR": {"status": "OK", "exp1": "2026-12-04", "dte1": 22, "iv30_atm": "IV-5.96", "F1": "STRIKE-9.875"},
                "NZD": {"status": "NA"},
            },
        }
        files = {
            "data/alerts/brief.json": brief,
            "data/alerts/state.json": state,
            "data/OPTIONS_SURFACE.json": surface,
            "data/CME_OPTIONS_C1_shadow.json": c1,
            "data/CME_FXVOL_shadow.json": fx,
        }
        for rel, obj in files.items():
            path = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(obj, fh)
        proc = subprocess.run(
            [sys.executable, SCRIPT, "redact", "--workspace", tmp],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out_brief = json.load(open(os.path.join(tmp, "data/alerts/brief.json"), encoding="utf-8"))
        out_state = json.load(open(os.path.join(tmp, "data/alerts/state.json"), encoding="utf-8"))
        status = json.load(open(os.path.join(tmp, "data/public/cme_status.json"), encoding="utf-8"))
        dates = json.load(open(os.path.join(tmp, "data/public/fxvol_dates.json"), encoding="utf-8"))
        self.assertEqual(out_brief["as_of"]["options"], "2026-11-12")
        self.assertEqual(out_brief["as_of"]["rates"], "2026-11-16")
        self.assertEqual(out_brief["walls"], [{"pair": "EUR/USD", "status": "CLEAN", "session": "2026-11-12"}])
        self.assertEqual(set(out_brief["walls"][0]), {"pair", "status", "session"})
        self.assertEqual(out_state["walls"], [{"pair": "EUR/USD", "status": "NA", "session": "2026-11-12"}])
        self.assertEqual(out_state["dqm"]["OPTIONS_SURFACE"], "LIVE")
        self.assertEqual(out_state["dqm"]["SOFR"], "LIVE")
        self.assertEqual(status["data/OPTIONS_SURFACE.json"], {
            "session": "2026-11-12", "generated_utc": "2026-11-15T21:29:40Z", "status": None,
        })
        self.assertEqual(status["data/CME_OPTIONS_C1_shadow.json"]["status"], "SHADOW")
        self.assertEqual(set(status["data/CME_FXVOL_shadow.json"]), {"session", "generated_utc", "status"})
        self.assertEqual(dates["session"], "2026-11-12")
        self.assertEqual(dates["fut_roll_window"], {
            "start": "2026-12-07", "end": "2026-12-19", "active": False, "imm": "2026-12-16",
        })
        self.assertEqual(dates["opex"]["EUR"], {"exp1": "2026-12-04", "dte1": 22})
        self.assertEqual(set(dates["opex"]["EUR"]), {"exp1", "dte1"})
        blob = "\n".join(
            open(os.path.join(tmp, rel), encoding="utf-8").read()
            for rel in (
                "data/alerts/brief.json", "data/alerts/state.json",
                "data/public/cme_status.json", "data/public/fxvol_dates.json",
            )
        )
        for figure in FIGURES:
            self.assertNotIn(figure, blob, figure)


class TokenNotLeaked(RepoCase):
    def test_token_is_not_in_argv_or_logs(self):
        bindir = os.path.join(self.tmp, "bin")
        os.makedirs(bindir)
        log = os.path.join(self.tmp, "argv.log")
        wrapper = textwrap.dedent("""\
            #!/bin/sh
            printf '%s\\n' "$*" >> "$GIT_ARGV_LOG"
            exec /usr/bin/git "$@"
            """)
        path = os.path.join(bindir, "git")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(wrapper)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        env = self.base_env(TOKEN)
        env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
        env["GIT_ARGV_LOG"] = log
        args = ["pull", self.clone, "--repo", self.remote, "--workspace", self.ws, "--rows", "11"]
        self.assertNotIn(TOKEN, " ".join(args))
        proc = self.run_pd(args, env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        argv = open(log, encoding="utf-8").read()
        ask = open(os.path.join(self.tmp, ".g8-askpass"), encoding="utf-8").read()
        for label, text in (("stdout", proc.stdout), ("stderr", proc.stderr), ("argv", argv), ("askpass", ask)):
            self.assertNotIn(TOKEN, text, label)
        self.assertIn(self.remote, argv)
        self.assertNotIn(TOKEN, argv)
        self.assertIn("G8_DATA_TOKEN", ask)
        self.assertNotIn(TOKEN, ask)


if __name__ == "__main__":
    unittest.main()

"""t346u: el sucio de TONA/CHF en el clon nzd-b2 es del propio job, no de otro.

Cada job tiene workdir y allowlist propios. El follow-up de Argos no aborta
por salidas sucias del mismo job, y sí aborta si aparece una ruta ajena.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from itertools import combinations
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "vps"))

import vps_git_push as P  # noqa: E402
import nzd_b2_daily as N  # noqa: E402


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


class IsolationCatalog(unittest.TestCase):
    def test_workdirs_are_distinct(self):
        values = list(P.JOB_WORKDIR_DEFAULTS.values())
        self.assertEqual(len(values), len(set(values)))
        self.assertEqual(set(P.JOB_WORKDIR_DEFAULTS), set(P.JOB_OWNED))

    def test_no_job_owns_another_job_output(self):
        self.assertTrue(P.path_owned("data/TONA.csv", P.NZD_B2_OWNED))
        self.assertTrue(P.path_owned("data/CHF_NOM_10Y.csv", P.NZD_B2_OWNED))
        self.assertTrue(P.path_owned("data/_ingest/evidence/TONA.csv.jsonl", P.NZD_B2_OWNED))
        self.assertTrue(P.path_owned("data/_ingest/runs/actions/fetch_tona/2026-11.jsonl", P.NZD_B2_OWNED))
        # SNB1A no es del clon nzd-b2 (t334u)
        self.assertFalse(P.path_owned("data/CHF_SPOT_10Y_DAILY.csv", P.NZD_B2_OWNED))
        self.assertTrue(P.path_owned("data/CHF_SPOT_10Y_DAILY.csv", P.SNB_ALLOWED))
        self.assertTrue(P.path_owned("data/CHF_SPOT_10Y.csv", P.NZD_B2_OWNED))
        self.assertFalse(P.path_owned("data/CHF_SPOT_10Y.csv", P.SNB_ALLOWED))
        for a, b in combinations(P.JOB_OWNED, 2):
            for path in P.JOB_OWNED[a]:
                if any(ch in path for ch in "*?[") or path.endswith("/"):
                    continue
                self.assertFalse(
                    P.path_owned(path, P.JOB_OWNED[b]),
                    "%s in %s also owned by %s" % (path, a, b),
                )

    def test_nzd_follow_up_declares_owned_allowlist(self):
        src = Path(ROOT, "vps", "nzd_b2_daily.py").read_text(encoding="utf-8")
        self.assertIn("owned=P.NZD_B2_OWNED", src)
        self.assertIn("allowed=P.NZD_B2_OWNED", src)


class FollowUpDoesNotLeakOrAbort(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.work = os.path.join(self.tmp, "work")
        self.remote = os.path.join(self.tmp, "remote.git")
        subprocess.check_call(
            ["git", "init", "-q", "--bare", "--initial-branch=main", self.remote],
            stdout=subprocess.DEVNULL,
        )
        subprocess.check_call(["git", "clone", "-q", self.remote, self.work], stdout=subprocess.DEVNULL)
        _git(self.work, "checkout", "-q", "-b", "main")
        _write(os.path.join(self.work, "data", "TONA.csv"),
               "DATE,OPEN,HIGH,LOW,CLOSE,VOLUME\n20261118,1,1,1,1,0\n")
        _write(os.path.join(self.work, "data", "CHF_NOM_10Y.csv"), "Date,Value,Source\n2026-11-20,1,curve\n")
        _write(os.path.join(self.work, "data", "argos_rates.json"), "{}\n")
        _write(os.path.join(self.work, "data", "_ingest", "latest", "mac-primary__nzchf-tona.json"), "{}\n")
        _write(os.path.join(self.work, "data", "DE_10Y_PAR_DAILY.csv"), "Date,Value\n2026-11-23,3.35\n")
        _git(self.work, "add", "data")
        _git(self.work, "commit", "-qm", "init")
        _git(self.work, "push", "-q", "origin", "HEAD:main")

    def test_same_job_dirty_does_not_abort_and_is_not_committed(self):
        _write(os.path.join(self.work, "data", "TONA.csv"),
               "DATE,OPEN,HIGH,LOW,CLOSE,VOLUME\n20261118,1,1,1,1,0\nDIRTY\n")
        _write(os.path.join(self.work, "data", "CHF_NOM_10Y.csv"), "Date,Value,Source\nDIRTY,1,curve\n")
        _write(os.path.join(self.work, "data", "_ingest", "evidence", "TONA.csv.jsonl"), "{}\n")
        os.remove(os.path.join(self.work, "data", "_ingest", "latest", "mac-primary__nzchf-tona.json"))
        _write(os.path.join(self.work, "data", "argos_rates.json"), '{"generated_at": "x"}\n')
        allowed = (
            "data/argos_rates.json",
            "data/_ingest/latest/mac-primary__nzchf-tona.json",
        )
        out = P.commit_and_push(
            self.work, allowed,
            "[vps-push] argos refresh / clear mac-primary latest [skip netlify]",
            token="x", owned=P.NZD_B2_OWNED,
        )
        self.assertTrue(out["changed"])
        self.assertIn("data/argos_rates.json", out["files"])
        self.assertIn("data/_ingest/latest/mac-primary__nzchf-tona.json", out["files"])
        self.assertNotIn("data/TONA.csv", out["files"])
        self.assertNotIn("data/CHF_NOM_10Y.csv", out["files"])
        # leftover same-job dirty still on disk, not discarded
        self.assertIn("DIRTY", Path(self.work, "data", "TONA.csv").read_text())
        self.assertIn("DIRTY", Path(self.work, "data", "CHF_NOM_10Y.csv").read_text())

    def test_foreign_dirty_still_aborts(self):
        _write(os.path.join(self.work, "data", "argos_rates.json"), '{"generated_at": "x"}\n')
        _write(os.path.join(self.work, "data", "DE_10Y_PAR_DAILY.csv"), "Date,Value\nFOREIGN,3.35\n")
        allowed = ("data/argos_rates.json", "data/_ingest/latest/mac-primary__nzchf-tona.json")
        with self.assertRaises(P.PushError) as cm:
            P.commit_and_push(
                self.work, allowed,
                "[vps-push] argos refresh / clear mac-primary latest [skip netlify]",
                token="x", owned=P.NZD_B2_OWNED,
            )
        self.assertIn("DE_10Y_PAR_DAILY.csv", str(cm.exception))
        self.assertIn("rutas no permitidas sucias", str(cm.exception))


if __name__ == "__main__":
    unittest.main()

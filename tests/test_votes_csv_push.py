"""ORDEN_ENZO_REVISION-TRIPLE-01 bloque 2 — votes.csv survives a rebase conflict.

Two local repositories. On data/votes.csv, main's bytes stay a prefix and only
keys missing from main are appended. Another file still follows -X theirs.
"""
import csv
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts", "tools"))
import votes_csv_merge as M  # noqa: E402

SCRIPT = os.path.join(ROOT, "scripts", "tools", "git_push_retry.sh")
HEADER = "fecha_cierre,script,ccy,estado\n"


def row(day, script, ccy, estado):
    return "%s,%s,%s,%s\n" % (day, script, ccy, estado)


def keys(text):
    return [
        (r["fecha_cierre"], r["script"], r["ccy"])
        for r in csv.DictReader(io.StringIO(text))
    ]


@unittest.skipUnless(shutil.which("git") and shutil.which("bash") and shutil.which("python3"), "sin git/bash/python3")
class VotesPush(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.remote = os.path.join(self.t, "remote.git")
        self.git(self.t, "init", "-q", "--bare", "-b", "main", self.remote)
        seed = os.path.join(self.t, "seed")
        self.git(self.t, "clone", "-q", self.remote, seed)
        self.cfg(seed)
        self.write(seed, "data/votes.csv", HEADER + row("2026-11-12", "E21", "EUR", "FRESH"))
        self.write(seed, "data/alerts/brief.json", "v0")
        self.git(seed, "add", ".")
        self.git(seed, "commit", "-qm", "seed")
        self.git(seed, "push", "-q", "origin", "HEAD:main")

    def tearDown(self):
        shutil.rmtree(self.t, ignore_errors=True)

    def git(self, cwd, *args):
        return subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True, check=True).stdout

    def cfg(self, path):
        self.git(path, "config", "user.email", "t@t")
        self.git(path, "config", "user.name", "t")

    def write(self, path, rel, text):
        dest = os.path.join(path, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)

    def blob(self, rel):
        proc = subprocess.run(
            ["git", "--git-dir", self.remote, "cat-file", "-p", "main:" + rel],
            cwd=self.t, capture_output=True, check=True,
        )
        return proc.stdout

    def clone(self, name):
        path = os.path.join(self.t, name)
        self.git(self.t, "clone", "-q", self.remote, path)
        self.cfg(path)
        return path

    def push_script(self, path):
        proc = subprocess.run(["bash", SCRIPT, "3"], cwd=path, capture_output=True, text=True, timeout=120)
        return proc.returncode, proc.stdout + proc.stderr

    def test_conflict_keeps_main_bytes_and_appends_only_missing_keys(self):
        work = self.clone("work")
        other = self.clone("other")
        seed = (HEADER + row("2026-11-12", "E21", "EUR", "FRESH")).encode("utf-8")
        self.assertEqual(self.blob("data/votes.csv"), seed)

        work_text = HEADER + row("2026-11-12", "E21", "EUR", "FRESH") + row("2026-11-13", "RISK_G8", "G8", "FRESH")
        self.write(work, "data/votes.csv", work_text)
        self.write(work, "data/alerts/brief.json", "this-run")
        self.git(work, "add", ".")
        self.git(work, "commit", "-qm", "work")

        main_text = HEADER + row("2026-11-12", "E21", "EUR", "FRESH") + row("2026-11-13", "E21", "GBP", "FRESH")
        self.write(other, "data/votes.csv", main_text)
        self.write(other, "data/alerts/brief.json", "main-run")
        self.git(other, "add", ".")
        self.git(other, "commit", "-qm", "other")
        self.git(other, "push", "-q", "origin", "HEAD:main")
        main_bytes = self.blob("data/votes.csv")
        self.assertEqual(main_bytes, main_text.encode("utf-8"))

        rc, out = self.push_script(work)
        self.assertEqual(rc, 0, out)
        got = self.blob("data/votes.csv")
        self.assertTrue(got.startswith(main_bytes), got)
        self.assertEqual(got[:len(main_bytes)], main_bytes)
        text = got.decode("utf-8")
        self.assertEqual(keys(text), [
            ("2026-11-12", "E21", "EUR"),
            ("2026-11-13", "E21", "GBP"),
            ("2026-11-13", "RISK_G8", "G8"),
        ])
        self.assertEqual(text.count("2026-11-13,E21,GBP,FRESH"), 1)
        self.assertEqual(text.count("2026-11-13,RISK_G8,G8,FRESH"), 1)
        self.assertEqual(self.blob("data/alerts/brief.json"), b"this-run")

    def test_same_key_is_not_appended_and_main_bytes_stay(self):
        work = self.clone("work")
        other = self.clone("other")
        self.write(work, "data/votes.csv", HEADER + row("2026-11-12", "E21", "EUR", "CHANGED"))
        self.git(work, "add", ".")
        self.git(work, "commit", "-qm", "work")
        main_text = HEADER + row("2026-11-12", "E21", "EUR", "FRESH") + row("2026-11-13", "E21", "GBP", "FRESH")
        self.write(other, "data/votes.csv", main_text)
        self.git(other, "add", ".")
        self.git(other, "commit", "-qm", "other")
        self.git(other, "push", "-q", "origin", "HEAD:main")
        main_bytes = self.blob("data/votes.csv")
        rc, out = self.push_script(work)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.blob("data/votes.csv"), main_bytes)
        self.assertNotIn(b"CHANGED", self.blob("data/votes.csv"))

    def test_helper_appends_without_rewriting_the_prefix(self):
        main_bytes = (HEADER + row("2026-11-12", "E21", "EUR", "FRESH")).encode("utf-8")
        other = main_bytes + row("2026-11-13", "PSI_G8", "EUR", "FRESH").encode("utf-8")
        merged = M.merge_votes_bytes(main_bytes, other)
        self.assertEqual(merged[:len(main_bytes)], main_bytes)
        self.assertIn(b"PSI_G8", merged)
        again = M.merge_votes_bytes(merged, other)
        self.assertEqual(again, merged)


if __name__ == "__main__":
    unittest.main()

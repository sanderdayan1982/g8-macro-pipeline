"""Acta P-7 · scripts/tools/git_push_retry.sh — el push de un workflow ya no se pierde si otro proceso sube a main
mientras corre (CME Options falló así el 28, 29 y 30-sep y el 1-oct: «rejected (fetch first)»).

R1 main avanzó con otros ficheros → integra y sube; los dos cambios quedan en main
R2 main avanzó tocando el MISMO fichero → gana la versión de este run; el resto de main se conserva
R3 clon superficial (checkout por defecto, depth 1) → funciona
R4 los workflows que suben datos usan el script (ninguno con «git push» suelto sin reintento)
"""
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(ROOT, "scripts", "tools", "git_push_retry.sh")


@unittest.skipUnless(shutil.which("git") and shutil.which("bash"), "sin git/bash")
class PushRetry(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.remote = os.path.join(self.t, "remote.git")
        self.git(self.t, "init", "-q", "--bare", "-b", "main", self.remote)
        seed = os.path.join(self.t, "seed")
        self.git(self.t, "clone", "-q", self.remote, seed)
        self.cfg(seed)
        self.write(seed, "data/alerts/brief.json", "v0")
        self.write(seed, "data/other.csv", "o0")
        self.git(seed, "add", ".")
        self.git(seed, "commit", "-qm", "seed")
        self.git(seed, "push", "-q", "origin", "HEAD:main")

    def tearDown(self):
        shutil.rmtree(self.t, ignore_errors=True)

    def git(self, cwd, *a):
        return subprocess.run(["git"] + list(a), cwd=cwd, capture_output=True, text=True, check=True).stdout

    def cfg(self, d):
        self.git(d, "config", "user.email", "t@t")
        self.git(d, "config", "user.name", "t")

    def write(self, d, rel, text):
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(text)

    def read_main(self, rel):
        return self.git(self.t, "--git-dir", self.remote, "show", "main:" + rel)

    def clone(self, name, depth=None):
        d = os.path.join(self.t, name)
        args = ["clone", "-q"] + (["--depth", str(depth), "file://" + self.remote] if depth else [self.remote]) + [d]
        self.git(self.t, *args)
        self.cfg(d)
        return d

    def other_pushes(self, rel, text):
        o = self.clone("other_%d" % len(os.listdir(self.t)))
        self.write(o, rel, text)
        self.git(o, "commit", "-qam", "other")
        self.git(o, "push", "-q", "origin", "HEAD:main")

    def run_script(self, d):
        r = subprocess.run(["bash", SCRIPT, "3"], cwd=d, capture_output=True, text=True, timeout=120)
        return r.returncode, r.stdout + r.stderr

    def test_R1_main_moved_other_files(self):
        w = self.clone("work")
        self.write(w, "data/options/2026-09-30/6A.csv", "x")
        self.git(w, "add", ".")
        self.git(w, "commit", "-qm", "options")
        self.other_pushes("data/other.csv", "o1")                     # p. ej. latido del Mac
        rc, out = self.run_script(w)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.read_main("data/options/2026-09-30/6A.csv"), "x")
        self.assertEqual(self.read_main("data/other.csv"), "o1")

    def test_R2_same_file_this_run_wins(self):
        w = self.clone("work")
        self.write(w, "data/alerts/brief.json", "options-run")
        self.write(w, "data/options/x.csv", "x")
        self.git(w, "add", ".")
        self.git(w, "commit", "-qm", "options")
        self.other_pushes("data/alerts/brief.json", "daily-run")
        self.other_pushes("data/other.csv", "o2")
        rc, out = self.run_script(w)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.read_main("data/alerts/brief.json"), "options-run")
        self.assertEqual(self.read_main("data/other.csv"), "o2")
        self.assertEqual(self.read_main("data/options/x.csv"), "x")

    def test_R3_shallow_clone(self):
        self.other_pushes("data/other.csv", "o3")
        w = self.clone("work", depth=1)
        self.write(w, "data/options/y.csv", "y")
        self.git(w, "add", ".")
        self.git(w, "commit", "-qm", "options")
        self.other_pushes("data/other.csv", "o4")
        rc, out = self.run_script(w)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.read_main("data/options/y.csv"), "y")
        self.assertEqual(self.read_main("data/other.csv"), "o4")


class Workflows(unittest.TestCase):
    def test_R4_data_workflows_use_retry(self):
        wdir = os.path.join(ROOT, ".github", "workflows")
        for name in ("cme_options.yml", "backfill-eur-real.yml", "backfill-jpy-real.yml"):
            with open(os.path.join(wdir, name), encoding="utf-8") as fh:
                y = fh.read()
            self.assertIn("bash scripts/tools/git_push_retry.sh", y, name)
            self.assertNotIn("\n            git push\n", y, name)
            self.assertNotIn("\n          git push\n", y, name)


if __name__ == "__main__":
    unittest.main()

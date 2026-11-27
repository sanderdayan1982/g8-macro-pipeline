"""t399u: el push privado de metals sale de data/alerts/private y conserva walls.

Fixtures sintéticos. No modifica el guardián ni otros tests.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "tools"))
sys.path.insert(0, str(ROOT / "vps"))
import private_data as PD  # noqa: E402
import metals_vps as M  # noqa: E402

# Valores inventados. Misma forma que una fuga de BRIEF_KEYS; no son de fuente.
HEAD_BRIEF = {
    "generated_utc": "2099-01-01T00:00:00Z",
    "as_of": {"rates": "2099-01-01"},
    "bullets": [{"sec": "QQ", "text": "synthetic"}],
}
FULL_BRIEF = {
    "generated_utc": "2099-01-01T00:00:00Z",
    "as_of": {"rates": "2099-01-01"},
    "bullets": [{"sec": "QQ", "text": "synthetic"}],
    "walls": [
        {
            "pair": "QQ/ZZ",
            "pin": 11,
            "call": 22,
            "put": 33,
            "strikes_c": 1,
            "strikes_p": 2,
            "front_oi": 3,
            "top": [{"oi": 4}, {"oi": 5}, {"oi": 6}],
        }
    ],
}
KEEP_BRIEF = {
    "generated_utc": "2099-01-02T00:00:00Z",
    "walls": [{"pair": "KEEP/ME", "pin": 8, "call": 8, "put": 8, "oi": 8}],
}
OTHER_BRIEF = {
    "generated_utc": "2099-01-03T00:00:00Z",
    "walls": [{"pair": "LOST/NO", "pin": 9, "call": 9, "put": 9, "oi": 9}],
}


def _dumps(doc):
    return json.dumps(doc, ensure_ascii=False)


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=t399u", "-c", "user.email=t399u@example.com", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    )


class RefreshOkKeepsPrivateWalls(unittest.TestCase):
    def test_public_brief_has_zero_keys_and_private_keeps_walls(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = os.path.join(tmp, "work")
            dest = os.path.join(tmp, "priv")
            os.makedirs(work)
            _write(os.path.join(work, "data/alerts/brief.json"), _dumps(FULL_BRIEF))
            _write(os.path.join(work, "data/alerts/private/brief.json"), _dumps(FULL_BRIEF))
            _write(os.path.join(work, "data/alerts/state.json"), _dumps({"walls": {"marker": "KEEP"}, "ok": 1}))
            _write(os.path.join(work, "data/alerts/private/state.json"), _dumps({"walls": {"marker": "KEEP"}, "ok": 1}))
            private_brief = Path(work, "data/alerts/private/brief.json").read_text(encoding="utf-8")
            remaining, _stashed = M.after_brief_snapshot(work, False, None)
            public = os.path.join(work, "data/alerts/brief.json")
            self.assertEqual(remaining, [])
            self.assertEqual(PD.forbidden_in_path(public), [])
            self.assertNotIn("walls", json.loads(Path(public).read_text(encoding="utf-8")))
            self.assertEqual(Path(work, "data/alerts/private/brief.json").read_text(encoding="utf-8"), private_brief)
            self.assertGreater(len(PD.forbidden_in_path(os.path.join(work, "data/alerts/private/brief.json"))), 0)
            copied = M.copy_private_alerts(work, dest)
            self.assertEqual(copied, ("data/alerts/brief.json", "data/alerts/state.json"))
            dest_brief = os.path.join(dest, "data/alerts/brief.json")
            self.assertIn("walls", json.loads(Path(dest_brief).read_text(encoding="utf-8")))
            self.assertEqual(Path(dest_brief).read_text(encoding="utf-8"), private_brief)
            self.assertNotEqual(Path(dest_brief).read_text(encoding="utf-8"), Path(public).read_text(encoding="utf-8"))


class RefreshFailKeepsPrivateWalls(unittest.TestCase):
    def test_failed_refresh_restores_public_and_keeps_private_walls(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = os.path.join(tmp, "work")
            dest = os.path.join(tmp, "priv")
            os.makedirs(work)
            subprocess.run(["git", "init", "-q", "-b", "main", work], check=True, capture_output=True)
            head = _dumps(HEAD_BRIEF)
            _write(os.path.join(work, "data/alerts/brief.json"), head)
            _git(work, "add", "data/alerts/brief.json")
            _git(work, "commit", "-qm", "head")
            full = _dumps(FULL_BRIEF)
            _write(os.path.join(work, "data/alerts/brief.json"), full)
            remaining, _stashed = M.after_brief_snapshot(work, True, None)
            public = os.path.join(work, "data/alerts/brief.json")
            private = os.path.join(work, "data/alerts/private/brief.json")
            self.assertEqual(remaining, [])
            self.assertEqual(Path(public).read_text(encoding="utf-8"), head)
            self.assertEqual(PD.forbidden_in_path(public), [])
            self.assertEqual(Path(private).read_text(encoding="utf-8"), full)
            self.assertIn("walls", json.loads(Path(private).read_text(encoding="utf-8")))
            copied = M.copy_private_alerts(work, dest)
            self.assertEqual(copied, ("data/alerts/brief.json",))
            self.assertEqual(Path(dest, "data/alerts/brief.json").read_text(encoding="utf-8"), full)
            self.assertNotEqual(
                Path(dest, "data/alerts/brief.json").read_text(encoding="utf-8"),
                Path(public).read_text(encoding="utf-8"),
            )

    def test_restore_does_not_overwrite_existing_private_brief(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = os.path.join(tmp, "work")
            os.makedirs(work)
            subprocess.run(["git", "init", "-q", "-b", "main", work], check=True, capture_output=True)
            head = _dumps(HEAD_BRIEF)
            _write(os.path.join(work, "data/alerts/brief.json"), head)
            _git(work, "add", "data/alerts/brief.json")
            _git(work, "commit", "-qm", "head")
            keep = _dumps(KEEP_BRIEF)
            _write(os.path.join(work, "data/alerts/private/brief.json"), keep)
            _write(os.path.join(work, "data/alerts/brief.json"), _dumps(OTHER_BRIEF))
            M.after_brief_snapshot(work, True, None)
            self.assertEqual(Path(work, "data/alerts/private/brief.json").read_text(encoding="utf-8"), keep)
            self.assertEqual(Path(work, "data/alerts/brief.json").read_text(encoding="utf-8"), head)
            self.assertEqual(PD.forbidden_in_path(os.path.join(work, "data/alerts/brief.json")), [])

    def test_copy_does_not_read_the_public_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = os.path.join(tmp, "work")
            dest = os.path.join(tmp, "priv")
            _write(os.path.join(work, "data/alerts/brief.json"), _dumps(FULL_BRIEF))
            copied = M.copy_private_alerts(work, dest)
            self.assertEqual(copied, ())
            self.assertFalse(os.path.exists(os.path.join(dest, "data/alerts/brief.json")))


class PublicTreeExcludesLicensed(unittest.TestCase):
    def test_public_tree_gets_no_figures_private_or_cme(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = os.path.join(tmp, "work")
            os.makedirs(work)
            subprocess.run(["git", "init", "-q", "-b", "main", work], check=True, capture_output=True)
            health = os.path.join(work, "data/_ingest/health.json")
            _write(health, '{"ok":1}\n')
            head = _dumps(HEAD_BRIEF)
            _write(os.path.join(work, "data/alerts/brief.json"), head)
            _git(work, "add", "data/_ingest/health.json", "data/alerts/brief.json")
            _git(work, "commit", "-qm", "head")
            _write(health, '{"ok":2}\n')
            _write(os.path.join(work, "data/alerts/brief.json"), _dumps(FULL_BRIEF))
            _write(os.path.join(work, "data/OPTIONS_SURFACE.json"), "synthetic-fixture\n")
            _write(os.path.join(work, "data/CME_OPTIONS_C1_shadow.json"), "synthetic-fixture\n")
            _write(os.path.join(work, "data/CME_FXVOL_shadow.json"), "synthetic-fixture\n")
            _write(os.path.join(work, "data/options/canonical/2099-01-01/QQ.csv"), "synthetic-fixture\n")
            _write(os.path.join(work, "data/alerts/private/brief.json"), _dumps(FULL_BRIEF))
            paths = M.prepare_public_push(work, True)
            self.assertFalse(os.path.exists(os.path.join(work, "data/OPTIONS_SURFACE.json")))
            self.assertFalse(os.path.exists(os.path.join(work, "data/CME_OPTIONS_C1_shadow.json")))
            self.assertFalse(os.path.exists(os.path.join(work, "data/CME_FXVOL_shadow.json")))
            self.assertFalse(os.path.exists(os.path.join(work, "data/options/canonical")))
            self.assertTrue(os.path.isfile(os.path.join(work, "data/alerts/private/brief.json")))
            for rel in paths:
                norm = rel.replace("\\", "/")
                base = norm.rsplit("/", 1)[-1]
                self.assertNotIn(base, M.PUBLIC_FIGURE_BASENAMES, rel)
                self.assertNotIn("data/alerts/private/", norm, rel)
                self.assertNotIn("options/canonical", norm, rel)
                self.assertFalse(base.startswith("CME_"), rel)
            self.assertNotIn("data/alerts/brief.json", paths)
            hostile = paths + (
                "data/OPTIONS_SURFACE.json",
                "data/options/canonical/2099-01-01/QQ.csv",
                "data/CME_OPTIONS_C1_shadow.json",
                "data/CME_FXVOL_shadow.json",
                "data/alerts/private/brief.json",
                "data/alerts/brief.json",
            )
            filtered = M.public_push_paths(hostile, work)
            for rel in filtered:
                norm = rel.replace("\\", "/")
                base = norm.rsplit("/", 1)[-1]
                self.assertNotIn(base, M.PUBLIC_FIGURE_BASENAMES)
                self.assertNotIn("data/alerts/private/", norm)
                self.assertNotIn("options/canonical", norm)
                self.assertFalse(base.startswith("CME_"))
            self.assertNotIn("data/alerts/brief.json", filtered)
            existing = [rel for rel in filtered if os.path.exists(os.path.join(work, rel))]
            if existing:
                _git(work, "add", "--", *existing)
            cached = subprocess.run(
                ["git", "diff", "--cached", "--name-only"], cwd=work, check=True, capture_output=True, text=True,
            ).stdout.split()
            for rel in cached:
                base = rel.rsplit("/", 1)[-1]
                self.assertNotIn(base, M.PUBLIC_FIGURE_BASENAMES)
                self.assertFalse(rel.startswith("data/alerts/private/"))
                self.assertNotIn("canonical", rel)
                self.assertFalse(base.startswith("CME_"))
            self.assertNotIn("data/alerts/brief.json", cached)
            self.assertIn("data/_ingest/health.json", cached)


class RunWiring(unittest.TestCase):
    def test_run_uses_private_copies_and_chain_rows(self):
        src = (ROOT / "vps/metals_vps.py").read_text(encoding="utf-8")
        run = src[src.index("def run("):]
        self.assertEqual(M.PULL_ROWS, "1,6,11,12")
        self.assertEqual(M.VERIFY_ROWS, "11,12")
        self.assertIn('"--rows", PULL_ROWS', run)
        self.assertIn('"--rows", VERIFY_ROWS', run)
        self.assertLess(run.index("after_brief_snapshot"), run.index("prepare_public_push"))
        self.assertLess(run.index("prepare_public_push"), run.index("P.commit_and_push"))
        self.assertLess(run.index("copy_private_alerts"), run.index('"--rows", VERIFY_ROWS'))
        self.assertIn('post.get("refresh_brief") != 0', run)
        self.assertNotIn("shutil.copyfile(os.path.join(workdir, rel)", src)
        self.assertIn('os.path.join(workdir, "data", "alerts", "private", name)', src)
        self.assertIn("scrub_licensed(workdir)", src[src.index("def cleanup"):])
        self.assertIn("data/options/canonical", M.LICENSED_UNTRACKED)
        self.assertIn("data/OPTIONS_SURFACE.json", M.LICENSED_UNTRACKED)


if __name__ == "__main__":
    unittest.main()

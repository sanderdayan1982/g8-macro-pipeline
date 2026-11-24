"""t354u/t357u: Factor USD al VPS, con sus dos turnos, clon y flock propios. Sin red."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vps"))
import usd_factor_vps as U


class UsdFactorVps(unittest.TestCase):
    def test_both_turns_in_one_timer(self):
        t = (ROOT / "vps/g8-usd-factor.timer").read_text()
        self.assertIn("OnCalendar=Mon..Fri 15:30:00", t)
        self.assertIn("OnCalendar=Mon..Fri 17:30:00", t)
        self.assertIn("Unit=g8-usd-factor.service", t)

    def test_own_clone_lock_and_protecthome(self):
        t = (ROOT / "vps/g8-usd-factor.service").read_text()
        self.assertIn("G8_USDF_WORKDIR=/srv/g8/prod/work/usd-factor", t)
        self.assertIn("G8_USDF_LOCK=/srv/g8/prod/state/locks/usd-factor.lock", t)
        self.assertIn("ProtectHome=yes", t)

    def test_github_only_manual_and_no_dispatch_slots(self):
        t = (ROOT / ".github/workflows/usd_factor.yml").read_text()
        self.assertIn("workflow_dispatch:", t)
        self.assertNotIn("  schedule:", t)
        ids = [s["id"] for s in json.loads((ROOT / "mac/dispatch_schedule.json").read_text())["slots"]]
        self.assertNotIn("usd_factor_1", ids)
        self.assertNotIn("usd_factor_2", ids)

    def test_public_allowlist_excludes_licensed_paths(self):
        for p in ("data/OPTIONS_SURFACE.json", "data/futures/canonical/DX.csv", "data/futures/canonical/6E.csv"):
            self.assertFalse(U.publishable(p), p)
        self.assertTrue(U.publishable("data/usd_factor/canonical.csv"))
        self.assertTrue(U.publishable("data/USD_FACTOR.json"))
        self.assertTrue(U.publishable("data/alerts/brief.json"))

    def test_strip_before_book_and_any_public_commit(self):
        t = (ROOT / "vps/usd_factor_vps.py").read_text()
        run = t[t.index("def run(workdir):"):]
        self.assertLess(run.index('"scripts/tools/strip_usd_factor_public.py"'), run.index('"scripts/book_risk.py"'))
        self.assertLess(run.index('"scripts/tools/strip_usd_factor_public.py"'), run.index("P.commit_and_push"))
        self.assertNotIn('"--commit"', run)

    def test_missing_secrets_fail_visibly_by_name(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "G8_DATA_READ_CI"):
                U.required_secrets()

    def test_cleanup_removes_licensed_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "data/futures/canonical").mkdir(parents=True)
            (p / "data/OPTIONS_SURFACE.json").write_text("{}")
            (p / "data/futures/canonical/6E.csv").write_text("Date,Value\n")
            U.remove_licensed(tmp)
            self.assertFalse((p / "data/OPTIONS_SURFACE.json").exists())
            self.assertFalse((p / "data/futures/canonical/6E.csv").exists())

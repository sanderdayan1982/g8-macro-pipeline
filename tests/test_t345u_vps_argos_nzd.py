"""t345u: timer soberano 10Y, evidencia B2 del VPS, argos al cambiar entradas, bak del code_lag."""
import json
import re
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
import sys
sys.path.insert(0, os.path.join(ROOT, "vps"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "mac"))

import health_derivatives as HD  # noqa: E402
import watch_vps_jobs as W  # noqa: E402
import push_nzd_to_github as PUSH  # noqa: E402


UNIT = os.path.join(ROOT, "vps", "g8-sovereign-10y-daily.service")
TIMER = os.path.join(ROOT, "vps", "g8-sovereign-10y-daily.timer")
JOB = os.path.join(ROOT, "vps", "sovereign_10y_daily.py")
GH_WF = os.path.join(ROOT, ".github", "workflows", "sovereign_10y_daily.yml")


class SovereignVpsTimer(unittest.TestCase):
    def test_timer_is_weekday_1400_wat_own_workdir_and_flock(self):
        self.assertTrue(os.path.isfile(UNIT))
        self.assertTrue(os.path.isfile(TIMER))
        self.assertTrue(os.path.isfile(JOB))
        timer = Path(TIMER).read_text(encoding="utf-8")
        unit = Path(UNIT).read_text(encoding="utf-8")
        job = Path(JOB).read_text(encoding="utf-8")
        self.assertIn("OnCalendar=Mon..Fri 13:00:00", timer)
        self.assertIn("G8_SOV10Y_WORKDIR=/srv/g8/prod/work/sovereign-10y", unit)
        self.assertIn("G8_SOV10Y_LOCK=/srv/g8/prod/state/locks/sovereign-10y.lock", unit)
        self.assertIn("job_flock", job)
        self.assertIn("data/DE_10Y_PAR_DAILY.csv", job)
        self.assertIn("data/ES_10Y_DAILY.csv", job)
        self.assertIn("data/ES_DE_10Y_SPREAD_DAILY.csv", job)
        self.assertIn("[vps-push]", job)
        self.assertIn("[skip netlify]", job)
        self.assertNotIn("cme_options", job)
        self.assertNotIn("g8_port_run", job)
        self.assertNotIn("dispatch_prod", job)

    def test_github_workflow_is_dispatch_only_cron_lives_on_vps(self):
        # t345u dejó este test exigiendo el cron de GitHub porque test_t339u lo pedía.
        # t346u, orden expresa Sander 23-nov: cron movido al VPS
        text = Path(GH_WF).read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", text)
        self.assertNotRegex(text, r"^\s*- cron:", re.M)
        self.assertNotIn("schedule:", text)


class CodeLagBak(unittest.TestCase):
    def test_bak_suffix_and_t342u_leftover_are_ignored(self):
        self.assertTrue(W.is_backup_name("dispatch_prod.py.bak"))
        self.assertTrue(W.is_backup_name("dispatch_prod.py.bak-t342u-20261123T082857Z"))
        self.assertFalse(W.is_backup_name("dispatch_prod.py"))
        lag, state = W.evaluate_code_lag(
            {"dispatch_prod.py.bak-t342u-20261123T082857Z": "aaa", "snb_daily.py": "bbb"},
            {"snb_daily.py": "bbb"},
            {},
            "2026-11-23T18:00:00Z",
        )
        self.assertEqual(lag, [])
        self.assertNotIn("dispatch_prod.py.bak-t342u-20261123T082857Z", state)


class CashPrefersVps(unittest.TestCase):
    def test_b2_path_prefers_vps_prod_over_mac_primary(self):
        tmp = Path(tempfile.mkdtemp())
        latest = tmp / "data" / "_ingest" / "latest"
        latest.mkdir(parents=True)
        (latest / "mac-primary__nzchf-tona.json").write_text("{}", encoding="utf-8")
        vps = latest / "vps-prod__nzchf-tona.json"
        vps.write_text("{}", encoding="utf-8")
        self.assertEqual(HD.b2_evidence_path(tmp), vps)
        vps.unlink()
        self.assertEqual(HD.b2_evidence_path(tmp), latest / "mac-primary__nzchf-tona.json")


class DownloadIdentityIgnoresClosedIibMtime(unittest.TestCase):
    def test_old_optional_iib_does_not_block_fresh_when_required_are_new(self):
        tmp = Path(tempfile.mkdtemp())
        data = tmp / "data"
        data.mkdir()
        required = ["NZD_CASH_ON.csv", "NZD_BOND_10Y.csv"]
        for name in required:
            (data / name).write_text("Date,Value\n2026-11-20,1\n", encoding="utf-8")
        iib = data / "NZD_IIB_2025.csv"
        iib.write_text("Date,Value\n2025-09-19,1\n", encoding="utf-8")
        old = 1_700_000_000
        os.utime(iib, (old, old))
        started = os.path.getmtime(data / required[0]) - 10
        prev = PUSH.LOCAL_DATA
        try:
            PUSH.LOCAL_DATA = str(data)
            files = required + ["NZD_IIB_2025.csv"]
            did, kind = PUSH.download_identity(
                "NZ-B2", files, "0", started, "run1", {}, mtime_files=required,
            )
            self.assertEqual(kind, "FRESH")
            self.assertTrue(did)
            did2, kind2 = PUSH.download_identity(
                "NZ-B2", files, "0", started, "run1", {},
            )
            self.assertEqual(kind2, "UNVERIFIED")
        finally:
            PUSH.LOCAL_DATA = prev


class ArgosRefresh(unittest.TestCase):
    def test_refresh_rewrites_json_only_when_an_input_changes(self):
        import argos_rates as AR
        src = Path(ROOT) / "tests" / "fixtures" / "t344u" / "argos"
        if not (src / "data").is_dir():
            self.skipTest("fixture t344u/argos ausente")
        tmp = Path(tempfile.mkdtemp())
        shutil.copytree(src / "data", tmp / "data")
        if (src / "argos_rates.json").is_file() and not (tmp / "data" / "argos_rates.json").is_file():
            shutil.copy2(src / "argos_rates.json", tmp / "data" / "argos_rates.json")
        clock = "2026-11-23T07:51:37Z"
        first = AR.refresh_if_inputs_changed(root=str(tmp), now=clock, data_dir=str(tmp / "data"))
        self.assertIn("changed", first)
        path = tmp / "data" / "argos_rates.json"
        self.assertTrue(path.is_file())
        before = path.read_text(encoding="utf-8")
        again = AR.refresh_if_inputs_changed(root=str(tmp), now="2026-11-23T08:00:00Z", data_dir=str(tmp / "data"))
        self.assertFalse(again["changed"])
        self.assertEqual(path.read_text(encoding="utf-8"), before)
        # tocar una entrada pintada
        cash = tmp / "data" / "NZD_CASH_ON.csv"
        if cash.is_file():
            cash.write_text(cash.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        else:
            self.skipTest("NZD_CASH_ON no está en la fixture")
        third = AR.refresh_if_inputs_changed(root=str(tmp), now="2026-11-23T08:05:00Z", data_dir=str(tmp / "data"))
        self.assertTrue(third["changed"], "un cambio en un CSV de entrada debe regenerar argos_rates.json")
        self.assertNotEqual(first["fingerprint"], third["fingerprint"])


if __name__ == "__main__":
    unittest.main()

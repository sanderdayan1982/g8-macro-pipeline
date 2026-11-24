"""t372u: pasada extra de ES 10Y a las 11:30 WAT; la de las 14:00 es respaldo.
orden Sander 24-nov t372u: timer y flock propios; la de las 14:00 solo se salta
ES si ya tiene el dato esperado del día; nunca dos publicaciones del mismo feed.
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "vps"))

import fetch_sovereign_10y_daily as F  # noqa: E402
import sovereign_10y_daily as D14  # noqa: E402
import sovereign_10y_es_1130 as D1130  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "t331u_b3"
UTC = timezone.utc
# Martes 24-nov 14:00 WAT: la obs. del 20-nov ya es exigible (T+2, 11:15 WAT).
NOW_1400 = datetime(2026, 11, 24, 13, 0, tzinfo=UTC)


def _dv(rows):
    lines = ["Date,Value,Source\n"]
    for day, val in rows:
        lines.append("%s,%.6f,test\n" % (day, val))
    return "".join(lines)


def _opener(de=None, es=None):
    de = de if de is not None else (FIX / "bbk_r10xx_zar.csv").read_bytes()
    es = es if es is not None else (FIX / "bde_ti_1_3_tail.csv").read_bytes()

    def opener(url):
        if "bundesbank" in url or "BBSIS" in url:
            return de
        if "bde.es" in url or "ti_1_3" in url:
            return es
        raise AssertionError("url inesperada")

    return opener


class UnitsAndTimer(unittest.TestCase):
    def test_1130_timer_own_clone_lock_and_protecthome(self):
        timer = (ROOT / "vps/g8-sovereign-10y-es-1130.timer").read_text(encoding="utf-8")
        unit = (ROOT / "vps/g8-sovereign-10y-es-1130.service").read_text(encoding="utf-8")
        job = (ROOT / "vps/sovereign_10y_es_1130.py").read_text(encoding="utf-8")
        self.assertIn("OnCalendar=Mon..Fri 10:30:00", timer)
        self.assertIn("g8-sovereign-10y-es-1130.service", timer)
        self.assertIn("G8_SOV10Y_ES1130_WORKDIR=/srv/g8/prod/work/sovereign-10y-es-1130", unit)
        self.assertIn("G8_SOV10Y_ES1130_LOCK=/srv/g8/prod/state/locks/sovereign-10y-es-1130.lock", unit)
        self.assertIn("ProtectHome=yes", unit)
        self.assertIn("job_flock", job)
        self.assertIn("[vps-push]", job)
        self.assertIn("[skip netlify]", job)
        self.assertIn("--no-push", job)
        self.assertNotIn("cme_options", job)
        self.assertNotIn("g8_port_run", job)
        self.assertNotIn("dispatch_prod", job)
        # no publica DE
        self.assertNotIn("data/DE_10Y_PAR_DAILY.csv", job)

    def test_1400_timer_unchanged(self):
        timer = (ROOT / "vps/g8-sovereign-10y-daily.timer").read_text(encoding="utf-8")
        self.assertIn("OnCalendar=Mon..Fri 13:00:00", timer)
        self.assertNotIn("10:30:00", timer)


class SkipAndBackup(unittest.TestCase):
    def test_skips_es_when_expected_already_present(self):
        with tempfile.TemporaryDirectory() as td:
            Path(td, "ES_10Y_DAILY.csv").write_text(_dv([("2026-11-20", 3.11)]), encoding="utf-8")
            has, ev = F.es_already_has_expected(td, now=NOW_1400, repo_root=str(ROOT))
            self.assertTrue(has)
            self.assertEqual(ev.get("have_max"), "2026-11-20")

            def opener(url):
                if "bde.es" in url or "ti_1_3" in url:
                    raise AssertionError("no debe pedir ES si ya tiene el dato esperado")
                if "bundesbank" in url or "BBSIS" in url:
                    return (FIX / "bbk_r10xx_zar.csv").read_bytes()
                raise AssertionError("url inesperada")

            before = Path(td, "ES_10Y_DAILY.csv").read_text(encoding="utf-8")
            # DE en disco no hace falta: se pide
            out = F.run(data_dir=td, opener=opener, fetch_de=True, fetch_es=False)
            self.assertFalse(out["fetch_es"])
            self.assertTrue(out["fetch_de"])
            self.assertEqual(Path(td, "ES_10Y_DAILY.csv").read_text(encoding="utf-8"), before)

    def test_fetches_es_when_expected_is_missing(self):
        with tempfile.TemporaryDirectory() as td:
            Path(td, "ES_10Y_DAILY.csv").write_text(_dv([("2026-11-18", 3.10)]), encoding="utf-8")
            has, ev = F.es_already_has_expected(td, now=NOW_1400, repo_root=str(ROOT))
            self.assertFalse(has)
            self.assertEqual(ev.get("have_max"), "2026-11-18")
            called = {"es": False}

            def opener(url):
                if "bde.es" in url or "ti_1_3" in url:
                    called["es"] = True
                    return (FIX / "bde_ti_1_3_tail.csv").read_bytes()
                if "bundesbank" in url or "BBSIS" in url:
                    return (FIX / "bbk_r10xx_zar.csv").read_bytes()
                raise AssertionError("url inesperada")

            out = F.run(data_dir=td, opener=opener, fetch_de=True, fetch_es=True)
            self.assertTrue(called["es"])
            self.assertTrue(out["fetch_es"])

    def test_1130_fetches_es_not_de(self):
        with tempfile.TemporaryDirectory() as td:
            Path(td, "DE_10Y_PAR_DAILY.csv").write_text(_dv([("2026-11-20", 2.52)]), encoding="utf-8")
            de_before = Path(td, "DE_10Y_PAR_DAILY.csv").read_text(encoding="utf-8")
            called = {"de": False, "es": False}

            def opener(url):
                if "bundesbank" in url or "BBSIS" in url:
                    called["de"] = True
                    return (FIX / "bbk_r10xx_zar.csv").read_bytes()
                if "bde.es" in url or "ti_1_3" in url:
                    called["es"] = True
                    return (FIX / "bde_ti_1_3_tail.csv").read_bytes()
                raise AssertionError("url inesperada")

            out = F.run(data_dir=td, opener=opener, fetch_de=False, fetch_es=True)
            self.assertFalse(called["de"])
            self.assertTrue(called["es"])
            self.assertFalse(out["fetch_de"])
            self.assertTrue(out["fetch_es"])
            self.assertEqual(Path(td, "DE_10Y_PAR_DAILY.csv").read_text(encoding="utf-8"), de_before)
            self.assertTrue(Path(td, "ES_10Y_DAILY.csv").exists())
            self.assertTrue(Path(td, "ES_DE_10Y_SPREAD_DAILY.csv").exists())

class JobNoPush(unittest.TestCase):
    def test_1130_no_push_does_not_commit(self):
        with tempfile.TemporaryDirectory() as td:
            Path(td, "data").mkdir()
            Path(td, "data/DE_10Y_PAR_DAILY.csv").write_text(_dv([("2026-11-20", 2.52)]), encoding="utf-8")
            with patch("vps_git_push.fetch_ff_only") as ff, patch("vps_git_push.commit_and_push") as push:
                # D1130.run imports P inside the function, so patch the module it will import
                import vps_git_push as P
                with patch.object(P, "fetch_ff_only") as ff2, patch.object(P, "commit_and_push") as push2:
                    out = D1130.run(td, token="x", no_push=True, opener=_opener())
                self.assertTrue(out["push"].get("no_push"))
                self.assertFalse(out["push"].get("changed"))
                ff2.assert_not_called()
                push2.assert_not_called()

    def test_1400_job_skips_es_and_no_push(self):
        with tempfile.TemporaryDirectory() as td:
            Path(td, "data").mkdir()
            Path(td, "data/ES_10Y_DAILY.csv").write_text(_dv([("2026-11-20", 3.11)]), encoding="utf-8")
            before = Path(td, "data/ES_10Y_DAILY.csv").read_text(encoding="utf-8")
            import vps_git_push as P
            with patch.object(P, "fetch_ff_only") as ff, patch.object(P, "commit_and_push") as push:
                out = D14.run(td, token="x", no_push=True, now=NOW_1400, opener=_opener(), repo_root=str(ROOT))
            self.assertTrue(out["fetch"]["es_skipped"])
            self.assertFalse(out["fetch"]["fetch_es"])
            self.assertEqual(Path(td, "data/ES_10Y_DAILY.csv").read_text(encoding="utf-8"), before)
            ff.assert_not_called()
            push.assert_not_called()


if __name__ == "__main__":
    unittest.main()

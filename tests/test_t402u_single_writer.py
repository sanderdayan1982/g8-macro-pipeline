"""t402u, orden Sander 26-nov: un solo escritor por ruta (TONA e ingest, argos_rates.json).

No modifica tests existentes. Si test_mac_shadow_workflow.py falla, es porque aún
exige el disparo por push y la línea de argos_rates.py en mac_nzd_chf_shadow.yml;
eso espera el comentario literal de Sander (véase el cuerpo de la PR).
"""
import os
import re
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts", "tools"))
sys.path.insert(0, os.path.join(ROOT, "vps"))
import refresh_feeds as RF  # noqa: E402
import vps_git_push as P  # noqa: E402

DAILY = os.path.join(ROOT, ".github", "workflows", "daily_update.yml")
INTRADAY = os.path.join(ROOT, ".github", "workflows", "intraday_fetch.yml")
SHADOW = os.path.join(ROOT, ".github", "workflows", "mac_nzd_chf_shadow.yml")
NZD = os.path.join(ROOT, "vps", "nzd_b2_daily.py")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TonaSingleWriter(unittest.TestCase):
    def test_refresh_feeds_does_not_collect_tona(self):
        jobs = [job for group in RF.GROUPS.values() for job in group]
        self.assertNotIn(("tona", "fetch_tona.py"), jobs)
        self.assertFalse(any(job[0] == "tona" or job[1] == "fetch_tona.py" for job in jobs))

    def test_daily_update_does_not_run_fetch_tona(self):
        text = _read(DAILY)
        self.assertNotIn("scripts/fetch_tona.py", text)
        self.assertNotIn("g8step.py --name tona", text)
        self.assertNotIn("id: tona", text)

    def test_vps_nzd_b2_is_the_tona_writer(self):
        src = _read(NZD)
        self.assertIn("fetch_tona.py", src)
        self.assertTrue(P.path_owned("data/TONA.csv", P.NZD_B2_OWNED))
        self.assertTrue(P.path_owned("data/_ingest/latest/actions__fetch_tona.json", P.NZD_B2_OWNED))
        self.assertTrue(P.path_owned("data/_ingest/evidence/TONA.csv.jsonl", P.NZD_B2_OWNED))
        self.assertTrue(P.path_owned("data/_ingest/runs/actions/fetch_tona/2026-11.jsonl", P.NZD_B2_OWNED))


class ArgosSingleWriter(unittest.TestCase):
    def test_intraday_and_daily_do_not_run_argos_rates(self):
        for path in (DAILY, INTRADAY):
            text = _read(path)
            self.assertNotIn("scripts/argos_rates.py", text, path)

    def test_shadow_workflow_has_no_push_trigger_and_does_not_commit_argos(self):
        text = _read(SHADOW)
        self.assertNotRegex(text, r"(?m)^\s+push:\s*$")
        self.assertNotIn("paths:", text)
        self.assertNotIn("scripts/argos_rates.py", text)
        self.assertNotIn("git commit", text)
        self.assertIn("workflow_dispatch", text)

    def test_vps_nzd_b2_is_the_argos_writer(self):
        src = _read(NZD)
        self.assertIn("refresh_if_inputs_changed", src)
        self.assertIn("data/argos_rates.json", src)
        self.assertIn("[skip netlify]", src)
        self.assertTrue(P.path_owned("data/argos_rates.json", P.NZD_B2_OWNED))

    def test_data_commits_in_touched_workflows_skip_netlify(self):
        for path in (DAILY, INTRADAY):
            text = _read(path)
            msgs = re.findall(r"git commit -m[^\n]+", text)
            self.assertTrue(msgs, path)
            for msg in msgs:
                self.assertIn("[skip netlify]", msg, msg)


if __name__ == "__main__":
    unittest.main()

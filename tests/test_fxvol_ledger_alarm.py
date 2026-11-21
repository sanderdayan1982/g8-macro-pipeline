"""t296u — el paso FXVOL del Daily deja su resultado en un registro que lee ingest_watch.

El paso corre después de «Registro de plazos» y de ingest_watch. Con continue-on-error un rc≠0
(TIMEOUT 124 incluido) no se veía. Ahora va a data/_ingest/latest/actions__job_daily_fxvol.json.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts", "tools"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import g8step  # noqa: E402

try:
    import yaml
except ImportError:
    yaml = None

WF = os.path.join(ROOT, ".github", "workflows", "daily_update.yml")
STEP = "cme_fxvol_shadow"


class FxvolLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log = os.path.join(self.tmp, "steps.jsonl")
        self.env = {"G8_STEP_LOG": self.log, "GITHUB_RUN_ID": "1"}

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _write(self, *recs):
        with open(self.log, "w", encoding="utf-8") as fh:
            for r in recs:
                fh.write(json.dumps(r) + "\n")

    def _ledger(self):
        g8step.ledger("daily_fxvol", env=self.env, root=self.tmp, only=[STEP])
        with open(os.path.join(self.tmp, "data", "_ingest", "latest", "actions__job_daily_fxvol.json")) as fh:
            return json.load(fh)

    def test_timeout_is_time_limited(self):
        self._write({"name": "s01b_final", "status": "FAILED", "rc": 1},
                    {"name": STEP, "status": g8step.TIMEOUT, "rc": 124, "detail": "cortado"})
        led = self._ledger()
        self.assertEqual([s["name"] for s in led["steps"]], [STEP])
        self.assertEqual(led["time_limited"], [{"name": STEP, "status": g8step.TIMEOUT, "detail": "cortado"}])
        self.assertEqual(led["failed"], [])

    def test_nonzero_rc_is_failed(self):
        self._write({"name": STEP, "status": g8step.FAILED, "rc": 1})
        led = self._ledger()
        self.assertEqual(led["failed"], [{"name": STEP, "rc": 1, "status": g8step.FAILED}])
        self.assertEqual(led["time_limited"], [])

    def test_ok_has_no_incident(self):
        self._write({"name": STEP, "status": g8step.OK, "rc": 0, "secs": 9.0})
        led = self._ledger()
        self.assertEqual(led["failed"], [])
        self.assertEqual(led["time_limited"], [])
        self.assertEqual(led["steps"][0]["rc"], 0)

    def test_missing_record_is_failed(self):
        self._write({"name": "otro", "status": g8step.OK, "rc": 0})
        led = self._ledger()
        self.assertEqual(led["failed"], [{"name": STEP, "rc": None, "status": g8step.FAILED}])

    def test_without_only_keeps_all_steps(self):
        self._write({"name": "a", "status": g8step.OK, "rc": 0}, {"name": STEP, "status": g8step.OK, "rc": 0})
        g8step.ledger("x", env=self.env, root=self.tmp)
        with open(os.path.join(self.tmp, "data", "_ingest", "latest", "actions__job_x.json")) as fh:
            self.assertEqual(len(json.load(fh)["steps"]), 2)


@unittest.skipIf(yaml is None, "pyyaml no disponible")
class FxvolLedgerWorkflow(unittest.TestCase):
    def test_step_follows_fxvol_and_keeps_cap_40(self):
        with open(WF, encoding="utf-8") as fh:
            steps = yaml.safe_load(fh)["jobs"]["fetch-data"]["steps"]
        names = [s.get("name", "") for s in steps]
        fx = names.index("Shadow CME FXVOL (IV30 / RR25 / BF25 / OI futuros)")
        self.assertIn("--cap 40 --min 5 -- python scripts/cme_fxvol_shadow.py", steps[fx]["run"])
        alarm = steps[fx + 1]
        self.assertEqual(alarm["name"], "Registro del paso FXVOL (alarma ingest_watch)")
        self.assertEqual(alarm["if"], "always()")
        self.assertEqual(alarm["run"], "python scripts/tools/g8step.py --ledger daily_fxvol --only cme_fxvol_shadow")
        self.assertLess(fx + 1, names.index("Commit updated data files"))


if __name__ == "__main__":
    unittest.main()

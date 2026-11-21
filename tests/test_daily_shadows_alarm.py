"""t297u (a) — las cinco sombras posteriores del Daily dejan su resultado en un registro que lee ingest_watch.

Corren después de «Registro de plazos» y de ingest_watch, con continue-on-error. Mismo patrón que FXVOL (t296u):
data/_ingest/latest/actions__job_daily_shadows.json.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts", "tools"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import g8step  # noqa: E402
import ingest_watch  # noqa: E402

try:
    import yaml
except ImportError:
    yaml = None

WF = os.path.join(ROOT, ".github", "workflows", "daily_update.yml")
JOB = "daily_shadows"
SHADOWS = {
    "Shadow CB_EVENT_48H": "cb_event_shadow",
    "Shadow CME options C2": "cme_options_shadow",
    "Shadow CHF nowcast": "chf_nowcast_shadow",
    "Shadow S01B A3": "s01b_shadow_a3",
    "Shadow CRD FRED (FAMILIAS-01 M1)": "credit_fred_shadow",
}
ALARM = "Registro de las sombras posteriores (alarma ingest_watch)"


class ShadowsLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log = os.path.join(self.tmp, "steps.jsonl")
        self.env = {"G8_STEP_LOG": self.log, "GITHUB_RUN_ID": "7"}

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _write(self, *recs):
        with open(self.log, "w", encoding="utf-8") as fh:
            for r in recs:
                fh.write(json.dumps(r) + "\n")

    def _ledger(self):
        g8step.ledger(JOB, env=self.env, root=self.tmp, only=list(SHADOWS.values()))
        with open(os.path.join(self.tmp, "data", "_ingest", "latest", "actions__job_%s.json" % JOB)) as fh:
            return json.load(fh)

    def _all_ok(self, **over):
        recs = []
        for n in SHADOWS.values():
            recs.append(over.get(n) or {"name": n, "status": g8step.OK, "rc": 0, "secs": 3.0})
        return recs

    def test_all_ok_no_incident(self):
        self._write({"name": "cme_fxvol_shadow", "status": g8step.TIMEOUT, "rc": 124}, *self._all_ok())
        led = self._ledger()
        self.assertEqual(sorted(s["name"] for s in led["steps"]), sorted(SHADOWS.values()))
        self.assertEqual(led["failed"], [])
        self.assertEqual(led["time_limited"], [])

    def test_each_failure_and_timeout_is_recorded(self):
        for n in SHADOWS.values():
            with self.subTest(step=n):
                self._write(*self._all_ok(**{n: {"name": n, "status": g8step.FAILED, "rc": 2}}))
                led = self._ledger()
                self.assertEqual(led["failed"], [{"name": n, "rc": 2, "status": g8step.FAILED}])
                self._write(*self._all_ok(**{n: {"name": n, "status": g8step.TIMEOUT, "rc": 124, "detail": "x"}}))
                led = self._ledger()
                self.assertEqual([x["name"] for x in led["time_limited"]], [n])

    def test_missing_line_is_failed(self):
        recs = [r for r in self._all_ok() if r["name"] != "s01b_shadow_a3"]
        self._write(*recs)
        led = self._ledger()
        self.assertEqual(led["failed"], [{"name": "s01b_shadow_a3", "rc": None, "status": g8step.FAILED}])

    def test_ingest_watch_raises_the_incident(self):
        self._write(*self._all_ok(chf_nowcast_shadow={"name": "chf_nowcast_shadow", "status": g8step.FAILED, "rc": 1}))
        self._ledger()
        os.makedirs(os.path.join(self.tmp, "sources"))
        shutil.copy(os.path.join(ROOT, "sources", "actions_jobs.csv"), os.path.join(self.tmp, "sources"))
        alerts, info = {}, {}
        ingest_watch.check_actions(self.tmp, datetime.now(timezone.utc), alerts, info)
        self.assertIn("actions:job:%s:step:chf_nowcast_shadow" % JOB, alerts)
        self.assertFalse([k for k in alerts if k.endswith(":silence")])


class ShadowsJobConfig(unittest.TestCase):
    def test_job_row_active(self):
        jobs = ingest_watch.load_jobs(ROOT)
        self.assertEqual(jobs["job_" + JOB]["status"], "ACTIVE")
        self.assertEqual(jobs["job_" + JOB]["max_silence_h"], 96.0)


@unittest.skipIf(yaml is None, "pyyaml no disponible")
class ShadowsLedgerWorkflow(unittest.TestCase):
    def test_step_after_the_five_shadows_and_before_commit(self):
        with open(WF, encoding="utf-8") as fh:
            steps = yaml.safe_load(fh)["jobs"]["fetch-data"]["steps"]
        names = [s.get("name", "") for s in steps]
        alarm = names.index(ALARM)
        self.assertLess(alarm, names.index("Commit updated data files"))
        run = steps[alarm]["run"]
        self.assertTrue(run.startswith("python scripts/tools/g8step.py --ledger %s " % JOB))
        self.assertEqual(steps[alarm]["if"], "always()")
        self.assertTrue(steps[alarm]["continue-on-error"])
        for title, name in SHADOWS.items():
            i = names.index(title)
            self.assertLess(i, alarm)
            self.assertTrue(steps[i]["continue-on-error"])
            self.assertIn("--name %s --phase post --cap 40 --min 5" % name, steps[i]["run"])
            self.assertIn("--only %s" % name, run)
        self.assertLess(names.index("Registro de plazos de la fase de descarga"), names.index("Shadow CB_EVENT_48H"))


if __name__ == "__main__":
    unittest.main()

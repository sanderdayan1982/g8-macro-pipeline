"""t297u (c) — Dashboard alerts, Twin-test POS, feed_recovery y votes_ingest: un fallo con continue-on-error
queda en un registro que lee ingest_watch (data/_ingest/latest/actions__job_<job>.json). No tumba el job.
"""
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts", "tools"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import step_alarm  # noqa: E402
import ingest_watch  # noqa: E402

try:
    import yaml
except ImportError:
    yaml = None

ALARM = "Registro de pasos con continue-on-error (alarma ingest_watch)"
# workflow → (job del registro, {nombre del registro: título del paso vigilado})
WATCHED = {
    "g8_port_run": ("g8_port_steps", {"twin_test_pos": "Twin-test POS",
                                      "dashboard_alerts_g8_port": "Dashboard alerts (refresh brief.json)"}),
    "usd_factor": ("usd_factor_steps", {"dashboard_alerts_usd_factor": "Dashboard alerts (Telegram on change)"}),
    "feed_recovery": ("recovery_steps", {
        "recovery_feeds": "Refresh official daily feeds",
        "recovery_nzd_b2": "Attempt current RBNZ B2 without historical downloads",
        "recovery_nz_linker": "Refresh daily NZ linker calculation",
        "recovery_ecb_factor": "Refresh ECB daily currency observations and existing factor",
        "recovery_ctf": "Recover latest elapsed CTF session without historical backfill"}),
    "votes_ingest": ("votes_steps", {"votes_ingest": "Ingest votes"}),
}


def _steps(wf):
    with open(os.path.join(ROOT, ".github", "workflows", wf + ".yml"), encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    (job,) = doc["jobs"].values()
    return job["steps"]


class Outcomes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = {"G8_STEP_LOG": os.path.join(self.tmp, "s.jsonl"), "GITHUB_RUN_ID": "3"}

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _publish(self, pairs, job="x_steps"):
        names = step_alarm.record_outcomes(pairs, env=self.env)
        step_alarm.publish(job, names, env=self.env, root=self.tmp)
        with open(os.path.join(self.tmp, "data", "_ingest", "latest", "actions__job_%s.json" % job)) as fh:
            return json.load(fh)

    def test_mapping(self):
        led = self._publish(["a=success", "b=skipped", "c=failure", "d=cancelled", "e="])
        st = {s["name"]: s["status"] for s in led["steps"]}
        self.assertEqual(st, {"a": "OK", "b": "OK", "c": "FAILED", "d": "FAILED", "e": "FAILED"})
        self.assertEqual(sorted(f["name"] for f in led["failed"]), ["c", "d", "e"])

    def test_failure_reaches_ingest_watch_and_ok_is_quiet(self):
        self._publish(["votes_ingest=failure"], job="votes_steps")
        os.makedirs(os.path.join(self.tmp, "sources"))
        shutil.copy(os.path.join(ROOT, "sources", "actions_jobs.csv"), os.path.join(self.tmp, "sources"))
        alerts = {}
        ingest_watch.check_actions(self.tmp, datetime.now(timezone.utc), alerts, {})
        self.assertIn("actions:job:votes_steps:step:votes_ingest", alerts)
        open(self.env["G8_STEP_LOG"], "w").close()
        self._publish(["votes_ingest=success"], job="votes_steps")
        alerts = {}
        ingest_watch.check_actions(self.tmp, datetime.now(timezone.utc), alerts, {})
        self.assertFalse([k for k in alerts if "votes_steps" in k])

    def test_cli_requires_names(self):
        with self.assertRaises(SystemExit):
            step_alarm.main(["publish", "x"])

    def test_job_rows(self):
        jobs = ingest_watch.load_jobs(ROOT)
        for job, _ in list(WATCHED.values()) + [("daily_dashboard", None)]:
            self.assertEqual(jobs["job_" + job]["status"], "ACTIVE")


@unittest.skipIf(yaml is None, "pyyaml no disponible")
class Workflows(unittest.TestCase):
    def test_alarm_step_maps_each_watched_step_by_id(self):
        for wf, (job, watched) in WATCHED.items():
            with self.subTest(wf=wf):
                steps = _steps(wf)
                names = [s.get("name") for s in steps]
                alarm = steps[names.index(ALARM)]
                self.assertEqual(alarm["if"], "always()")
                self.assertTrue(alarm["continue-on-error"])
                self.assertIn("step_alarm.py publish %s " % job, alarm["run"])
                self.assertTrue(alarm["run"].rstrip().endswith("--commit"))
                env = alarm["env"]
                for rname, title in watched.items():
                    st = steps[names.index(title)]
                    self.assertTrue(st["continue-on-error"], title)      # sigue sin tumbar el job
                    m = re.search(r'--outcome "%s=\$(OUT_\d+)"' % re.escape(rname), alarm["run"])
                    self.assertIsNotNone(m, rname)
                    self.assertEqual(env[m.group(1)], "${{ steps.%s.outcome }}" % st["id"])
                    self.assertLess(names.index(title), names.index(ALARM))

    def test_daily_dashboard_ledger_before_commit(self):
        steps = _steps("daily_update")
        names = [s.get("name") for s in steps]
        i = names.index("Registro de Dashboard alerts (alarma ingest_watch)")
        self.assertEqual(steps[i]["run"], "python scripts/tools/g8step.py --ledger daily_dashboard --only dashboard_alerts")
        self.assertEqual(steps[i]["if"], "always()")
        self.assertLess(names.index("Dashboard alerts (Telegram on change)"), i)
        self.assertLess(i, names.index("Commit updated data files"))
        self.assertIn("--name dashboard_alerts ", steps[names.index("Dashboard alerts (Telegram on change)")]["run"])


if __name__ == "__main__":
    unittest.main()

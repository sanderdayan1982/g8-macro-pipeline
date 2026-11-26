"""ORDEN RELOJES-01 A2 (Sander 14-nov-2026 21:47 WAT).

Monday 21:05 UTC and Tuesday 09:05 UTC collect the CFTC COT when Friday is a
US holiday. The collector leaves the file untouched unless the report date is
strictly newer. The weekly metals MDP runs only after that write, and only on
those two passes.
"""
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import pos_g8_cot_collector as C  # noqa: E402


def _doc(day, generated="2026-11-16T21:05:00+00:00", lf_z=0.2):
    row = {"ccy": "EUR", "report_date": day, "lf_z": lf_z}
    metal = {"ccy": "XAU", "report_date": day, "mm_z": -0.4}
    return {
        "report_date": day,
        "generated_utc": generated,
        "currencies": [row],
        "metals": [metal],
    }


class MondayTuesdayCrons(unittest.TestCase):
    def test_monday_and_tuesday_crons_are_present(self):
        yml = (ROOT / ".github" / "workflows" / "g8_port_run.yml").read_text(encoding="utf-8")
        # t402u · orden expresa de Sander 26-nov-2026: g8_port_run pasa al timer g8-port-run del VPS; el cron de GitHub se retira y la comprobación apunta al timer.
        timer = (ROOT / "vps" / "g8-port-run.timer").read_text(encoding="utf-8")
        for oncal in (
            "OnCalendar=Fri *-*-* 21:05:00",
            "OnCalendar=Sat *-*-* 09:05:00",
            "OnCalendar=Mon *-*-* 21:05:00",
            "OnCalendar=Tue *-*-* 09:05:00",
        ):
            self.assertIn(oncal, timer)
        self.assertIn("20:30 UTC in winter", yml)
        self.assertIn("report as of Tue", yml)
        self.assertIn("--metals-after-cot", yml)
        self.assertNotIn("metals_fairvalue_g8.py", yml)
        self.assertEqual(
            {line for line in timer.splitlines() if line.startswith("OnCalendar=")},
            {
                "OnCalendar=Mon *-*-* 21:05:00",
                "OnCalendar=Tue *-*-* 09:05:00",
                "OnCalendar=Fri *-*-* 21:05:00",
                "OnCalendar=Sat *-*-* 09:05:00",
            },
        )
        with open(ROOT / "sources" / "schedules.csv", encoding="utf-8") as fh:
            rows = {r["pass_id"]: r for r in csv.DictReader(fh)}
        self.assertEqual(rows["COT_MON"]["cron_utc"], "5 21 * * 1")
        self.assertEqual(rows["COT_TUE"]["cron_utc"], "5 9 * * 2")
        self.assertEqual(rows["COT1"]["cron_utc"], "5 21 * * 5")
        self.assertEqual(rows["COT2"]["cron_utc"], "5 9 * * 6")


class CollectorWritesNothingWhenDateIsNotNew(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "pos_g8_cot.json"
        self.original = json.dumps(_doc("2026-11-10", generated="2026-11-14T21:05:00+00:00", lf_z=0.1), indent=2)
        self.path.write_text(self.original)

    def tearDown(self):
        self.tmp.cleanup()

    def test_same_report_date_does_not_write(self):
        doc = _doc("2026-11-10", generated="2026-11-16T21:06:00+00:00", lf_z=9.9)
        self.assertFalse(C.commit_cot(self.path, doc))
        self.assertEqual(self.path.read_text(), self.original)

    def test_older_report_date_does_not_write(self):
        doc = _doc("2026-11-03", generated="2026-11-16T21:06:00+00:00", lf_z=9.9)
        self.assertFalse(C.commit_cot(self.path, doc))
        self.assertEqual(self.path.read_text(), self.original)

    def test_newer_report_date_writes(self):
        doc = _doc("2026-11-17", generated="2026-11-16T21:06:00+00:00", lf_z=0.4)
        self.assertTrue(C.commit_cot(self.path, doc))
        written = json.loads(self.path.read_text())
        self.assertEqual(written["report_date"], "2026-11-17")
        self.assertEqual(written["generated_utc"], "2026-11-16T21:06:00+00:00")
        self.assertEqual(written["currencies"][0]["lf_z"], 0.4)

    def test_missing_file_is_written(self):
        fresh = Path(self.tmp.name) / "new.json"
        doc = _doc("2026-11-17")
        self.assertTrue(C.commit_cot(fresh, doc))
        self.assertEqual(json.loads(fresh.read_text())["report_date"], "2026-11-17")

    def test_corrupt_file_is_not_replaced(self):
        self.path.write_text("{not json")
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            C.commit_cot(self.path, _doc("2026-11-17"))
        self.assertEqual(self.path.read_bytes(), before)

    def test_mixed_dates_are_not_written(self):
        doc = _doc("2026-11-17")
        doc["metals"][0]["report_date"] = "2026-11-10"
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            C.commit_cot(self.path, doc)
        self.assertEqual(self.path.read_bytes(), before)

    def test_missing_row_date_is_not_written(self):
        doc = _doc("2026-11-17")
        doc["currencies"][0]["report_date"] = None
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            C.commit_cot(self.path, doc)
        self.assertEqual(self.path.read_bytes(), before)


class MetalsOnlyAfterNewMondayCot(unittest.TestCase):
    def test_recompute_only_after_a_new_cot_on_monday_or_tuesday(self):
        calls = []

        def run():
            calls.append("run")

        self.assertTrue(C.recompute_metals_if_due("5 21 * * 1", "true", runner=run))
        self.assertTrue(C.recompute_metals_if_due("5 9 * * 2", True, runner=run))
        self.assertEqual(calls, ["run", "run"])
        calls.clear()
        for schedule, wrote in (
            ("5 21 * * 5", "true"),
            ("5 9 * * 6", "true"),
            ("5 21 * * 1", "false"),
            ("5 9 * * 2", "false"),
            ("5 21 * * 1", ""),
            ("", "true"),
        ):
            self.assertFalse(C.recompute_metals_if_due(schedule, wrote, runner=run), (schedule, wrote))
        self.assertEqual(calls, [])

    def test_skipped_recompute_does_not_touch_generated_at(self):
        folder = tempfile.TemporaryDirectory()
        try:
            path = Path(folder.name) / "MFV_G8_state.json"
            body = '{"generated": "2026-11-14T07:00:00", "generated_at": "2026-11-14T07:00:00Z"}\n'
            path.write_text(body)

            def run():
                path.write_text('{"generated": "CHANGED", "generated_at": "CHANGED"}\n')

            self.assertFalse(C.recompute_metals_if_due("5 21 * * 1", "false", runner=run))
            self.assertFalse(C.recompute_metals_if_due("5 21 * * 5", "true", runner=run))
            self.assertEqual(path.read_text(), body)
        finally:
            folder.cleanup()

    def test_cli_launches_metals_only_for_a_new_monday_or_tuesday_report(self):
        with patch("pos_g8_cot_collector.subprocess.call", return_value=0) as call:
            self.assertEqual(C.main(["--metals-after-cot", "--schedule", "5 21 * * 1", "--cot-wrote", "true"]), 0)
            self.assertEqual(C.main(["--metals-after-cot", "--schedule", "5 9 * * 2", "--cot-wrote", "true"]), 0)
            self.assertEqual(call.call_count, 2)
            for args, _kwargs in call.call_args_list:
                cmd = args[0]
                self.assertTrue(cmd[-1].endswith("metals_fairvalue_g8.py"), cmd)
                self.assertIn("g8step.py", cmd[1])
            call.reset_mock()
            self.assertEqual(C.main(["--metals-after-cot", "--schedule", "5 21 * * 5", "--cot-wrote", "true"]), 0)
            self.assertEqual(C.main(["--metals-after-cot", "--schedule", "5 21 * * 1", "--cot-wrote", "false"]), 0)
            self.assertEqual(C.main(["--metals-after-cot", "--schedule", "", "--cot-wrote", "true"]), 0)
            call.assert_not_called()


if __name__ == "__main__":
    unittest.main()

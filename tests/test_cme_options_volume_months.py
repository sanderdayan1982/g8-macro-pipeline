"""t192u — volume_only, one calendar month at a time. Synthetic rows, no network.

Does not edit an existing test. Full-mode settlement CSVs stay sentinels.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import cme_options_backfill as B  # noqa: E402
import cme_options_collector as C  # noqa: E402
import test_cme_options_volume_backfill as V  # noqa: E402

WF = os.path.join(ROOT, ".github", "workflows", "cme_options_backfill.yml")
KEY = V.KEY


def _query(url):
    return V._query(url)


def _endpoint(url):
    return V._endpoint(url)


class _Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.canon = os.path.join(self.tmp, "canonical")
        self.full_report = os.path.join(self.tmp, "backfill_report.json")
        self.vol = os.path.join(self.tmp, "sibling")
        self.vol_report = os.path.join(self.tmp, "volume_backfill_report.json")
        self.raw = os.path.join(self.tmp, "raw")
        os.makedirs(os.path.join(self.canon, "2026-07-01"))
        with open(os.path.join(self.canon, "2026-07-01", "EUU.csv"), "wb") as fh:
            fh.write(V.CANON_SENTINEL)
        with open(self.full_report, "wb") as fh:
            fh.write(V.FULL_REPORT_SENTINEL)
        self.summary = os.path.join(self.tmp, "step_summary.md")
        self._prev = os.environ.get("GITHUB_STEP_SUMMARY")
        os.environ["GITHUB_STEP_SUMMARY"] = self.summary
        self.addCleanup(self._restore)

    def _restore(self):
        if self._prev is None:
            os.environ.pop("GITHUB_STEP_SUMMARY", None)
        else:
            os.environ["GITHUB_STEP_SUMMARY"] = self._prev

    def _summary(self):
        with open(self.summary, encoding="utf-8") as fh:
            return fh.read()

    def _sentinels(self):
        with open(os.path.join(self.canon, "2026-07-01", "EUU.csv"), "rb") as fh:
            self.assertEqual(fh.read(), V.CANON_SENTINEL)
        with open(self.full_report, "rb") as fh:
            self.assertEqual(fh.read(), V.FULL_REPORT_SENTINEL)

    def _run(self, http, start, end, download=True, env=None):
        argv = ["--mode", "volume_only", "--volume-start", start, "--volume-end", end]
        if download:
            argv.append("--download")
        merged = {"DATABENTO_API_KEY": KEY}
        if env:
            merged.update(env)
        return B.main(
            argv, http=http, env=merged, out_dir=self.canon, report_path=self.full_report,
            volume_dir=self.vol, volume_report=self.vol_report, raw_dir=self.raw)

    def _seed(self, body):
        with open(self.vol_report, "w", encoding="utf-8") as fh:
            json.dump(body, fh)

    def _report(self):
        with open(self.vol_report, encoding="utf-8") as fh:
            return json.load(fh)


def _http(calls, costs, stats, defs, range_status=None):
    """costs maps a window start to {schema: bytes}. range_status(query) may return 504."""
    def http(method, url, headers, body):
        calls.append(url)
        endpoint = _endpoint(url)
        query = _query(url)
        if endpoint == "timeseries.get_range":
            status = 200 if range_status is None else range_status(query)
            if status == 504:
                return 504, {}, b"gateway timeout"
            schema = query["schema"][0]
            parent = query["symbols"][0]
            if schema == "statistics":
                rows = stats if parent == "EUU.OPT" else []
                return 200, {}, V._csv_bytes(rows, C.STATS_FINGERPRINT)
            if schema == "definition":
                rows = defs if parent == "EUU.OPT" else []
                return 200, {}, V._csv_bytes(rows, V.DEF_FIELDS)
            raise AssertionError(schema)
        if endpoint == "metadata.get_cost":
            schema = query["schema"][0]
            start = query["start"][0]
            table = costs[start]
            if schema not in table:
                raise AssertionError(schema)
            return 200, {}, table[schema]
        if endpoint in ("metadata.get_record_count", "metadata.get_billable_size"):
            return 200, {}, b"4"
        raise AssertionError(endpoint)
    return http


def _flat(amount):
    return {"statistics": amount, "definition": b"0"}


def _windows(calls):
    out = []
    for url in calls:
        query = _query(url)
        out.append((_endpoint(url), query["start"][0], query["end"][0]))
    return out


class MonthOrder(unittest.TestCase):
    def test_list_volume_months_is_clipped_and_needs_no_key(self):
        def http(*_a):
            raise AssertionError("Databento was called")

        rc = B.main(
            ["--list-volume-months", "--volume-start", "2026-07-01", "--volume-end", "2026-11-15"],
            http=http, env={})
        self.assertEqual(rc, 0)
        # main prints; re-call the lister so the assertion does not depend on stdout capture.
        self.assertEqual(list(B.iter_volume_months(B.date(2026, 7, 1), B.date(2026, 11, 15))), [
            ("2026-07", B.date(2026, 7, 1), B.date(2026, 8, 1)),
            ("2026-08", B.date(2026, 8, 1), B.date(2026, 9, 1)),
            ("2026-09", B.date(2026, 9, 1), B.date(2026, 10, 1)),
            ("2026-10", B.date(2026, 10, 1), B.date(2026, 11, 1)),
            ("2026-11", B.date(2026, 11, 1), B.date(2026, 11, 15)),
        ])
        self.assertEqual(list(B.iter_volume_months(B.date(2026, 7, 15), B.date(2026, 8, 10))), [
            ("2026-07", B.date(2026, 7, 15), B.date(2026, 8, 1)),
            ("2026-08", B.date(2026, 8, 1), B.date(2026, 8, 10)),
        ])
        self.assertEqual(list(B.iter_chunks())[0], "2024-06")
        self.assertEqual(list(B.iter_chunks())[-1], "2026-05")
        self.assertNotIn("2026-07", list(B.iter_chunks()))


class OrderingAndResume(_Case):
    def test_months_are_quoted_and_downloaded_in_order(self):
        calls = []
        stats, defs = V._fixture_rows()
        costs = {
            "2026-07-01": _flat(b"1.10"),
            "2026-08-01": _flat(b"1.20"),
            "2026-09-01": _flat(b"1.30"),
        }
        rc = self._run(_http(calls, costs, stats, defs), "2026-07-01", "2026-09-15", download=True)
        self.assertEqual(rc, 0)
        windows = _windows(calls)
        july = [i for i, row in enumerate(windows) if row[1] == "2026-07-01"]
        august = [i for i, row in enumerate(windows) if row[1] == "2026-08-01"]
        september = [i for i, row in enumerate(windows) if row[1] == "2026-09-01"]
        self.assertTrue(july and august and september)
        self.assertLess(max(july), min(august))
        self.assertLess(max(august), min(september))
        self.assertEqual({row[2] for row in windows if row[1] == "2026-09-01"}, {"2026-09-15"})
        self.assertNotIn("2026-10-01", [row[1] for row in windows])
        for start in ("2026-07-01", "2026-08-01", "2026-09-01"):
            meta = [i for i in range(len(windows)) if windows[i][1] == start and windows[i][0].startswith("metadata.")]
            ranges = [i for i in range(len(windows)) if windows[i][1] == start and windows[i][0] == "timeseries.get_range"]
            self.assertTrue(meta and ranges)
            self.assertLess(max(meta), min(ranges))
        report = self._report()
        self.assertNotIn("chunks", report)
        self.assertEqual(report["spent_usd"], "3.60")
        by_month = {row["month"]: row for row in report["months"]}
        self.assertFalse(by_month["2026-07"]["partial"])
        self.assertEqual(by_month["2026-07"]["end_exclusive"], "2026-08-01")
        self.assertTrue(by_month["2026-09"]["partial"])
        self.assertEqual(by_month["2026-09"]["end_exclusive"], "2026-09-15")
        self.assertTrue(by_month["2026-09"]["complete"])
        self._sentinels()
        text = self._summary()
        self.assertIn("get_cost USD: 3.60", text)
        self.assertIn("month 2026-07", text)
        self.assertIn("month 2026-08", text)
        self.assertIn("month 2026-09", text)

    def test_complete_month_is_not_requested_again(self):
        self._seed({
            "spent_usd": "1.10",
            "months": [{
                "month": "2026-07", "start": "2026-07-01", "end_exclusive": "2026-08-01",
                "complete": True, "partial": False, "get_cost": "1.10", "downloaded": True,
            }],
        })
        calls = []
        stats, defs = V._fixture_rows()
        costs = {"2026-08-01": _flat(b"0.40")}
        rc = self._run(_http(calls, costs, stats, defs), "2026-07-01", "2026-09-01", download=True)
        self.assertEqual(rc, 0)
        self.assertFalse(any(_query(url)["start"][0] == "2026-07-01" for url in calls))
        self.assertTrue(any(_query(url)["start"][0] == "2026-08-01" for url in calls))
        self.assertIn("not requested", self._summary())
        self.assertEqual(self._report()["spent_usd"], "1.50")

    def test_partial_month_rerun_requests_only_the_tail(self):
        self._seed({
            "spent_usd": "0.20",
            "months": [{
                "month": "2026-11", "start": "2026-11-01", "end_exclusive": "2026-11-15",
                "complete": True, "partial": True, "get_cost": "0.20", "downloaded": True,
            }],
        })
        calls = []
        stats, defs = V._fixture_rows()
        same = self._run(_http(calls, {}, stats, defs), "2026-11-01", "2026-11-15", download=True)
        self.assertEqual(same, 0)
        self.assertEqual(calls, [])
        calls.clear()
        costs = {"2026-11-15": _flat(b"0.10")}
        rc = self._run(_http(calls, costs, stats, defs), "2026-11-01", "2026-11-20", download=True)
        self.assertEqual(rc, 0)
        self.assertTrue(calls)
        self.assertTrue(all(_query(url)["start"][0] == "2026-11-15" for url in calls))
        self.assertTrue(all(_query(url)["end"][0] == "2026-11-20" for url in calls))
        row = self._report()["months"][0]
        self.assertEqual(row["start"], "2026-11-01")
        self.assertEqual(row["end_exclusive"], "2026-11-20")
        self.assertTrue(row["partial"])
        self.assertTrue(row["complete"])
        self.assertEqual(self._report()["spent_usd"], "0.30")
        self._sentinels()

    def test_dry_run_quotes_every_month_and_the_total_and_writes_nothing(self):
        calls = []
        stats, defs = V._fixture_rows()
        costs = {
            "2026-07-01": _flat(b"1.10"),
            "2026-08-01": _flat(b"1.20"),
            "2026-09-01": _flat(b"1.30"),
        }
        before = set(os.listdir(self.tmp))
        rc = self._run(_http(calls, costs, stats, defs), "2026-07-01", "2026-09-15", download=False)
        self.assertEqual(rc, 0)
        self.assertFalse(any("timeseries" in url for url in calls))
        self.assertEqual(set(os.listdir(self.tmp)), before | {"step_summary.md"})
        self.assertFalse(os.path.exists(self.vol_report))
        text = self._summary()
        self.assertIn("1.10", text)
        self.assertIn("1.20", text)
        self.assertIn("1.30", text)
        self.assertIn("3.60", text)
        self.assertNotIn("HARD CAP", text)
        self.assertNotIn(KEY, text)


class CapAndTimeout(_Case):
    def test_cap_stops_before_the_next_month_and_keeps_earlier_ones(self):
        calls = []
        stats, defs = V._fixture_rows()
        costs = {
            "2026-07-01": _flat(b"2.00"),
            "2026-08-01": _flat(b"2.00"),
            "2026-09-01": _flat(b"2.01"),
        }
        rc = self._run(_http(calls, costs, stats, defs), "2026-07-01", "2026-10-01", download=True)
        self.assertEqual(rc, 3)
        self.assertTrue(any(row[0] == "timeseries.get_range" and row[1] == "2026-07-01"
                            for row in _windows(calls)))
        self.assertTrue(any(row[0] == "timeseries.get_range" and row[1] == "2026-08-01"
                            for row in _windows(calls)))
        self.assertTrue(any(row[0] == "metadata.get_cost" and row[1] == "2026-09-01"
                            for row in _windows(calls)))
        self.assertFalse(any(row[0] == "timeseries.get_range" and row[1] == "2026-09-01"
                             for row in _windows(calls)))
        self.assertTrue(os.path.isdir(os.path.join(self.vol, "2026-07-01")))
        self.assertFalse(os.path.isdir(os.path.join(self.vol, "2026-09-01")))
        report = self._report()
        self.assertEqual(report["spent_usd"], "4.00")
        self.assertIn("HARD CAP", report["reason"])
        self.assertIn("2026-09", report["reason"])
        self.assertIn("6.00", report["reason"])
        by_month = {row["month"]: row for row in report["months"]}
        self.assertTrue(by_month["2026-07"]["complete"])
        self.assertTrue(by_month["2026-08"]["complete"])
        self.assertFalse(by_month["2026-09"]["complete"])
        self._sentinels()

    def test_recorded_spend_plus_the_next_quote_stops_with_nothing_downloaded(self):
        self._seed({
            "spent_usd": "5.50",
            "months": [{
                "month": "2026-07", "start": "2026-07-01", "end_exclusive": "2026-08-01",
                "complete": True, "partial": False, "get_cost": "5.50", "downloaded": True,
            }],
        })
        calls = []
        stats, defs = V._fixture_rows()
        rc = self._run(
            _http(calls, {"2026-08-01": _flat(b"0.51")}, stats, defs),
            "2026-07-01", "2026-09-01", download=True)
        self.assertEqual(rc, 3)
        self.assertFalse(any("timeseries" in url for url in calls))
        self.assertFalse(any(_query(url)["start"][0] == "2026-07-01" for url in calls))
        self.assertEqual(self._report()["spent_usd"], "5.50")
        self.assertIn("HARD CAP", self._report()["reason"])
        july = next(row for row in self._report()["months"] if row["month"] == "2026-07")
        self.assertTrue(july["complete"])
        self.assertEqual(july["end_exclusive"], "2026-08-01")

    def test_exact_cap_still_downloads(self):
        calls = []
        stats, defs = V._fixture_rows()
        costs = {"2026-07-01": _flat(b"4.00"), "2026-08-01": _flat(b"2.00")}
        rc = self._run(_http(calls, costs, stats, defs), "2026-07-01", "2026-09-01", download=True)
        self.assertEqual(rc, 0)
        self.assertEqual(self._report()["spent_usd"], "6.00")
        self.assertNotIn("HARD CAP", self._summary())
        self.assertTrue(any(row[1] == "2026-08-01" and row[0] == "timeseries.get_range"
                            for row in _windows(calls)))

    def test_dry_run_quotes_past_a_running_total_then_stops_on_the_sum(self):
        calls = []
        stats, defs = V._fixture_rows()
        costs = {"2026-07-01": _flat(b"3.01"), "2026-08-01": _flat(b"3.01")}
        rc = self._run(_http(calls, costs, stats, defs), "2026-07-01", "2026-09-01", download=False)
        self.assertEqual(rc, 3)
        self.assertTrue(any(_query(url)["start"][0] == "2026-08-01" for url in calls))
        self.assertFalse(any("timeseries" in url for url in calls))
        self.assertFalse(os.path.exists(self.vol_report))
        self.assertIn("6.02", self._summary())
        self.assertIn("HARD CAP", self._summary())

    def test_single_504_is_retried_and_the_month_completes(self):
        calls = []
        statuses = []
        stats, defs = V._fixture_rows()

        def range_status(_query):
            # First paid pull of the month fails once. The retry of that month succeeds.
            code = 504 if statuses.count(504) == 0 else 200
            statuses.append(code)
            return code

        rc = self._run(
            _http(calls, {"2026-07-01": _flat(b"0.40")}, stats, defs, range_status),
            "2026-07-01", "2026-08-01", download=True)
        self.assertEqual(rc, 0)
        self.assertEqual(statuses.count(504), 1)
        self.assertGreater(statuses.count(200), 1)
        ranges = [url for url in calls if "timeseries.get_range" in url]
        self.assertEqual(len(ranges), len(statuses))
        self.assertTrue(all(_query(url)["start"][0] == "2026-07-01" for url in ranges))
        self.assertTrue(all(_query(url)["end"][0] == "2026-08-01" for url in ranges))
        self.assertNotIn("twice", self._summary())
        self.assertTrue(self._report()["months"][0]["complete"])
        self.assertFalse(self._report()["months"][0]["partial"])
        self.assertTrue(os.path.isdir(self.vol))

    def test_two_504s_in_one_month_stop_and_keep_earlier_months(self):
        calls = []
        stats, defs = V._fixture_rows()
        costs = {"2026-07-01": _flat(b"0.40"), "2026-08-01": _flat(b"0.40"),
                 "2026-09-01": _flat(b"0.40")}

        def range_status(query):
            if query["start"][0] == "2026-08-01":
                return 504
            return 200

        rc = self._run(
            _http(calls, costs, stats, defs, range_status),
            "2026-07-01", "2026-10-01", download=True)
        self.assertEqual(rc, 1)
        august_ranges = [url for url in calls
                         if "timeseries.get_range" in url and _query(url)["start"][0] == "2026-08-01"]
        self.assertEqual(len(august_ranges), 2)
        self.assertFalse(any(_query(url)["start"][0] == "2026-09-01" for url in calls))
        self.assertTrue(os.path.isdir(os.path.join(self.vol, "2026-07-01")))
        self.assertFalse(os.path.isdir(os.path.join(self.vol, "2026-08-01")))
        report = self._report()
        self.assertIn("HTTP 504 twice", report["reason"])
        self.assertIn("2026-08", report["reason"])
        self.assertIn("Earlier months are kept", report["reason"])
        by_month = {row["month"]: row for row in report["months"]}
        self.assertTrue(by_month["2026-07"]["complete"])
        self.assertFalse(by_month["2026-08"]["complete"])
        self.assertEqual(by_month["2026-08"]["error"], "HTTP 504 twice")
        self.assertNotIn("2026-09", by_month)
        self.assertEqual(report["spent_usd"], "0.40")
        self.assertIn("HTTP 504 twice", self._summary())
        self._sentinels()


class WorkflowMonths(unittest.TestCase):
    def setUp(self):
        with open(WF, encoding="utf-8") as fh:
            self.text = fh.read()
            fh.seek(0)
            self.wf = yaml.safe_load(fh)
        self.backfill = self.wf["jobs"]["backfill"]
        self.plan = self.wf["jobs"]["volume-plan"]
        self.month = self.wf["jobs"]["volume-month"]
        step = next(s for s in self.backfill["steps"]
                    if s.get("name") == "Backfill one calendar month at a time")
        self.run = step["run"]

    def test_matrix_uploads_one_artifact_per_month_before_the_next(self):
        self.assertNotIn("schedule:", self.text)
        self.assertNotIn("cron:", self.text)
        self.assertEqual(self.month["strategy"]["max-parallel"], 1)
        self.assertIs(self.month["strategy"]["fail-fast"], True)
        self.assertIn("volume_only", self.month["if"])
        self.assertIn("dry_run", self.month["if"])
        self.assertEqual(self.month["needs"], "volume-plan")
        upload = next(s for s in self.month["steps"] if s.get("uses") == "actions/upload-artifact@v4")
        download = next(s for s in self.month["steps"]
                        if s.get("name") == "Download one volume month and commit it")
        self.assertLess(self.month["steps"].index(download), self.month["steps"].index(upload))
        self.assertEqual(upload["with"]["retention-days"], 90)
        self.assertEqual(upload["with"]["if-no-files-found"], "ignore")
        self.assertEqual(upload["if"], "always()")
        self.assertIn("cme-options-volume-raw-${{ github.run_id }}-${{ matrix.item.id }}",
                      upload["with"]["name"])
        self.assertIn("runner.temp", upload["with"]["path"])
        self.assertIn("matrix.item.id", upload["with"]["path"])
        script = download["run"]
        self.assertIn(
            'git commit -m "data: cme options volume-only ${ym} [skip netlify]"', script)
        self.assertIn(
            "git add -- data/options/volume_shadow data/options/volume_backfill_report.json",
            script)
        self.assertNotIn("runner.temp", script)
        self.assertNotIn("data/options/canonical", script)
        self.assertNotIn("data/options/backfill_report.json", script)
        self.assertLess(script.index("--download"), script.index("git commit"))
        self.assertNotIn("DATABENTO_API_KEY", self.plan["steps"][-1].get("env", {}))
        self.assertIn("--list-volume-months", self.plan["steps"][-1]["run"])
        env = next(s for s in self.backfill["steps"]
                   if s.get("name") == "Backfill one calendar month at a time")["env"]
        self.assertIn("VOLUME_MONTHS_JOB", env)

    def _shell(self, dry_value, extra, git_diff_rc="1"):
        script = self.run.replace("${{ inputs.dry_run }}", dry_value)
        self.assertNotIn("${{", script)
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", tmp], check=False))
        bindir = os.path.join(tmp, "bin")
        os.makedirs(bindir)
        os.makedirs(os.path.join(tmp, "scripts", "tools"))
        log = os.path.join(tmp, "calls")
        python_stub = textwrap.dedent("""\
            #!/bin/sh
            printf '%s\\n' "python $*" >> "$CALLLOG"
            exit 0
            """)
        git_stub = textwrap.dedent("""\
            #!/bin/sh
            printf '%s\\n' "git $*" >> "$CALLLOG"
            if [ "$1" = "diff" ]; then
              exit "$GIT_DIFF_RC"
            fi
            exit 0
            """)
        date_stub = textwrap.dedent("""\
            #!/bin/sh
            printf '%s\\n' "date $*" >> "$CALLLOG"
            if [ "$1" = "+%s" ]; then echo 100; exit 0; fi
            if [ "$1" = "-u" ]; then echo 2026-11-15; exit 0; fi
            echo "unexpected date $*" >&2
            exit 9
            """)
        push = os.path.join(tmp, "scripts", "tools", "git_push_retry.sh")
        with open(push, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\nprintf '%s\\n' \"push $*\" >> \"$CALLLOG\"\nexit 0\n")
        for name, body in (("python", python_stub), ("git", git_stub), ("date", date_stub)):
            path = os.path.join(bindir, name)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(body)
            os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        os.chmod(push, os.stat(push).st_mode | stat.S_IEXEC)
        env = os.environ.copy()
        env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
        env["CALLLOG"] = log
        env["GIT_DIFF_RC"] = str(git_diff_rc)
        env["BACKFILL_MODE"] = "volume_only"
        env.pop("DATABENTO_API_KEY", None)
        env.pop("BACKFILL_MONTH", None)
        env.pop("VOLUME_START", None)
        env.pop("VOLUME_END", None)
        env.pop("VOLUME_MONTHS_JOB", None)
        env.update(extra)
        proc = subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail"],
            input=script, text=True, capture_output=True, check=False, env=env, cwd=tmp)
        calls = []
        if os.path.exists(log):
            with open(log, encoding="utf-8") as fh:
                calls = [line.rstrip("\n") for line in fh]
        return proc.returncode, calls, proc.stderr

    def test_fallback_commits_each_month_and_the_real_job_does_not_download_here(self):
        rc, calls, err = self._shell("false", {
            "VOLUME_START": "2026-07-01",
            "VOLUME_END": "2026-11-15",
        })
        self.assertEqual(rc, 0, err)
        commits = [c for c in calls if c.startswith("git commit")]
        self.assertEqual(commits, [
            "git commit -m data: cme options volume-only 2026-07 [skip netlify]",
            "git commit -m data: cme options volume-only 2026-08 [skip netlify]",
            "git commit -m data: cme options volume-only 2026-09 [skip netlify]",
            "git commit -m data: cme options volume-only 2026-10 [skip netlify]",
            "git commit -m data: cme options volume-only 2026-11 [skip netlify]",
        ])
        py = [c for c in calls if c.startswith("python ")]
        self.assertEqual(py, [
            "python scripts/cme_options_backfill.py --mode volume_only "
            "--volume-start 2026-07-01 --volume-end 2026-08-01 --download",
            "python scripts/cme_options_backfill.py --mode volume_only "
            "--volume-start 2026-08-01 --volume-end 2026-09-01 --download",
            "python scripts/cme_options_backfill.py --mode volume_only "
            "--volume-start 2026-09-01 --volume-end 2026-10-01 --download",
            "python scripts/cme_options_backfill.py --mode volume_only "
            "--volume-start 2026-10-01 --volume-end 2026-11-01 --download",
            "python scripts/cme_options_backfill.py --mode volume_only "
            "--volume-start 2026-11-01 --volume-end 2026-11-15 --download",
        ])
        self.assertEqual(sum(1 for c in calls if c.startswith("push ")), 5)
        self.assertFalse(any("canonical" in c for c in calls))
        rc, calls, err = self._shell("false", {
            "VOLUME_START": "2026-07-01",
            "VOLUME_END": "2026-11-15",
            "VOLUME_MONTHS_JOB": "1",
        })
        self.assertEqual(rc, 0, err)
        self.assertFalse(any(c.startswith("python ") for c in calls))
        self.assertFalse(any(c.startswith("git commit") for c in calls))


if __name__ == "__main__":
    unittest.main()

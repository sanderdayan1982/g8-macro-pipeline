"""t189u — volume_only backfill. Synthetic rows, no network, no Databento call.

Does not edit an existing test. The full-mode report and canonical CSVs are sentinels.
"""
import csv
import gzip
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
import urllib.parse

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import cme_options_backfill as B  # noqa: E402
import cme_options_collector as C  # noqa: E402

WF = os.path.join(ROOT, ".github", "workflows", "cme_options_backfill.yml")
START = "2026-07-01"
END = "2026-07-03"  # exclusive: Wed 2026-07-01 and Thu 2026-07-02
KEY = "unit-test-key"
CANON_SENTINEL = b"CANONICAL-KEEP\n"
FULL_REPORT_SENTINEL = b'{"chunks":[{"month":"2024-06","complete":true}]}\n'
DEF_FIELDS = ["instrument_id", "strike_price", "expiration", "instrument_class", "symbol"]


def _csv_bytes(rows, fieldnames):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


def _stat(iid, stat_type, session, price="", quantity=""):
    row = {col: "" for col in C.STATS_FINGERPRINT}
    row.update({
        "instrument_id": str(iid),
        "stat_type": stat_type,
        "ts_ref": "%sT21:00:00.000000000Z" % session,
        "price": price,
        "quantity": quantity,
        "symbol": "FROMSTAT",
        "stat_flags": "3",
    })
    return row


def _defn(iid, strike):
    return {
        "instrument_id": str(iid),
        "strike_price": strike,
        "expiration": "2026-08-07T00:00:00.000000000Z",
        "instrument_class": "C",
        "symbol": "EUUQ6",
    }


def _fixture_rows():
    """Jul 1 has cleared volume. Jul 2 does not carry it. A real 0 stays 0."""
    stats = [
        _stat("1", C.STAT_SETTLE, "2026-07-01", price="0.0123"),
        _stat("1", C.STAT_VOLUME, "2026-07-01", quantity="99"),
        _stat("9", C.STAT_VOLUME, "2026-07-01", quantity="999"),  # no settlement
        _stat("1", C.STAT_SETTLE, "2026-07-02", price="0.0123"),
        _stat("2", C.STAT_SETTLE, "2026-07-02", price="0.0200"),
        _stat("2", C.STAT_VOLUME, "2026-07-02", quantity="0"),
        _stat("3", C.STAT_SETTLE, "2026-07-02", price="0.0300"),
        _stat("3", C.STAT_VOLUME, "2026-07-02", quantity="   "),
    ]
    defs = [_defn("1", "1.1000"), _defn("2", "1.2000"), _defn("3", "1.3000")]
    return stats, defs


def _query(url):
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)


def _endpoint(url):
    return urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]


def _http(calls, costs, stats, defs, block_range=False):
    def http(method, url, headers, body):
        calls.append(url)
        endpoint = _endpoint(url)
        query = _query(url)
        if endpoint == "timeseries.get_range":
            if block_range:
                raise AssertionError("download started")
            schema = query["schema"][0]
            parent = query["symbols"][0]
            if schema == "statistics":
                rows = stats if parent == "EUU.OPT" else []
                return 200, {}, _csv_bytes(rows, C.STATS_FINGERPRINT)
            if schema == "definition":
                rows = defs if parent == "EUU.OPT" else []
                return 200, {}, _csv_bytes(rows, DEF_FIELDS)
            raise AssertionError("unexpected schema %s" % schema)
        if endpoint == "metadata.get_cost":
            schema = query["schema"][0]
            if schema not in costs:
                raise AssertionError("unexpected cost schema %s" % schema)
            return 200, {}, costs[schema]
        if endpoint in ("metadata.get_record_count", "metadata.get_billable_size"):
            return 200, {}, b"4"
        raise AssertionError(endpoint)
    return http


def _read_csv(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


class _VolumeCase(unittest.TestCase):
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
            fh.write(CANON_SENTINEL)
        with open(self.full_report, "wb") as fh:
            fh.write(FULL_REPORT_SENTINEL)
        self.summary = os.path.join(self.tmp, "step_summary.md")
        self._prev_summary = os.environ.get("GITHUB_STEP_SUMMARY")
        os.environ["GITHUB_STEP_SUMMARY"] = self.summary
        self.addCleanup(self._restore_summary)
        # orden Sander 16-nov, licencia
        self.runner = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.runner, True)
        self._prev_runner = os.environ.get("RUNNER_TEMP")
        os.environ["RUNNER_TEMP"] = self.runner
        self.addCleanup(self._restore_runner)

    def _restore_summary(self):
        if self._prev_summary is None:
            os.environ.pop("GITHUB_STEP_SUMMARY", None)
        else:
            os.environ["GITHUB_STEP_SUMMARY"] = self._prev_summary

    def _restore_runner(self):
        # orden Sander 16-nov, licencia
        if self._prev_runner is None:
            os.environ.pop("RUNNER_TEMP", None)
        else:
            os.environ["RUNNER_TEMP"] = self._prev_runner

    def _argv(self, download):
        argv = ["--mode", "volume_only", "--volume-start", START, "--volume-end", END]
        if download:
            argv.append("--download")
        return argv

    def _run(self, http, download):
        return B.main(
            self._argv(download),
            http=http,
            env={"DATABENTO_API_KEY": KEY},
            out_dir=self.canon,
            report_path=self.full_report,
            volume_dir=self.vol,
            volume_report=self.vol_report,
            raw_dir=self.raw,
        )

    def _summary_text(self):
        # orden Sander 16-nov, licencia — mismas comprobaciones, sobre el log privado
        public = ""
        if os.path.exists(self.summary):
            with open(self.summary, encoding="utf-8") as fh:
                public = fh.read()
        for marker in ("get_cost", "record_count", "billable_size", "HARD CAP", "cumulative spent", "cap:"):
            self.assertNotIn(marker, public)
        with open(os.path.join(self.runner, "cme.log"), encoding="utf-8") as fh:
            return fh.read()

    def _assert_sentinels(self):
        with open(os.path.join(self.canon, "2026-07-01", "EUU.csv"), "rb") as fh:
            self.assertEqual(fh.read(), CANON_SENTINEL)
        with open(self.full_report, "rb") as fh:
            self.assertEqual(fh.read(), FULL_REPORT_SENTINEL)
        self.assertEqual(os.listdir(self.canon), ["2026-07-01"])
        self.assertNotIn(KEY, self._summary_text())

    def _assert_metadata_before_any_range(self, calls):
        meta = [i for i, url in enumerate(calls) if "/metadata." in url]
        ranges = [i for i, url in enumerate(calls) if "timeseries.get_range" in url]
        self.assertTrue(meta)
        if ranges:
            self.assertLess(max(meta), min(ranges))
        schemas = set()
        for url in calls:
            if _endpoint(url) in ("metadata.get_cost", "timeseries.get_range"):
                schemas.add(_query(url)["schema"][0])
                self.assertEqual(_query(url)["start"][0], START)
                self.assertEqual(_query(url)["end"][0], END)
        self.assertEqual(schemas, {"statistics", "definition"})


class CapAndDryRun(_VolumeCase):
    def test_cost_above_cap_aborts_before_download(self):
        calls = []
        stats, defs = _fixture_rows()
        http = _http(calls, {"statistics": b"3.01", "definition": b"3.00"}, stats, defs,
                     block_range=True)
        rc = self._run(http, download=True)
        self.assertEqual(rc, 3)
        self.assertFalse(any("timeseries.get_range" in url for url in calls))
        self._assert_metadata_before_any_range(calls)
        self.assertEqual(len([u for u in calls if _endpoint(u) == "metadata.get_cost"]), 2)
        self.assertTrue(any(_endpoint(u) == "metadata.get_record_count" for u in calls))
        self.assertTrue(any(_endpoint(u) == "metadata.get_billable_size" for u in calls))
        cost_symbols = _query([u for u in calls if _endpoint(u) == "metadata.get_cost"][0])["symbols"][0]
        self.assertEqual(cost_symbols.split(","), list(B.PARENTS))
        text = self._summary_text()
        self.assertIn("6.01", text)
        self.assertIn("record_count", text)
        self.assertIn("billable_size", text)
        self.assertIn("HARD CAP", text)
        self.assertIn("6.00", text)
        self.assertFalse(os.path.exists(self.vol))
        self.assertFalse(os.path.exists(self.raw))
        self.assertFalse(os.path.exists(self.vol_report))
        self._assert_sentinels()

    def test_cost_equal_to_cap_does_not_abort_on_dry_run(self):
        calls = []
        stats, defs = _fixture_rows()
        http = _http(calls, {"statistics": b"3.00", "definition": b"3.00"}, stats, defs,
                     block_range=True)
        rc = self._run(http, download=False)
        self.assertEqual(rc, 0)
        self.assertFalse(any("timeseries" in url for url in calls))
        self.assertIn("6.00", self._summary_text())
        self.assertNotIn("HARD CAP", self._summary_text())
        self.assertFalse(os.path.exists(self.vol_report))

    def test_dry_run_writes_nothing(self):
        calls = []
        stats, defs = _fixture_rows()
        http = _http(calls, {"statistics": b"1.00", "definition": b"0.25"}, stats, defs,
                     block_range=True)
        before = set(os.listdir(self.tmp))
        rc = self._run(http, download=False)
        self.assertEqual(rc, 0)
        self.assertFalse(any("timeseries.get_range" in url for url in calls))
        self.assertEqual(set(os.listdir(self.tmp)), before | {"step_summary.md"})
        self.assertFalse(os.path.exists(self.vol))
        self.assertFalse(os.path.exists(self.raw))
        self.assertFalse(os.path.exists(self.vol_report))
        text = self._summary_text()
        self.assertIn("1.25", text)
        self.assertIn("record_count", text)
        self.assertIn("billable_size", text)
        self._assert_sentinels()


class DownloadScope(_VolumeCase):
    def _download(self):
        calls = []
        stats, defs = _fixture_rows()
        http = _http(calls, {"statistics": b"0.40", "definition": b"0.10"}, stats, defs)
        rc = self._run(http, download=True)
        return rc, calls

    def test_volume_only_does_not_touch_canonical_or_full_report(self):
        rc, calls = self._download()
        self.assertEqual(rc, 0)
        self._assert_metadata_before_any_range(calls)
        ranges = [u for u in calls if "timeseries.get_range" in u]
        self.assertEqual(len(ranges), len(B.PARENTS) * 2)
        self._assert_sentinels()
        with open(self.vol_report, encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(report["mode"], "volume_only")
        self.assertEqual(report["spent_usd"], "0.50")
        self.assertNotIn("chunks", report)
        self.assertTrue(report["downloaded"])
        archives = []
        for dirpath, _dirs, names in os.walk(self.raw):
            for name in names:
                archives.append(os.path.join(dirpath, name))
        self.assertEqual(len(archives), len(B.PARENTS) * 2)
        for path in archives:
            self.assertTrue(path.endswith(".csv.gz"), path)
            self.assertNotIn(KEY, os.path.basename(path))
            self.assertFalse(path.startswith(self.vol + os.sep))
            self.assertFalse(path.startswith(self.canon + os.sep))
        euu_stats = next(p for p in archives if os.path.basename(p).startswith("statistics_EUU.OPT_"))
        with gzip.open(euu_stats, "rb") as fh:
            body = fh.read()
        self.assertNotIn(KEY.encode("ascii"), body)
        self.assertIn(b"stat_type", body)
        with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as fh:
            ignore = fh.read()
        self.assertIn("data/options/raw/", ignore)

    def test_missing_stat_type_6_is_empty_and_real_zero_stays_zero(self):
        rc, _calls = self._download()
        self.assertEqual(rc, 0)
        day1 = _read_csv(os.path.join(self.vol, "2026-07-01", "EUU.csv"))
        day2 = _read_csv(os.path.join(self.vol, "2026-07-02", "EUU.csv"))
        self.assertEqual(list(day1[0].keys()), C.VOLUME_HEADER)
        self.assertNotIn("settle", day1[0])
        self.assertNotIn("oi", day1[0])
        self.assertNotIn("origin", day1[0])
        by1 = {row["strike"]: row for row in day1}
        by2 = {row["strike"]: row for row in day2}
        self.assertEqual(set(by1), {"1.1"})
        self.assertEqual(by1["1.1"]["volume"], "99")
        self.assertEqual(by1["1.1"]["symbol"], "EUUQ6")
        self.assertNotEqual(by1["1.1"]["symbol"], "FROMSTAT")
        self.assertEqual(by1["1.1"]["root"], "EUU")
        self.assertEqual(by1["1.1"]["right"], "C")
        self.assertEqual(by1["1.1"]["session"], "2026-07-01")
        self.assertEqual(by2["1.1"]["volume"], "")
        self.assertNotEqual(by2["1.1"]["volume"], "0")
        self.assertNotEqual(by2["1.1"]["volume"], "99")
        self.assertEqual(by2["1.2"]["volume"], "0")
        self.assertEqual(by2["1.3"]["volume"], "")
        self.assertNotIn("999", [row["volume"] for row in day1 + day2])
        header_only = os.path.join(self.vol, "2026-07-02", "6E.csv")
        with open(header_only, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "session,root,type,symbol,expiry,right,strike,volume\n")

    def test_existing_session_file_is_skipped(self):
        keep = os.path.join(self.vol, "2026-07-01", "EUU.csv")
        os.makedirs(os.path.dirname(keep))
        with open(keep, "wb") as fh:
            fh.write(b"KEEP\n")
        rc, calls = self._download()
        self.assertEqual(rc, 0)
        with open(keep, "rb") as fh:
            self.assertEqual(fh.read(), b"KEEP\n")
        self.assertTrue(any("timeseries.get_range" in url for url in calls))
        day2 = _read_csv(os.path.join(self.vol, "2026-07-02", "EUU.csv"))
        self.assertEqual({row["strike"]: row["volume"] for row in day2}["1.2"], "0")
        with open(self.vol_report, encoding="utf-8") as fh:
            report = json.load(fh)
        skipped = [row for row in report["sessions_skipped"] if row["root"] == "EUU"]
        self.assertEqual(skipped, [{"session": "2026-07-01", "root": "EUU", "reason": "exists"}])
        self._assert_sentinels()

    def test_window_already_on_disk_does_not_download(self):
        for session in ("2026-07-01", "2026-07-02"):
            for parent in B.PARENTS:
                root = parent.split(".")[0]
                folder = os.path.join(self.vol, session)
                os.makedirs(folder, exist_ok=True)
                with open(os.path.join(folder, "%s.csv" % root), "wb") as fh:
                    fh.write(b"KEEP\n")
        calls = []
        stats, defs = _fixture_rows()
        http = _http(calls, {"statistics": b"0.40", "definition": b"0.10"}, stats, defs,
                     block_range=True)
        rc = self._run(http, download=True)
        self.assertEqual(rc, 0)
        self.assertFalse(any("timeseries" in url for url in calls))
        self.assertFalse(os.path.exists(self.raw))
        for session in ("2026-07-01", "2026-07-02"):
            for parent in B.PARENTS:
                root = parent.split(".")[0]
                with open(os.path.join(self.vol, session, "%s.csv" % root), "rb") as fh:
                    self.assertEqual(fh.read(), b"KEEP\n")
        with open(self.vol_report, encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(report["spent_usd"], "0")
        self.assertFalse(report["downloaded"])
        self.assertIn("already exists", report["reason"])
        self._assert_sentinels()


class WorkflowVolumeOnly(unittest.TestCase):
    def setUp(self):
        with open(WF, encoding="utf-8") as fh:
            self.text = fh.read()
            fh.seek(0)
            self.wf = yaml.safe_load(fh)
        trigger = self.wf[True] if True in self.wf else self.wf["on"]
        self.inputs = trigger["workflow_dispatch"]["inputs"]
        step = next(s for s in self.wf["jobs"]["backfill"]["steps"]
                    if s.get("name") == "Backfill one calendar month at a time")
        self.run = step["run"]

    def test_inputs_concurrency_and_artifact(self):
        self.assertEqual(self.wf["concurrency"]["group"], "cme-options-backfill")
        self.assertIs(self.wf["concurrency"]["cancel-in-progress"], False)
        self.assertNotIn("schedule:", self.text)
        mode = self.inputs["mode"]
        self.assertEqual(mode["default"], "full")
        self.assertEqual(mode["options"], ["full", "volume_only"])
        self.assertEqual(self.inputs["volume_start"]["default"], "2026-07-01")
        self.assertEqual(self.inputs["volume_end"]["default"], "")
        self.assertIs(self.inputs["dry_run"]["default"], True)
        self.assertNotIn("exit 0", self.text)
        # orden Sander 18-nov t245u
        uploads = [s for s in self.wf["jobs"]["backfill"]["steps"]
                   if "upload-artifact" in str(s.get("uses") or "")]
        self.assertEqual(uploads, [])
        self.assertNotIn("cme-options-volume-raw-${{ github.run_id }}", self.text)
        self.assertIn("git add -- data/options/volume_shadow data/options/volume_backfill_report.json", self.text)
        self.assertIn("[skip netlify]", self.text)
        commit = 'git commit -m "data: cme options volume-only ${vol_start} ${vol_end} [skip netlify]"'
        self.assertIn(commit, self.text)
        start = self.run.index('if [ "${BACKFILL_MODE:-full}" = "volume_only" ]; then')
        end = self.run.index('if [ -z "${BACKFILL_MONTH}" ]')
        volume = self.run[start:end]
        self.assertIn("volume_only", volume)
        self.assertNotIn("commit_month", volume)
        self.assertNotIn("data/options/canonical", volume)
        self.assertNotIn("data/options/backfill_report.json", volume)

    def _shell(self, dry_run_value, env_extra, py_rc="0", git_diff_rc="0"):
        script = self.run.replace("${{ inputs.dry_run }}", dry_run_value)
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
            exit "$PY_RC"
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
            if [ "$1" = "+%s" ]; then
              echo 100
              exit 0
            fi
            if [ "$1" = "-u" ]; then
              echo 2026-11-15
              exit 0
            fi
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
        env = os.environ.copy()
        env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
        env["CALLLOG"] = log
        env["PY_RC"] = str(py_rc)
        env["GIT_DIFF_RC"] = str(git_diff_rc)
        env["BACKFILL_MODE"] = "volume_only"
        env.pop("DATABENTO_API_KEY", None)
        env.pop("BACKFILL_MONTH", None)
        env.pop("VOLUME_START", None)
        env.pop("VOLUME_END", None)
        env.update(env_extra)
        proc = subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail"],
            input=script, text=True, capture_output=True, check=False, env=env, cwd=tmp,
        )
        calls = []
        if os.path.exists(log):
            with open(log, encoding="utf-8") as fh:
                calls = [line.rstrip("\n") for line in fh]
        return proc.returncode, calls, proc.stderr

    def test_dry_run_shell_does_not_commit_and_bad_date_calls_nothing(self):
        rc, calls, err = self._shell("true", {
            "VOLUME_START": "2026-07-01",
            "VOLUME_END": "2026-11-15",
        })
        self.assertEqual(rc, 0, err)
        py = [c for c in calls if c.startswith("python ")]
        self.assertEqual(py, [
            "python scripts/cme_options_backfill.py --mode volume_only "
            "--volume-start 2026-07-01 --volume-end 2026-11-15 --dry-run",
        ])
        self.assertFalse(any(c.startswith("git add") or c.startswith("git commit") for c in calls))
        self.assertFalse(any(c.startswith("push ") for c in calls))
        rc, calls, err = self._shell("true", {"VOLUME_START": "2026-7-1", "VOLUME_END": "2026-11-15"})
        self.assertEqual(rc, 1, err)
        self.assertFalse(any(c.startswith("python ") for c in calls))
        rc, calls, err = self._shell("true", {"VOLUME_START": "2026-07-01"})
        self.assertEqual(rc, 0, err)
        py = [c for c in calls if c.startswith("python ")]
        self.assertEqual(py, [
            "python scripts/cme_options_backfill.py --mode volume_only "
            "--volume-start 2026-07-01 --volume-end 2026-11-15 --dry-run",
        ])
        self.assertTrue(any(c.startswith("date -u") for c in calls))

    def test_download_shell_commits_only_the_sibling_tree_and_keeps_a_failure(self):
        rc, calls, err = self._shell("false", {
            "VOLUME_START": "2026-07-01",
            "VOLUME_END": "2026-11-15",
        }, git_diff_rc="1")
        self.assertEqual(rc, 0, err)
        self.assertIn(
            "git add -- data/options/volume_shadow data/options/volume_backfill_report.json",
            calls)
        self.assertNotIn("git add data/options/canonical data/options/backfill_report.json", calls)
        commit = next(c for c in calls if c.startswith("git commit"))
        self.assertIn("[skip netlify]", commit)
        self.assertTrue(commit.endswith("[skip netlify]"))
        self.assertIn("volume-only", commit)
        self.assertTrue(any(c.startswith("push ") for c in calls))
        rc, calls, err = self._shell("false", {
            "VOLUME_START": "2026-07-01",
            "VOLUME_END": "2026-11-15",
        }, py_rc="3")
        self.assertEqual(rc, 3, err)
        self.assertFalse(any(c.startswith("git add") or c.startswith("git commit") for c in calls))


if __name__ == "__main__":
    unittest.main()

"""Monthly per-product CME options backfill. Offline: every HTTP call is fake.

Does not edit tests/test_cme_options_backfill.py and does not touch the network.
"""
import csv
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.parse
from datetime import date
from decimal import Decimal

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import cme_options_backfill as B  # noqa: E402
import cme_options_collector as C  # noqa: E402

WF = os.path.join(ROOT, ".github", "workflows", "cme_options_backfill.yml")
STATS_HEADER = ",".join(C.STATS_FINGERPRINT)
DEF_FIELDS = ["instrument_id", "strike_price", "expiration", "instrument_class", "symbol"]


class Body(io.RawIOBase):
    """Binary stream with no decode(). The downloader must iterate it, not slurp via bytes.decode."""

    def __init__(self, payload):
        super().__init__()
        self._raw = io.BytesIO(payload)
        self.saw_close = False

    def readable(self):
        return True

    def readinto(self, buf):
        data = self._raw.read(len(buf))
        n = len(data)
        buf[:n] = data
        return n

    def close(self):
        self.saw_close = True
        self._raw.close()
        super().close()


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
        "symbol": "X%s" % iid,
        "stat_flags": "3",
    })
    return row


def _defn(iid, iclass, strike=""):
    return {
        "instrument_id": str(iid),
        "strike_price": strike,
        "expiration": "2024-07-05T00:00:00.000000000Z",
        "instrument_class": iclass,
        "symbol": "S%s" % iid,
    }


def _query(url):
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)


def _endpoint(url):
    path = urllib.parse.urlparse(url).path
    return path.rsplit("/", 1)[-1]


class Plan(unittest.TestCase):
    def test_get_range_count_is_24_chunks_times_12_parents_times_2_schemas(self):
        plan = B.planned_calls()
        self.assertEqual(plan["chunks"], 24)
        self.assertEqual(plan["parents"], 12)
        self.assertEqual(list(B.PARENTS), C.OPT_PARENTS + C.FUT_PARENTS)
        self.assertEqual(plan["schemas"], 2)
        self.assertEqual(plan["get_range_per_chunk"], 24)
        self.assertEqual(plan["get_range"], 24 * 12 * 2)
        self.assertEqual(plan["get_range"], 576)
        self.assertEqual(plan["metadata_per_chunk"], 72)
        self.assertEqual(plan["metadata"], 576 * 3)

    def test_months_and_windows(self):
        months = list(B.iter_chunks())
        self.assertEqual(len(months), 24)
        self.assertEqual(months[0], "2024-06")
        self.assertEqual(months[-1], "2026-05")
        start, end, sessions = B.month_window("2024-06")
        self.assertEqual(start, "2024-06-03")
        self.assertEqual(sessions[0], date(2024, 6, 3))
        self.assertEqual(sessions[-1], date(2024, 6, 28))
        self.assertEqual(end, "2024-07-01T06:00:00")
        self.assertEqual(end, B.pull_window(sessions[-1])[1])
        start, end, sessions = B.month_window("2026-05")
        self.assertEqual(sessions[-1], date(2026, 5, 29))
        self.assertEqual(end, "2026-06-01T06:00:00")
        self.assertLess(sessions[-1], B.PROTECT_FROM)
        for month in months:
            for session in B.month_sessions(month):
                self.assertLess(session, date(2026, 6, 1))
                self.assertLess(session.weekday(), 5)

    def test_whole_span_params_stay_available(self):
        params = B._range_params("statistics")
        self.assertEqual(params["start"], "2024-06-01")
        self.assertEqual(params["end"], "2026-06-01")
        self.assertEqual(params["symbols"].split(","), list(B.PARENTS))


class _Run(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.out = os.path.join(self.tmp, "canonical")
        self.report = os.path.join(self.tmp, "backfill_report.json")
        os.makedirs(self.out)

    def main(self, argv, http, env=None, clock=None, budget_s=None):
        kwargs = dict(http=http, env=env if env is not None else {"DATABENTO_API_KEY": "unit-test-key"},
                      out_dir=self.out, report_path=self.report)
        if clock is not None:
            kwargs["clock"] = clock
        if budget_s is not None:
            kwargs["budget_s"] = budget_s
        return B.main(argv, **kwargs)

    def load(self):
        with open(self.report, encoding="utf-8") as fh:
            return json.load(fh)

    def assert_no_raw(self):
        for dirpath, _dirs, names in os.walk(self.tmp):
            for name in names:
                rel = os.path.relpath(os.path.join(dirpath, name), self.tmp)
                self.assertNotIn(".tmp", rel, rel)
                self.assertFalse(name.endswith((".dbn", ".parquet", ".bin", ".jsonl")), rel)
                if name == "backfill_report.json":
                    continue
                parts = rel.split(os.sep)
                self.assertEqual(parts[0], "canonical", rel)
                self.assertEqual(len(parts), 3, rel)
                self.assertTrue(parts[2].endswith(".csv"), rel)
                with open(os.path.join(dirpath, name), encoding="utf-8") as fh:
                    header = fh.readline()
                self.assertTrue(header.startswith("session,root,type,"), rel)


def _header_http(calls, cost=b"0.01", timeseries_status=200, fail_on=None):
    def http(method, url, headers, body):
        calls.append(url)
        endpoint = _endpoint(url)
        if endpoint == "timeseries.get_range":
            if fail_on and len([u for u in calls if "timeseries.get_range" in u]) == fail_on:
                return 402, {}, b"monthly budget exceeded"
            if timeseries_status == 402:
                return 402, {}, b"monthly budget exceeded"
            kind = _query(url)["schema"][0]
            payload = _csv_bytes([], C.STATS_FINGERPRINT if kind == "statistics" else DEF_FIELDS)
            return 200, {}, Body(payload)
        if endpoint == "metadata.get_cost":
            return 200, {}, cost
        return 200, {}, b"1"
    return http


class ChunkCalls(_Run):
    def test_one_month_is_one_range_per_parent_per_schema(self):
        calls = []
        rc = self.main(["--download", "--month", "2024-06"], _header_http(calls))
        self.assertEqual(rc, 0)
        ranges = [u for u in calls if _endpoint(u) == "timeseries.get_range"]
        costs = [u for u in calls if _endpoint(u) == "metadata.get_cost"]
        counts = [u for u in calls if _endpoint(u) == "metadata.get_record_count"]
        sizes = [u for u in calls if _endpoint(u) == "metadata.get_billable_size"]
        self.assertEqual(len(ranges), 12 * 2)
        self.assertEqual(len(costs), 12 * 2)
        self.assertEqual(len(counts), 12 * 2)
        self.assertEqual(len(sizes), 12 * 2)
        seen = set()
        for url in ranges:
            query = _query(url)
            symbols = query["symbols"][0]
            self.assertNotIn(",", symbols)
            self.assertNotIn("%2C", url)
            self.assertEqual(query["start"][0], "2024-06-03")
            self.assertEqual(query["end"][0], "2024-07-01T06:00:00")
            self.assertEqual(query["stype_in"][0], "parent")
            self.assertEqual(query["dataset"][0], "GLBX.MDP3")
            self.assertEqual(query["encoding"][0], "csv")
            seen.add((symbols, query["schema"][0]))
        self.assertEqual(seen, {(parent, schema) for parent in B.PARENTS for schema in B.SCHEMAS})
        for url in costs:
            query = _query(url)
            self.assertEqual(query["symbols"][0].count(","), 0)
            self.assertEqual(query["start"][0], "2024-06-03")
            self.assertEqual(query["end"][0], "2024-07-01T06:00:00")
            self.assertNotIn("encoding", query)
        report = self.load()
        chunk = report["chunks"][0]
        self.assertEqual(chunk["month"], "2024-06")
        self.assertTrue(chunk["complete"])
        self.assertEqual(chunk["holidays"], [s.isoformat() for s in B.month_sessions("2024-06")])
        self.assertEqual(chunk["sessions_added"], [])
        self.assertEqual(Decimal(report["spent_usd"]), Decimal("0.24"))
        self.assertIsNone(chunk["committed_sha"])
        self.assert_no_raw()
        # header-only month writes no session directory
        self.assertEqual(os.listdir(self.out), [])


class Resume(_Run):
    def test_complete_month_skips_http_and_does_not_overwrite(self):
        day = os.path.join(self.out, "2024-06-03")
        os.makedirs(day)
        with open(os.path.join(day, "6E.csv"), "wb") as fh:
            fh.write(b"OLD\n")
        report = B.blank_report()
        report["spent_usd"] = "1.50"
        report["chunks"] = [{
            "month": "2024-06", "complete": True, "get_cost": "1.50",
            "sessions_added": ["2024-06-03"], "skipped": [], "holidays": [],
            "committed_sha": "abc123",
        }]
        with open(self.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh)
        calls = []

        def http(method, url, headers, body):
            raise AssertionError("complete month was requested: %s" % url)

        rc = self.main(["--download", "--month", "2024-06"], http)
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])
        with open(os.path.join(day, "6E.csv"), "rb") as fh:
            self.assertEqual(fh.read(), b"OLD\n")
        self.assertEqual(Decimal(self.load()["spent_usd"]), Decimal("1.50"))

    def test_existing_session_inside_an_incomplete_month_is_not_overwritten(self):
        day = os.path.join(self.out, "2024-06-03")
        os.makedirs(day)
        with open(os.path.join(day, "6E.csv"), "wb") as fh:
            fh.write(b"OLD\n")
        calls = []
        rc = self.main(["--download", "--month", "2024-06"], _header_http(calls))
        self.assertEqual(rc, 0)
        with open(os.path.join(day, "6E.csv"), "rb") as fh:
            self.assertEqual(fh.read(), b"OLD\n")
        report = self.load()
        skipped = report["chunks"][0]["skipped"]
        self.assertIn({"session": "2024-06-03", "reason": "exists"}, skipped)
        self.assertNotIn("2024-06-03", report["chunks"][0]["holidays"])
        self.assertTrue(report["chunks"][0]["complete"])

    def test_stale_stop_loop_does_not_stick(self):
        report = B.blank_report()
        report["stop_loop"] = True
        report["chunks"] = [{"month": "2024-06", "complete": True, "get_cost": "1",
                             "sessions_added": [], "skipped": [], "holidays": [],
                             "committed_sha": None}]
        with open(self.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh)

        def http(method, url, headers, body):
            raise AssertionError(url)

        rc = self.main(["--download", "--month", "2024-06"], http)
        self.assertEqual(rc, 0)
        self.assertFalse(self.load()["stop_loop"])


class Cap(_Run):
    def test_cumulative_cap_is_a_clean_exit_before_any_range(self):
        calls = []
        rc = self.main(["--download", "--month", "2024-06"], _header_http(calls, cost=b"2"))
        self.assertEqual(rc, 0)
        self.assertFalse(any("timeseries.get_range" in url for url in calls))
        report = self.load()
        self.assertTrue(report["blocked"])
        self.assertTrue(report["stop_loop"])
        self.assertIn("HARD CAP", report["reason"])
        self.assertIn("2024-06", report["reason"])
        self.assertIn("35.00", report["reason"])
        self.assertEqual(report["spent_usd"], "0")
        self.assertFalse(report["chunks"][0]["complete"])
        self.assertEqual(os.listdir(self.out), [])
        self.assert_no_raw()

    def test_exact_35_downloads_and_one_cent_over_does_not(self):
        calls = []
        state = {"n": 0}

        def http(method, url, headers, body):
            calls.append(url)
            if _endpoint(url) == "metadata.get_cost":
                state["n"] += 1
                return 200, {}, b"35.00" if state["n"] == 1 else b"0"
            if _endpoint(url) == "timeseries.get_range":
                kind = _query(url)["schema"][0]
                payload = _csv_bytes([], C.STATS_FINGERPRINT if kind == "statistics" else DEF_FIELDS)
                return 200, {}, Body(payload)
            return 200, {}, b"1"

        rc = self.main(["--download", "--month", "2024-06"], http)
        self.assertEqual(rc, 0, self.load()["reason"] if os.path.exists(self.report) else rc)
        self.assertEqual(len([u for u in calls if "timeseries.get_range" in u]), 24)
        self.assertEqual(Decimal(self.load()["spent_usd"]), Decimal("35.00"))

        calls2 = []
        state2 = {"n": 0}

        def http2(method, url, headers, body):
            calls2.append(url)
            if _endpoint(url) == "metadata.get_cost":
                state2["n"] += 1
                return 200, {}, b"0.01" if state2["n"] == 1 else b"0"
            if "timeseries" in url:
                raise AssertionError("downloaded past the cap")
            return 200, {}, b"1"

        # 35.00 already spent; next chunk quotes 0.01 → 35.01
        rc = self.main(["--download", "--month", "2024-07"], http2)
        self.assertEqual(rc, 0)
        self.assertFalse(any("timeseries" in url for url in calls2))
        report = self.load()
        self.assertTrue(report["blocked"])
        self.assertEqual(Decimal(report["spent_usd"]), Decimal("35.00"))
        self.assertFalse(B.month_complete(report, "2024-07"))

    def test_spent_accumulates_across_runs_and_blocks_the_next_chunk(self):
        calls = []
        rc = self.main(["--download", "--month", "2024-06"], _header_http(calls, cost=b"1"))
        self.assertEqual(rc, 0)
        self.assertEqual(len([u for u in calls if "timeseries.get_range" in u]), 24)
        self.assertEqual(Decimal(self.load()["spent_usd"]), Decimal("24"))
        calls2 = []
        rc = self.main(["--download", "--month", "2024-07"], _header_http(calls2, cost=b"1"))
        self.assertEqual(rc, 0)
        self.assertFalse(any("timeseries.get_range" in u for u in calls2))
        self.assertTrue(any("metadata.get_cost" in u for u in calls2))
        report = self.load()
        self.assertEqual(Decimal(report["spent_usd"]), Decimal("24"))
        self.assertTrue(report["stop_loop"])
        self.assertIn("2024-07", report["reason"])

    def test_legacy_whole_range_quote_is_not_spent(self):
        legacy = {
            "dataset": "GLBX.MDP3",
            "get_cost_usd": {"statistics": "12.519850283861", "definition": "7.961810432374",
                             "total": "20.481660716235"},
            "record_count": {"statistics": "168038586", "definition": "9670734"},
            "billable_size": {"statistics": "13443086880", "definition": "5028781680"},
            "dry_run": True,
            "reason": "cost-only default: no timeseries.get_range call",
        }
        with open(self.report, "w", encoding="utf-8") as fh:
            json.dump(legacy, fh)
        calls = []
        rc = self.main(["--dry-run", "--month", "2024-06"], _header_http(calls, cost=b"0.01"))
        self.assertEqual(rc, 0)
        self.assertFalse(any("timeseries" in url for url in calls))
        report = self.load()
        self.assertEqual(report["spent_usd"], "0")
        self.assertEqual(report["legacy_whole_range_quote"]["get_cost_usd"]["total"], "20.481660716235")
        self.assertFalse(report["chunks"][0]["complete"])
        self.assertFalse(B.month_complete(report, "2024-06"))


class Sanity(_Run):
    def _bodies(self):
        """June responses: 3rd passes, 4th fails the floor, 5th has unknown definitions.

        All of that data is in the single month-range body. A July 1 ts_ref must not
        create a session.
        """
        per_parent = {}
        for parent in B.PARENTS:
            root = parent.split(".")[0]
            is_opt = parent.endswith(".OPT")
            stats, defs = [], []
            if is_opt:
                for i in range(200):
                    iid = 1000 + i
                    stats.append(_stat(iid, C.STAT_SETTLE, "2024-06-03", price="1.1000"))
                    if i < 50:
                        stats.append(_stat(iid, C.STAT_OI, "2024-06-03", quantity="4"))
                    defs.append(_defn(iid, "C", "1.1000"))
                    stats.append(_stat(iid, C.STAT_SETTLE, "2024-06-04", price="1.1000"))
                    if i < 50:
                        stats.append(_stat(iid, C.STAT_OI, "2024-06-04", quantity="4"))
                    stats.append(_stat(iid, C.STAT_SETTLE, "2024-06-05", price="1.1000"))
                    if i < 50:
                        stats.append(_stat(iid, C.STAT_OI, "2024-06-05", quantity="4"))
            else:
                for i in range(5):
                    iid = 1000 + i
                    for day in ("2024-06-03", "2024-06-04", "2024-06-05"):
                        stats.append(_stat(iid, C.STAT_SETTLE, day, price="1.10"))
                    defs.append(_defn(iid, "F"))
            if root == "EUU":
                stats = [row for row in stats if row["ts_ref"][:10] != "2024-06-04"]
                stats.append(_stat(1000, C.STAT_SETTLE, "2024-06-04", price="1.1000"))
                stats = [row for row in stats if row["ts_ref"][:10] != "2024-06-05"]
                for i in range(101):
                    stats.append(_stat(5000 + i, C.STAT_SETTLE, "2024-06-05", price="1.1000"))
                stats.append(_stat(1000, C.STAT_SETTLE, "2024-07-01", price="9.99"))
            per_parent[parent] = (
                _csv_bytes(stats, C.STATS_FINGERPRINT),
                _csv_bytes(defs, DEF_FIELDS),
            )
        return per_parent

    def test_sanity_skip_keeps_the_good_session_and_writes_no_raw(self):
        bodies = self._bodies()
        calls = []

        def http(method, url, headers, body):
            calls.append(url)
            endpoint = _endpoint(url)
            if endpoint == "timeseries.get_range":
                query = _query(url)
                stats, defs = bodies[query["symbols"][0]]
                payload = stats if query["schema"][0] == "statistics" else defs
                return 200, {}, Body(payload)
            if endpoint == "metadata.get_cost":
                return 200, {}, b"0.01"
            return 200, {}, b"1"

        rc = self.main(["--download", "--month", "2024-06"], http)
        self.assertEqual(rc, 0, self.load()["reason"] if os.path.exists(self.report) else rc)
        self.assertEqual(len([u for u in calls if "timeseries.get_range" in u]), 24)
        good = os.path.join(self.out, "2024-06-03")
        self.assertTrue(os.path.isdir(good))
        self.assertEqual(sorted(os.listdir(good)), sorted("%s.csv" % p.split(".")[0] for p in B.PARENTS))
        with open(os.path.join(good, "EUU.csv"), encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 200)
        self.assertEqual(rows[0]["origin"], "backfill")
        self.assertEqual(rows[0]["session"], "2024-06-03")
        self.assertEqual(rows[0]["strike"], "1.1")
        self.assertFalse(os.path.exists(os.path.join(self.out, "2024-06-04")))
        self.assertFalse(os.path.exists(os.path.join(self.out, "2024-06-05")))
        self.assertFalse(os.path.exists(os.path.join(self.out, "2024-07-01")))
        report = self.load()
        skipped = report["chunks"][0]["skipped"]
        floor = [row for row in skipped if row["session"] == "2024-06-04"]
        unknown = [row for row in skipped if row["session"] == "2024-06-05"]
        self.assertEqual([row["root"] for row in floor], ["EUU"])
        self.assertIn("below floor", floor[0]["reason"])
        self.assertEqual([row["root"] for row in unknown], ["EUU"])
        self.assertIn("missing definitions", unknown[0]["reason"])
        self.assertIn("2024-06-06", report["chunks"][0]["holidays"])
        self.assertIn("2024-06-03", report["chunks"][0]["sessions_added"])
        self.assertNotIn("2024-06-04", report["chunks"][0]["sessions_added"])
        self.assertTrue(report["chunks"][0]["complete"])
        self.assert_no_raw()


class Http402(_Run):
    def test_402_on_first_range_leaves_no_partial_and_keeps_existing(self):
        kept = os.path.join(self.out, "2024-06-10")
        os.makedirs(kept)
        with open(os.path.join(kept, "6E.csv"), "wb") as fh:
            fh.write(b"KEEP\n")
        protected = os.path.join(self.out, "2026-06-01")
        os.makedirs(protected)
        with open(os.path.join(protected, "6E.csv"), "wb") as fh:
            fh.write(b"LIVE\n")
        calls = []
        rc = self.main(["--download", "--month", "2024-06"],
                       _header_http(calls, timeseries_status=402))
        self.assertEqual(rc, 2)
        self.assertFalse(os.path.exists(os.path.join(self.out, "2024-06-03")))
        with open(os.path.join(kept, "6E.csv"), "rb") as fh:
            self.assertEqual(fh.read(), b"KEEP\n")
        with open(os.path.join(protected, "6E.csv"), "rb") as fh:
            self.assertEqual(fh.read(), b"LIVE\n")
        report = self.load()
        self.assertEqual(report.get("aborted"), "HTTP 402")
        self.assertIn("402", report["reason"])
        self.assertFalse(B.month_complete(report, "2024-06"))
        self.assertEqual(report["spent_usd"], "0")
        self.assertEqual(sorted(os.listdir(self.out)), ["2024-06-10", "2026-06-01"])

    def test_402_after_a_successful_range_still_writes_no_session(self):
        calls = []
        rc = self.main(["--download", "--month", "2024-06"], _header_http(calls, fail_on=2))
        self.assertEqual(rc, 2)
        self.assertEqual(os.listdir(self.out), [])
        self.assertGreaterEqual(len([u for u in calls if "timeseries.get_range" in u]), 2)
        self.assertFalse(B.month_complete(self.load(), "2024-06"))
        self.assert_no_raw()


class DryAndBudget(_Run):
    def test_dry_run_records_the_table_and_does_not_spend_or_download(self):
        calls = []
        rc = self.main(["--month", "2024-06"], _header_http(calls, cost=b"0.50"))
        self.assertEqual(rc, 0)
        self.assertFalse(any("timeseries.get_range" in url for url in calls))
        self.assertEqual(len([u for u in calls if "metadata.get_cost" in u]), 24)
        self.assertEqual(len([u for u in calls if "metadata.get_record_count" in u]), 24)
        self.assertEqual(len([u for u in calls if "metadata.get_billable_size" in u]), 24)
        report = self.load()
        self.assertEqual(report["spent_usd"], "0")
        self.assertFalse(report["chunks"][0]["complete"])
        self.assertEqual(report["chunks"][0]["get_cost"], "12.00")
        self.assertTrue(report["dry_run"])
        self.assertEqual(os.listdir(self.out), [])

    def test_time_budget_stops_before_the_next_chunk(self):
        calls = []
        seq = iter([0, 0, B.CHUNK_BUDGET_S])

        def clock():
            return next(seq)

        rc = self.main([], _header_http(calls, cost=b"0.01"), clock=clock, budget_s=B.CHUNK_BUDGET_S)
        self.assertEqual(rc, 0)
        starts = [_query(url)["start"][0] for url in calls]
        self.assertTrue(starts)
        self.assertTrue(all(item == "2024-06-03" for item in starts))
        report = self.load()
        self.assertTrue(report["stop_loop"])
        self.assertIn("time budget", report["reason"])
        self.assertIn("320 min", report["reason"])
        self.assertEqual([c["month"] for c in report["chunks"]], ["2024-06"])
        self.assertFalse(any("timeseries" in url for url in calls))


class Workflow(unittest.TestCase):
    def test_dispatch_loop_commit_and_budget(self):
        with open(WF, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("schedule:", text)
        self.assertNotIn("cron:", text)
        self.assertIn("timeout-minutes: 360", text)
        self.assertIn("budget_s=$((320 * 60))", text)
        self.assertIn("default: true", text)
        self.assertIn('git commit -m "data: cme options backfill ${month} [skip netlify]"', text)
        self.assertIn("bash scripts/tools/git_push_retry.sh 5", text)
        self.assertIn("--list-months", text)
        self.assertIn('--month "$month"', text)
        self.assertIn("--dry-run", text)
        self.assertIn("--download", text)
        self.assertIn("--record-stop", text)
        self.assertNotIn("\n            git push\n", text)
        self.assertNotIn("\n          git push\n", text)
        self.assertIn("data/options/canonical", text)
        self.assertIn("data/options/backfill_report.json", text)


if __name__ == "__main__":
    unittest.main()

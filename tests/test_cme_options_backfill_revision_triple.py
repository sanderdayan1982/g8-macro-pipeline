"""ORDEN_ENZO_REVISION-TRIPLE-01, block 1. Offline. No existing test edited here.

Punto 3 is not implemented: existing tests require complete true with
committed_sha None. Punto 4 is not implemented: a 00:00 end drops the last
session's open interest (ts_recv after 00:00) and existing tests pin 06:00.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.parse
from decimal import Decimal
from pathlib import Path
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import cme_options_backfill as B  # noqa: E402
import cme_options_collector as C  # noqa: E402


def _endpoint(url):
    return urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]


def _header_http(calls, cost=b"0.01", on_range=None):
    def http(method, url, headers, body):
        calls.append(url)
        endpoint = _endpoint(url)
        if endpoint == "timeseries.get_range":
            if on_range:
                on_range(url)
            kind = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["schema"][0]
            fields = C.STATS_FINGERPRINT if kind == "statistics" else [
                "instrument_id", "strike_price", "expiration", "instrument_class", "symbol"]
            return 200, {}, (",".join(fields) + "\n").encode("utf-8")
        if endpoint == "metadata.get_cost":
            return 200, {}, cost
        return 200, {}, b"1"
    return http


def _boom(method, url, headers, body):
    raise AssertionError("Databento was called: %s" % url)


class _Run(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.out = os.path.join(self.tmp, "canonical")
        self.report = os.path.join(self.tmp, "backfill_report.json")
        os.makedirs(self.out)

    def main(self, argv, http=_boom, env=None):
        return B.main(
            argv, http=http,
            env={"DATABENTO_API_KEY": "unit-test-key"} if env is None else env,
            out_dir=self.out, report_path=self.report)

    def load(self):
        with open(self.report, encoding="utf-8") as fh:
            return json.load(fh)

    def save(self, report):
        with open(self.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh)


class CompleteRule(unittest.TestCase):
    def test_only_exists_protected_or_verified_closure_allow_complete(self):
        self.assertTrue(B.skipped_allows_complete([]))
        self.assertTrue(B.skipped_allows_complete([
            {"session": "2024-06-03", "reason": "exists"},
            {"session": "2026-06-01", "reason": "protected"},
            {"session": "2025-12-25", "reason": "closed", "calendar": "CME-no-settlement"},
        ]))
        self.assertFalse(B.skipped_allows_complete([
            {"session": "2025-12-25", "reason": "no records"},
        ]))
        self.assertFalse(B.skipped_allows_complete([
            {"session": "2024-06-03", "reason": "closed"},
        ]))
        self.assertFalse(B.skipped_allows_complete([
            {"session": "2024-06-04", "reason": "sanity: EUU settles=1 below floor"},
        ]))
        self.assertFalse(B.skipped_allows_complete(["exists"]))

    def test_calendar_is_new_year_and_christmas_not_an_empty_pull(self):
        for day in ("2024-12-25", "2025-01-01", "2025-12-25", "2026-01-01"):
            self.assertTrue(B.market_closed(day), day)
            self.assertEqual(B.closure_skip(day)["reason"], "closed")
        for day in ("2024-06-03", "2024-06-19", "2025-04-18", "2025-11-27", "2026-05-25"):
            self.assertFalse(B.market_closed(day), day)
            self.assertIsNone(B.closure_skip(day))


class ClosureInDownload(_Run):
    def test_christmas_is_a_closed_skip_and_a_normal_empty_day_is_not(self):
        calls = []
        rc = self.main(["--download", "--month", "2024-12"], _header_http(calls))
        self.assertEqual(rc, 0, self.load().get("reason"))
        chunk = self.load()["chunks"][0]
        self.assertTrue(chunk["complete"])
        self.assertEqual(chunk["reservation"], "spent")
        self.assertIn("2024-12-25", chunk["holidays"])
        self.assertEqual(chunk["skipped"], [{
            "session": "2024-12-25", "reason": "closed", "calendar": "CME-no-settlement",
        }])
        self.assertNotIn("2024-12-24", [row["session"] for row in chunk["skipped"]])
        self.assertIn("2024-12-24", chunk["holidays"])

    def test_no_records_in_skipped_keeps_the_reservation_pending(self):
        seen = {}

        def download(http, key, month, out_dir):
            with open(self.report, encoding="utf-8") as fh:
                seen["before"] = json.load(fh)
            return {
                "sessions_added": [],
                "skipped": [{"session": "2024-06-04", "reason": "no records"}],
                "holidays": [],
            }

        calls = []
        with mock.patch.object(B, "download_month", download):
            rc = self.main(["--download", "--month", "2024-06"], _header_http(calls))
        self.assertEqual(rc, 0)
        self.assertFalse(any("timeseries.get_range" in url for url in calls))
        self.assertEqual(seen["before"]["chunks"][0]["reservation"], "pending")
        self.assertEqual(Decimal(seen["before"]["reserved_usd"]), Decimal("0.24"))
        report = self.load()
        self.assertFalse(report["chunks"][0]["complete"])
        self.assertEqual(report["chunks"][0]["reservation"], "pending")
        self.assertEqual(report["spent_usd"], "0")
        self.assertEqual(Decimal(report["reserved_usd"]), Decimal("0.24"))
        self.assertIn("pending reservation", report["reason"])

        calls2 = []
        rc = self.main(["--download", "--month", "2024-06"], _header_http(calls2))
        self.assertEqual(rc, 4)
        self.assertEqual(calls2, [])
        self.assertIn("pending reservation for 2024-06", self.load()["reason"])
        self.assertIn("0.24", self.load()["reason"])


class Reservation(_Run):
    def test_reservation_is_on_disk_before_get_range(self):
        calls = []

        def on_range(_url):
            with open(self.report, encoding="utf-8") as fh:
                body = json.load(fh)
            chunk = body["chunks"][0]
            self.assertEqual(chunk["month"], "2024-06")
            self.assertEqual(chunk["reservation"], "pending")
            self.assertEqual(chunk["reserved_usd"], chunk["get_cost"])
            self.assertEqual(body["reserved_usd"], chunk["reserved_usd"])
            self.assertFalse(chunk["complete"])

        rc = self.main(["--download", "--month", "2024-06"], _header_http(calls, on_range=on_range))
        self.assertEqual(rc, 0, self.load().get("reason"))
        self.assertEqual(len([u for u in calls if "timeseries.get_range" in u]), 24)
        report = self.load()
        self.assertEqual(report["chunks"][0]["reservation"], "spent")
        self.assertTrue(report["chunks"][0]["complete"])
        self.assertEqual(Decimal(report["spent_usd"]), Decimal("0.24"))
        self.assertEqual(report["reserved_usd"], "0")
        self.assertFalse(os.path.exists(self.report + ".tmp"))

    def test_pending_blocks_until_release_then_a_new_download_is_allowed(self):
        report = B.blank_report()
        report["chunks"] = [{
            "month": "2024-06", "complete": False, "get_cost": "1.25",
            "reserved_usd": "1.25", "reservation": "pending",
            "sessions_added": [], "skipped": [], "holidays": [],
            "committed_sha": None,
        }]
        self.save(report)
        rc = self.main(["--download", "--month", "2024-06"], _boom)
        self.assertEqual(rc, 4)
        self.assertIn("pending reservation for 2024-06 of 1.25 USD", self.load()["reason"])
        self.assertIn("reconcile", self.load()["reason"])

        rc = self.main(["--reconcile-reservation", "2024-06", "--reconcile-as", "release"], _boom)
        self.assertEqual(rc, 0)
        released = self.load()["chunks"][0]
        self.assertEqual(released["reservation"], "released")
        self.assertEqual(released["reserved_usd"], "0")
        self.assertEqual(self.load()["spent_usd"], "0")
        self.assertEqual(self.load()["reserved_usd"], "0")

        calls = []
        rc = self.main(["--download", "--month", "2024-06"], _header_http(calls))
        self.assertEqual(rc, 0, self.load().get("reason"))
        self.assertTrue(any("timeseries.get_range" in url for url in calls))
        self.assertEqual(self.load()["chunks"][0]["reservation"], "spent")

    def test_reconcile_spent_counts_once_and_a_later_download_is_a_new_charge(self):
        report = B.blank_report()
        report["spent_usd"] = "2"
        report["chunks"] = [{
            "month": "2024-06", "complete": False, "get_cost": "1.50",
            "reserved_usd": "1.50", "reservation": "pending",
            "sessions_added": [], "skipped": [], "holidays": [],
            "committed_sha": None,
        }]
        self.save(report)
        rc = self.main(["--reconcile-reservation", "2024-06", "--reconcile-as", "spent"], _boom)
        self.assertEqual(rc, 0)
        self.assertEqual(Decimal(self.load()["spent_usd"]), Decimal("3.50"))
        self.assertEqual(self.load()["chunks"][0]["reservation"], "spent")
        self.assertEqual(self.load()["reserved_usd"], "0")
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            rc = self.main(["--reconcile-reservation", "2024-06", "--reconcile-as", "spent"], _boom)
        self.assertEqual(rc, 2)
        self.assertIn("no pending reservation", err.getvalue())
        self.assertEqual(Decimal(self.load()["spent_usd"]), Decimal("3.50"))
        calls = []
        rc = self.main(["--download", "--month", "2024-06"], _header_http(calls))
        self.assertEqual(rc, 0, self.load().get("reason"))
        self.assertEqual(Decimal(self.load()["spent_usd"]), Decimal("3.74"))

    def test_cap_is_spent_plus_reserved_plus_chunk_and_over_cap_does_not_reserve(self):
        report = B.blank_report()
        report["spent_usd"] = "20"
        report["chunks"] = [{
            "month": "2024-07", "complete": False, "get_cost": "10",
            "reserved_usd": "10", "reservation": "pending",
            "sessions_added": [], "skipped": [], "holidays": [],
            "committed_sha": None,
        }]
        self.save(report)
        calls = []
        state = {"n": 0}

        def http(method, url, headers, body):
            calls.append(url)
            if _endpoint(url) == "metadata.get_cost":
                state["n"] += 1
                return 200, {}, b"6" if state["n"] == 1 else b"0"
            if _endpoint(url) == "timeseries.get_range":
                raise AssertionError("downloaded past spent + reserved + chunk")
            return 200, {}, b"1"

        rc = self.main(["--download", "--month", "2024-08"], http)
        self.assertEqual(rc, 3)
        self.assertFalse(any("timeseries.get_range" in url for url in calls))
        saved = self.load()
        self.assertIn("HARD CAP", saved["reason"])
        self.assertIn("reserved", saved["reason"])
        self.assertIn("2024-08", saved["reason"])
        self.assertEqual(Decimal(saved["spent_usd"]), Decimal("20"))
        self.assertEqual(Decimal(saved["reserved_usd"]), Decimal("10"))
        august = [c for c in saved["chunks"] if c["month"] == "2024-08"][0]
        self.assertNotEqual(august.get("reservation"), "pending")
        self.assertFalse(august["complete"])

    def test_spent_plus_reserved_plus_chunk_equal_to_the_cap_still_downloads(self):
        report = B.blank_report()
        report["spent_usd"] = "20"
        report["chunks"] = [{
            "month": "2024-07", "complete": False, "get_cost": "10",
            "reserved_usd": "10", "reservation": "pending",
            "sessions_added": [], "skipped": [], "holidays": [],
            "committed_sha": None,
        }]
        self.save(report)
        calls = []
        state = {"n": 0}

        def http(method, url, headers, body):
            calls.append(url)
            if _endpoint(url) == "metadata.get_cost":
                state["n"] += 1
                return 200, {}, b"5" if state["n"] == 1 else b"0"
            if _endpoint(url) == "timeseries.get_range":
                kind = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["schema"][0]
                fields = C.STATS_FINGERPRINT if kind == "statistics" else [
                    "instrument_id", "strike_price", "expiration", "instrument_class", "symbol"]
                return 200, {}, (",".join(fields) + "\n").encode("utf-8")
            return 200, {}, b"1"

        rc = self.main(["--download", "--month", "2024-08"], http)
        self.assertEqual(rc, 0, self.load().get("reason"))
        self.assertEqual(len([u for u in calls if "timeseries.get_range" in u]), 24)
        saved = self.load()
        self.assertEqual(Decimal(saved["spent_usd"]), Decimal("25"))
        self.assertEqual(Decimal(saved["reserved_usd"]), Decimal("10"))
        august = [c for c in saved["chunks"] if c["month"] == "2024-08"][0]
        self.assertEqual(august["reservation"], "spent")
        self.assertTrue(august["complete"])


class AtomicReport(_Run):
    def test_write_report_replaces_a_tmp_file_and_keeps_the_previous_on_failure(self):
        path = Path(self.report)
        first = B.blank_report()
        first["reason"] = "first"
        B.write_report(path, first)
        original = path.read_text(encoding="utf-8")
        self.assertNotIn(".tmp", original)
        self.assertFalse(path.with_name(path.name + ".tmp").exists())
        second = B.blank_report()
        second["reason"] = "second"

        def explode(src, dst):
            raise OSError("disk full")

        with mock.patch("cme_options_backfill.os.replace", explode):
            with self.assertRaises(OSError):
                B.write_report(path, second)
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        tmp = path.with_name(path.name + ".tmp")
        self.assertTrue(tmp.is_file())
        self.assertIn("second", tmp.read_text(encoding="utf-8"))
        tmp.unlink()
        B.write_report(path, second)
        self.assertIn("second", path.read_text(encoding="utf-8"))
        self.assertFalse(tmp.exists())


class ListSkipped(_Run):
    def test_lists_only_months_with_a_non_empty_skipped_and_never_calls_databento(self):
        report = B.blank_report()
        report["chunks"] = [
            {"month": "2024-06", "skipped": [{"session": "2024-06-04", "reason": "sanity"}]},
            {"month": "2024-07", "skipped": []},
            {"month": "2024-08"},
            {"month": "2024-09", "skipped": [{"session": "2024-09-02", "reason": "exists"}]},
        ]
        self.save(report)
        with open(self.report, "rb") as fh:
            before = fh.read()
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            rc = self.main(["--list-skipped"], _boom, env={})
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue().splitlines(), ["2024-06", "2024-09"])
        with open(self.report, "rb") as fh:
            self.assertEqual(fh.read(), before)

    def test_reconcile_flags_do_not_call_databento_when_the_month_is_unknown(self):
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            rc = self.main(["--reconcile-as", "spent"], _boom, env={})
        self.assertEqual(rc, 2)
        self.assertIn("--reconcile-reservation", buf.getvalue())
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            rc = self.main(["--reconcile-reservation", "1999-01", "--reconcile-as", "release"], _boom, env={})
        self.assertEqual(rc, 2)
        self.assertIn("unknown month", err.getvalue())
        self.assertFalse(os.path.exists(self.report))


if __name__ == "__main__":
    unittest.main()

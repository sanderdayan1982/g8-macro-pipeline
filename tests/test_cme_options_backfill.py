"""FAMILIAS-00 parte B — backfill Databento. Sin red: el HTTP es una función falsa.

No modifica el colector. Comprueba el tope de 35 USD, que no se pisan sesiones
existentes (tampoco las de 2026-06-01 en adelante) y que el CSV lleva origin=backfill.
"""
import csv
import io
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import cme_options_backfill as B  # noqa: E402
import cme_options_collector as C  # noqa: E402


def _stat(iid, stat_type, price="", quantity=""):
    row = {col: "" for col in C.STATS_FINGERPRINT}
    row.update({
        "instrument_id": iid,
        "stat_type": stat_type,
        "ts_ref": "2024-06-03T21:00:00.000000000Z",
        "price": price,
        "quantity": quantity,
        "symbol": "EUUQ4",
        "stat_flags": "3",
    })
    return row


def _defs():
    return [{
        "instrument_id": "1",
        "strike_price": "1.1000",
        "expiration": "2024-07-05T00:00:00.000000000Z",
        "instrument_class": "C",
        "symbol": "EUUQ4 C1100",
    }]


class Cap(unittest.TestCase):
    def test_above_35_blocks_and_below_or_equal_does_not(self):
        self.assertTrue(B.over_cap(Decimal("35.01")))
        self.assertFalse(B.over_cap(Decimal("35.00")))
        self.assertFalse(B.over_cap(Decimal("34.99")))

    def test_main_does_not_download_when_cost_exceeds_cap(self):
        calls = []

        def http(method, url, headers, body):
            calls.append(url)
            if "timeseries" in url:
                raise AssertionError("download started")
            if "get_cost" in url:
                return 200, {}, b"20.00"
            return 200, {}, b"1"

        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        rc = B.main(
            ["--download"],
            http=http,
            env={"DATABENTO_API_KEY": "unit-test-key"},
            out_dir=os.path.join(tmp, "canonical"),
            report_path=os.path.join(tmp, "backfill_report.json"),
        )
        self.assertNotEqual(rc, 0)
        self.assertFalse(any("timeseries" in url for url in calls))
        self.assertEqual(os.listdir(os.path.join(tmp, "canonical")) if os.path.isdir(os.path.join(tmp, "canonical")) else [], [])

    def test_default_is_cost_only_even_when_under_cap(self):
        calls = []

        def http(method, url, headers, body):
            calls.append(url)
            if "timeseries" in url:
                raise AssertionError("dry-run downloaded")
            if "get_cost" in url:
                return 200, {}, b"1.00"
            return 200, {}, b"3"

        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        rc = B.main(
            [],
            http=http,
            env={"DATABENTO_API_KEY": "unit-test-key"},
            out_dir=os.path.join(tmp, "canonical"),
            report_path=os.path.join(tmp, "backfill_report.json"),
        )
        self.assertEqual(rc, 0)
        self.assertFalse(any("timeseries" in url for url in calls))
        self.assertTrue(any("metadata.get_cost" in url for url in calls))
        self.assertTrue(any("metadata.get_record_count" in url for url in calls))
        self.assertTrue(any("metadata.get_billable_size" in url for url in calls))


class OverwriteAndOrigin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.out = os.path.join(self.tmp, "canonical")
        os.makedirs(self.out)

    def test_existing_and_protected_sessions_are_not_overwritten(self):
        old = os.path.join(self.out, "2024-06-03")
        os.makedirs(old)
        with open(os.path.join(old, "6E.csv"), "wb") as fh:
            fh.write(b"OLD\n")
        protected = os.path.join(self.out, "2026-06-01")
        os.makedirs(protected)
        with open(os.path.join(protected, "6E.csv"), "wb") as fh:
            fh.write(b"KEEP\n")
        self.assertEqual(B.session_blocked(self.out, date(2024, 6, 3)), "exists")
        self.assertEqual(B.session_blocked(self.out, date(2026, 6, 1)), "protected")
        sample = {"6E": [{"session": "2024-06-03", "root": "6E", "type": "FUT", "symbol": "6EM4",
                          "expiry": "2024-06-17", "right": "", "strike": "", "settle": "1",
                          "settle_flag": "3", "oi": "1", "origin": "backfill"}]}
        self.assertFalse(B.write_session(self.out, date(2024, 6, 3), sample))
        self.assertFalse(B.write_session(self.out, date(2026, 6, 1), sample))
        with open(os.path.join(old, "6E.csv"), "rb") as fh:
            self.assertEqual(fh.read(), b"OLD\n")
        with open(os.path.join(protected, "6E.csv"), "rb") as fh:
            self.assertEqual(fh.read(), b"KEEP\n")

    def test_origin_column_is_backfill(self):
        recs, unknown, is_opt = B.assemble_product(
            [_stat("1", C.STAT_SETTLE, price="1.10"), _stat("1", C.STAT_OI, quantity="4")],
            _defs(), "EUU.OPT", "2024-06-03")
        self.assertEqual(unknown, 0)
        self.assertTrue(is_opt)
        self.assertEqual(recs[0]["origin"], "backfill")
        self.assertTrue(B.write_session(self.out, date(2024, 6, 3), {"EUU": recs}))
        with open(os.path.join(self.out, "2024-06-03", "EUU.csv"), encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(rows[0]["origin"], "backfill")
        self.assertEqual(rows[0]["session"], "2024-06-03")
        self.assertEqual(rows[0]["strike"], "1.1")

    def test_402_removes_partial_session_and_keeps_existing(self):
        protected = os.path.join(self.out, "2026-06-01")
        os.makedirs(protected)
        with open(os.path.join(protected, "6E.csv"), "wb") as fh:
            fh.write(b"KEEP\n")

        def http(method, url, headers, body):
            if "timeseries" in url:
                # A partial directory may already exist if a previous product wrote one.
                return 402, {}, b"monthly budget exceeded"
            return 200, {}, b"1"

        with self.assertRaises(B.PaymentRequired):
            B.download_sessions(http, "unit-test-key", [date(2024, 6, 3)], self.out)
        self.assertFalse(os.path.exists(os.path.join(self.out, "2024-06-03")))
        with open(os.path.join(protected, "6E.csv"), "rb") as fh:
            self.assertEqual(fh.read(), b"KEEP\n")


class QuoteShape(unittest.TestCase):
    def test_missing_key_writes_nothing(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)

        def http(method, url, headers, body):
            raise AssertionError("network")

        rc = B.main(["--dry-run"], http=http, env={}, out_dir=tmp,
                    report_path=os.path.join(tmp, "backfill_report.json"))
        self.assertEqual(rc, 2)
        self.assertEqual(os.listdir(tmp), [])

    def test_range_and_parents_match_the_order(self):
        self.assertEqual(B.START, date(2024, 6, 1))
        self.assertEqual(B.END_INCLUSIVE, date(2026, 5, 31))
        self.assertEqual(B.END_EXCLUSIVE, date(2026, 6, 1))
        params = B._range_params("statistics")
        self.assertEqual(params["dataset"], "GLBX.MDP3")
        self.assertEqual(params["stype_in"], "parent")
        self.assertEqual(params["symbols"].split(","), C.OPT_PARENTS + C.FUT_PARENTS)
        self.assertEqual(params["start"], "2024-06-01")
        self.assertEqual(params["end"], "2026-06-01")


if __name__ == "__main__":
    unittest.main()

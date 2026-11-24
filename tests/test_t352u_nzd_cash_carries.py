"""t352u, orden expresa Sander 23-nov: un valor arrastrado SIN marca PROXY cuenta como incidencia y no vota.
Los días PROXY en que la fuente no imprime no cuentan como arrastrados. El desfase T+1 documentado no es un arrastre."""
import csv
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import health_derivatives as HD  # noqa: E402


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class OfficialCarries(unittest.TestCase):
    def test_proxy_gaps_do_not_count(self):
        official = [datetime(2026, 11, d).date() for d in (12, 20)]
        all_dates = official + [datetime(2026, 11, d).date() for d in (13, 16, 17, 18, 19)]
        self.assertEqual(HD.official_carries(5, official, all_dates), 0)

    def test_official_keep_is_a_carry(self):
        official = [datetime(2026, 11, d).date() for d in (12, 20)]
        self.assertEqual(HD.official_carries(1, official, official), 1)

    def test_documented_tplus1_is_not_in_this_count(self):
        # T+1 lag is calendar delay, not a conserved row: kept_from_previous=0.
        official = [datetime(2026, 11, 20).date()]
        self.assertEqual(HD.official_carries(0, official, official), 0)


class CashCarries(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        src_cal = Path(ROOT) / "sources" / "calendars.csv"
        (self.tmp / "sources").mkdir()
        shutil.copy(src_cal, self.tmp / "sources" / "calendars.csv")
        (self.tmp / "data" / "_ingest" / "latest").mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _write(self, rows, kept, max_date="20261120"):
        # rows: list of (date, value, status or "")
        lines = ["Date,Value,Status,Source,ProxyDate\n"]
        for d, v, st in rows:
            if st == "PROXY":
                lines.append("%s,%s,PROXY,NZD_OCR,%s\n" % (d, v, d))
            else:
                lines.append("%s,%s,,,\n" % (d, v))
        (self.tmp / "data" / "NZD_CASH_ON.csv").write_text("".join(lines), encoding="utf-8")
        ev = {
            "fetch_detail": {
                "nzd": {
                    "rc": 0,
                    "errors": [],
                    "finished_utc": "2026-11-23T20:02:55Z",
                    "files": {
                        "NZD_CASH_ON.csv": {
                            "status": "WRITTEN",
                            "kept_from_previous": kept,
                            "max_date": max_date,
                            "rows": len(rows),
                        },
                        "NZD_BOND_10Y.csv": {"max_date": "20261120"},
                    },
                }
            },
            "families": {
                "NZ-B2": {
                    "download_kind": "FRESH",
                    "status": "NOOP",
                    "held": None,
                    "files": {"NZD_CASH_ON.csv": {"status": "NOOP", "held": None, "src_max": max_date}},
                }
            },
        }
        path = self.tmp / "data" / "_ingest" / "latest" / "vps-prod__nzchf-tona.json"
        path.write_text(json.dumps(ev), encoding="utf-8")
        return ev

    def _run(self, rows, kept, max_date="20261120"):
        ev = self._write(rows, kept, max_date)
        # evidence must not be rewritten
        before = path_text = (self.tmp / "data" / "_ingest" / "latest" / "vps-prod__nzchf-tona.json").read_text()
        item = {"status": "UNKNOWN", "file": "NZD_CASH_ON.csv"}
        HD.cash(self.tmp, ts("2026-11-23T21:00:00Z"), item, lambda p: json.loads(Path(p).read_text()))
        after = (self.tmp / "data" / "_ingest" / "latest" / "vps-prod__nzchf-tona.json").read_text()
        self.assertEqual(before, after)
        self.assertEqual(json.loads(after)["fetch_detail"]["nzd"]["files"]["NZD_CASH_ON.csv"]["kept_from_previous"], kept)
        return item

    def test_proxy_days_source_does_not_print_certify_current(self):
        # t352u 3b: the 5 PROXY gaps (13,16-19) plus official 12 and 20.
        rows = [
            ("2026-11-12", "2.80", ""),
            ("2026-11-13", "2.75", "PROXY"),
            ("2026-11-16", "2.75", "PROXY"),
            ("2026-11-17", "2.75", "PROXY"),
            ("2026-11-18", "2.75", "PROXY"),
            ("2026-11-19", "2.75", "PROXY"),
            ("2026-11-20", "2.80", ""),
        ]
        item = self._run(rows, kept=5)
        self.assertEqual(item["status"], "CURRENT")
        self.assertNotEqual(item.get("state"), "CARRIED_OFFICIAL")
        self.assertEqual(item["have_max"], "2026-11-20")

    def test_official_carry_is_incidencia_and_does_not_vote(self):
        rows = [("2026-11-12", "2.80", ""), ("2026-11-20", "2.80", "")]
        item = self._run(rows, kept=1)
        self.assertEqual(item["status"], "LATE")
        self.assertEqual(item["state"], "CARRIED_OFFICIAL")
        self.assertIn("sin marca PROXY", item["reason"])
        self.assertNotEqual(item["status"], "CURRENT")

    def test_zero_kept_still_current(self):
        rows = [("2026-11-20", "2.80", "")]
        item = self._run(rows, kept=0)
        self.assertEqual(item["status"], "CURRENT")
        self.assertEqual(item["state"], "VERIFIED_SPARSE")


if __name__ == "__main__":
    unittest.main()

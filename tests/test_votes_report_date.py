"""Orden Sander 17-nov t237u. report_date al final.

Semanales (dealers y flujos de Japón/MoF): fecha de publicación del dato.
Diarios: vacía. El parser no nombra esos módulos ni un campo de publicación,
así que no se copia fecha_cierre ni se estima. La migración es idempotente.
"""
import csv
import io
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "scripts", "tools"))
import votes_ingest as V  # noqa: E402
import votes_csv_merge as M  # noqa: E402

VOTES = os.path.join(ROOT, "data", "votes.csv")
OLD = "fecha_cierre,script,ccy,estado,session_type\n2026-01-05,E21,EUR,FRESH,REGULAR\n"


class ReportDateColumn(unittest.TestCase):
    def test_columns_end_with_report_date(self):
        self.assertEqual(V.COLUMNS[-1], "report_date")
        self.assertEqual(V.COLUMNS[-2], "session_type")

    def test_published_header_ends_with_an_empty_report_date(self):
        with open(VOTES, encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            self.assertEqual(reader.fieldnames[-1], "report_date")
            for row in reader:
                self.assertEqual(row.get("report_date") or "", "", row.get("script"))

    def test_migration_is_idempotent_and_leaves_old_cells(self):
        once = V._ensure_report_date_column(OLD.encode("utf-8"))
        twice = V._ensure_report_date_column(once)
        self.assertEqual(twice, once)
        text = once.decode("utf-8")
        self.assertTrue(text.startswith("fecha_cierre,script,ccy,estado,session_type,report_date\n"))
        row = next(csv.DictReader(io.StringIO(text)))
        self.assertEqual(row["estado"], "FRESH")
        self.assertEqual(row["session_type"], "REGULAR")
        self.assertEqual(row["report_date"], "")
        already = V._ensure_report_date_column(once)
        self.assertEqual(already, once)

    def test_merge_keeps_an_empty_report_date_and_is_idempotent(self):
        header = "fecha_cierre,script,ccy,estado,report_date\n"
        main = (header + "2026-01-05,E21,EUR,FRESH,\n").encode("utf-8")
        other = (header + "2026-01-05,E21,EUR,FRESH,\n2026-01-06,E21,GBP,FRESH,\n").encode("utf-8")
        merged = M.merge_votes_bytes(main, other)
        self.assertTrue(merged.startswith(main))
        again = M.merge_votes_bytes(merged, other)
        self.assertEqual(again, merged)
        rows = list(csv.DictReader(io.StringIO(merged.decode("utf-8"))))
        self.assertEqual([r["ccy"] for r in rows], ["EUR", "GBP"])
        self.assertTrue(all(r["report_date"] == "" for r in rows))

    def test_new_rows_do_not_estimate_report_date(self):
        path = os.path.join(tempfile.mkdtemp(), "votes.csv")
        with open(path, "wb") as fh:
            fh.write(OLD.encode("utf-8"))
        from datetime import date
        row = V._blank_row(
            date(2026, 1, 6), "E21", "EUR", "FRESH", "NA", False,
            "", "", "", None, 1, 1, "1", "",
        )
        self.assertEqual(row["report_date"], "")
        self.assertEqual(V.append_rows(path, [row]), 1)
        with open(path, encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(rows[0]["report_date"], "")
        self.assertEqual(rows[1]["report_date"], "")
        self.assertEqual(rows[0]["estado"], "FRESH")


if __name__ == "__main__":
    unittest.main()

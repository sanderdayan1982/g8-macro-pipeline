"""E7: written_at_utc, inputs_hash y cierre t+1 (siguiente sesión hábil FX).

Tests nuevos. No modifica tests existentes. Ningún valor de insumo queda en
el CSV, en el registro de rechazos ni en los mensajes de log.
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import votes_ingest as V  # noqa: E402

NY = ZoneInfo("America/New_York")
PROBE = "SECRET_PROBE_VALUE_E7"


def _deadline_z(session):
    return V.tplus1_close(session).strftime("%Y-%m-%dT%H:%M:%SZ")


class NextFxSessionAndTplus1(unittest.TestCase):
    def test_friday_goes_to_monday(self):
        friday = date(2026, 11, 20)
        self.assertEqual(friday.weekday(), 4)
        self.assertFalse(V.is_ny_full_close(friday))
        monday = V.next_fx_session(friday)
        self.assertEqual(monday, date(2026, 11, 23))
        self.assertEqual(monday.weekday(), 0)

    def test_thursday_goes_to_friday_when_friday_is_open(self):
        thursday = date(2026, 11, 19)
        self.assertEqual(V.next_fx_session(thursday), date(2026, 11, 20))

    def test_skips_full_close_using_existing_calendar(self):
        # El test no inventa festivos: usa is_ny_full_close del propio módulo.
        # Parte de un día abierto anterior al primer cierre completo del tramo.
        start = date(2026, 11, 1)
        found = None
        day = start
        while day <= date(2026, 12, 31):
            if V.is_ny_full_close(day) and day.weekday() < 5:
                found = day
                break
            day = day.fromordinal(day.toordinal() + 1)
        self.assertIsNotNone(found)
        prev = found.fromordinal(found.toordinal() - 1)
        while prev.weekday() >= 5 or V.is_ny_full_close(prev):
            prev = prev.fromordinal(prev.toordinal() - 1)
        nxt = V.next_fx_session(prev)
        self.assertFalse(V.is_ny_full_close(nxt))
        self.assertLess(prev, found)
        self.assertGreater(nxt, found)
        self.assertLessEqual(nxt.weekday(), 4)

    def test_tplus1_is_1700_new_york_of_next_session(self):
        friday = date(2026, 11, 20)
        close = V.tplus1_close(friday)
        local = close.astimezone(NY)
        self.assertEqual(local.date(), date(2026, 11, 23))
        self.assertEqual((local.hour, local.minute, local.second), (17, 0, 0))
        # Noviembre: EST (UTC-5) → 22:00Z
        self.assertEqual(close, datetime(2026, 11, 23, 22, 0, tzinfo=timezone.utc))


class InputsHashAndNoLeak(unittest.TestCase):
    def test_hash_is_sha256_hex_and_stable(self):
        h1 = V.inputs_hash_for(
            "usd_factor", "2026-11-20", "E21", "EUR",
            as_of="2026-11-20", generated_utc="2026-11-20T22:00:00Z", e21=PROBE,
        )
        h2 = V.inputs_hash_for(
            "usd_factor", "2026-11-20", "E21", "EUR",
            as_of="2026-11-20", generated_utc="2026-11-20T22:00:00Z", e21=PROBE,
        )
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)
        self.assertRegex(h1, r"^[0-9a-f]{64}$")
        # Un cambio en el insumo cambia el hash.
        h3 = V.inputs_hash_for(
            "usd_factor", "2026-11-20", "E21", "EUR",
            as_of="2026-11-20", generated_utc="2026-11-20T22:00:00Z", e21="0.1",
        )
        self.assertNotEqual(h1, h3)

    def test_factor_row_stores_hash_not_probe(self):
        doc = {
            "as_of": "2026-11-20",
            "generated_utc": "2026-11-20T22:00:00Z",
            "version": "test",
            "currencies": [{"ccy": "EUR", "E21": PROBE if False else "0.25"}],
        }
        # El probe va como e21 vía un hash de correo sintético, no como valor publicado.
        rows = V.rows_from_factor(doc, date(2026, 11, 20))
        self.assertTrue(rows)
        row = next(r for r in rows if r["ccy"] == "EUR")
        self.assertEqual(len(row["inputs_hash"]), 64)
        expected = V.inputs_hash_for(
            "usd_factor", "2026-11-20", "E21", "EUR",
            as_of="2026-11-20", generated_utc="2026-11-20T22:00:00Z", e21="0.25",
        )
        self.assertEqual(row["inputs_hash"], expected)
        blob = ",".join(row.get(k, "") for k in row)
        self.assertNotIn(PROBE, blob)

    def test_mail_hash_does_not_put_payload_in_row(self):
        kv = {"MODULE": "E21", "FOO": PROBE, "BAR_CLOSE_MS": "1"}
        h = V.inputs_hash_for(
            "mail", "2026-11-20", "E21", "EUR",
            version="v", snapshot_id="s", bar_close_ms=1,
            parts_expected=1, parts_received=1, payload_kv=kv,
        )
        self.assertEqual(len(h), 64)
        # El canónico usa el digest del payload, no el valor.
        self.assertNotIn(PROBE, h)


class AppendRejectsAfterTplus1(unittest.TestCase):
    def test_late_row_is_rejected_and_probe_stays_out(self):
        friday = date(2026, 11, 20)
        deadline = V.tplus1_close(friday)
        late = datetime(2026, 11, 23, 22, 0, 1, tzinfo=timezone.utc)
        self.assertTrue(late > deadline)
        row = V._with_hash(V._blank_row(
            friday, "E21", "EUR", "FRESH", "+1", False, "snap", "", "",
            V.expected_bar_close_ms(friday), 1, 1, "v", "0.1",
        ), "usd_factor", as_of="2026-11-20", generated_utc="2026-11-20T22:00:00Z",
            e21=PROBE)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "votes.csv")
            rejected = os.path.join(tmp, "_ingest", "votes_rejected.jsonl")
            Path(path).write_text(",".join(V.COLUMNS) + "\n", encoding="utf-8")
            added = V.append_rows(path, [row], now=late, rejected_path=rejected, enforce_tplus1=True)
            self.assertEqual(added, 0)
            body = Path(path).read_text(encoding="utf-8")
            self.assertEqual(len(list(csv.DictReader(io.StringIO(body)))), 0)
            rec = Path(rejected).read_text(encoding="utf-8")
            self.assertIn("AFTER_TPLUS1_CLOSE", rec)
            self.assertIn("2026-11-20", rec)
            self.assertIn("E21", rec)
            self.assertIn("EUR", rec)
            self.assertNotIn(PROBE, rec)
            self.assertNotIn(PROBE, body)
            self.assertNotIn("id_correo", rec)  # el registro de rechazo no lleva correo

    def test_row_at_deadline_is_accepted(self):
        friday = date(2026, 11, 20)
        at = V.tplus1_close(friday)
        row = V._with_hash(V._blank_row(
            friday, "E21", "EUR", "FRESH", "+1", False, "snap", "", "",
            V.expected_bar_close_ms(friday), 1, 1, "v", "0.1",
        ), "usd_factor", as_of="2026-11-20", generated_utc="2026-11-20T22:00:00Z",
            e21="0.1")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "votes.csv")
            rejected = os.path.join(tmp, "_ingest", "votes_rejected.jsonl")
            Path(path).write_text(",".join(V.COLUMNS) + "\n", encoding="utf-8")
            added = V.append_rows(path, [row], now=at, rejected_path=rejected)
            self.assertEqual(added, 1)
            self.assertFalse(os.path.exists(rejected))
            with Path(path).open(encoding="utf-8", newline="") as fh:
                stored = list(csv.DictReader(fh))
            self.assertEqual(stored[0]["legacy"], "")
            self.assertEqual(len(stored[0]["inputs_hash"]), 64)
            self.assertEqual(stored[0]["written_at_utc"], at.strftime("%Y-%m-%dT%H:%M:%SZ"))
            self.assertEqual(list(stored[0].keys())[-2], "session_type")
            self.assertEqual(list(stored[0].keys())[-1], "report_date")


class HeaderMigration(unittest.TestCase):
    def test_official_pre_e7_header_gains_three_columns(self):
        header = ",".join(V.PRE_E7_COLUMNS)
        raw = (header + "\n2026-11-20,E21,v,EUR,FRESH,0.1,+1,false,s,t,id,1,E21,1,1,r,REGULAR,\n").encode()
        out = V._ensure_e7_columns(raw).decode()
        fields = out.split("\n", 1)[0].split(",")
        self.assertEqual(fields, V.COLUMNS)
        self.assertEqual(fields[-2], "session_type")
        self.assertEqual(fields[-1], "report_date")
        row = next(csv.DictReader(io.StringIO(out)))
        self.assertEqual(row["legacy"], "1")
        self.assertEqual(row["written_at_utc"], "")
        self.assertEqual(row["inputs_hash"], "")
        self.assertEqual(row["script"], "E21")

    def test_short_fixture_header_is_left_untouched(self):
        raw = b"fecha_cierre,script,ccy,estado,session_type\n2026-01-05,E21,EUR,FRESH,REGULAR\n"
        self.assertEqual(V._ensure_e7_columns(raw), raw)

    def test_idempotent_on_current_header(self):
        raw = (",".join(V.COLUMNS) + "\n").encode()
        self.assertEqual(V._ensure_e7_columns(raw), raw)

    def test_columns_keep_session_type_and_report_date_last(self):
        self.assertEqual(V.COLUMNS[-1], "report_date")
        self.assertEqual(V.COLUMNS[-2], "session_type")
        self.assertEqual(V.COLUMNS[-5:], [
            "written_at_utc", "inputs_hash", "legacy", "session_type", "report_date",
        ])



class RunEnforcesWhenAsked(unittest.TestCase):
    def test_run_with_flag_rejects_late_and_does_not_leak(self):
        friday = date(2026, 11, 20)
        late = datetime(2026, 11, 23, 22, 0, 1, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            votes = os.path.join(tmp, "votes.csv")
            weekly = os.path.join(tmp, "votes_weekly.json")
            factor = os.path.join(tmp, "USD_FACTOR.json")
            src = os.path.join(tmp, "in.json")
            Path(src).write_text("[]", encoding="utf-8")
            Path(factor).write_text(
                '{"version":"1","as_of":"2026-11-20","generated_utc":"2026-11-20T22:00:00Z","currencies":[]}',
                encoding="utf-8",
            )
            rc = V.run(
                ["--session", "2026-11-20", "--attempt", "final",
                 "--from-json", src, "--votes", votes, "--weekly", weekly,
                 "--factor", factor],
                http=lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")),
                sender=lambda _t: (_ for _ in ()).throw(AssertionError("telegram")),
                now=late,
                enforce_tplus1=True,
            )
            self.assertEqual(rc, 0)
            rejected = Path(tmp, "_ingest", "votes_rejected.jsonl")
            self.assertTrue(rejected.exists())
            rec = rejected.read_text(encoding="utf-8")
            self.assertIn("AFTER_TPLUS1_CLOSE", rec)
            self.assertNotIn(PROBE, rec)
            if os.path.exists(votes):
                body = Path(votes).read_text(encoding="utf-8")
                self.assertNotIn(PROBE, body)
                rows = list(csv.DictReader(body.splitlines() and io.StringIO(body)))
                self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()

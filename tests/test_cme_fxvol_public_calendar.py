"""t275u — lista blanca de CME_FXVOL_public.json (solo calendario, sin cifras).

Falla si el projector o el fichero versionado emiten un campo fuera de la
lista permitida. No toca los tests existentes.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts", "tools"))
import cme_public_projection as P  # noqa: E402

# Contrato congelado en este test (independiente de la constante del script).
ALLOWED_TOP = frozenset({
    "status",
    "as_of",
    "reason",
    "generated_utc",
    "fut_roll_window",
    "currencies",
})
ALLOWED_ROLL = frozenset({"start", "end", "active", "imm"})
ALLOWED_CURRENCY = frozenset({"status", "vote", "date", "exp1"})
PUBLIC_FILES = (
    "data/public/CME_FXVOL_public.json",
    "data/public/CME_C1_public.json",
    "data/public/CME_OPTIONS_SURFACE_public.json",
)


def _assert_allowed(self, doc):
    self.assertIsInstance(doc, dict)
    extra = set(doc) - ALLOWED_TOP
    self.assertEqual(extra, set(), extra)
    roll = doc.get("fut_roll_window")
    if roll is not None:
        self.assertIsInstance(roll, dict)
        self.assertEqual(set(roll) - ALLOWED_ROLL, set())
    currencies = doc.get("currencies")
    if currencies is not None:
        self.assertIsInstance(currencies, dict)
        for row in currencies.values():
            self.assertIsInstance(row, dict)
            self.assertEqual(set(row) - ALLOWED_CURRENCY, set())


class CmeFxvolPublicCalendar(unittest.TestCase):
    def test_fxvol_projection_copies_only_calendar_fields(self):
        source = {
            "status": "OK",
            "generated_utc": "2026-11-19T22:00:00Z",
            "as_of": "2026-11-19",
            "fut_roll_window": {
                "start": "2026-12-07",
                "end": "2026-12-19",
                "active": False,
                "imm": "2026-12-16",
                "extra_roll": "no",
            },
            "currencies": {
                "EUR": {
                    "status": "OK",
                    "gate0": {"verdict": "WAIT"},
                    "exp1": "2026-12-04",
                    "dte1": 15,
                    "iv30_atm": 1,
                },
            },
            "iv30_atm": 1,
            "secret": 2,
        }
        out = P.project(source, fxvol_calendar=True)
        _assert_allowed(self, out)
        self.assertEqual(out["generated_utc"], "2026-11-19T22:00:00Z")
        self.assertEqual(out["fut_roll_window"], {
            "start": "2026-12-07",
            "end": "2026-12-19",
            "active": False,
            "imm": "2026-12-16",
        })
        self.assertEqual(out["currencies"]["EUR"]["exp1"], "2026-12-04")
        self.assertNotIn("dte1", out["currencies"]["EUR"])
        self.assertNotIn("iv30_atm", out["currencies"]["EUR"])
        self.assertNotIn("iv30_atm", out)
        self.assertNotIn("secret", out)
        self.assertEqual(out["currencies"]["NZD"]["exp1"], None)

    def test_c1_and_options_do_not_gain_calendar_fields(self):
        source = {
            "status": "OK",
            "as_of": "2026-11-19",
            "generated_utc": "2026-11-19T22:00:00Z",
            "fut_roll_window": {
                "start": "2026-12-07",
                "end": "2026-12-19",
                "active": False,
                "imm": "2026-12-16",
            },
            "currencies": {"EUR": {"status": "OK", "exp1": "2026-12-04", "dte1": 15}},
        }
        out = P.project(source, fxvol_calendar=False)
        _assert_allowed(self, out)
        self.assertNotIn("generated_utc", out)
        self.assertNotIn("fut_roll_window", out)
        self.assertNotIn("exp1", out["currencies"]["EUR"])
        self.assertNotIn("dte1", out["currencies"]["EUR"])

    def test_numbers_are_still_rejected(self):
        with self.assertRaises(SystemExit) as raised:
            P._reject_numbers({"status": "OK", "dte1": 15})
        self.assertEqual(raised.exception.code, 2)

    def test_disallowed_field_is_rejected(self):
        with self.assertRaises(SystemExit) as raised:
            P._reject_disallowed({"status": "OK", "as_of": "2026-11-19", "leak": "x"}, "top")
        self.assertEqual(raised.exception.code, 2)

    def test_cli_only_adds_calendar_on_fxvol_dest(self):
        source = {
            "status": "OK",
            "generated_utc": "2026-11-19T22:00:00Z",
            "as_of": "2026-11-19",
            "fut_roll_window": {
                "start": "2026-12-07",
                "end": "2026-12-19",
                "active": True,
                "imm": "2026-12-16",
            },
            "currencies": {"JPY": {"status": "OK", "exp1": "2026-12-11", "dte1": 22}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "CME_FXVOL_shadow.json")
            fxvol_dest = os.path.join(tmp, "CME_FXVOL_public.json")
            c1_dest = os.path.join(tmp, "CME_C1_public.json")
            with open(src, "w", encoding="utf-8") as fh:
                json.dump(source, fh)
            self.assertEqual(P.main(["cme_public_projection.py", "--out", fxvol_dest, src, "--out", c1_dest, src]), 0)
            with open(fxvol_dest, encoding="utf-8") as fh:
                fxvol = json.load(fh)
            with open(c1_dest, encoding="utf-8") as fh:
                c1 = json.load(fh)
        _assert_allowed(self, fxvol)
        _assert_allowed(self, c1)
        self.assertIn("generated_utc", fxvol)
        self.assertIn("fut_roll_window", fxvol)
        self.assertIn("exp1", fxvol["currencies"]["JPY"])
        self.assertNotIn("dte1", fxvol["currencies"]["JPY"])
        self.assertNotIn("generated_utc", c1)
        self.assertNotIn("fut_roll_window", c1)
        self.assertNotIn("exp1", c1["currencies"]["JPY"])

    def test_versioned_public_files_have_only_allowed_fields(self):
        listed = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
        for path in PUBLIC_FILES:
            self.assertIn(path, listed, path)
            with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
                doc = json.load(fh)
            _assert_allowed(self, doc)
            raw = json.dumps(doc)
            self.assertNotIn("dte1", raw)
            self.assertNotIn("iv30_atm", raw)


if __name__ == "__main__":
    unittest.main()

"""DATOS-ARGOS-01 — paridad del driver con §01-b y campos obligatorios no vacíos.

No modifica tests existentes. El cuadrante 2×2 no está en el render: c3 sigue UNAVAILABLE.
"""
import csv
import json
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import argos_rates as AR  # noqa: E402

JSON_PATH = os.path.join(ROOT, "data", "argos_rates.json")
REQUIRED = (
    "date", "nominal_10y", "real_10y", "breakeven_10y", "term_premium", "rny",
    "dominant_driver", "staleness", "level_change_1d", "level_change_22d", "s01b",
)


def _blank(value):
    if value is None or value == "":
        return True
    if isinstance(value, dict):
        return any(_blank(v) for v in value.values())
    if isinstance(value, list):
        return any(_blank(v) for v in value)
    return False


class DatosArgos01(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(JSON_PATH, encoding="utf-8") as fh:
            cls.doc = json.load(fh)
        cls.raw = AR.render(cls.doc["generated_at"])

    def test_driver_matches_the_panel_22_session_window(self):
        for ccy, row in self.doc["currencies"].items():
            legs = (self.raw.get("legs") or {}).get(ccy) or {}
            self.assertIn("d_be_22", legs, ccy)
            self.assertEqual(row["dominant_driver"], AR.panel_driver(legs), ccy)
            self.assertIn(row["dominant_driver"], ("BE_LED", "REAL_LED", "UNAVAILABLE"), ccy)
            be = legs.get("d_be_22")
            if not isinstance(be, (int, float)) or isinstance(be, bool):
                self.assertEqual(row["dominant_driver"], "UNAVAILABLE", ccy)

    def test_no_mandatory_field_is_blank(self):
        self.assertEqual(self.doc["c3"], {
            "quadrant": "UNAVAILABLE", "value": "UNAVAILABLE", "label": "UNAVAILABLE",
        })
        blob = self.raw["attr"] + self.raw["s01b"] + self.raw["floors"]
        self.assertNotIn("cuadrante", blob.lower())
        self.assertNotIn("2×2", blob)
        for ccy, row in self.doc["currencies"].items():
            for key in REQUIRED:
                self.assertIn(key, row, ccy + " " + key)
                self.assertFalse(_blank(row[key]), "%s %s está vacío" % (ccy, key))
            self.assertNotEqual(row["dominant_driver"], "", ccy)

    def test_chf_real_is_ex_post_nominal_minus_cpi(self):
        """REAL CHF = NOM SNB − IPC a/a BFS. El salto de BE en pb es el paso mensual, no un %."""
        def last_rows(path, n):
            with open(path, encoding="utf-8") as fh:
                rows = [r for r in csv.reader(fh) if r and not r[0].startswith("#")]
            return rows[-n:]

        nom = last_rows(os.path.join(ROOT, "data", "CHF_NOM_10Y.csv"), 1)[0]
        cpi = last_rows(os.path.join(ROOT, "data", "CHF_CPI_YOY.csv"), 2)
        nom_v = float(nom[1])
        cpi_v = float(cpi[-1][4])
        prev = float(cpi[-2][4])
        chf = self.doc["currencies"]["CHF"]
        self.assertAlmostEqual(chf["nominal_10y"], round(nom_v, 2), places=2)
        self.assertAlmostEqual(chf["breakeven_10y"], cpi_v, places=2)
        self.assertAlmostEqual(chf["real_10y"], round(nom_v - cpi_v, 2), places=2)
        self.assertLess(chf["real_10y"], 0)
        step_bp = round((cpi_v - prev) * 100.0, 2)
        painted = (chf.get("panel_deltas_bp") or {}).get("breakeven_10y") or {}
        self.assertEqual(painted.get("1M"), int(round(step_bp)))
        self.assertIsInstance(chf["level_change_1d"]["breakeven_10y"], float)
        self.assertAlmostEqual(chf["level_change_1d"]["breakeven_10y"], step_bp, places=2)


if __name__ == "__main__":
    unittest.main()

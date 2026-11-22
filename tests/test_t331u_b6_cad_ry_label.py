"""t331u B6 — la etiqueta de CAD_RY es BE/real largo (RRB 2050), no 10Y. Sin cambio de cálculo."""
import csv
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CadRyLabel(unittest.TestCase):
    def test_registry_name_is_long_rrb_2050_not_10y(self):
        with open(ROOT / "sources" / "registry.csv", encoding="utf-8") as fh:
            rows = {r["feed_id"]: r for r in csv.DictReader(fh)}
        name = rows["CAD_RY"]["name"]
        self.assertIn("RRB 2050", name)
        self.assertIn("largo", name.lower())
        self.assertNotIn("10Y", name)
        self.assertEqual(rows["CAD_RY"]["primary_access"], "data/RY_G8_CAD.csv")

    def test_dashboard_catalog_and_horizon(self):
        html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        self.assertIn("CAD Real/BE largo (RRB 2050, BoC)", html)
        self.assertNotIn("CAD Real/BE 10Y (RRB, BoC)", html)
        self.assertRegex(html, r"CAD:\s*\{\s*tier:\s*1,\s*instr:\s*'RRB 2050',\s*hz:\s*'largo'\s*\}")
        # otras curvas 10Y no se tocan
        self.assertIn("USD Nominal/Real/BE 10Y", html)
        self.assertIn("GBP Real/BE 10Y", html)


if __name__ == "__main__":
    unittest.main()

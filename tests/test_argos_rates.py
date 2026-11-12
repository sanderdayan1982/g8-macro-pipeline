"""FLUJOS-01 Sander 12-nov-2026 — paridad de data/argos_rates.json contra el render de §01, §01-b y §02.

El HTML sale de los IIFE del dashboard (scripts/argos_render.js). El JSON no recalcula.
"""
import json
import os
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
import sys
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import argos_rates as AR  # noqa: E402

JSON_PATH = os.path.join(ROOT, "data", "argos_rates.json")


def _row_html(html, ccy):
    for row in AR._rows(html):
        if 'class="attr-ccy">%s<' % ccy in row or "<b>%s</b>" % ccy in row:
            if "−USD" in row or "-USD" in row:
                continue
            return row
    return ""


class ArgosRatesParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(JSON_PATH, encoding="utf-8") as fh:
            cls.doc = json.load(fh)
        cls.raw = AR.render(cls.doc["generated_at"])
        cls.again = AR.parse(cls.raw)

    def test_file_matches_a_fresh_render_at_the_same_utc_clock(self):
        self.assertEqual(self.again, self.doc)

    def test_clock_and_unavailable_points(self):
        self.assertRegex(self.doc["generated_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(self.doc["as_of"], self.doc["generated_at"][:10])
        self.assertEqual(self.doc["clock"], "UTC")
        self.assertEqual(self.doc["c3"], {"quadrant": "UNAVAILABLE", "value": "UNAVAILABLE", "label": "UNAVAILABLE"})
        blob = self.raw["attr"] + self.raw["s01b"] + self.raw["floors"]
        self.assertNotIn("driver", blob.lower())
        self.assertNotIn("2×2", blob)
        self.assertNotIn("cuadrante", blob.lower())
        for ccy, row in self.doc["currencies"].items():
            self.assertEqual(row["dominant_driver"], "UNAVAILABLE", ccy)
            self.assertEqual(row["level_change_1d"], "UNAVAILABLE", ccy)
            self.assertEqual(row["level_change_22d"], "UNAVAILABLE", ccy)
            self.assertEqual(row["stale"], row["staleness"] in ("STALE", "DEAD"), ccy)
            self.assertIn(row["staleness"], ("LIVE", "DEGRADED", "STALE", "DEAD"), ccy)

    def test_levels_and_floor_spreads_are_the_painted_numbers(self):
        for ccy, row in self.doc["currencies"].items():
            html = _row_html(self.raw["attr"], ccy)
            self.assertTrue(html, ccy)
            for key in ("nominal_10y", "real_10y", "breakeven_10y", "term_premium", "rny"):
                val = row[key]
                if val == "UNAVAILABLE":
                    self.assertTrue("attr-na" in html or "—" in html, ccy + " " + key)
                else:
                    self.assertIn("%.2f" % val, html, ccy + " " + key)
            deltas = row["panel_deltas_bp"]
            if isinstance(deltas, dict):
                for key, labs in deltas.items():
                    if not isinstance(labs, dict):
                        continue
                    for lab, n in labs.items():
                        if n > 0:
                            token = "%s +%d" % (lab, n)
                        elif n < 0:
                            token = "%s %d" % (lab, n)
                        else:
                            token = "%s +0" % lab
                            if token not in html:
                                token = "%s 0" % lab
                        self.assertIn(token, html, "%s %s %s" % (ccy, key, token))
        for key, row in self.doc["floors"].items():
            html = ""
            for tr in AR._rows(self.raw["floors"]):
                if 'data-fls-ccy="%s"' % key in tr:
                    html = tr
                    break
            self.assertTrue(html, key)
            if row["spread_bp"] != "UNAVAILABLE":
                self.assertIn("%.1f" % abs(row["spread_bp"]), html)
            self.assertIn(row["regime"], html)

    def test_s01b_integers_are_the_painted_cells(self):
        for ccy, row in self.doc["currencies"].items():
            block = row["s01b"]
            html = ""
            for tr in AR._rows(self.raw["s01b"]):
                if "<b>%s</b>" % ccy in tr and "a3-row" not in tr[:40]:
                    html = tr
                    break
            self.assertTrue(html, ccy)
            for key in ("d_rny", "d_tp", "d_fit", "resid"):
                val = block[key]
                if isinstance(val, int):
                    token = ("%+d" % val) if val > 0 else str(val)
                    # bp() pinta +n, -n o 0
                    if val > 0:
                        self.assertIn("+%d" % val, html, ccy + " " + key)
                    else:
                        self.assertIn(str(val), html, ccy + " " + key)
                elif val == "UNAVAILABLE":
                    self.assertTrue(any(w in AR._text(html) for w in ("sin ", "·", "—")), ccy)


if __name__ == "__main__":
    unittest.main()

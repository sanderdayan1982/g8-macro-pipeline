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
        # t332u, orden expresa Sander 22-nov
        nzd = self.doc["floors"]["NZD"]
        self.assertEqual(nzd["spread_bp"], "NA")
        self.assertEqual(nzd["regime"], "SIN DATO")
        self.assertNotEqual(nzd["regime"], "AMPLE")

    def test_clock_and_unavailable_points(self):
        self.assertRegex(self.doc["generated_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(self.doc["as_of"], AR.closed_session(AR.parse_clock(self.doc["generated_at"])).isoformat())
        self.assertEqual(self.doc["clock"], "UTC")
        self.assertEqual(self.doc["c3"], {"quadrant": "UNAVAILABLE", "value": "UNAVAILABLE", "label": "UNAVAILABLE"})
        blob = self.raw["attr"] + self.raw["s01b"] + self.raw["floors"]
        self.assertNotIn("driver", blob.lower())
        self.assertNotIn("2×2", blob)
        self.assertNotIn("cuadrante", blob.lower())
        # FLUJOS-01-PR2 autorizado Sander 12-nov-2026 08:57
        for ccy, row in self.doc["currencies"].items():
            block = AR._computed_one((self.raw.get("legs") or {}).get(ccy))
            self.assertEqual(row["level_change_1d"], block["level_change_1d"], ccy)
            self.assertEqual(row["level_change_22d"], block["level_change_22d"], ccy)
            self.assertEqual(row["level_change_1d"]["source"], "computed", ccy)
            self.assertEqual(row["level_change_22d"]["source"], "computed", ccy)
            self.assertEqual(row["dominant_driver"], block["dominant_driver"], ccy)
            self.assertNotIn("d1d", row, ccy)
            self.assertNotIn("d22d", row, ccy)
            self.assertNotIn("driver", row, ccy)
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
            # t332u, orden expresa Sander 22-nov
            if key == "NZD":
                self.assertEqual(row["spread_bp"], "NA")
                self.assertEqual(row["regime"], "SIN DATO")
                self.assertNotIn("AMPLE", html)
                self.assertIn("NA", html)
                self.assertIn("SIN DATO", html)
                continue
            if row["spread_bp"] not in ("UNAVAILABLE", "NA"):
                self.assertIn("%.1f" % abs(row["spread_bp"]), html)
            self.assertIn(row["regime"], html)

    def test_s01b_integers_are_the_painted_cells(self):
        # Reloj y as_of fijos (orden Sander 17-nov-2026). setUpClass re-renderiza
        # con generated_at del JSON, que sale del reloj del sistema: si esa hora
        # es anterior a las 17:00 America/New_York, closed_session es la sesión
        # previa y cutS01B vacía el tbody cuando S01B.as_of va por delante.
        # Aquí no se lee ese reloj. Los dos instantes son el mismo día de
        # noviembre (EST, UTC−5): 21:00Z = 16:00 NY, 22:30Z = 17:30 NY.
        # as_of queda en 2026-11-16, ya cerrada en los dos cortes.
        import shutil
        import tempfile

        pinned_as_of = "2026-11-16"
        clocks = (
            ("2026-11-17T21:00:00Z", "2026-11-16"),
            ("2026-11-17T22:30:00Z", "2026-11-17"),
        )

        def render_pinned(now):
            data = os.path.join(ROOT, "data")
            tmp = tempfile.mkdtemp(prefix="argos_s01b_pin_")
            try:
                for name in os.listdir(data):
                    if name == "S01B.json":
                        continue
                    os.symlink(os.path.join(data, name), os.path.join(tmp, name))
                with open(os.path.join(data, "S01B.json"), encoding="utf-8") as fh:
                    pinned = json.load(fh)
                pinned["as_of"] = pinned_as_of
                with open(os.path.join(tmp, "S01B.json"), "w", encoding="utf-8") as fh:
                    json.dump(pinned, fh)
                return AR.render(now, data_dir=tmp)
            finally:
                shutil.rmtree(tmp)

        for now, closed in clocks:
            self.assertEqual(AR.closed_session(now).isoformat(), closed, now)
            raw = render_pinned(now)
            doc = AR.parse(raw)
            self.assertEqual(doc["as_of"], closed, now)
            self.assertEqual(doc["s01b_as_of"], pinned_as_of, now)
            for ccy, row in doc["currencies"].items():
                block = row["s01b"]
                html = ""
                for tr in AR._rows(raw["s01b"]):
                    if "<b>%s</b>" % ccy in tr and "a3-row" not in tr[:40]:
                        html = tr
                        break
                self.assertTrue(html, "%s %s" % (ccy, now))
                for key in ("d_rny", "d_tp", "d_fit", "resid"):
                    val = block[key]
                    if isinstance(val, int):
                        # bp() pinta +n, -n o 0
                        if val > 0:
                            self.assertIn("+%d" % val, html, "%s %s %s" % (ccy, key, now))
                        else:
                            self.assertIn(str(val), html, "%s %s %s" % (ccy, key, now))
                    elif val == "UNAVAILABLE":
                        self.assertTrue(
                            any(w in AR._text(html) for w in ("sin ", "·", "—")),
                            "%s %s" % (ccy, now),
                        )


if __name__ == "__main__":
    unittest.main()

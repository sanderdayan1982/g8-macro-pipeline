"""t331u B3 — DE 10Y par (BBSIS R10XX ZAR) y ES 10Y (D_G0B1F0ZP) diarios; spread misma sesión."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import fetch_sovereign_10y_daily as S  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "t331u_b3"


class ParseDE(unittest.TestCase):
    def test_bbsis_r10xx_zar_ultima_obs_2026_11_20_y_omite_huecos(self):
        stand, series = S.parse_de_csv((FIX / "bbk_r10xx_zar.csv").read_bytes())
        self.assertEqual(stand, "20.11.2026 12:29:00 Uhr")
        self.assertEqual(max(series), "2026-11-20")
        self.assertAlmostEqual(series["2026-11-20"], 3.37)
        # weekends / 'Kein Wert vorhanden' not filled
        self.assertNotIn("2026-11-15", series)
        self.assertNotIn("2026-11-14", series)
        self.assertIn("2026-11-16", series)

    def test_url_es_par_zar_no_wt1010(self):
        self.assertIn("BBSIS", S.DE_URL)
        self.assertIn("R10XX", S.DE_URL)
        self.assertIn(".ZAR.", S.DE_URL)
        self.assertNotIn("WT1010", S.DE_URL)
        self.assertNotIn("BBSSY", S.DE_URL)
        with self.assertRaisesRegex(RuntimeError, "WT1010"):
            S.assert_not_wtontherun("https://example/WT1010")


class ParseES(unittest.TestCase):
    def test_d_g0b1f0zp_ultima_obs_2026_11_18_sin_rellenar(self):
        series, source = S.parse_es_csv((FIX / "bde_ti_1_3_tail.csv").read_bytes())
        self.assertEqual(source, "Bolsas y Mercados Españoles")
        self.assertEqual(max(series), "2026-11-18")
        self.assertAlmostEqual(series["2026-11-18"], 3.944)
        self.assertNotIn("2026-11-19", series)
        self.assertNotIn("2026-11-20", series)
        # 14-15 nov are in the file as "_" across the board
        self.assertNotIn("2026-11-14", series)
        self.assertNotIn("2026-11-15", series)
        self.assertIn("2026-11-16", series)


class SameSessionSpread(unittest.TestCase):
    def test_si_falta_una_pata_el_spread_queda_en_blanco_no_cero(self):
        es = {"2026-11-18": 3.944, "2026-11-20": None}
        # clean: only real obs
        es = {"2026-11-18": 3.944, "2026-11-16": 3.908}
        de = {"2026-11-18": 3.41, "2026-11-20": 3.37, "2026-11-16": 3.43}
        sp = S.same_session_spread(es, de)
        self.assertAlmostEqual(sp["2026-11-18"][2], 53.4, places=3)  # (3.944-3.41)*100
        self.assertIsNone(sp["2026-11-20"][2])  # falta ES
        self.assertEqual(sp["2026-11-20"][0], None)
        self.assertAlmostEqual(sp["2026-11-20"][1], 3.37)
        text = S.render_spread(sp)
        lines = {ln.split(",")[0]: ln for ln in text.splitlines()[1:]}
        self.assertTrue(lines["2026-11-20"].endswith(","))  # spread field blank
        self.assertNotRegex(lines["2026-11-20"], r",0(\.0+)?$")
        self.assertIn(",53.4000", lines["2026-11-18"])

    def test_run_escribe_tres_ficheros_y_respeta_sesion(self):
        de = (FIX / "bbk_r10xx_zar.csv").read_bytes()
        es = (FIX / "bde_ti_1_3_tail.csv").read_bytes()

        def opener(url):
            if "bundesbank" in url or "BBSIS" in url:
                return de
            if "bde.es" in url or "ti_1_3" in url:
                return es
            raise AssertionError("url inesperada %s" % url)

        with tempfile.TemporaryDirectory() as td:
            out = S.run(data_dir=td, opener=opener)
            self.assertEqual(out["de_last"], "2026-11-20")
            self.assertEqual(out["es_last"], "2026-11-18")
            self.assertGreater(out["spread_blank"], 0)
            self.assertGreater(out["spread_same_session"], 0)
            spread = Path(td, "ES_DE_10Y_SPREAD_DAILY.csv").read_text(encoding="utf-8").splitlines()
            # 20-nov: DE sí, ES no → spread en blanco
            row20 = [ln for ln in spread if ln.startswith("2026-11-20,")][0]
            self.assertTrue(row20.endswith(","))
            self.assertIn(",3.370000,", row20)
            # 18-nov: ambas
            row18 = [ln for ln in spread if ln.startswith("2026-11-18,")][0]
            parts = row18.split(",")
            self.assertEqual(len(parts), 4)
            self.assertTrue(parts[3])  # spread presente
            self.assertTrue((Path(td) / "DE_10Y_PAR_DAILY.csv").exists())
            self.assertTrue((Path(td) / "ES_10Y_DAILY.csv").exists())


if __name__ == "__main__":
    unittest.main()

"""t331u B2 — cubo diario SNB1A con dimSel explícito (incl. J30M0) y control de fin de mes.

No modifica tests existentes ni data/CHF_SPOT_*.csv (historia rendeiduebd).
"""
import csv
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import fetch_chf_snb1a as S  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "t331u_b2"


class DimSel(unittest.TestCase):
    def test_dimsel_incluye_j30m0_y_los_30_plazos_anuales(self):
        d = S.dimsel()
        self.assertIn("LAUFZEIT(", d)
        self.assertIn("J30M0", d)
        self.assertIn("J01M0", d)
        self.assertIn("ZEITPUNKT(A1100)", d)
        self.assertIn("frequency(P1D_L)", d)
        self.assertIn("AGGREGATIONSMETHODE(ZZ)", d)
        for i in range(1, 31):
            self.assertIn("J%02dM0" % i, d)
        self.assertIn("J30M0", S.dimsel(["J01M0", "J10M0"]))

    def test_url_usa_puntos_no_arroba_y_dimsel(self):
        u = S.cube_url(from_date="2026-11-01")
        self.assertIn("SNB1A.SNB.NSS.KZS.EID", u)
        self.assertNotIn("SNB1A@SNB", u)
        self.assertIn("dimSel=", u)
        self.assertIn("J30M0", u)
        self.assertIn("fromDate=2026-11-01", u)


class ParseLiveFixture(unittest.TestCase):
    def test_volcado_sin_filtro_no_trae_j30m0(self):
        parsed = S.parse_snb_csv((FIX / "snb1a_unfiltered_nov2026.csv").read_bytes())
        series = S.rows_to_series(parsed)
        self.assertEqual(parsed["publishing_date"], "2026-11-20 08:31")
        self.assertIsNone(series.get("J30M0"))
        self.assertIn("J01M0", series)
        self.assertIn("J25M0", series)
        missing = S.completeness(series)
        self.assertIn("J30M0", missing)

    def test_dimsel_anual_trae_1y_a_30y_y_j30m0_hasta_2026_11_19(self):
        parsed = S.parse_snb_csv((FIX / "snb1a_yearly_nov2026.csv").read_bytes())
        series = S.rows_to_series(parsed)
        self.assertEqual(S.completeness(series), [])
        self.assertEqual(S.last_observation(series), "2026-11-19")
        self.assertEqual(parsed["publishing_date"], "2026-11-20 08:31")
        self.assertEqual(len(series["J30M0"]), 14)
        self.assertIn("2026-11-19", series["J30M0"])
        self.assertIn("2026-11-19", series["J10M0"])
        self.assertIn("2026-11-19", series["J01M0"])


class IncompletoSeRechaza(unittest.TestCase):
    def test_run_falla_si_falta_j30m0(self):
        raw = (FIX / "snb1a_unfiltered_nov2026.csv").read_bytes()
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(RuntimeError, "J30M0"):
                S.run(data_dir=td, opener=lambda url: raw)


class MonthEndControlYRuptura(unittest.TestCase):
    def test_control_solo_fechas_con_ambas_patas_diff_en_pb_sin_rellenar(self):
        daily = {10: {"2026-10-30": 0.50, "2026-10-15": 0.49, "2026-11-19": 0.52}}
        monthly = {10: {"2026-10-30": 0.48}}  # 11-19 no está en el mensual
        rows = S.month_end_control(daily, monthly)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Date"], "2026-10-30")
        self.assertEqual(rows[0]["TenorY"], 10)
        self.assertAlmostEqual(rows[0]["Diff_bp"], 2.0)
        # no se inventa el 15-oct como fin de mes si hay 30-oct; no se rellena noviembre
        self.assertEqual([r["Date"] for r in rows], ["2026-10-30"])

    def test_run_escribe_diarios_control_y_marca_sin_tocar_spot_mensual(self):
        raw = (FIX / "snb1a_yearly_nov2026.csv").read_bytes()
        with tempfile.TemporaryDirectory() as td:
            # historia mensual preexistente (no debe reescribirse)
            monthly_path = os.path.join(td, "CHF_SPOT_10Y.csv")
            Path(monthly_path).write_text("Date,Value\n2026-10-30,0.400000\n", encoding="utf-8")
            # SNB1A fixture also has 2026-10-30? yearly file starts 2026-11-02 — no overlap, control 0
            # add a synthetic overlap by appending to a local daily? run() uses fetched series only.
            # Write a 10Y monthly date that exists in the fixture (2026-11-19 is not month-end of a published month
            # in rendeiduebd). Control can be empty; break file still marked.
            out = S.run(data_dir=td, opener=lambda url: raw)
            self.assertEqual(out["publishing_date"], "2026-11-20 08:31")
            self.assertEqual(out["last_observation"], "2026-11-19")
            self.assertEqual(out["n_daily_files"], 30)
            # monthly history intact
            self.assertEqual(Path(monthly_path).read_text(encoding="utf-8"), "Date,Value\n2026-10-30,0.400000\n")
            daily10 = Path(td, "CHF_SPOT_10Y_DAILY.csv").read_text(encoding="utf-8").splitlines()
            self.assertEqual(daily10[0], "Date,Value,Source,PublishingDate")
            self.assertTrue(daily10[-1].startswith("2026-11-19,"))
            self.assertIn(",SNB1A,2026-11-20 08:31", daily10[-1])
            brk = json.loads(Path(td, "CHF_CURVE_METH_BREAK.json").read_text(encoding="utf-8"))
            self.assertTrue(brk["marked"])
            self.assertFalse(brk["history_rewritten"])
            self.assertEqual(brk["backup_cube"], "rendeiduebd")
            self.assertEqual(brk["daily_cube"], "SNB1A.SNB.NSS.KZS.EID")
            self.assertEqual(brk["last_rendeiduebd_spot_date"], "2026-10-30")
            self.assertIn("J30M0", brk["daily_dimsel"])

    def test_control_con_solape_de_fin_de_mes(self):
        raw = (FIX / "snb1a_yearly_nov2026.csv").read_bytes()
        parsed = S.parse_snb_csv(raw)
        series = S.rows_to_series(parsed)
        # first date in fixture is 2026-11-02; invent a monthly 10Y at 2026-11-02 as if it were month-end of a stub
        with tempfile.TemporaryDirectory() as td:
            v = series["J10M0"]["2026-11-02"]
            Path(td, "CHF_SPOT_10Y.csv").write_text(
                "Date,Value\n2026-11-02,%.6f\n" % (v - 0.01), encoding="utf-8")
            out = S.run(data_dir=td, opener=lambda url: raw)
            with open(os.path.join(td, "CHF_CURVE_MONTH_END_CONTROL.csv"), encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            ten10 = [r for r in rows if r["TenorY"] == "10"]
            self.assertEqual(len(ten10), 1)
            self.assertEqual(ten10[0]["Date"], "2026-11-02")
            self.assertAlmostEqual(float(ten10[0]["Diff_bp"]), 1.0, places=3)
            self.assertEqual(out["n_control"], 1)


class NoReescribeHistoriaAjena(unittest.TestCase):
    def test_fetch_chf_snb_py_no_esta_en_este_diff_conceptualmente(self):
        # salvaguarda: este PR no debe alterar el descargador mensual
        src = (ROOT / "scripts" / "fetch_chf_snb.py").read_text(encoding="utf-8")
        self.assertIn("rendeiduebd", src)
        self.assertNotIn("SNB1A.SNB.NSS.KZS.EID", src)


if __name__ == "__main__":
    unittest.main()

"""Acta P-10 · CHF real ex post = nominal 10Y (SNB) − IPC interanual oficial (BFS, mensual); BE = IPC a/a, no breakeven.

C1 el lector BFS toma la columna «% m-12» de la hoja Index_m y fecha a fin de mes; formato cambiado → error
C2 la API de activos: se elige el asset más reciente con el nº de pedido exacto (cc-e-05.02.08)
C3 §00: CHF lleva real = nom − IPC y el flag «REAL ex post … BE = IPC a/a, no breakeven»; IPC de hace > 2 meses → nada
C4 §01: etiqueta propia (cpiTag) y tope de antigüedad (monthsBehind ≤ 2); nunca la palabra breakeven para CHF
C5 el descargador es reparable por el agente (fetch_*) y corre en la diaria y en la pasada EU
"""
import io
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "scripts", "tools"))
import fetch_chf_cpi as F  # noqa: E402


def bfs_xlsx(yoy_header="% m-12", sheet="Index_m", months=140):
    """Libro con la misma forma que el de la BFS (4 filas de título, cabecera «Datum / Date», bases, % m-1, % m-12)."""
    rows = [["Landesindex der Konsumentenpreise / Indice des prix à la consommation"] + [None] * 4,
            ["Indexierungstabelle / Tableau d'indexation"] + [None] * 4,
            ["Originalbasen / Bases originales"] + [None] * 4,
            ["Datum / Date", datetime(2020, 12, 1), datetime(2025, 12, 1), "% m-1", yoy_header]]
    dates = pd.date_range("2015-01-01", periods=months, freq="MS")
    for i, d in enumerate(dates):
        rows.append([d.to_pydatetime(), 100 + i * 0.1, None, 0.1, round(0.01 * i, 2)])
    rows.append([datetime(2026, 12, 1), None, None, None, None])           # meses futuros vacíos, como en la BFS
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame(rows).to_excel(xw, sheet_name=sheet, header=False, index=False)
        pd.DataFrame([["x"]]).to_excel(xw, sheet_name="Index_y", header=False, index=False)
    return buf.getvalue()


class Reader(unittest.TestCase):
    def test_C1_yoy_column_month_end(self):
        rows = F.parse_yoy(bfs_xlsx())
        self.assertEqual(rows[0], ("20150131", 0.0))
        self.assertEqual(rows[1][0], "20150228")
        self.assertEqual(len(rows), 140)

    def test_C1_format_change_is_loud(self):
        with self.assertRaises(ValueError):
            F.parse_yoy(bfs_xlsx(yoy_header="% Vorjahr"))
        with self.assertRaises(ValueError):
            F.parse_yoy(bfs_xlsx(sheet="Tabelle1"))
        with self.assertRaises(ValueError):
            F.parse_yoy(bfs_xlsx(months=30))

    def test_C2_latest_asset_by_order_number(self):
        listing = {"data": [
            {"ids": {"damId": 15284632}, "shop": {"orderNr": "cc-e-05.02.08"}, "bfs": {"embargo": "2021-01-05T07:30:00Z"}},
            {"ids": {"damId": 36878073}, "shop": {"orderNr": "cc-e-05.02.08"}, "bfs": {"embargo": "2026-10-01T06:30:00Z"}},
            {"ids": {"damId": 99999999}, "shop": {"orderNr": "gd-e-10.03.01.12"}, "bfs": {"embargo": "2026-10-05T06:30:00Z"}},
        ]}
        self.assertEqual(F.latest_asset(listing), (36878073, "2026-10-01T06:30:00Z"))
        with self.assertRaises(ValueError):
            F.latest_asset({"data": [listing["data"][2]]})


class Brief(unittest.TestCase):
    def run_book(self, cpi_rows):
        import dashboard_alerts as A
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        shutil.copytree(os.path.join(ROOT, "data"), os.path.join(tmp, "data"),
                        ignore=shutil.ignore_patterns("options", "futures", "_ingest", "s01b"))
        with open(os.path.join(tmp, "data", "CHF_CPI_YOY.csv"), "wb") as fh:
            fh.write(F.render_csv(cpi_rows))
        with patch.object(A, "DATA", os.path.join(tmp, "data")):
            return [r for r in A.build_book({}) if r["ccy"] == "CHF"][0], A

    def test_C3_ex_post_real(self):
        import dashboard_alerts as A
        last = A.TODAY.replace(day=1) - pd.Timedelta(days=1)                  # fin del mes anterior
        chf, _ = self.run_book([(last.strftime("%Y%m%d"), 1.0)])
        if chf.get("nom") is None:
            self.skipTest("sin nominal CHF vigente en los datos del repo")
        self.assertEqual(chf["be"], 1.0)
        self.assertAlmostEqual(chf["real"], chf["nom"] - 1.0, places=4)
        self.assertTrue(any("REAL ex post" in f and "no breakeven" in f for f in chf["flags"]), chf["flags"])

    def test_C3_stale_cpi_not_used(self):
        chf, _ = self.run_book([("20250131", 0.4)])
        self.assertIsNone(chf.get("be"))
        self.assertFalse(any("REAL ex post" in f for f in chf["flags"]))


class Dashboard(unittest.TestCase):
    def test_C4_labels_and_age(self):
        with open(os.path.join(ROOT, "docs", "index.html"), encoding="utf-8") as fh:
            h = fh.read()
        self.assertIn("RAW + 'CHF_CPI_YOY.csv'", h)
        self.assertIn("monthsBehind(raw.chfCpi[raw.chfCpi.length - 1].d) <= 2", h)
        self.assertIn("function cpiTag(label, month)", h)
        self.assertIn("NO breakeven", h)
        self.assertIn("CHF_CPI_YOY: repo('CHF_CPI_YOY.csv'", h)

    def test_C5_agent_can_repair_and_schedules(self):
        import agent_gate as G
        import refresh_feeds as RF
        self.assertEqual(G.classify(["scripts/fetch_chf_cpi.py"])["decision"], "AUTO")
        self.assertIn(("chf_cpi", "fetch_chf_cpi.py"), RF.GROUPS["EU"])
        with open(os.path.join(ROOT, ".github", "workflows", "daily_update.yml"), encoding="utf-8") as fh:
            self.assertIn("g8step.py --name chf_cpi", fh.read())


if __name__ == "__main__":
    unittest.main()

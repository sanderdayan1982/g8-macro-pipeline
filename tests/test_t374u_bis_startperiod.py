"""t374u (orden Sander 24-nov-2026) · fetch_bis_policy: startPeriod en la API y respaldo comparado solo con filas BIS.

Diagnóstico 24-nov: la API v1 WS_CBPOL sin startPeriod responde 200 con 0 bytes; con startPeriod trae la serie.
El respaldo (bulk oficial) se disparaba con `rows[-1] < published[-1]`, pero lo publicado incluye filas del banco
central directo (Acta P-4: RBA F1 / BoE IADB), por delante del BIS: AU y GB salían DEGRADED en cada pasada con
el texto engañoso «BIS API failed».

S1 la llamada a la API lleva startPeriod = inicio de la ventana de 5 años que ya usa el script
S2 AU: filas RBA más recientes que el BIS y bulk igual a la API → sin DEGRADED, se publica hasta la RBA, rc 0
S3 GB: idem con BoE IADB
S4 API por detrás del bulk → DEGRADED con «API BIS por detrás del bulk» (no «BIS API failed»), se usa el bulk
S5 API caída (503) + bulk verificado → el texto «BIS API failed» se conserva (es un fallo real); rc 0 como antes
S6 bulk inaccesible con la API al día y filas RBA por delante → sin DEGRADED, rc 0 (igual que antes)
"""
import io
import json
import os
import shutil
import sys
import unittest
import zipfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import fetcher_harness as H  # noqa: E402
from test_p4_cb_direct import f1, iadb, ohlcv  # noqa: E402

from g8common import policy_decisions as P  # noqa: E402


def bdays(start, n):
    d, out = datetime.strptime(start, "%Y%m%d"), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return out


DAYS = bdays("20260803", 35)                  # 03-ago … 18-sep-2026 (H.NOW = 24-sep-2026)
BIS = [(d, "4.3500") for d in DAYS[:30]]      # BIS hasta 11-sep
CB_AHEAD = [(d, "4.3500") for d in DAYS]      # banco central hasta 18-sep (por delante del BIS)


def bulk_zip(rows, country):
    head = ("FREQ:Frequency,REF_AREA:Reference area,TIME_PERIOD:Time period or range,OBS_VALUE:Observation Value,"
            "UNIT_MEASURE:Unit of measure,UNIT_MULT:Unit Multiplier\n")
    body = "".join("D: Daily,%s: X,%s,%s,368: Per cent per year,0: Units\n" % (country, H.iso(d), v) for d, v in rows)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("WS_CBPOL_csv_flat.csv", head + body)
    return buf.getvalue()


class T374uBIS(unittest.TestCase):
    def setUp(self):
        self.roots = []

    def tearDown(self):
        for r in self.roots:
            shutil.rmtree(r, ignore_errors=True)

    def run_fetch(self, country, published, api, bulk, rba=(503, b"busy"), boe=(503, b"busy")):
        fname = "%s_POLICY.csv" % country

        def serve(url):
            if "stats.bis.org" in url:
                return api
            if "data.bis.org/static/bulk" in url:
                return bulk
            if "rba.gov.au" in url:
                return rba
            if "bankofengland" in url:
                return boe
            return 404, b""
        root = H.make_root({fname: ohlcv(published)})
        self.roots.append(root)
        rc, calls, _ = H.run_new("fetch_bis_policy", serve, root, (country,))
        with open(os.path.join(root, "data", "_ingest", "latest", "actions__fetch_bis_policy_%s.json" % country),
                  encoding="utf-8") as fh:
            latest = json.load(fh)
        degraded = [e["detail"] for e in latest.get("errors", []) if e.get("kind") == "DEGRADED"]
        return rc, calls, P.read_published(os.path.join(root, "data", fname)), degraded

    def test_S1_api_call_carries_startPeriod(self):
        rc, calls, _, _ = self.run_fetch("JP", BIS, (200, H.bis_csv(BIS)), (404, b""))
        api = [c for c in calls if "stats.bis.org" in c]
        start = (H.NOW - timedelta(days=365 * 5)).strftime("%Y-%m-%d")
        self.assertEqual(rc, 0)
        self.assertTrue(api)
        self.assertTrue(all("startPeriod=%s" % start in c for c in api), api)
        self.assertTrue(all("format=csv" in c for c in api), api)

    def test_S2_au_rba_rows_newer_than_bis_do_not_degrade(self):
        rc, calls, pub, degraded = self.run_fetch(
            "AU", CB_AHEAD, (200, H.bis_csv(BIS)), (200, bulk_zip(BIS, "AU")), rba=(200, f1(CB_AHEAD)))
        self.assertEqual(rc, 0)
        self.assertEqual(degraded, [])
        self.assertEqual(pub[-1], (DAYS[-1], 4.35))

    def test_S3_gb_boe_rows_newer_than_bis_do_not_degrade(self):
        rc, calls, pub, degraded = self.run_fetch(
            "GB", CB_AHEAD, (200, H.bis_csv(BIS)), (200, bulk_zip(BIS, "GB")), boe=(200, iadb(CB_AHEAD)))
        self.assertEqual(rc, 0)
        self.assertEqual(degraded, [])
        self.assertEqual(pub[-1], (DAYS[-1], 4.35))

    def test_S4_api_behind_bulk_new_message(self):
        api = BIS[:25]                                         # API atrasada 5 días hábiles frente al bulk
        rc, calls, pub, degraded = self.run_fetch("JP", BIS, (200, H.bis_csv(api)), (200, bulk_zip(BIS, "JP")))
        self.assertEqual(rc, 0)
        self.assertEqual(len(degraded), 1)
        self.assertIn("API BIS por detrás del bulk", degraded[0])
        self.assertNotIn("BIS API failed", degraded[0])
        self.assertEqual(pub[-1], (BIS[-1][0], 4.35))

    def test_S5_api_down_keeps_failed_message(self):
        rc, calls, pub, degraded = self.run_fetch("JP", BIS, (503, b"busy"), (200, bulk_zip(BIS, "JP")))
        self.assertEqual(rc, 0)                                # sin cambio: respaldo verificado → rc 0 + DEGRADED
        self.assertEqual(len(degraded), 1)
        self.assertIn("BIS API failed", degraded[0])

    def test_S6_bulk_unreachable_api_current_no_degrade(self):
        rc, calls, pub, degraded = self.run_fetch(
            "AU", CB_AHEAD, (200, H.bis_csv(BIS)), (503, b"busy"), rba=(200, f1(CB_AHEAD)))
        self.assertEqual(rc, 0)
        self.assertEqual(degraded, [])
        self.assertEqual(pub[-1], (DAYS[-1], 4.35))


if __name__ == "__main__":
    unittest.main()

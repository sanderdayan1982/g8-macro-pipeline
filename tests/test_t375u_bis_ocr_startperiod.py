"""t375u (orden Sander 25-nov-2026) · fetch_bis_ocr (NZ): startPeriod en la API y respaldo comparado solo con filas BIS.

Mismo arreglo que fetch_bis_policy (t374u / PR #144). NZ no tiene fuente de banco central directo en este
script; lo publicado puede ir por delante del BIS por decisiones verificadas (Acta P-1).

S1 la llamada a la API lleva startPeriod = inicio de la ventana de 5 años que ya usa el script
S2 filas publicadas (decisión P-1) más recientes que el BIS y bulk igual a la API → sin DEGRADED, se publica
   hasta la decisión, rc 0
S4 API por detrás del bulk → DEGRADED con «API BIS por detrás del bulk» (no «BIS API failed»), se usa el bulk
S5 API caída (503) + bulk verificado → el texto «BIS API failed» se conserva (es un fallo real); rc 0 como antes
S6 bulk inaccesible con la API al día y decisión por delante → sin DEGRADED, rc 0
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
from test_p4_cb_direct import ohlcv  # noqa: E402

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
PUB_AHEAD = [(d, "4.3500") for d in DAYS]     # publicado (decisión P-1) hasta 18-sep, por delante del BIS
DEC_EFF = datetime.strptime(DAYS[30], "%Y%m%d").date().isoformat()


def bulk_zip(rows, country="NZ"):
    head = ("FREQ:Frequency,REF_AREA:Reference area,TIME_PERIOD:Time period or range,OBS_VALUE:Observation Value,"
            "UNIT_MEASURE:Unit of measure,UNIT_MULT:Unit Multiplier\n")
    body = "".join("D: Daily,%s: X,%s,%s,368: Per cent per year,0: Units\n" % (country, H.iso(d), v) for d, v in rows)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("WS_CBPOL_csv_flat.csv", head + body)
    return buf.getvalue()


def write_dec(root, lines):
    os.makedirs(os.path.join(root, "data", "manual"), exist_ok=True)
    with open(os.path.join(root, "data", "manual", "policy_decisions.csv"), "w") as fh:
        fh.write("country,effective_date,rate,announced_date,source,verified\n")
        for line in lines:
            fh.write(line + "\n")


class T375uBISOCR(unittest.TestCase):
    def setUp(self):
        self.roots = []

    def tearDown(self):
        for r in self.roots:
            shutil.rmtree(r, ignore_errors=True)

    def run_fetch(self, published, api, bulk, decisions=None):
        def serve(url):
            if "stats.bis.org" in url:
                return api
            if "data.bis.org/static/bulk" in url:
                return bulk
            return 404, b""
        root = H.make_root({"NZD_OCR.csv": ohlcv(published)})
        self.roots.append(root)
        if decisions:
            write_dec(root, decisions)
        rc, calls, _ = H.run_new("fetch_bis_ocr", serve, root, ())
        with open(os.path.join(root, "data", "_ingest", "latest", "actions__fetch_bis_ocr.json"),
                  encoding="utf-8") as fh:
            latest = json.load(fh)
        degraded = [e["detail"] for e in latest.get("errors", []) if e.get("kind") == "DEGRADED"]
        return rc, calls, P.read_published(os.path.join(root, "data", "NZD_OCR.csv")), degraded

    def test_S1_api_call_carries_startPeriod(self):
        rc, calls, _, _ = self.run_fetch(BIS, (200, H.bis_csv(BIS)), (404, b""))
        api = [c for c in calls if "stats.bis.org" in c]
        start = (H.NOW - timedelta(days=365 * 5)).strftime("%Y-%m-%d")
        self.assertEqual(rc, 0)
        self.assertTrue(api)
        self.assertTrue(all("startPeriod=%s" % start in c for c in api), api)
        self.assertTrue(all("format=csv" in c for c in api), api)

    def test_S2_published_decision_rows_newer_than_bis_do_not_degrade(self):
        rc, calls, pub, degraded = self.run_fetch(
            PUB_AHEAD, (200, H.bis_csv(BIS)), (200, bulk_zip(BIS)),
            decisions=["NZ,%s,4.35,2026-09-11,RBNZ statement,v" % DEC_EFF])
        self.assertEqual(rc, 0)
        self.assertEqual(degraded, [])
        self.assertEqual(pub[-1], (H.NOW.strftime("%Y%m%d"), 4.35))

    def test_S4_api_behind_bulk_new_message(self):
        api = BIS[:25]                                         # API atrasada 5 días hábiles frente al bulk
        rc, calls, pub, degraded = self.run_fetch(BIS, (200, H.bis_csv(api)), (200, bulk_zip(BIS)))
        self.assertEqual(rc, 0)
        self.assertEqual(len(degraded), 1)
        self.assertIn("API BIS por detrás del bulk", degraded[0])
        self.assertNotIn("BIS API failed", degraded[0])
        self.assertEqual(pub[-1], (BIS[-1][0], 4.35))

    def test_S5_api_down_keeps_failed_message(self):
        rc, calls, pub, degraded = self.run_fetch(BIS, (503, b"busy"), (200, bulk_zip(BIS)))
        self.assertEqual(rc, 0)                                # sin cambio: respaldo verificado → rc 0 + DEGRADED
        self.assertEqual(len(degraded), 1)
        self.assertIn("BIS API failed", degraded[0])

    def test_S6_bulk_unreachable_api_current_no_degrade(self):
        rc, calls, pub, degraded = self.run_fetch(
            PUB_AHEAD, (200, H.bis_csv(BIS)), (503, b"busy"),
            decisions=["NZ,%s,4.35,2026-09-11,RBNZ statement,v" % DEC_EFF])
        self.assertEqual(rc, 0)
        self.assertEqual(degraded, [])
        self.assertEqual(pub[-1], (H.NOW.strftime("%Y%m%d"), 4.35))


if __name__ == "__main__":
    unittest.main()

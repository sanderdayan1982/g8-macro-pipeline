"""Acta P-4 · tipo oficial directo del banco central (RBA F1 FIRMMCRTD, BoE IADB IUDBEDR) por delante del BIS.

P1 parsers: RBA F1 por Series ID (celdas vacías fuera), BoE IADB; formato cambiado → DirectError
P2 merge: banco central prolonga por delante del BIS; mismo día distinto → manda el banco central + aviso
I1 AU: BIS atrasado + RBA al día → se publica hasta el último dato de la RBA, rc 0
I2 GB: idem con BoE IADB
I3 fuente directa caída (503): mismo fichero que solo con BIS, rc 0 (el fallo directo es un aviso)
I4 BIS caído + RBA al día: se publica la prolongación de la RBA y rc sigue en 1 (incidencia del BIS abierta)
I5 países sin fuente directa (JP/CH): no se llama a ningún banco central
I6 banco central con 503 persistente: un solo intento, sin esperas, después del BIS (el BIS conserva su presupuesto)
"""
import os
import shutil
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
import fetcher_harness as H  # noqa: E402

from g8common import cb_direct as CB  # noqa: E402
from g8common import policy_decisions as P  # noqa: E402

BIS = [("20260916", "4.3500"), ("20260917", "4.3500"), ("20260918", "4.3500")]


def ohlcv(rows):
    return ("DATE,OPEN,HIGH,LOW,CLOSE,VOLUME\n" +
            "".join("%s,%s,%s,%s,%s,0\n" % (d, v, v, v, v) for d, v in rows)).encode()


def f1(rows):
    head = ["F1 INTEREST RATES AND YIELDS – MONEY MARKET", "Title,Cash Rate Target,Change,Interbank Overnight Cash Rate",
            "Frequency,Daily,as announced,Daily", "Series ID,FIRMMCRTD,FIRMMCCRT,FIRMMCRID"]
    body = ["%s,%s,,%s" % (datetime.strptime(d, "%Y%m%d").strftime("%d-%b-%Y"), v, v) for d, v in rows]
    return ("\n".join(head + body) + "\n25-Sep-2026,,,\n").encode()


def iadb(rows):
    return ("DATE,IUDBEDR\n" + "".join("%s,%s\n" % (datetime.strptime(d, "%Y%m%d").strftime("%d %b %Y"), v)
                                       for d, v in rows)).encode()


class Parsers(unittest.TestCase):
    def test_P1_rba_by_series_id_skips_blank(self):
        rows = CB.parse_rba_f1(f1([("20260923", "4.35"), ("20260924", "4.60")]).decode())
        self.assertEqual(rows, [("20260923", 4.35), ("20260924", 4.60)])

    def test_P1_rba_real_excerpt(self):
        # extracto real de rba.gov.au/statistics/tables/csv/f1-data.csv (publicación 01-Oct-2026, CC BY 4.0)
        with open(os.path.join(os.path.dirname(__file__), "fixtures", "rba_f1_excerpt_2026-10-01.csv"), encoding="utf-8") as fh:
            rows = CB.parse_rba_f1(fh.read())
        self.assertEqual(rows[0], ("20110104", 4.75))
        self.assertEqual(rows[-2:], [("20260929", 4.35), ("20260930", 4.60)])   # 01-Oct vacío: fuera

    def test_P1_boe(self):
        self.assertEqual(CB.parse_boe_iadb(iadb([("20260930", "3.75")]).decode()), [("20260930", 3.75)])

    def test_P1_format_change_is_loud(self):
        with self.assertRaises(CB.DirectError):
            CB.parse_rba_f1("Title,x\nSeries ID,OTHER\n01-Sep-2026,1\n")
        with self.assertRaises(CB.DirectError):
            CB.parse_rba_f1("no header here\n")
        with self.assertRaises(CB.DirectError):
            CB.parse_boe_iadb("DATE,IUDSOIA\n30 Sep 2026,3.9\n")

    def test_P2_merge(self):
        rows, ahead, fixed, w = CB.merge([("20260917", 4.35), ("20260918", 4.35)],
                                         [("20260918", 4.35), ("20260921", 4.35), ("20260922", 4.60)], "AU")
        self.assertEqual((rows[-1], ahead, fixed, w), (("20260922", 4.60), 2, 0, []))
        rows, ahead, fixed, w = CB.merge([("20260918", 4.35)], [("20260918", 4.60)], "AU")
        self.assertEqual((rows, ahead, fixed), ([("20260918", 4.60)], 0, 1))
        self.assertIn("manda el banco central", w[0])


class Integration(unittest.TestCase):
    def setUp(self):
        self.roots = []

    def tearDown(self):
        for r in self.roots:
            shutil.rmtree(r, ignore_errors=True)

    def run_fetch(self, country, serve):
        fname = "%s_POLICY.csv" % country
        root = H.make_root({fname: ohlcv(BIS)})
        self.roots.append(root)
        rc, calls, _ = H.run_new("fetch_bis_policy", serve, root, (country,))
        return rc, calls, P.read_published(os.path.join(root, "data", fname)), H.read(root, fname)

    @staticmethod
    def router(bis=(200, None), rba=(503, b"busy"), boe=(503, b"busy")):
        def serve(url):
            if "stats.bis.org" in url:
                return bis if bis[1] is not None else (200, H.bis_csv(BIS))
            if "rba.gov.au" in url:
                return rba
            if "bankofengland" in url:
                return boe
            return 404, b""
        return serve

    def test_I1_au_rba_ahead_of_bis(self):
        rba = (200, f1([("20260918", "4.35"), ("20260921", "4.35"), ("20260922", "4.35"), ("20260923", "4.35"),
                        ("20260924", "4.60")]))
        rc, calls, pub, _ = self.run_fetch("AU", self.router(rba=rba))
        self.assertEqual(rc, 0)
        self.assertTrue(any("f1-data.csv" in c for c in calls))
        self.assertEqual(pub[-1], ("20260924", 4.60))
        self.assertEqual(pub[:3], [(d, float(v)) for d, v in BIS])

    def test_I2_gb_boe_ahead_of_bis(self):
        boe = (200, iadb([("20260918", "4.35"), ("20260921", "4.35"), ("20260922", "4.35")]))
        rc, calls, pub, _ = self.run_fetch("GB", self.router(boe=boe))
        self.assertEqual(rc, 0)
        self.assertTrue(any("IUDBEDR" in c for c in calls))
        self.assertEqual(pub[-1], ("20260922", 4.35))

    def test_I3_direct_down_equals_bis_only(self):
        rc, _, pub, raw = self.run_fetch("AU", self.router())
        self.assertEqual(rc, 0)
        self.assertEqual(pub, [(d, float(v)) for d, v in BIS])

    def test_I4_bis_down_rba_ok_publishes_rc1(self):
        rba = (200, f1([("20260921", "4.35"), ("20260922", "4.35")]))
        rc, _, pub, _ = self.run_fetch("AU", self.router(bis=(503, b"busy"), rba=rba))
        self.assertEqual(rc, 1)
        self.assertEqual(pub[-1], ("20260922", 4.35))

    def test_I5_no_direct_source_for_jp_ch(self):
        for c in ("JP", "CH"):
            _, calls, _, _ = self.run_fetch(c, self.router())
            self.assertFalse(any("rba.gov.au" in u or "bankofengland" in u for u in calls), c)

    def test_I6_direct_single_attempt_after_bis(self):
        fname = "GB_POLICY.csv"
        root = H.make_root({fname: ohlcv(BIS)})
        self.roots.append(root)
        clock = H.clock_at_now()
        rc, calls, clock = H.run_new("fetch_bis_policy", self.router(), root, ("GB",), clock=clock)
        boe = [c for c in calls if "bankofengland" in c]
        self.assertEqual(rc, 0)
        self.assertEqual(len(boe), 1)
        self.assertEqual(getattr(clock, "slept", []), [])
        self.assertTrue(all("stats.bis.org" in c for c in calls[:calls.index(boe[0])]))


if __name__ == "__main__":
    unittest.main()

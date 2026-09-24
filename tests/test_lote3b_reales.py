"""Lote 3B · rendimientos reales EUR (Bundesbank) y JPY (JSDA): original congelado frente a nuevo.

El XLSX del Bundesbank y el CSV Shift-JIS del JSDA se sirven como bytes opacos; el cálculo (código sin cambios)
se sustituye por el mismo doble en las dos versiones: el resultado de la hoja es la fila del repo (o una nueva).
Se compara lo que cambia en el lote: HTTP, compuerta de frescura registrada y publicación segura del CSV."""
import csv
import io
import json
import os
import shutil
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.dirname(__file__))

import fetcher_harness as H  # noqa: E402
import ingest_watch as W  # noqa: E402

TODAY = H.NOW.date()


def repo_last(fname):
    rows = [r for r in csv.reader(open(os.path.join(ROOT, "data", fname))) if r and r[0].isdigit()]
    return rows[-1]


def result_for(d, nom, real, be):
    return {"date": d, "NOM10": nom, "REAL10": real, "BE10": be, "sheet": d.strftime("%d.%m.%Y"), "linkers": [],
            "n_nominals": 5, "otr_num": 30, "otr_resid": 9.4, "otr_coupon": 0.005, "otr_price": 100.0, "n_jgbi": 3,
            "n_nom": 10}


class RealBase(object):
    script = fname = None

    def setUp(self):
        self.orig = open(os.path.join(ROOT, "data", self.fname), "rb").read()
        self.roots = []

    def tearDown(self):
        for r in self.roots:
            shutil.rmtree(r, ignore_errors=True)

    def root(self):
        r = H.make_root({self.fname: self.orig})
        self.roots.append(r)
        return r

    def run_both(self, res, serve=None):
        serve = serve or self.serve_ok
        ro, rn = self.root(), self.root()
        rco, _ = H.run_original(self.script, serve, ro, patch=self.patch(res))
        rcn, calls, clock = H.run_new(self.script, serve, rn, patch=self.patch(res))
        return rco, rcn, ro, rn, calls, clock

    def last(self):
        d, nom, real, be = repo_last(self.fname)[:4]
        return datetime.strptime(d, "%Y%m%d").date(), float(nom), float(real), float(be)

    def alerts(self, root, hours=1):
        a = {}
        W.check_actions(root, (H.NOW + timedelta(hours=hours)).replace(tzinfo=timezone.utc), a, [])
        return a

    def test_E1_new_day_equivalent(self):
        d, nom, real, be = self.last()
        res = result_for(TODAY if TODAY > d else d + timedelta(days=1), nom + 0.01, real + 0.01, be)
        rco, rcn, ro, rn, *_ = self.run_both(res)
        self.assertEqual((rco, rcn), (0, 0))
        self.assertEqual(H.read(rn, self.fname), H.read(ro, self.fname))
        self.assertNotEqual(H.read(rn, self.fname), self.orig)

    def test_E3_same_day_same_values_identical(self):
        d, nom, real, be = self.last()
        rco, rcn, ro, rn, *_ = self.run_both(result_for(d, nom, real, be))
        self.assertEqual(H.read(rn, self.fname), self.orig)
        self.assertEqual(H.read(ro, self.fname), self.orig)

    def test_F1_503_retried_file_intact_alert(self):
        rn = self.root()
        rc, calls, clock = H.run_new(self.script, lambda u: (503, b"busy"), rn, patch=self.patch(None))
        self.assertEqual(rc, 1)
        self.assertEqual(H.read(rn, self.fname), self.orig)
        self.assertIn(10, clock.slept)
        self.assertIn("actions:%s:fail" % self.script, self.alerts(rn))

    def test_F2_stale_source_recorded_and_alerted(self):
        d, nom, real, be = self.last()
        res = result_for(TODAY - timedelta(days=20), nom, real, be)
        rn = self.root()
        rc, *_ = H.run_new(self.script, self.serve_ok, rn, patch=self.patch(res))
        self.assertEqual(rc, 1)
        a = self.alerts(rn)
        self.assertIn("fuente sin actualizar", a["actions:%s:fail" % self.script])
        self.assertEqual(H.read(rn, self.fname), self.orig)

    def test_F3_empty_response(self):
        rn = self.root()
        rc, *_ = H.run_new(self.script, lambda u: (200, b""), rn, patch=self.patch(None))
        self.assertEqual(rc, 1)
        self.assertEqual(H.read(rn, self.fname), self.orig)

    def test_F4_implausible_value_held(self):
        d, nom, real, be = self.last()
        res = result_for(TODAY if TODAY > d else d + timedelta(days=1), nom + 3.0, real, be)
        rn = self.root()
        rc, *_ = H.run_new(self.script, self.serve_ok, rn, patch=self.patch(res))
        self.assertEqual(H.read(rn, self.fname), self.orig)
        self.assertIn("actions:held:%s" % self.fname, self.alerts(rn))

    def test_F6_interruption_atomic(self):
        d, nom, real, be = self.last()
        res = result_for(TODAY if TODAY > d else d + timedelta(days=1), nom, real, be)
        rn = self.root()
        with mock.patch("g8common.series.os.replace", side_effect=KeyboardInterrupt("corte")):
            with self.assertRaises(KeyboardInterrupt):
                H.run_new(self.script, self.serve_ok, rn, patch=self.patch(res))
        self.assertEqual(H.read(rn, self.fname), self.orig)

    def test_F7_recovery(self):
        rn = self.root()
        H.run_new(self.script, lambda u: (503, b"busy"), rn, patch=self.patch(None))
        d, nom, real, be = self.last()
        res = result_for(TODAY if TODAY > d else d + timedelta(days=1), nom, real, be)
        rc, *_ = H.run_new(self.script, self.serve_ok, rn, patch=self.patch(res), clock=H.Clock(H.NOW_EPOCH + 3600))
        self.assertEqual(rc, 0)
        self.assertNotIn("actions:%s:fail" % self.script, self.alerts(rn, 2))

    def test_F8_retry_after_deferred(self):
        rn = self.root()
        clock = H.clock_at_now()
        H.run_new(self.script, lambda u: (503, (b"", {"retry-after": "7200"})), rn, patch=self.patch(None), clock=clock)
        clock.t += 600
        d, nom, real, be = self.last()
        rc, calls, _ = H.run_new(self.script, self.serve_ok, rn, clock=clock,
                                 patch=self.patch(result_for(d + timedelta(days=1), nom, real, be)))
        self.assertEqual(calls, [])
        self.assertEqual(H.read(rn, self.fname), self.orig)


LISTING = ('<a href="/resource/blob/123/abcdef/0123ABCD/%04d-%02d-excel-data.xlsx">xlsx</a>'
           % (TODAY.year, TODAY.month)).encode()


class Test_fetch_eur_real(RealBase, unittest.TestCase):
    script, fname = "fetch_eur_real", "RY_G8_EUR.csv"

    @staticmethod
    def serve_ok(url):
        return 200, (b"X" * 5000 if url.endswith(".xlsx") else LISTING)

    def patch(self, res):
        def p(mod):
            mod.parse_workbook = lambda src: res
            mod.load_workbook = lambda *a, **k: None
        return p


class Test_fetch_jpy_real(RealBase, unittest.TestCase):
    script, fname = "fetch_jpy_real", "RY_G8_JPY.csv"

    @staticmethod
    def serve_ok(url):
        return 200, b"X" * 5000

    def patch(self, res):
        def p(mod):
            mod.parse_csv = lambda text: (res["date"] if res else None, [], [])
            mod.compute = lambda fd, jgbi, noms: res
            mod._summary = lambda r: None
        return p

    def test_F9_missing_days_are_no_publication_not_failure(self):
        d, nom, real, be = self.last()
        res = result_for(TODAY - timedelta(days=1), nom, real, be)
        seen = []

        def serve(url):
            seen.append(url)
            return (404, b"") if len(seen) == 1 else (200, b"X" * 5000)   # hoy aún no publicado
        rn = self.root()
        rc, calls, clock = H.run_new(self.script, serve, rn, patch=self.patch(res))
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 2)                                    # el 404 no se reintenta
        self.assertNotIn("actions:fetch_jpy_real:fail", self.alerts(rn))


class Backfills(unittest.TestCase):
    """Backfills manuales (backfill_eur_real / backfill_jpy_real): siguen funcionando con el motor compartido
    nuevo y escriben de forma atómica."""

    def setUp(self):
        from g8common import legacy
        self.legacy = legacy
        self.tmp = __import__("tempfile").mkdtemp()
        legacy.STATE_PATH = os.path.join(self.tmp, "nb")
        legacy.TEST_CLOCK = H.clock_at_now()

    def tearDown(self):
        self.legacy.TEST_TRANSPORT = self.legacy.TEST_CLOCK = self.legacy.STATE_PATH = None
        self.legacy._STORE.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_jpy_open_compat_404_and_200(self):
        import urllib.error
        import fetch_jpy_real as F
        self.legacy.TEST_TRANSPORT = lambda m, u, *a: (404, {}, b"") if "0101" in u else (200, {}, b"Z" * 2000)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            F._open("https://x.invalid/ES260101.csv")
        self.assertEqual(cm.exception.code, 404)
        with F._open("https://x.invalid/ES260924.csv") as r:
            self.assertEqual((r.status, len(r.read())), (200, 2000))

    def test_eur_backfill_budget_and_atomic_upsert(self):
        import importlib
        import fetch_eur_real as F
        import backfill_eur_real  # noqa: F401  (fija el presupuesto largo del backfill)
        self.assertEqual(F.BUDGET_S, 6 * 3600)
        F.BUDGET_S = 360
        path = os.path.join(self.tmp, "RY.csv")
        orig = open(os.path.join(ROOT, "data", "RY_G8_EUR.csv"), "rb").read()
        open(path, "wb").write(orig)
        with mock.patch("os.replace", side_effect=KeyboardInterrupt("corte")):
            with self.assertRaises(KeyboardInterrupt):
                F._upsert({"20260925": ["20260925", "3.5", "1.2", "2.3"]}, path)
        self.assertEqual(open(path, "rb").read(), orig)                   # intacto tras el corte
        F._upsert({}, path)
        self.assertEqual(open(path, "rb").read(), orig)                   # mismos bytes que antes


if __name__ == "__main__":
    unittest.main()

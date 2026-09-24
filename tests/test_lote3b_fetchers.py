"""Lote 3B · descargadores multiserie de Actions: bills ×6, ACM, suelos (floor_spreads), reales EUR/JPY.

Para cada uno, ORIGINAL (congelado de ed9ed64 en tests/fixtures/original) frente a NUEVO, sin red, con las
MISMAS respuestas construidas en el formato de cada fuente a partir de los CSV reales del repo:
  E1 equivalencia con un día nuevo (byte a byte en TODOS los ficheros)   E3 sin novedad → sin cambios
  F1 503 persistente: 4 intentos 10/40/90, ficheros intactos             F2 respuesta truncada: se conserva
  F3 respuesta vacía                                                     F4 datos inválidos (salto implausible)
  F5 fallo de UNA serie: las demás se publican, la fallida intacta, aviso F6 interrupción a mitad de publicación
  F7 recuperación tras fallo (el aviso se resuelve)                      F8 Retry-After: aplazado entre ejecuciones
Los formatos binarios (ZIP/XLSX de la BoE, XLS de la NY Fed, XLSX Bundesbank, CSV Shift-JIS del JSDA) se sirven como
bytes opacos y su PARSER (código sin cambios en el lote) se sustituye por el mismo doble en las dos versiones: se
compara la capa que sí cambia (HTTP, reintentos, publicación)."""
import io
import json
import os
import shutil
import sys
import unittest
import zipfile
from datetime import date, datetime, timedelta, timezone
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.dirname(__file__))

import fetcher_harness as H  # noqa: E402
import ingest_watch as W  # noqa: E402
from g8common import series as S  # noqa: E402

LO5Y = (H.NOW - timedelta(days=365 * 5)).strftime("%Y%m%d")


def iso(d):
    return "%s-%s-%s" % (d[:4], d[4:6], d[6:])


def next_bd(d):
    x = datetime.strptime(d, "%Y%m%d") + timedelta(days=1)
    while x.weekday() >= 5:
        x += timedelta(days=1)
    return x.strftime("%Y%m%d")


# ── constructores de respuesta en el formato de cada fuente ───────────────────
def us_serve(data):
    ids = {"DGS3MO": "US_BILL_3M.csv", "DGS6MO": "US_BILL_6M.csv", "DGS1": "US_BILL_1Y.csv", "DGS2": "US_BILL_2Y.csv"}

    def serve(url):
        sid = url.split("id=")[1].split("&")[0]
        return 200, H.fred_csv(data[ids[sid]], series=sid)
    return serve


def cad_serve(data):
    cols = [("TB.CDN.180D.MID", "CAD_BILL_6M.csv"), ("TB.CDN.1Y.MID", "CAD_BILL_1Y.csv"), ("TB.CDN.90D.MID", "CAD_BILL_3M.csv")]
    dates = sorted({d for _, f in cols for d, _ in data[f]})
    look = {f: dict(data[f]) for _, f in cols}
    out = ['"TERMS AND CONDITIONS"', '"https://www.bankofcanada.ca/terms/"', "", '"SERIES"', '"id","label"',
           '"TB.CDN.90D.MID","3m"', "", '"OBSERVATIONS"', '"date",' + ",".join('"%s"' % c for c, _ in cols)]
    for d in dates:
        out.append('"%s",' % iso(d) + ",".join('"%s"' % look[f].get(d, "") for _, f in cols))
    body = ("\n".join(out) + "\n").encode()
    return lambda url: (200, body)


def aud_serve(data):
    files = ["AUD_BILL_1M.csv", "AUD_BILL_3M.csv", "AUD_BILL_6M.csv"]
    dates = sorted({d for f in files for d, _ in data[f]})
    look = {f: dict(data[f]) for f in files}
    head = ["F1 INTEREST RATES AND YIELDS - MONEY MARKET", "Title,Cash Rate Target,BAB 1M,BAB 3M,BAB 6M",
            "Frequency,Daily,Daily,Daily,Daily", "Series ID,FIRMMCRTD,FIRMMBAB30D,FIRMMBAB90D,FIRMMBAB180D"]
    body = [datetime.strptime(d, "%Y%m%d").strftime("%d-%b-%Y") + ",4.35," + ",".join(look[f].get(d, "") for f in files)
            for d in dates]
    b = ("\n".join(head + body) + "\n").encode()
    return lambda url: (200, b)


def eur_serve(data):
    def serve(url):
        t = url.split("SR_")[1].split("?")[0]
        return 200, H.ecb_csv(data["EUR_BILL_%s.csv" % t])
    return serve


JPY_T = ["1Y", "2Y", "3Y", "4Y", "5Y", "6Y", "7Y", "8Y", "9Y", "10Y", "15Y", "20Y", "25Y", "30Y", "40Y"]


def jpy_serve(data):
    look = {t: dict(data.get("JPY_BILL_%s.csv" % t, [])) for t in JPY_T}
    dates = sorted({d for t in look for d in look[t]})

    def csvtext(ds):
        lines = ["Interest Rate (%)", "Date," + ",".join(JPY_T)]
        for d in ds:
            dd = datetime.strptime(d, "%Y%m%d")
            lines.append("%d/%d/%d," % (dd.year, dd.month, dd.day) + ",".join(look[t].get(d, "-") for t in JPY_T))
        return ("\n".join(lines) + "\n").encode()
    hist, cur = csvtext(dates[:-15]), csvtext(dates[-25:])

    def serve(url):
        return 200, (hist if "jgbcme_all" in url else cur)
    return serve


GBP_T = {0.5: "GBP_BILL_6M.csv", 1.0: "GBP_BILL_1Y.csv", 2.0: "GBP_BILL_2Y.csv", 5.0: "GBP_BILL_5Y.csv",
         10.0: "GBP_BILL_10Y.csv"}


def gbp_serve(data):
    arch, latest = io.BytesIO(), io.BytesIO()
    with zipfile.ZipFile(arch, "w") as z:
        z.writestr("GLC Nominal daily data_2016 to present.xlsx", b"ARCHIVE")
    with zipfile.ZipFile(latest, "w") as z:
        z.writestr("GLC Nominal daily data current month.xlsx", b"LATEST")
    a, l = arch.getvalue(), latest.getvalue()
    return lambda url: (200, a if "glcnominalddata" in url else l)


def gbp_patch(data):
    def parse(xbytes, date_from, date_to):
        out = {}
        for t, f in GBP_T.items():
            rows = [r for r in data[f] if date_from.strftime("%Y%m%d") <= r[0] <= date_to.strftime("%Y%m%d")]
            rows = rows[:-10] if xbytes == b"ARCHIVE" else rows[-30:]
            out[t] = {d: float(v) for d, v in rows}
        return out
    return lambda mod: setattr(mod, "_parse_xlsx_bytes", parse)


def acm_serve(data):
    return lambda url: (200, b"XLS" * 1000)


def acm_patch(data):
    def parse(xls, column, date_from, date_to):
        lo, hi = date_from.strftime("%Y%m%d"), date_to.strftime("%Y%m%d")
        return [(d, float(v)) for d, v in data["ACM_TP_10Y.csv"] if lo <= d <= hi]
    return lambda mod: setattr(mod, "parse_acm_daily", parse)


def floor_serve(data):
    def serve(url):
        if "api.stlouisfed.org" in url:
            return 200, json.dumps({"observations": [{"date": iso(d), "value": v} for d, v in data["FLOOR_USD.csv"]]}).encode()
        if "fredgraph" in url:
            return 200, ("observation_date,IORB\n" + "".join("%s,%s\n" % (iso(d), v) for d, v in data["FLOOR_USD.csv"])).encode()
        if "ecb.europa.eu" in url:
            return 200, H.ecb_csv(data["FLOOR_EUR.csv"])
        return 200, json.dumps({"observations": [{"d": iso(d), "V39079": {"v": v}} for d, v in data["FLOOR_CAD.csv"]]}).encode()
    return serve


SPECS = {
    "fetch_us_bills": (["US_BILL_3M.csv", "US_BILL_6M.csv", "US_BILL_1Y.csv", "US_BILL_2Y.csv"], us_serve, None, "window", "DGS6MO"),
    "fetch_cad_bills": (["CAD_BILL_3M.csv", "CAD_BILL_6M.csv", "CAD_BILL_1Y.csv"], cad_serve, None, "window", None),
    "fetch_aud_bills": (["AUD_BILL_1M.csv", "AUD_BILL_3M.csv", "AUD_BILL_6M.csv"], aud_serve, None, "window", None),
    "fetch_eur_bills": (["EUR_BILL_3M.csv", "EUR_BILL_6M.csv", "EUR_BILL_1Y.csv", "EUR_BILL_2Y.csv", "EUR_BILL_5Y.csv",
                         "EUR_BILL_10Y.csv"], eur_serve, None, "window", "SR_5Y"),
    "fetch_jpy_bills": (["JPY_BILL_1Y.csv", "JPY_BILL_2Y.csv", "JPY_BILL_3Y.csv", "JPY_BILL_5Y.csv", "JPY_BILL_10Y.csv",
                         "JPY_BILL_20Y.csv"], jpy_serve, None, "window", "jgbcme.csv"),
    "fetch_gbp_bills": (list(GBP_T.values()), gbp_serve, gbp_patch, "window", "latest-yield"),
    "fetch_acm": (["ACM_TP_10Y.csv"], acm_serve, acm_patch, "window", None),
    "fetch_floor_spreads": (["FLOOR_USD.csv", "FLOOR_EUR.csv", "FLOOR_CAD.csv"], floor_serve, None, "fixed", "ecb.europa.eu"),
}


class Base(object):
    script = None

    def setUp(self):
        self.files, self.serve_fn, self.patch_fn, self.window, self.one_series = SPECS[self.script]
        self.orig = {f: open(os.path.join(ROOT, "data", f), "rb").read() for f in self.files}
        self.rows = {f: H.repo_rows(f) for f in self.files}
        self.roots = []
        os.environ.pop("FRED_API_KEY", None)
        if self.script == "fetch_floor_spreads":        # el original (ed9ed64) exige la clave; la clave se lee al importar
            os.environ["FRED_API_KEY"] = "TESTKEY"
            self.addCleanup(os.environ.pop, "FRED_API_KEY", None)

    def tearDown(self):
        for r in self.roots:
            shutil.rmtree(r, ignore_errors=True)

    def root(self, content=None):
        r = H.make_root(content or self.orig)
        self.roots.append(r)
        return r

    def data(self, extra=None, now=H.NOW):
        lo = (now - timedelta(days=365 * 5)).strftime("%Y%m%d") if self.window == "window" else "00000000"
        out = {}
        for f in self.files:
            rows = [r for r in self.rows[f] if r[0] >= lo]
            if extra:
                rows = rows + extra(f, rows)
            out[f] = rows
        return out

    def new_day(self, f, rows):
        d, v = rows[-1]
        nd = next_bd(max(rr[0] for fr in self.rows.values() for rr in fr[-1:]))
        return [(nd, v)]

    def patch(self, data):
        return self.patch_fn(data) if self.patch_fn else None

    def orig_run(self, serve, root, data, now=H.NOW):
        return H.run_original(self.script, serve, root, now=now, patch=self.patch(data))

    def new_run(self, serve, root, data, now=H.NOW, clock=None):
        return H.run_new(self.script, serve, root, now=now, clock=clock, patch=self.patch(data))

    def both(self, data, serve=None, content=None):
        serve = serve or self.serve_fn(data)
        ro, rn = self.root(content), self.root(content)
        rco, _ = self.orig_run(serve, ro, data)
        rcn, calls, clock = self.new_run(serve, rn, data)
        return rco, rcn, ro, rn, calls, clock

    def read(self, root):
        return {f: H.read(root, f) for f in self.files}

    def job(self):
        return self.script

    def pointer(self, root):
        return json.load(open(os.path.join(root, "data", "_ingest", "latest", "actions__%s.json" % self.job())))

    def alerts(self, root, when=None):
        a = {}
        W.check_actions(root, (when or (H.NOW + timedelta(hours=1))).replace(tzinfo=timezone.utc), a, [])
        return a

    # ── equivalencia ────────────────────────────────────────────────────────
    def test_E1_equivalence_new_day(self):
        data = self.data(self.new_day)
        rco, rcn, ro, rn, *_ = self.both(data)
        self.assertEqual((rco, rcn), (0, 0))
        new, old = self.read(rn), self.read(ro)
        for f in self.files:
            self.assertEqual(new[f], old[f], f)
            self.assertNotEqual(new[f], self.orig[f], f)

    def test_E3_no_news_identical(self):
        data = self.data()
        rco, rcn, ro, rn, *_ = self.both(data)
        new, old = self.read(rn), self.read(ro)
        for f in self.files:
            self.assertEqual(new[f], old[f], f)
            self.assertEqual(new[f], self.orig[f], f)

    # ── fallos ──────────────────────────────────────────────────────────────
    def test_F1_503_persistent_retried_and_intact(self):
        rn = self.root()
        rc, calls, clock = self.new_run(lambda url: (503, b"busy"), rn, self.data())
        self.assertEqual(rc, 1)
        self.assertEqual(self.read(rn), self.orig)
        self.assertEqual(clock.slept[:3], [10, 40, 90])
        self.assertIn("actions:%s:fail" % self.job(), self.alerts(rn))

    def test_F2_truncated_response_keeps_history(self):
        data = self.data()
        f0 = self.files[0]
        data[f0] = data[f0][:20] + data[f0][-3:]
        rn = self.root()
        rc, *_ = self.new_run(self.serve_fn(data), rn, data)
        self.assertEqual(self.read(rn)[f0], self.orig[f0])
        self.assertEqual(rc, 1)
        a = self.alerts(rn)                       # rechazo de la publicación (INVALID) o del propio descargador
        self.assertTrue([k for k in a if k.startswith("actions:%s:" % self.job())], a)

    def test_F3_empty_response(self):
        rn = self.root()
        rc, *_ = self.new_run(lambda url: (200, b""), rn, self.data())
        self.assertEqual(rc, 1)
        self.assertEqual(self.read(rn), self.orig)

    def test_F4_implausible_jump_held_others_published(self):
        f0 = self.files[0]

        def extra(f, rows):
            (nd, v), = self.new_day(f, rows)
            return [(nd, "%.4f" % (float(v) + 5.0) if f == f0 else v)]
        data = self.data(extra)
        rn = self.root()
        rc, *_ = self.new_run(self.serve_fn(data), rn, data)
        got = self.read(rn)
        self.assertEqual(got[f0], self.orig[f0])                               # último válido
        q = json.load(open(os.path.join(rn, "data", "_ingest", "quarantine", f0 + ".json")))
        self.assertEqual([c["status"] for c in q["candidates"].values()], ["PENDING"])
        for f in self.files[1:]:
            self.assertNotEqual(got[f], self.orig[f], f)                        # las demás series avanzan
        self.assertIn("actions:held:%s" % f0, self.alerts(rn))

    def test_F5_one_series_fails_others_published(self):
        if not self.one_series:
            self.skipTest("una sola respuesta para todas las series (no hay fallo parcial posible)")
        data = self.data(self.new_day)
        base = self.serve_fn(data)
        serve = lambda url: (503, b"busy") if self.one_series in url else base(url)   # noqa: E731
        ro, rn = self.root(), self.root()
        rco, _ = self.orig_run(serve, ro, data)
        rcn, *_ = self.new_run(serve, rn, data)
        got, old = self.read(rn), self.read(ro)
        protected = 0
        for f in self.files:
            if got[f] != self.orig[f]:
                self.assertEqual(got[f], old[f], f)                             # lo que se publica = original
            elif old[f] != self.orig[f]:
                # el original sobrescribía con datos más viejos (solo la fuente histórica): ahora se conserva
                self.assertLess(S.parse(old[f]).max_date, S.parse(self.orig[f]).max_date, f)
                protected += 1
        self.assertTrue([f for f in self.files if got[f] != self.orig[f]] or protected)
        a = self.alerts(rn)
        self.assertTrue(("actions:%s:fail" % self.job()) in a or ("actions:%s:degraded" % self.job()) in a, a)

    def test_F6_interruption_during_publication_is_atomic(self):
        data = self.data(self.new_day)
        rn = self.root()
        real = os.replace
        seen = []

        def flaky(src, dst):
            if dst.endswith(".csv"):
                seen.append(dst)
                if len(seen) == min(2, len(self.files)):
                    raise KeyboardInterrupt("corte simulado a mitad de la publicación")
            return real(src, dst)
        with mock.patch("g8common.series.os.replace", flaky):
            with self.assertRaises(KeyboardInterrupt):
                self.new_run(self.serve_fn(data), rn, data)
        got = self.read(rn)
        if len(self.files) > 1:
            first = os.path.basename(seen[0])
            self.assertNotEqual(got[first], self.orig[first])                  # la primera, completa
        second = os.path.basename(seen[-1])
        self.assertEqual(got[second], self.orig[second])                       # la interrumpida, intacta
        for f in self.files:
            S.parse(got[f])                                                     # ninguna a medias

    def test_F7_recovery_after_failure(self):
        rn = self.root()
        self.new_run(lambda url: (503, b"busy"), rn, self.data())
        self.assertIn("actions:%s:fail" % self.job(), self.alerts(rn))
        data = self.data(self.new_day)
        rc, *_ = self.new_run(self.serve_fn(data), rn, data, clock=H.Clock(H.NOW_EPOCH + 3600))
        self.assertEqual(rc, 0)
        self.assertNotIn("actions:%s:fail" % self.job(), self.alerts(rn, H.NOW + timedelta(hours=2)))
        for f in self.files:
            self.assertNotEqual(self.read(rn)[f], self.orig[f], f)

    def test_F8_retry_after_deferred_across_runs(self):
        rn = self.root()
        clock = H.clock_at_now()
        rc, calls, _ = self.new_run(lambda url: (503, (b"", {"retry-after": "3600"})), rn, self.data(), clock=clock)
        self.assertEqual(rc, 1)
        n = len(calls)
        self.assertLessEqual(n, len(self.files) + 1)                            # sin reintentos inútiles
        clock.t += 600
        data = self.data(self.new_day)
        rc2, calls2, _ = self.new_run(self.serve_fn(data), rn, data, clock=clock)
        self.assertEqual(calls2, [])                                            # no se consulta antes de tiempo
        self.assertEqual(self.read(rn), self.orig)
        self.assertNotIn("actions:%s:fail" % self.job(), self.alerts(rn))        # espera prevista, no fallo


def _mk(script):
    name = "Test_" + script
    return type(name, (Base, unittest.TestCase), {"script": script})


for _s in SPECS:
    _c = _mk(_s)
    globals()[_c.__name__] = _c


if __name__ == "__main__":
    unittest.main()

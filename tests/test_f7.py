"""F7 — correcciones puntuales: clave FRED rechazada → endpoint sin clave (visible); sin reintentos de 4xx;
TLS siempre verificado (inseguro solo con G8_ALLOW_INSECURE_TLS=1, nunca en workflows); crédito del BoJ."""
import glob
import io
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.dirname(__file__))

from g8common import g8http, legacy  # noqa: E402
from g8fakes import Clock  # noqa: E402

FRED_CSV = b"observation_date,IORB\n2026-09-22,3.90\n2026-09-23,3.90\n"


def serve(table):
    calls = []

    def transport(method, url, headers, body, ct, rt, tt):
        calls.append(url)
        for key, resp in table:
            if key in url:
                return resp
        return 404, {}, b""
    return transport, calls


class LegacyHttp(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        legacy.TEST_CLOCK = self.clock
        self.tmp = tempfile.mkdtemp()
        legacy.STATE_PATH = os.path.join(self.tmp, "not_before")

    def tearDown(self):
        legacy.TEST_TRANSPORT = None
        legacy.TEST_CLOCK = None
        legacy.STATE_PATH = None
        legacy._STORE.clear()
        shutil.rmtree(self.tmp)

    def test_fred_bad_key_not_retried(self):
        legacy.TEST_TRANSPORT, calls = serve([("api.stlouisfed", (400, {}, b'{"error_message":"api_key is not registered"}'))])
        with self.assertRaises(legacy.LegacyHTTPError) as cm:
            legacy.http_get_text("https://api.stlouisfed.org/fred/series/observations?series_id=X&api_key=K", log=None)
        self.assertEqual(cm.exception.cls, g8http.FAIL_AUTH)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("api_key=K", str(cm.exception))

    def test_404_not_retried_503_retried(self):
        legacy.TEST_TRANSPORT, calls = serve([("x", (404, {}, b""))])
        with self.assertRaises(legacy.LegacyHTTPError):
            legacy.http_get_text("https://h/x", log=None)
        self.assertEqual(len(calls), 1)
        seq = [(503, {}, b""), (200, {}, b"ok")]

        def t(method, url, headers, body, ct, rt, tt):
            return seq.pop(0)
        legacy.TEST_TRANSPORT = t
        self.assertEqual(legacy.http_get_text("https://h/y", log=None), "ok")
        self.assertEqual(self.clock.slept, [10])

    def test_acm_fetch_fred_falls_back_without_key_visible(self):
        import acm_g8
        legacy.TEST_TRANSPORT, calls = serve([
            ("api.stlouisfed", (400, {}, b'{"error_message":"Bad Request. The value for variable api_key is not registered."}')),
            ("fredgraph", (200, {}, b"observation_date,DGS10\n2026-09-22,4.1\n2026-09-23,4.12\n"))])
        os.environ["FRED_API_KEY"] = "BADKEY"
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                s = acm_g8.fetch_fred("DGS10", start="2026-09-01")
        finally:
            os.environ.pop("FRED_API_KEY")
        self.assertEqual(list(s.values), [4.1, 4.12])
        self.assertIn("::error title=FRED_API_KEY::", buf.getvalue())
        self.assertNotIn("BADKEY", buf.getvalue())

    def test_floor_usd_fallback(self):
        # lote 3B: fetch_floor_spreads usa el contexto de ingestión (g8http + publicación segura) en vez de legacy
        import fetch_floor_spreads as F
        from g8common import ingest
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "data"))
        shutil.copytree(os.path.join(ROOT, "sources"), os.path.join(d, "sources"))
        F.FRED_KEY = "BAD"
        try:
            ingest.TEST_TRANSPORT, _ = serve([("api.stlouisfed", (403, {}, b'{"error_message":"api_key invalid"}')),
                                              ("fredgraph", (200, {}, FRED_CSV * 1))])
            F._ctx = ingest.Ingest("fetch_floor_spreads", root=d, now=self.clock, env={})
            with redirect_stdout(io.StringIO()):
                with self.assertRaises(RuntimeError):     # 2 observaciones < 500: la guardia del script sigue activa
                    F.fetch_usd()
            ingest.TEST_TRANSPORT, calls = serve([("api.stlouisfed", (403, {}, b'{"error_message":"api_key invalid"}')),
                                                  ("fredgraph", (200, {}, b"observation_date,IORB\n" + b"".join(
                                                      b"%d-%02d-%02d,4.40\n" % (y, m, dd) for y in (2024, 2025) for m in range(1, 13) for dd in range(1, 29))))])
            F._ctx = ingest.Ingest("fetch_floor_spreads", root=d, now=self.clock, env={})
            with redirect_stdout(io.StringIO()) as out:
                F.fetch_usd()
            self.assertIn("::error title=FRED_API_KEY::", out.getvalue())          # clave rechazada: visible
            self.assertTrue(os.path.exists(os.path.join(d, "data", "FLOOR_USD.csv")))
            self.assertEqual(sum("fredgraph" in c for c in calls), 1)
            self.assertEqual(sum("api.stlouisfed" in c for c in calls), 1)          # 4xx: sin reintentos
        finally:
            ingest.TEST_TRANSPORT = None
            F._ctx = None
            shutil.rmtree(d)


@unittest.skipUnless(shutil.which("openssl"), "openssl no disponible")
class TlsStrict(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=127.0.0.1",
                        "-keyout", os.path.join(cls.d, "k.pem"), "-out", os.path.join(cls.d, "c.pem")],
                       check=True, capture_output=True)

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")
        cls.httpd = HTTPServer(("127.0.0.1", 0), H)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(os.path.join(cls.d, "c.pem"), os.path.join(cls.d, "k.pem"))
        cls.httpd.socket = ctx.wrap_socket(cls.httpd.socket, server_side=True)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.url = "https://127.0.0.1:%d/" % cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()                     # cierra el socket TLS de escucha (ResourceWarning al salir)
        shutil.rmtree(cls.d)

    def test_self_signed_rejected_not_retried(self):
        os.environ.pop("G8_ALLOW_INSECURE_TLS", None)
        r = g8http.fetch(self.url, budget=g8http.Budget(30, env={}))
        self.assertEqual(r.cls, g8http.FAIL_TLS)
        self.assertEqual(len(r.attempts), 1)

    def test_manual_opt_in_only(self):
        os.environ["G8_ALLOW_INSECURE_TLS"] = "1"
        try:
            with redirect_stdout(io.StringIO()) as buf:
                r = g8http.fetch(self.url, budget=g8http.Budget(30, env={}))
        finally:
            os.environ.pop("G8_ALLOW_INSECURE_TLS")
        self.assertEqual(r.cls, g8http.OK)
        self.assertIn("DESACTIVADA", buf.getvalue())


class Static(unittest.TestCase):
    def test_workflows_never_disable_tls(self):
        for f in glob.glob(os.path.join(ROOT, ".github", "workflows", "*.yml")):
            self.assertNotIn("G8_ALLOW_INSECURE_TLS", open(f, encoding="utf-8").read(), f)

    def test_real_yield_fetchers_no_silent_insecure_fallback(self):
        # lote 3B: los reales ya no abren conexiones propias; todo pasa por g8http, cuyo transporte solo desactiva
        # la verificación con G8_ALLOW_INSECURE_TLS=1 (probado en TlsStrict) y nunca en los workflows.
        for f in ("fetch_eur_real.py", "fetch_jpy_real.py"):
            src = open(os.path.join(ROOT, "scripts", f), encoding="utf-8").read()
            self.assertNotIn("urlopen(", src, f)
            self.assertNotIn("_create_unverified_context", src, f)
            self.assertIn("_context().requests(", src, f)

    def test_boj_credit_line(self):
        html = open(os.path.join(ROOT, "docs", "index.html"), encoding="utf-8").read()
        self.assertIn("This service uses the API provided by the", html)
        self.assertIn("Bank of Japan Time-Series Data Search", html)
        self.assertIn("The Bank of Japan does not guarantee the content of the service.", html)


if __name__ == "__main__":
    unittest.main()


class LegacyDeferral(unittest.TestCase):
    """Hallazgo #5: el adaptador F7 conserva y aplica Retry-After entre llamadas, scripts y ejecuciones, por
    ámbito del proveedor; un endpoint alternativo del mismo ámbito no elude el límite."""

    def setUp(self):
        self.clock = Clock()
        legacy.TEST_CLOCK = self.clock
        self.tmp = tempfile.mkdtemp()
        legacy.STATE_PATH = os.path.join(self.tmp, "data", "_ingest", "not_before")
        self.calls = []

        def t(method, url, headers, body, *to):
            self.calls.append(url)
            if "fail" in url:
                return 503, {"retry-after": "3600"}, b""
            return 200, {}, b"DATE,CLOSE\n20260922,1\n"
        legacy.TEST_TRANSPORT = t

    def tearDown(self):
        legacy.TEST_TRANSPORT = None
        legacy.TEST_CLOCK = None
        legacy.STATE_PATH = None
        legacy._STORE.clear()
        shutil.rmtree(self.tmp)

    def get(self, url):
        b = g8http.Budget(360, now=self.clock, env={})
        try:
            legacy.http_get_text(url, budget_obj=b, log=None)
            return "OK"
        except legacy.LegacyHTTPError as e:
            return e.cls

    def test_reviewer_repro_second_call_not_sent(self):
        self.assertEqual(self.get("https://source.invalid/fail/series"), g8http.DEFERRED)
        n = len(self.calls)
        self.assertEqual(self.get("https://source.invalid/fail/series"), g8http.DEFERRED)
        self.assertEqual(len(self.calls), n)                           # ninguna consulta nueva
        self.assertEqual(self.clock.t, 1790000000.0)

    def test_alternate_endpoint_same_scope_blocked_other_scope_free(self):
        self.get("https://api.stlouisfed.org/fail/fred/series/observations?series_id=X")
        n = len(self.calls)
        self.assertEqual(self.get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=X"), g8http.DEFERRED)
        self.assertEqual(len(self.calls), n)
        self.assertEqual(self.get("https://www.bankofcanada.ca/valet/x"), "OK")   # otro proveedor: libre
        self.assertEqual(len(self.calls), n + 1)

    def test_persists_across_runs_and_expires(self):
        self.get("https://www.rba.gov.au/fail/f1.csv")
        legacy._STORE.clear()                                           # «nueva ejecución»: sin memoria
        n = len(self.calls)
        self.clock.t += 1800
        self.assertEqual(self.get("https://www.rba.gov.au/statistics/f2.csv"), g8http.DEFERRED)
        self.assertEqual(len(self.calls), n)
        with open(os.path.join(legacy.STATE_PATH, "local.json")) as fh:
            self.assertIn("rba", fh.read())
        self.clock.t += 1801                                            # pasado Retry-After
        self.assertEqual(self.get("https://www.rba.gov.au/statistics/f2.csv"), "OK")
        self.assertEqual(len(self.calls), n + 1)

    def test_shared_with_actions_fetchers(self):
        from g8common import ingest
        self.get("https://api.stlouisfed.org/fail/fred/series/observations")
        ingest.TEST_TRANSPORT = legacy.TEST_TRANSPORT
        try:
            ctx = ingest.Ingest("fetch_sofr", root=self.tmp, now=self.clock, env={})
            n = len(self.calls)
            with self.assertRaises(ingest.HTTPError) as cm:
                ctx.requests(provider="fred").get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=SOFR")
            self.assertEqual(cm.exception.g8cls, g8http.DEFERRED)
            self.assertEqual(len(self.calls), n)
        finally:
            ingest.TEST_TRANSPORT = None

    def test_other_workflow_file_is_read_not_written(self):
        from g8common import defer
        other = defer.Store(legacy.STATE_PATH, now=self.clock, job="metals")
        other.set("stlouisfed", self.clock.t + 600, source="metals")
        self.assertEqual(self.get("https://api.stlouisfed.org/fred/x"), g8http.DEFERRED)   # la unión se respeta
        self.assertEqual(sorted(os.listdir(legacy.STATE_PATH)), ["metals.json"])       # y no se reescribe

    def test_scope_table(self):
        from g8common import defer
        self.assertEqual(defer.scope_for("https://api.stlouisfed.org/x"), defer.scope_for("https://fred.stlouisfed.org/y"))
        self.assertEqual(defer.scope_for("https://data-api.ecb.europa.eu/x"), "ecb")
        self.assertNotEqual(defer.scope_for("https://ec.europa.eu/x"), "ecb")
        self.assertEqual(defer.scope_for("https://www.example.co.uk/x"), "example.co.uk")

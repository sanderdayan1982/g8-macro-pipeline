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

    def tearDown(self):
        legacy.TEST_TRANSPORT = None
        legacy.TEST_CLOCK = None

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
        import fetch_floor_spreads as F
        legacy.TEST_TRANSPORT, _ = serve([("api.stlouisfed", (403, {}, b'{"error_message":"api_key invalid"}')),
                                          ("fredgraph", (200, {}, FRED_CSV * 1))])
        d = tempfile.mkdtemp()
        F.OUT_DIR, F.FRED_KEY = d, "BAD"
        try:
            with redirect_stdout(io.StringIO()):
                with self.assertRaises(RuntimeError):     # 2 observaciones < 500: la guardia del script sigue activa
                    F.fetch_usd()
            legacy.TEST_TRANSPORT, calls = serve([("api.stlouisfed", (403, {}, b'{"error_message":"api_key invalid"}')),
                                                  ("fredgraph", (200, {}, b"observation_date,IORB\n" + b"".join(
                                                      b"%d-%02d-%02d,4.40\n" % (y, m, dd) for y in (2024, 2025) for m in range(1, 13) for dd in range(1, 29))))])
            with redirect_stdout(io.StringIO()):
                F.fetch_usd()
            self.assertTrue(os.path.exists(os.path.join(d, "FLOOR_USD.csv")))
            self.assertEqual(sum("fredgraph" in c for c in calls), 1)
        finally:
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
        import fetch_eur_real as E

        def boom(req, timeout=None, context=None):
            if context is None:
                raise urllib.error.URLError(ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED"))
            return "OPENED_INSECURE"
        orig = E.urllib.request.urlopen
        E.urllib.request.urlopen = boom
        try:
            os.environ.pop("G8_ALLOW_INSECURE_TLS", None)
            with self.assertRaises(urllib.error.URLError):
                E._open("https://x/")
            os.environ["G8_ALLOW_INSECURE_TLS"] = "1"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(E._open("https://x/"), "OPENED_INSECURE")
        finally:
            E.urllib.request.urlopen = orig
            os.environ.pop("G8_ALLOW_INSECURE_TLS", None)

    def test_boj_credit_line(self):
        html = open(os.path.join(ROOT, "docs", "index.html"), encoding="utf-8").read()
        self.assertIn("This service uses the API provided by the", html)
        self.assertIn("Bank of Japan Time-Series Data Search", html)
        self.assertIn("The Bank of Japan does not guarantee the content of the service.", html)


if __name__ == "__main__":
    unittest.main()

"""Pruebas de g8common.g8http — reintentos, Retry-After, aplazamiento, clasificación, presupuesto, redacción."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.dirname(__file__))

from g8common import g8http  # noqa: E402
from g8fakes import Clock, ScriptedServer  # noqa: E402


def ok(body=b"DATE,CLOSE\n20260922,3.87\n", headers=None):
    return (200, headers or {}, body)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.srv = None
        self.clock = Clock()

    def tearDown(self):
        if self.srv:
            self.srv.close()

    def run_fetch(self, script, path="/x", budget_s=600, **kw):
        self.srv = ScriptedServer({path: script})
        b = g8http.Budget(budget_s, now=self.clock, env={})
        return g8http.fetch(self.srv.url + path, budget=b, sleep=self.clock.sleep, now=self.clock, **kw)

    def test_ok_first_attempt(self):
        r = self.run_fetch([ok()])
        self.assertEqual(r.cls, g8http.OK)
        self.assertEqual(len(r.attempts), 1)

    def test_transient_then_ok_uses_10_40(self):
        r = self.run_fetch([(503, {}, b""), (502, {}, b""), ok()])
        self.assertEqual(r.cls, g8http.OK)
        self.assertEqual(self.clock.slept, [10, 40])

    def test_max_four_attempts_10_40_90(self):
        r = self.run_fetch([(500, {}, b"")])
        self.assertEqual(r.cls, g8http.FAIL_TRANSIENT)
        self.assertEqual(len(r.attempts), 4)
        self.assertEqual(self.clock.slept, [10, 40, 90])
        self.assertEqual(len(self.srv.requests), 4)

    def test_retry_after_respected_never_earlier(self):
        r = self.run_fetch([(429, {"Retry-After": "55"}, b""), ok()])
        self.assertEqual(r.cls, g8http.OK)
        self.assertEqual(self.clock.slept, [55.0])            # max(55, 10): nunca antes de lo indicado

    def test_retry_after_shorter_than_schedule_uses_schedule(self):
        self.run_fetch([(503, {"Retry-After": "3"}, b""), ok()])
        self.assertEqual(self.clock.slept, [10.0])

    def test_retry_after_beyond_budget_defers(self):
        r = self.run_fetch([(503, {"Retry-After": "3600"}, b"")], budget_s=360)
        self.assertEqual(r.cls, g8http.DEFERRED)
        self.assertAlmostEqual(r.not_before, self.clock() + 3600, delta=1)
        self.assertEqual(len(self.srv.requests), 1)             # no reintenta antes de lo indicado
        self.assertFalse(getattr(self.clock, "slept", []))

    def test_not_before_blocks_call(self):
        self.srv = ScriptedServer({"/x": [ok()]})
        r = g8http.fetch(self.srv.url + "/x", not_before=self.clock() + 100, now=self.clock,
                         budget=g8http.Budget(600, now=self.clock, env={}))
        self.assertEqual(r.cls, g8http.DEFERRED)
        self.assertEqual(len(self.srv.requests), 0)

    def test_budget_exhausted_no_sleep_beyond(self):
        r = self.run_fetch([(500, {}, b"")], budget_s=45)
        self.assertEqual(r.cls, g8http.FAIL_TRANSIENT)
        self.assertEqual(self.clock.slept, [10])                # 40 ya no cabe
        self.assertIn("presupuesto", r.detail)

    def test_job_deadline_caps_budget(self):
        b = g8http.Budget(600, now=self.clock, env={"G8_JOB_DEADLINE_EPOCH": str(self.clock() + 100),
                                                    "G8_JOB_RESERVE_S": "80"})
        self.assertAlmostEqual(b.remaining(), 20, delta=0.01)

    def test_403_waf_is_access_not_auth(self):
        r = self.run_fetch([(403, {}, b"blocked")], provider="rbnz")
        self.assertEqual(r.cls, g8http.FAIL_ACCESS)
        self.assertEqual(len(self.srv.requests), 1)

    def test_github_403_permission_vs_ratelimit(self):
        r = self.run_fetch([(403, {}, b'{"message":"Resource not accessible by personal access token"}')], provider="github")
        self.assertEqual(r.cls, g8http.FAIL_PERMISSION)
        self.srv.close()
        self.clock = Clock()
        r = self.run_fetch([(403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(self.clock()) + 50)}, b""), ok()],
                           provider="github")
        self.assertEqual(r.cls, g8http.OK)
        self.assertGreaterEqual(self.clock.slept[0], 50)

    def test_github_401_auth(self):
        r = self.run_fetch([(401, {}, b"Bad credentials")], provider="github")
        self.assertEqual(r.cls, g8http.FAIL_AUTH)

    def test_fred_bad_key(self):
        r = self.run_fetch([(400, {}, b'{"error_message":"Bad Request. The value for variable api_key is not registered."}')],
                           provider="fred_api")
        self.assertEqual(r.cls, g8http.FAIL_AUTH)

    def test_404_date_url_is_no_publication(self):
        r = self.run_fetch([(404, {}, b"")], not_found_is_no_publication=True)
        self.assertEqual(r.cls, g8http.NO_PUBLICATION)
        self.srv.close()
        r = self.run_fetch([(404, {}, b"")])
        self.assertEqual(r.cls, g8http.FAIL_INVALID)

    def test_validate_hook_boj_status_503_transient(self):
        bad = ok(b'{"STATUS":503,"MESSAGE":"db"}')
        good = ok(b'{"STATUS":200}')

        def v(body, h):
            import json
            st = json.loads(body.decode()).get("STATUS")
            return None if st == 200 else ((g8http.FAIL_TRANSIENT if st == 503 else g8http.FAIL_INVALID), "STATUS %s" % st)
        r = self.run_fetch([bad, good], validate=v)
        self.assertEqual(r.cls, g8http.OK)
        self.assertEqual(self.clock.slept, [10])

    def test_truncated_transfer_is_transient_and_retried(self):
        r = self.run_fetch(["TRUNCATE", ok()])
        self.assertEqual(r.cls, g8http.OK)
        self.assertIn("incomplete", r.attempts[0]["detail"])

    def test_read_timeout_real_clock(self):
        self.srv = ScriptedServer({"/x": ["HANG"]})
        b = g8http.Budget(3, env={})
        r = g8http.fetch(self.srv.url + "/x", budget=b, read_timeout=0.3, connect_timeout=1, max_attempts=1)
        self.assertEqual(r.cls, g8http.FAIL_TRANSIENT)
        self.assertIn("timeout", r.attempts[0]["detail"])

    def test_writes_not_retried_unless_idempotent(self):
        r = self.run_fetch([(503, {}, b""), ok()], method="POST", body=b"x")
        self.assertEqual(r.cls, g8http.FAIL_TRANSIENT)
        self.assertEqual(len(self.srv.requests), 1)

    def test_redaction(self):
        u = g8http.redact("https://api.stlouisfed.org/fred/series/observations?series_id=IORB&api_key=SECRET123&file_type=json")
        self.assertNotIn("SECRET123", u)
        u2 = g8http.redact("https://api.telegram.org/bot123:ABCsecret/sendMessage")
        self.assertNotIn("ABCsecret", u2)

    def test_secret_never_in_record(self):
        r = self.run_fetch([(500, {}, b"")], path="/fred", budget_s=5)
        rec = r.record()
        self.assertNotIn("api_key=", str(rec))


if __name__ == "__main__":
    unittest.main()


class RedirectTests(unittest.TestCase):
    """Hallazgo #6: seguir redirecciones válidas con límite de saltos, presupuesto común, sin reenviar
    credenciales a otro origen y sin degradar https → http."""

    def setUp(self):
        self.clock = Clock()
        self.servers = []

    def tearDown(self):
        for s in self.servers:
            s.close()

    def srv(self, script):
        s = ScriptedServer(script)
        self.servers.append(s)
        return s

    def budget(self, s=600):
        return g8http.Budget(s, now=self.clock, env={})

    def test_relative_302_followed_over_real_socket(self):
        a = self.srv({"/old": [(302, {"Location": "/new?x=1"}, b"")], "/new": [ok()]})
        r = g8http.fetch(a.url + "/old", budget=self.budget(), sleep=self.clock.sleep, now=self.clock)
        self.assertEqual(r.cls, g8http.OK)
        self.assertEqual(r.body, ok()[2])
        self.assertEqual([x["status"] for x in r.redirects], [302])
        self.assertEqual([p for _, p, _, _ in a.requests], ["/old", "/new?x=1"])
        self.assertEqual(len(r.attempts), 1)                              # la redirección no es un reintento

    def test_all_redirect_codes(self):
        for code in (301, 302, 303, 307, 308):
            a = self.srv({"/o": [(code, {"Location": "/n"}, b"")], "/n": [ok()]})
            r = g8http.fetch(a.url + "/o", budget=self.budget(), sleep=self.clock.sleep, now=self.clock)
            self.assertEqual(r.cls, g8http.OK, code)

    def test_cross_origin_strips_credentials_same_origin_keeps_them(self):
        b = self.srv({"/n": [ok()]})
        a = self.srv({"/o": [(302, {"Location": b.url + "/n"}, b"")], "/same": [(302, {"Location": "/o2"}, b"")],
                      "/o2": [ok()]})
        hdr = {"Authorization": "Bearer SECRETO", "X-Api-Key": "K", "Accept": "text/csv"}
        r = g8http.fetch(a.url + "/o", headers=hdr, budget=self.budget(), sleep=self.clock.sleep, now=self.clock)
        self.assertEqual(r.cls, g8http.OK)
        sent_a = a.requests[0][2]
        sent_b = b.requests[0][2]
        self.assertEqual(sent_a.get("Authorization"), "Bearer SECRETO")
        self.assertNotIn("Authorization", sent_b)
        self.assertNotIn("X-Api-Key", sent_b)
        self.assertEqual(sent_b.get("Accept"), "text/csv")                # cabeceras no sensibles se conservan
        r2 = g8http.fetch(a.url + "/same", headers=hdr, budget=self.budget(), sleep=self.clock.sleep, now=self.clock)
        self.assertEqual(r2.cls, g8http.OK)
        self.assertEqual(a.requests[-1][2].get("Authorization"), "Bearer SECRETO")

    def test_loop_is_cut(self):
        a = self.srv({"/a": [(302, {"Location": "/b"}, b"")], "/b": [(302, {"Location": "/a"}, b"")]})
        r = g8http.fetch(a.url + "/a", budget=self.budget(), sleep=self.clock.sleep, now=self.clock)
        self.assertEqual(r.cls, g8http.FAIL_INVALID)
        self.assertIn("bucle", r.detail)
        self.assertEqual(len(a.requests), 2)
        self.assertEqual(len(r.attempts), 1)                              # no se reintenta un bucle

    def test_max_hops(self):
        script = {"/r%d" % i: [(302, {"Location": "/r%d" % (i + 1)}, b"")] for i in range(10)}
        a = self.srv(script)
        r = g8http.fetch(a.url + "/r0", budget=self.budget(), sleep=self.clock.sleep, now=self.clock)
        self.assertEqual(r.cls, g8http.FAIL_INVALID)
        self.assertIn("más de %d" % g8http.MAX_REDIRECTS, r.detail)
        self.assertEqual(len(a.requests), g8http.MAX_REDIRECTS + 1)

    def test_https_to_http_downgrade_refused(self):
        seen = []

        def t(method, url, headers, body, *to):
            seen.append(url)
            return (302, {"location": "http://source.invalid/new"}, b"") if url.startswith("https") else ok()
        r = g8http.fetch("https://source.invalid/old", transport=t, budget=self.budget(), now=self.clock,
                         sleep=self.clock.sleep)
        self.assertEqual(r.cls, g8http.FAIL_INVALID)
        self.assertIn("https a http", r.detail)
        self.assertEqual(seen, ["https://source.invalid/old"])            # nunca se llama a la URL http

    def test_other_scheme_and_missing_location(self):
        t = lambda *a: (302, {"location": "ftp://x/y"}, b"")               # noqa: E731
        self.assertEqual(g8http.fetch("https://s.invalid/o", transport=t, budget=self.budget(), now=self.clock).cls,
                         g8http.FAIL_INVALID)
        t2 = lambda *a: (302, {}, b"")                                     # noqa: E731
        r = g8http.fetch("https://s.invalid/o", transport=t2, budget=self.budget(), now=self.clock)
        self.assertEqual(r.cls, g8http.FAIL_INVALID)

    def test_write_is_not_redirected(self):
        a = self.srv({"/w": [(307, {"Location": "/w2"}, b"")], "/w2": [ok()]})
        r = g8http.fetch(a.url + "/w", method="POST", body=b"{}", budget=self.budget(), now=self.clock,
                         sleep=self.clock.sleep)
        self.assertEqual(r.cls, g8http.FAIL_INVALID)
        self.assertEqual(len(a.requests), 1)

    def test_hops_share_the_budget(self):
        calls = []

        def t(method, url, headers, body, ct, rt, total):
            calls.append(total)
            self.clock.t += 40                                             # cada salto tarda 40 s
            return (302, {"location": url + "x"}, b"") if len(calls) < 4 else ok()
        r = g8http.fetch("https://s.invalid/o", transport=t, budget=self.budget(100), now=self.clock,
                         sleep=self.clock.sleep)
        self.assertEqual(len(calls), 3)                                    # 100 s: el 4.º salto ya no cabe
        self.assertNotEqual(r.cls, g8http.OK)
        self.assertTrue(all(b <= a for a, b in zip(calls, calls[1:])))     # el plazo de cada salto decrece

    def test_reviewer_repro_302_now_followed(self):
        seq = []

        def t(method, url, *a):
            seq.append(url)
            return (302, {"location": "https://source.invalid/new"}, b"") if url.endswith("/old") else ok()
        r = g8http.fetch("https://source.invalid/old", transport=t, now=self.clock)
        self.assertEqual((r.cls, len(r.attempts)), (g8http.OK, 1))
        self.assertEqual(seq, ["https://source.invalid/old", "https://source.invalid/new"])

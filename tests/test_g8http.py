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

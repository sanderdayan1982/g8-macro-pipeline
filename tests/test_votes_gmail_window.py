"""Session window on the Gmail query, pause between gets, rate-limit retries.

Orden de Sander 14-nov-2026 16:28 WAT. Sleeps are the injected callable, so
these tests do not wait on the clock.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.parse
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import votes_ingest as V  # noqa: E402

LEAK = "LEAK_RATE_LIMIT_BODY_MESSAGE"
NY = ZoneInfo("America/New_York")
WAT = ZoneInfo("Africa/Malabo")
ENV = {
    "GMAIL_OAUTH_CLIENT_ID": "client-id.apps.googleusercontent.com",
    "GMAIL_OAUTH_CLIENT_SECRET": "LEAK_CLIENT_SECRET",
    "GMAIL_READONLY_REFRESH_TOKEN": "refresh-token",
}


def _epoch(dt):
    return int(dt.timestamp())


def _rate_body(reason, code):
    return json.dumps({
        "error": {
            "errors": [{"domain": "global", "reason": reason, "message": LEAK}],
            "code": code,
            "message": LEAK,
            "status": "RESOURCE_EXHAUSTED",
        }
    }).encode("utf-8")


def _message(mid):
    return json.dumps({
        "id": mid,
        "internalDate": "1794607300000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [{"name": "Subject", "value": "Alert: RISK_G8"}],
            "body": {"data": ""},
        },
    }).encode("utf-8")


class SessionWindow(unittest.TestCase):
    """after: is the 1D close; before: is 05:15 WAT the next morning."""

    def _query(self, session):
        seen = []

        def http(method, url, headers, body):
            seen.append(url)
            if url == V.TOKEN_URL:
                return 200, {}, b'{"access_token":"access"}'
            if url.startswith(V.LIST_URL + "?"):
                return 200, {}, b'{"messages":[]}'
            raise AssertionError(url)

        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        factor = os.path.join(tmp, "factor.json")
        with open(factor, "w", encoding="utf-8") as fh:
            json.dump({
                "version": "1.0.1",
                "as_of": session,
                "generated_utc": session + "T18:00:00Z",
                "currencies": [],
            }, fh)
        rc = V.run(
            ["--session", session, "--attempt", "first", "--dry-run",
             "--factor", factor,
             "--votes", os.path.join(tmp, "votes.csv"),
             "--weekly", os.path.join(tmp, "weekly.json")],
            http=http, sender=lambda _t: (_ for _ in ()).throw(AssertionError("telegram")),
            env=ENV, sleep=lambda _s: None,
            now=datetime(2026, 11, 14, 3, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(rc, 0)
        listed = [url for url in seen if url.startswith(V.LIST_URL + "?")]
        self.assertEqual(len(listed), 1)
        return urllib.parse.parse_qs(urllib.parse.urlparse(listed[0]).query)["q"][0]

    def test_winter_session_2026_11_13(self):
        # Friday. EST: 17:00 NY = 22:00 UTC = 23:00 WAT. Cutoff 05:15 WAT = 04:15 UTC.
        close = datetime(2026, 11, 13, 17, 0, tzinfo=NY)
        cutoff = datetime(2026, 11, 14, 5, 15, tzinfo=WAT)
        self.assertEqual(close.astimezone(timezone.utc), datetime(2026, 11, 13, 22, 0, tzinfo=timezone.utc))
        self.assertEqual(close.astimezone(WAT).hour, 23)
        self.assertEqual(cutoff.astimezone(timezone.utc), datetime(2026, 11, 14, 4, 15, tzinfo=timezone.utc))
        after_s, before_s = _epoch(close), _epoch(cutoff)
        self.assertEqual(after_s, V.expected_bar_close_ms("2026-11-13") // 1000)
        self.assertEqual(before_s, int(round(V.session_cutoff("2026-11-13").timestamp())))
        q = self._query("2026-11-13")
        self.assertEqual(q, "%s after:%d before:%d" % (V.GMAIL_QUERY, after_s, before_s))
        self.assertIn("after:1794607200", q)
        self.assertIn("before:1794629700", q)

    def test_summer_session_2026_07_15(self):
        # Wednesday. EDT: 17:00 NY = 21:00 UTC = 22:00 WAT. Cutoff stays 04:15 UTC.
        close = datetime(2026, 7, 15, 17, 0, tzinfo=NY)
        cutoff = datetime(2026, 7, 16, 5, 15, tzinfo=WAT)
        self.assertEqual(close.astimezone(timezone.utc), datetime(2026, 7, 15, 21, 0, tzinfo=timezone.utc))
        self.assertEqual(close.astimezone(WAT).hour, 22)
        self.assertEqual(cutoff.astimezone(timezone.utc), datetime(2026, 7, 16, 4, 15, tzinfo=timezone.utc))
        after_s, before_s = _epoch(close), _epoch(cutoff)
        self.assertEqual(after_s, V.expected_bar_close_ms("2026-07-15") // 1000)
        self.assertEqual(before_s, int(round(V.session_cutoff("2026-07-15").timestamp())))
        q = self._query("2026-07-15")
        self.assertEqual(q, "%s after:%d before:%d" % (V.GMAIL_QUERY, after_s, before_s))
        self.assertIn("after:1784149200", q)
        self.assertIn("before:1784175300", q)


class PauseBetweenGets(unittest.TestCase):
    def test_pause_is_between_consecutive_gets_only(self):
        log = []

        def http(method, url, headers, body):
            log.append(("http", url))
            if url == V.TOKEN_URL:
                return 200, {}, b'{"access_token":"access"}'
            if url.startswith(V.LIST_URL + "?"):
                return 200, {}, b'{"messages":[{"id":"m1"},{"id":"m2"}]}'
            if url.startswith(V.LIST_URL + "/m1"):
                return 200, {}, _message("m1")
            if url.startswith(V.LIST_URL + "/m2"):
                return 200, {}, _message("m2")
            raise AssertionError(url)

        def sleep(seconds):
            log.append(("sleep", seconds))

        msgs = V.fetch_messages(
            "cid", "sec", "ref", http, sleep, session="2026-11-13", jitter_unit=lambda: 0.0)
        self.assertEqual([m["id"] for m in msgs], ["m1", "m2"])
        self.assertGreaterEqual(V.MESSAGES_GET_PAUSE_S, 0.1)
        self.assertLessEqual(V.MESSAGES_GET_PAUSE_S, 0.2)
        gets = [i for i, (kind, url) in enumerate(log)
                 if kind == "http" and url.startswith(V.LIST_URL + "/m")]
        sleeps = [(i, value) for i, (kind, value) in enumerate(log) if kind == "sleep"]
        self.assertEqual(len(gets), 2)
        self.assertEqual([value for _i, value in sleeps], [V.MESSAGES_GET_PAUSE_S])
        self.assertLess(gets[0], sleeps[0][0])
        self.assertLess(sleeps[0][0], gets[1])


class RateLimitRetry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.votes = os.path.join(self.tmp, "votes.csv")
        self.weekly = os.path.join(self.tmp, "weekly.json")
        self.factor = os.path.join(self.tmp, "factor.json")
        with open(self.factor, "w", encoding="utf-8") as fh:
            json.dump({
                "version": "1.0.1",
                "as_of": "2026-11-13",
                "generated_utc": "2026-11-13T18:00:00Z",
                "currencies": [{"ccy": "EUR", "E21": -0.38}],
            }, fh)
        self.sent = []

    def _http(self, status, body, failures):
        """Token succeeds. The next `failures` Gmail calls return `status`, then an empty list."""
        gmail_calls = []

        def http(method, url, headers, req_body):
            if url == V.TOKEN_URL:
                return 200, {}, b'{"access_token":"access"}'
            gmail_calls.append(url)
            if len(gmail_calls) <= failures:
                return status, {}, body
            return 200, {}, b'{"messages":[]}'

        return http, gmail_calls

    def _assert_retried_then_ok(self, status, body, failures=2):
        http, gmail_calls = self._http(status, body, failures)
        sleeps = []
        msgs = V.fetch_messages(
            "cid", "sec", "ref", http, sleeps.append,
            session="2026-11-13", jitter_unit=lambda: 0.0)
        self.assertEqual(msgs, [])
        self.assertEqual(len(gmail_calls), failures + 1)
        self.assertEqual(len(sleeps), failures)
        for delay in sleeps:
            self.assertGreater(delay, 0)
            self.assertLessEqual(delay, V.RATE_LIMIT_SLEEP_CAP_S)
        self.assertLessEqual(sum(sleeps), V.RATE_LIMIT_TOTAL_WAIT_S)
        self.assertNotIn(LEAK, repr(msgs))

    def test_403_rate_limit_exceeded_retries_then_succeeds(self):
        self._assert_retried_then_ok(403, _rate_body("rateLimitExceeded", 403))

    def test_403_user_rate_limit_exceeded_retries_then_succeeds(self):
        self._assert_retried_then_ok(403, _rate_body("userRateLimitExceeded", 403))

    def test_429_retries_then_succeeds_without_a_rate_limit_reason(self):
        # 429 is a rate limit even when errors[0].reason is something else.
        self._assert_retried_then_ok(429, _rate_body("backendError", 429))

    def _run(self, http, sleep):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = V.run(
                ["--attempt", "final", "--session", "2026-11-13",
                 "--votes", self.votes, "--weekly", self.weekly, "--factor", self.factor],
                http=http, sender=self.sent.append, env=ENV, sleep=sleep,
                now=datetime(2026, 11, 14, 4, 0, tzinfo=timezone.utc),
            )
        return rc, err.getvalue()

    def test_exhausted_rate_limit_is_api_not_auth(self):
        body = _rate_body("rateLimitExceeded", 403)
        http, gmail_calls = self._http(403, body, failures=V.RATE_LIMIT_ATTEMPTS + 5)
        sleeps = []
        with self.assertRaises(V.GmailError) as caught:
            V.fetch_messages(
                "cid", "sec", "ref", http, sleeps.append,
                session="2026-11-13", jitter_unit=lambda: 0.0)
        exc = caught.exception
        self.assertEqual(exc.kind, "api")
        self.assertNotEqual(exc.kind, "auth")
        self.assertEqual(exc.status, 403)
        self.assertIsNone(exc.oauth_error)
        self.assertIsNone(exc.gmail_reason)
        self.assertNotIn("auth", str(exc))
        self.assertNotIn(LEAK, str(exc))
        self.assertEqual(len(gmail_calls), V.RATE_LIMIT_ATTEMPTS)
        expected = [V.rate_limit_backoff_seconds(i, lambda: 0.0) for i in range(V.RATE_LIMIT_ATTEMPTS - 1)]
        self.assertEqual(sleeps, expected)
        self.assertLessEqual(sum(sleeps), V.RATE_LIMIT_TOTAL_WAIT_S)

        http2, gmail_calls2 = self._http(403, body, failures=V.RATE_LIMIT_ATTEMPTS + 5)
        rc, err = self._run(http2, sleep=lambda _s: None)
        self.assertEqual(rc, 1)
        self.assertEqual(len(gmail_calls2), V.RATE_LIMIT_ATTEMPTS)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Gmail failure (api) on attempt final. Session 2026-11-13.", err)
        self.assertNotIn("(auth)", err)
        self.assertNotIn("kind=auth", self.sent[0])
        self.assertIn("kind=api", self.sent[0])
        self.assertIn("http_status=403", self.sent[0])
        self.assertNotIn("gmail_reason", err)
        self.assertNotIn("gmail_reason", self.sent[0])
        self.assertNotIn(LEAK, err)
        self.assertNotIn(LEAK, self.sent[0])
        self.assertNotIn("LEAK_CLIENT_SECRET", err)
        self.assertNotIn("refresh-token", self.sent[0])

    def test_exhausted_429_is_api_not_auth(self):
        body = _rate_body("backendError", 429)
        http, gmail_calls = self._http(429, body, failures=20)
        with self.assertRaises(V.GmailError) as caught:
            V.fetch_messages(
                "cid", "sec", "ref", http, lambda _s: None,
                session="2026-11-13", jitter_unit=lambda: 0.0)
        self.assertEqual(caught.exception.kind, "api")
        self.assertNotEqual(caught.exception.kind, "auth")
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(len(gmail_calls), V.RATE_LIMIT_ATTEMPTS)
        self.assertNotIn(LEAK, str(caught.exception))
        self.assertNotIn("auth", str(caught.exception))

    def test_plain_403_is_still_auth_and_is_not_retried(self):
        body = _rate_body("insufficientPermissions", 403).replace(
            b"RESOURCE_EXHAUSTED", b"PERMISSION_DENIED")
        http, gmail_calls = self._http(403, body, failures=5)
        sleeps = []
        with self.assertRaises(V.GmailError) as caught:
            V.fetch_messages(
                "cid", "sec", "ref", http, sleeps.append,
                session="2026-11-13", jitter_unit=lambda: 0.0)
        exc = caught.exception
        self.assertEqual(exc.kind, "auth")
        self.assertEqual(exc.status, 403)
        self.assertEqual(exc.gmail_reason, "insufficientPermissions")
        self.assertEqual(exc.gmail_status, "PERMISSION_DENIED")
        self.assertEqual(len(gmail_calls), 1)
        self.assertEqual(sleeps, [])
        self.assertNotIn(LEAK, str(exc))

        http2, gmail_calls2 = self._http(403, body, failures=5)
        recorded = []
        rc, err = self._run(http2, sleep=recorded.append)
        self.assertEqual(rc, 1)
        self.assertEqual(len(gmail_calls2), 1)
        self.assertEqual(recorded, [])
        self.assertEqual(len(self.sent), 1)
        expected = ("Gmail failure (auth) on attempt final. Session 2026-11-13. "
                    "HTTP 403 gmail_reason=insufficientPermissions gmail_status=PERMISSION_DENIED")
        suffix = "HTTP 403 gmail_reason=insufficientPermissions gmail_status=PERMISSION_DENIED"
        self.assertIn(expected, err)
        self.assertIn(suffix, self.sent[0])
        self.assertIn("kind=auth", self.sent[0])
        self.assertNotIn(LEAK, err)
        self.assertNotIn(LEAK, self.sent[0])
        self.assertNotIn("LEAK_CLIENT_SECRET", err)


if __name__ == "__main__":
    unittest.main()

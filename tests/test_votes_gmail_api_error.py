"""Gmail API read 401/403: whitelisted reason and status, no body.

Orden de Sander 14-nov-2026 15:47 WAT, narrowed 16:08 WAT (option A):
the suffix is appended only for HTTP 401 or 403 on the read. The token
exchange is unchanged, and a 503 keeps the previous log line.
"""
import contextlib
import csv
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import votes_ingest as V  # noqa: E402

LEAK_MESSAGE = "LEAK_GMAIL_ERROR_MESSAGE"
LEAK_INNER = "LEAK_GMAIL_ERRORS_MESSAGE"
LEAK_HEADER = "LEAK_GMAIL_HEADER"
LEAK_TOKEN = "LEAK_ACCESS_TOKEN"
LEAK_SECRET = "LEAK_CLIENT_SECRET"
BAD_REASON = "bad reason LEAK_BAD_REASON"
BAD_STATUS = "THIS_VALUE_IS_WAY_TOO_LONG_FOR_THE_WHITELIST_XX"


def _factor():
    return {
        "version": "1.0.1",
        "as_of": "2026-11-13",
        "generated_utc": "2026-11-13T18:00:00Z",
        "currencies": [
            {"ccy": "EUR", "E21": -0.38},
            {"ccy": "GBP", "E21": 0.962},
        ],
    }


def _gmail_body(reason=None, status=None, include_reason=True, include_status=True):
    entry = {"domain": "global", "message": LEAK_INNER}
    if include_reason:
        entry["reason"] = reason
    err = {
        "errors": [entry],
        "code": 403,
        "message": LEAK_MESSAGE,
    }
    if include_status:
        err["status"] = status
    return json.dumps({"error": err}).encode("utf-8")


class GmailApiErrorFields(unittest.TestCase):
    """Gmail read 401/403. Token exchange and 5xx stay on the previous text."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.votes = os.path.join(self.tmp, "votes.csv")
        self.weekly = os.path.join(self.tmp, "votes_weekly.json")
        self.factor = os.path.join(self.tmp, "USD_FACTOR.json")
        with open(self.factor, "w", encoding="utf-8") as fh:
            json.dump(_factor(), fh)
        self.sent = []

    def _http(self, gmail_status, body, headers=None):
        extra = headers or {"X-Debug": LEAK_HEADER}

        def http(method, url, req_headers, req_body):
            if url == V.TOKEN_URL:
                return 200, {}, json.dumps({"access_token": LEAK_TOKEN}).encode("utf-8")
            return gmail_status, dict(extra), body

        return http

    def _run(self, http):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = V.run(
                ["--attempt", "final", "--session", "2026-11-13",
                 "--votes", self.votes, "--weekly", self.weekly, "--factor", self.factor],
                http=http, sender=self.sent.append,
                env={
                    "GMAIL_OAUTH_CLIENT_ID": "client-id.apps.googleusercontent.com",
                    "GMAIL_OAUTH_CLIENT_SECRET": LEAK_SECRET,
                    "GMAIL_READONLY_REFRESH_TOKEN": "refresh-token",
                    "TELEGRAM_BOT_TOKEN": "LEAK_TELEGRAM_BOT",
                    "TELEGRAM_CHAT_ID": "123",
                },
                sleep=lambda _s: None,
                now=datetime(2026, 11, 14, 4, 0, tzinfo=timezone.utc),
            )
        return rc, err.getvalue()

    def _assert_no_leak(self, text):
        for needle in (
            LEAK_MESSAGE, LEAK_INNER, LEAK_HEADER, LEAK_TOKEN, LEAK_SECRET,
            "LEAK_TELEGRAM_BOT", "refresh-token", "error.message",
            "Insufficient Permission",
        ):
            self.assertNotIn(needle, text)

    def _assert_rows(self, rc):
        self.assertEqual(rc, 1)
        self.assertEqual(len(self.sent), 1)
        with open(self.votes, encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        missing = [r for r in rows if r["estado"] == "MISSING"]
        self.assertEqual(len(missing), 1 + 12 * 8)
        self.assertTrue(all(r["voto"] == "NA" for r in missing))

    def test_403_logs_reason_and_status_only(self):
        body = _gmail_body("insufficientPermissions", "PERMISSION_DENIED")
        http = self._http(403, body)
        rc, err = self._run(http)
        self._assert_rows(rc)
        expected = ("Gmail failure (auth) on attempt final. Session 2026-11-13. "
                    "HTTP 403 gmail_reason=insufficientPermissions gmail_status=PERMISSION_DENIED")
        self.assertEqual(err.strip(), expected)
        self.assertIn("http_status=403", self.sent[0])
        self.assertIn(
            "HTTP 403 gmail_reason=insufficientPermissions gmail_status=PERMISSION_DENIED",
            self.sent[0])
        self.assertNotIn("oauth_error", err)
        self.assertNotIn("oauth_error", self.sent[0])
        self._assert_no_leak(err)
        self._assert_no_leak(self.sent[0])
        with self.assertRaises(V.GmailError) as caught:
            V.fetch_messages("cid", "sec", "ref", http, sleep=lambda _s: None)
        self.assertEqual(caught.exception.kind, "auth")
        self.assertEqual(caught.exception.status, 403)
        self.assertIsNone(caught.exception.oauth_error)
        self.assertEqual(caught.exception.gmail_reason, "insufficientPermissions")
        self.assertEqual(caught.exception.gmail_status, "PERMISSION_DENIED")
        self._assert_no_leak(str(caught.exception))

    def test_value_failing_the_whitelist_is_unknown(self):
        body = _gmail_body(BAD_REASON, BAD_STATUS)
        http = self._http(403, body)
        rc, err = self._run(http)
        self._assert_rows(rc)
        expected = ("Gmail failure (auth) on attempt final. Session 2026-11-13. "
                    "HTTP 403 gmail_reason=unknown gmail_status=unknown")
        self.assertEqual(err.strip(), expected)
        self.assertIn("http_status=403", self.sent[0])
        self.assertIn("HTTP 403 gmail_reason=unknown gmail_status=unknown", self.sent[0])
        self._assert_no_leak(err)
        self._assert_no_leak(self.sent[0])
        self.assertNotIn(BAD_REASON, err)
        self.assertNotIn(BAD_STATUS, err)
        self.assertNotIn(BAD_REASON, self.sent[0])
        self.assertNotIn(BAD_STATUS, self.sent[0])
        self.assertNotIn("THIS_VALUE_IS_WAY_TOO_LONG", err)
        self.assertNotIn("THIS_VALUE_IS_WAY_TOO_LONG", self.sent[0])
        with self.assertRaises(V.GmailError) as caught:
            V.fetch_messages("cid", "sec", "ref", http, sleep=lambda _s: None)
        self.assertEqual(caught.exception.gmail_reason, "unknown")
        self.assertEqual(caught.exception.gmail_status, "unknown")
        self._assert_no_leak(str(caught.exception))
        self.assertNotIn(BAD_REASON, str(caught.exception))
        self.assertNotIn(BAD_STATUS, str(caught.exception))

    def test_missing_fields_are_none(self):
        body = _gmail_body(include_reason=False, include_status=False)
        http = self._http(403, body)
        rc, err = self._run(http)
        self._assert_rows(rc)
        expected = ("Gmail failure (auth) on attempt final. Session 2026-11-13. "
                    "HTTP 403 gmail_reason=none gmail_status=none")
        self.assertEqual(err.strip(), expected)
        self.assertIn("http_status=403", self.sent[0])
        self.assertIn("HTTP 403 gmail_reason=none gmail_status=none", self.sent[0])
        self._assert_no_leak(err)
        self._assert_no_leak(self.sent[0])
        with self.assertRaises(V.GmailError) as caught:
            V.fetch_messages("cid", "sec", "ref", http, sleep=lambda _s: None)
        self.assertEqual(caught.exception.gmail_reason, "none")
        self.assertEqual(caught.exception.gmail_status, "none")
        self._assert_no_leak(str(caught.exception))

    def test_non_json_body_is_none_and_is_not_logged(self):
        raw = b"<html>LEAK_RAW_GMAIL_BODY error.message=super-secret-phrase not-json"
        http = self._http(403, raw, headers={"X-Debug": LEAK_HEADER, "Set-Cookie": "LEAK_COOKIE"})
        rc, err = self._run(http)
        self._assert_rows(rc)
        expected = ("Gmail failure (auth) on attempt final. Session 2026-11-13. "
                    "HTTP 403 gmail_reason=none gmail_status=none")
        self.assertEqual(err.strip(), expected)
        self.assertIn("http_status=403", self.sent[0])
        self.assertIn("HTTP 403 gmail_reason=none gmail_status=none", self.sent[0])
        for text in (err, self.sent[0]):
            self._assert_no_leak(text)
            self.assertNotIn("LEAK_RAW_GMAIL_BODY", text)
            self.assertNotIn("super-secret-phrase", text)
            self.assertNotIn("not-json", text)
            self.assertNotIn("<html>", text)
            self.assertNotIn("LEAK_COOKIE", text)
        with self.assertRaises(V.GmailError) as caught:
            V.fetch_messages("cid", "sec", "ref", http, sleep=lambda _s: None)
        self.assertEqual(caught.exception.gmail_reason, "none")
        self.assertEqual(caught.exception.gmail_status, "none")
        self.assertNotIn("LEAK_RAW_GMAIL_BODY", str(caught.exception))
        self.assertNotIn("<html>", str(caught.exception))

    def test_503_log_line_and_telegram_stay_unchanged(self):
        leak = "LEAK_503_BODY_not_for_the_log"
        body = json.dumps({
            "error": {
                "errors": [{
                    "domain": "global",
                    "reason": "backendError",
                    "message": leak,
                }],
                "code": 503,
                "message": leak,
                "status": "UNAVAILABLE",
            },
        }).encode("utf-8")
        http = self._http(503, body, headers={"X-Debug": LEAK_HEADER})
        rc, err = self._run(http)
        self._assert_rows(rc)
        self.assertEqual(
            err.strip(),
            "Gmail failure (api) on attempt final. Session 2026-11-13.",
        )
        self.assertNotIn("gmail_reason", err)
        self.assertNotIn("gmail_status", err)
        self.assertNotIn("oauth_error", err)
        self.assertIn("http_status=503", self.sent[0])
        self.assertNotIn("gmail_reason", self.sent[0])
        self.assertNotIn("gmail_status", self.sent[0])
        self.assertNotIn("oauth_error", self.sent[0])
        self.assertNotIn("HTTP 503", err)
        self.assertNotIn("HTTP 503", self.sent[0])
        self._assert_no_leak(err)
        self._assert_no_leak(self.sent[0])
        self.assertNotIn(leak, err)
        self.assertNotIn(leak, self.sent[0])
        self.assertNotIn("backendError", err)
        self.assertNotIn("UNAVAILABLE", err)
        self.assertNotIn("backendError", self.sent[0])
        self.assertNotIn("UNAVAILABLE", self.sent[0])
        with self.assertRaises(V.GmailError) as caught:
            V.fetch_messages("cid", "sec", "ref", http, sleep=lambda _s: None)
        self.assertEqual(caught.exception.kind, "api")
        self.assertEqual(caught.exception.status, 503)
        self.assertIsNone(caught.exception.gmail_reason)
        self.assertIsNone(caught.exception.gmail_status)
        self.assertNotIn(leak, str(caught.exception))
        self.assertNotIn("backendError", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

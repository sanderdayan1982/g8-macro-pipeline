"""ORDEN_ENZO_REVISION-TRIPLE-01 bloque 2 (14-nov-2026 19:16 WAT).

Final pass at 04:20 UTC lists every Gmail page before MISSING.
A CME/New York full close writes no rows and is not an incomplete session.
workflow_dispatch attempt defaults to first.
"""
import base64
import contextlib
import csv
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.parse
from datetime import date, datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import votes_ingest as V  # noqa: E402

ENV = {
    "GMAIL_OAUTH_CLIENT_ID": "client-id.apps.googleusercontent.com",
    "GMAIL_OAUTH_CLIENT_SECRET": "LEAK_CLIENT_SECRET",
    "GMAIL_READONLY_REFRESH_TOKEN": "refresh-token",
}
WF = os.path.join(ROOT, ".github", "workflows", "votes_ingest.yml")
SCHED = os.path.join(ROOT, "mac", "dispatch_schedule.json")


def _b64(text):
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _risk_body(session):
    bar = V.expected_bar_close_ms(session)
    return "G8|MODULE=RISK_G8|SCRIPT_VERSION=1.2.1|REGIME=RISK-OFF|SCORE=1.1|BAR_CLOSE_MS=%s|SENT_MS=1000|SNAPSHOT_ID=RISK_G8:inst:%s" % (bar, bar)


def _gmail_doc(mid, body):
    return json.dumps({
        "id": mid,
        "internalDate": "1794607300000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [{"name": "Subject", "value": "Alert: RISK_G8"}],
            "body": {"data": _b64(body) if body else ""},
        },
    }).encode("utf-8")


class Schedule(unittest.TestCase):
    def test_final_cron_is_0420_utc_and_dispatch_defaults_to_first(self):
        with open(WF, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("cron: '0 3 * * 2-6'", text)
        self.assertIn("cron: '20 4 * * 2-6'", text)
        self.assertNotIn("cron: '0 4 * * 2-6'", text)
        self.assertIn("default: first", text)
        self.assertNotIn("default: final", text)
        self.assertIn('"0 3 * * 2-6") ATTEMPT=first', text)
        self.assertIn('"20 4 * * 2-6") ATTEMPT=final', text)
        with open(SCHED, encoding="utf-8") as fh:
            sched = json.load(fh)
        by_id = {slot["id"]: slot for slot in sched["slots"]}
        self.assertEqual(by_id["votes_first"]["cron"], "0 3 * * 2-6")
        self.assertEqual(by_id["votes_first"]["inputs"]["attempt"], "first")
        self.assertEqual(by_id["votes_final"]["cron"], "20 4 * * 2-6")
        self.assertEqual(by_id["votes_final"]["inputs"]["attempt"], "final")


class FullClose(unittest.TestCase):
    """Rule, not a stored list. Dates checked against the NYSE 2026–2028 calendar."""

    def test_named_closes_and_days_that_still_have_a_candle(self):
        closed = (
            date(2026, 11, 26),  # Thanksgiving
            date(2026, 12, 25),  # Christmas
            date(2026, 7, 3),    # Independence Day observed (4 Jul is Saturday)
            date(2027, 7, 5),    # Independence Day observed (4 Jul is Sunday)
            date(2026, 4, 3),    # Good Friday
            date(2027, 3, 26),   # Good Friday
            date(2028, 4, 14),   # Good Friday
            date(2027, 12, 24),  # Christmas observed
            date(2027, 6, 18),   # Juneteenth observed
            date(2026, 1, 1),    # New Year's Day
        )
        open_days = (
            date(2026, 11, 27),  # day after Thanksgiving, early close, candle exists
            date(2026, 12, 24),  # Christmas Eve, early close
            date(2026, 11, 11),  # Veterans Day, market open
            date(2026, 10, 12),  # Columbus Day, market open
            date(2026, 11, 13),  # ordinary Friday
            date(2027, 12, 31),  # NYSE does not observe New Year's Day 2028 (Saturday)
        )
        for day in closed:
            self.assertTrue(V.is_ny_full_close(day), day.isoformat())
            self.assertLess(day.weekday(), 5, day.isoformat())
        for day in open_days:
            self.assertFalse(V.is_ny_full_close(day), day.isoformat())

    def test_holiday_writes_no_rows_and_leaves_files_byte_for_byte(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        votes = os.path.join(tmp, "votes.csv")
        weekly = os.path.join(tmp, "votes_weekly.json")
        src = os.path.join(tmp, "in.json")
        sentinel_votes = b"fecha_cierre,script,ccy\nKEEP,ROW,USD\n"
        sentinel_weekly = '{"sentinel":true}\n'
        with open(votes, "wb") as fh:
            fh.write(sentinel_votes)
        with open(weekly, "w", encoding="utf-8") as fh:
            fh.write(sentinel_weekly)
        with open(src, "w", encoding="utf-8") as fh:
            json.dump([{"id": "x", "date": "2026-11-27T01:00:00Z", "subject": "Alert: RISK_G8", "body": "G8|MODULE=RISK_G8"}], fh)

        def boom(*_a, **_k):
            raise AssertionError("holiday must not touch Gmail or Telegram")

        out = io.StringIO()
        for session in ("2026-11-26", "2026-12-25", "2026-07-03", "2026-04-03"):
            with contextlib.redirect_stdout(out):
                rc = V.run(
                    ["--session", session, "--attempt", "final", "--from-json", src,
                     "--votes", votes, "--weekly", weekly],
                    http=boom, sender=boom,
                )
            self.assertEqual(rc, 0, session)
            self.assertIn("holiday session %s: no rows" % session, out.getvalue())
        with open(votes, "rb") as fh:
            self.assertEqual(fh.read(), sentinel_votes)
        with open(weekly, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), sentinel_weekly)

    def test_weekly_does_not_count_a_full_close_as_incomplete(self):
        holiday = "2026-11-26"
        session = "2026-11-27"
        missing = (
            V.missing_rows(date.fromisoformat(holiday))
            + V.missing_rows(date.fromisoformat(session))
        )
        report = V.weekly_report(missing, "2026-11-28T04:20:00Z")
        self.assertNotIn(holiday, report["incomplete_sessions"])
        self.assertIn(session, report["incomplete_sessions"])
        self.assertEqual(report["complete_days_total"], 0)
        self.assertEqual(report["weeks"], [])
        text = json.dumps(report)
        self.assertNotIn(holiday, text)


class MailboxPages(unittest.TestCase):
    """The final pass reads every page before it writes MISSING."""

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
        self.session = "2026-11-13"
        self.calls = []

    def _http(self):
        risk = _risk_body(self.session)
        pages = {
            None: {"messages": [{"id": "noise"}], "nextPageToken": "page-2"},
            "page-2": {"messages": [{"id": "also-noise"}], "nextPageToken": "page-3"},
            "page-3": {"messages": [{"id": "risk-late"}]},
        }

        def http(method, url, headers, body):
            self.calls.append(url)
            if url == V.TOKEN_URL:
                return 200, {}, b'{"access_token":"access"}'
            if url.startswith(V.LIST_URL + "?"):
                token = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("pageToken", [None])[0]
                return 200, {}, json.dumps(pages[token]).encode("utf-8")
            if url.startswith(V.LIST_URL + "/noise"):
                return 200, {}, _gmail_doc("noise", "")
            if url.startswith(V.LIST_URL + "/also-noise"):
                return 200, {}, _gmail_doc("also-noise", "")
            if url.startswith(V.LIST_URL + "/risk-late"):
                return 200, {}, _gmail_doc("risk-late", risk)
            raise AssertionError(url)

        return http

    def _run(self, attempt):
        err = io.StringIO()
        out = io.StringIO()
        seen = {}
        real = V.append_rows

        def append(path, rows):
            seen["logged_before_write"] = "gmail window pages=3" in err.getvalue()
            seen["gets_before_write"] = any(url.startswith(V.LIST_URL + "/risk-late") for url in self.calls)
            return real(path, rows)

        V.append_rows = append
        try:
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
                rc = V.run(
                    ["--attempt", attempt, "--session", self.session,
                     "--votes", self.votes, "--weekly", self.weekly, "--factor", self.factor],
                    http=self._http(), sender=lambda _t: (_ for _ in ()).throw(AssertionError("telegram")),
                    env=ENV, sleep=lambda _s: None,
                    now=datetime(2026, 11, 14, 4, 20, tzinfo=timezone.utc),
                )
        finally:
            V.append_rows = real
        return rc, err.getvalue(), seen

    def _rows(self):
        with open(self.votes, encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))

    def test_final_lists_every_page_before_missing(self):
        rc, err, seen = self._run("final")
        self.assertEqual(rc, 0, err)
        listed = [url for url in self.calls if url.startswith(V.LIST_URL + "?")]
        gets = [url for url in self.calls if url.startswith(V.LIST_URL + "/")]
        self.assertEqual(len(listed), 3)
        self.assertLess(self.calls.index(listed[-1]), self.calls.index(gets[0]))
        self.assertTrue(any("pageToken=page-3" in url for url in listed))
        query = urllib.parse.parse_qs(urllib.parse.urlparse(listed[0]).query)["q"][0]
        self.assertIn("after:", query)
        self.assertIn("before:", query)
        self.assertIn("gmail window pages=3 ids=3", err)
        self.assertTrue(seen["logged_before_write"])
        self.assertTrue(seen["gets_before_write"])
        rows = self._rows()
        risk = [r for r in rows if r["script"] == "RISK_G8"]
        self.assertEqual(len(risk), 1)
        self.assertEqual(risk[0]["estado"], "RISK-OFF")
        self.assertNotEqual(risk[0]["estado"], "MISSING")
        self.assertTrue(any(r["estado"] == "MISSING" and r["script"] == "PSI_G8" for r in rows))

    def test_first_pass_still_reads_every_page_and_writes_no_missing(self):
        rc, err, _seen = self._run("first")
        self.assertEqual(rc, 0, err)
        listed = [url for url in self.calls if url.startswith(V.LIST_URL + "?")]
        self.assertEqual(len(listed), 3)
        self.assertNotIn("gmail window pages=", err)
        rows = self._rows()
        self.assertTrue(any(r["script"] == "RISK_G8" and r["estado"] == "RISK-OFF" for r in rows))
        self.assertFalse(any(r["estado"] == "MISSING" for r in rows))


if __name__ == "__main__":
    unittest.main()

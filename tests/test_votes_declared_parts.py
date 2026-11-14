"""PARTS is the count declared by the mail, not the 12-nov catalog.

Orden de Sander 14-nov-2026 (FFVA/TOT 13-nov). The catalog count stays the
reference used by MISSING rows. New file only: no existing test is edited.
"""
import contextlib
import io
import json
import os
import sys
import unittest
from datetime import date

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import votes_ingest as V  # noqa: E402

SESSION = date(2026, 11, 12)
BAR = V.expected_bar_close_ms(SESSION)
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "votes_1311_ffva_tot.json")


def mail(name, part, parts, fields=None, sent=1000, bar=None, snap=None, gmail_id=None, msgid=None):
    spec = V.MODULE[name]
    bar = BAR if bar is None else bar
    snap = snap or "%s:inst:FX:EURUSD:%s:%s" % (name, bar, sent)
    bits = [
        "G8", "SCHEMA=1", "MODULE=%s" % name,
        "SCRIPT_VERSION=%s" % spec["version"],
        "TRANSPORT=G8_PARTS_V1", "TRANSPORT_REV=1.3",
        "INSTANCE=i", "HOST=FX:EURUSD", "TF=1D",
        "BAR_CLOSE_MS=%s" % bar, "SENT_MS=%s" % sent,
        "SNAPSHOT_ID=%s" % snap,
        "MESSAGE_ID=%s" % (msgid or "%s:P%s" % (snap, part)),
        "PART=%s" % part, "PARTS=%s" % parts,
        "BATCH_STATUS=DATA",
    ]
    for key, val in (fields or {}).items():
        bits.append("%s=%s" % (key, val))
    return {
        "id": gmail_id or "%s-p%s-%s" % (name, part, sent),
        "date": "2026-11-13T01:10:00Z",
        "subject": "Alert: synthetic",
        "body": "|".join(bits),
    }


def ffva_fields():
    out = {}
    for ccy in V.CCYS:
        out["%s_REGIME" % ccy] = "DIST"
        out["%s_STATUS" % ccy] = "FRESH"
        out["%s_ROLL" % ccy] = "NO"
    return out


def complete(name, n, fields=None):
    """Parts 1..n, every one declaring PARTS=n. Fields ride on part 1."""
    msgs = []
    for part in range(1, n + 1):
        msgs.append(mail(name, part, n, fields if part == 1 else None))
    return msgs


def run(messages, write_missing):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rows = V.rows_from_messages(messages, SESSION, write_missing)
    return rows, err.getvalue()


class DeclaredParts(unittest.TestCase):
    def setUp(self):
        self.assertEqual(V.MODULE["FFVA_G8"]["parts"], 5)
        self.assertEqual(V.MODULE["TOT_G8"]["parts"], 5)

    def test_reference_five_complete_with_parts_six_is_normal(self):
        rows, err = run(complete("FFVA_G8", 6, ffva_fields()), False)
        ffva = [r for r in rows if r["script"] == "FFVA_G8"]
        self.assertEqual(len(ffva), 8)
        self.assertTrue(all(r["estado"] == "DIST" and r["voto"] == "-1" for r in ffva))
        self.assertTrue(all(r["parts_expected"] == "6" and r["parts_received"] == "6" for r in ffva))
        self.assertNotIn("INGEST_INCOMPLETE", {r["estado"] for r in ffva})
        self.assertEqual(err, "")

    def test_reference_five_complete_with_parts_four_is_normal(self):
        rows, err = run(complete("FFVA_G8", 4, ffva_fields()), False)
        ffva = [r for r in rows if r["script"] == "FFVA_G8"]
        self.assertEqual(len(ffva), 8)
        self.assertTrue(all(r["estado"] == "DIST" and r["voto"] == "-1" for r in ffva))
        self.assertTrue(all(r["parts_expected"] == "4" and r["parts_received"] == "4" for r in ffva))
        self.assertEqual(err, "")

    def test_parts_six_with_part_six_missing(self):
        msgs = [m for m in complete("FFVA_G8", 6, ffva_fields()) if "|PART=6|" not in m["body"]]
        self.assertEqual(len(msgs), 5)
        first, err = run(msgs, False)
        self.assertEqual([r for r in first if r["script"] == "FFVA_G8"], [])
        self.assertEqual(
            err.strip(),
            "skip FFVA_G8 incomplete declared=6 missing=[6] extra=[] reason=missing",
        )
        self.assertNotIn("G8|", err)
        self.assertNotIn("DIST", err)
        final, err_final = run(msgs, True)
        ffva = [r for r in final if r["script"] == "FFVA_G8"]
        self.assertEqual(len(ffva), 8)
        self.assertTrue(all(
            r["estado"] == "INGEST_INCOMPLETE" and r["voto"] == "NA"
            and r["parts_expected"] == "6" and r["parts_received"] == "5"
            for r in ffva
        ))
        self.assertEqual(err_final, "")

    def test_parts_that_disagree_are_incomplete(self):
        msgs = complete("FFVA_G8", 6, ffva_fields())
        msgs[-1] = mail("FFVA_G8", 6, 4, None)
        first, err = run(msgs, False)
        self.assertEqual([r for r in first if r["script"] == "FFVA_G8"], [])
        self.assertEqual(
            err.strip(),
            "skip FFVA_G8 incomplete declared=none missing=[] extra=[] reason=parts_disagree",
        )
        self.assertNotIn("BODY", err)
        self.assertNotIn(msgs[0]["body"], err)
        final, _err = run(msgs, True)
        ffva = [r for r in final if r["script"] == "FFVA_G8"]
        self.assertEqual(len(ffva), 8)
        self.assertTrue(all(r["estado"] == "INGEST_INCOMPLETE" and r["voto"] == "NA" for r in ffva))
        self.assertTrue(all(r["parts_expected"] == "" for r in ffva))

    def test_parts_expected_is_the_declared_n(self):
        six, _err = run(complete("TOT_G8", 6, {"TOT_EUR_REGIME": "NEUTRAL", "TOT_EUR_STATUS": "FRESH"}), False)
        self.assertTrue(all(r["parts_expected"] == "6" for r in six if r["script"] == "TOT_G8"))
        four, _err = run(complete("TOT_G8", 4, {"TOT_EUR_REGIME": "NEUTRAL", "TOT_EUR_STATUS": "FRESH"}), False)
        self.assertTrue(all(r["parts_expected"] == "4" for r in four if r["script"] == "TOT_G8"))
        missing = [m for m in complete("TOT_G8", 4) if "|PART=4|" not in m["body"]]
        final, _err = run(missing, True)
        incomplete = [r for r in final if r["script"] == "TOT_G8"]
        self.assertTrue(incomplete)
        self.assertTrue(all(r["parts_expected"] == "4" and r["estado"] == "INGEST_INCOMPLETE" for r in incomplete))

    def test_missing_rows_keep_the_reference_count(self):
        rows = V.missing_rows(SESSION, names=["FFVA_G8", "TOT_G8"])
        self.assertTrue(rows)
        self.assertTrue(all(r["parts_expected"] == "5" and r["estado"] == "MISSING" for r in rows))

    def test_synthetic_ffva_and_tot_fixture(self):
        with open(FIXTURE, encoding="utf-8") as fh:
            raw = fh.read()
        messages = json.loads(raw)
        self.assertEqual(len(messages), 10)
        self.assertTrue(all(m["id"].startswith("syn-") for m in messages))
        self.assertTrue(all(m["subject"] == "Alert: synthetic" for m in messages))
        self.assertNotIn("1a1d", raw)
        self.assertNotIn("Institutional", raw)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rows = V.rows_from_messages(messages, date(2026, 11, 13), False)
        self.assertEqual(err.getvalue(), "")
        ffva = [r for r in rows if r["script"] == "FFVA_G8"]
        tot = [r for r in rows if r["script"] == "TOT_G8"]
        self.assertEqual(len(ffva), 8)
        self.assertEqual(len(tot), 8)
        self.assertEqual({r["fecha_cierre"] for r in ffva + tot}, {"2026-11-13"})
        self.assertTrue(all(r["parts_expected"] == "6" and r["parts_received"] == "6" for r in ffva))
        self.assertTrue(all(r["parts_expected"] == "4" and r["parts_received"] == "4" for r in tot))
        self.assertTrue(all(r["estado"] == "DIST" and r["voto"] == "-1" for r in ffva))
        self.assertTrue(all(r["voto"] == "NA" and r["estado"] == "NEUTRAL" for r in tot))
        self.assertEqual({r["ccy"] for r in tot}, set(V.CCYS))

    def test_bad_parts_values_are_incomplete(self):
        cases = ("0", "-1", "nope", "65", "100", "６")
        for token in cases:
            msg = mail("FFVA_G8", 1, token, ffva_fields())
            rows, err = run([msg], False)
            self.assertEqual([r for r in rows if r["script"] == "FFVA_G8"], [], token)
            self.assertIn("reason=bad_parts", err, token)
            self.assertNotIn(token, err) if token == "nope" or token == "６" else None
            self.assertNotIn("DIST", err)

    def test_part_out_of_range_is_incomplete(self):
        msgs = complete("FFVA_G8", 6, ffva_fields())
        msgs.append(mail("FFVA_G8", 7, 6, None, sent=1000))
        # Same snapshot as complete(): sent=1000 builds the same SNAPSHOT_ID.
        rows, err = run(msgs, False)
        self.assertEqual([r for r in rows if r["script"] == "FFVA_G8"], [])
        self.assertIn("extra=[7]", err)
        self.assertIn("reason=extra", err)
        self.assertNotIn("DIST", err)

    def test_identical_part_copies_count_once(self):
        msgs = complete("FFVA_G8", 6, ffva_fields())
        copy = dict(msgs[0])
        copy["id"] = "syn-ffva-copy"
        rows, err = run(msgs + [copy], False)
        ffva = [r for r in rows if r["script"] == "FFVA_G8"]
        self.assertEqual(len(ffva), 8)
        self.assertEqual(err, "")
        self.assertTrue(all(r["voto"] == "-1" for r in ffva))

    def test_differing_part_copies_are_incomplete_before_dedup(self):
        snap = "FFVA_G8:inst:FX:EURUSD:%s:1000" % BAR
        msgs = []
        for part in range(1, 7):
            fields = ffva_fields() if part == 1 else None
            msgs.append(mail("FFVA_G8", part, 6, fields, sent=1000, snap=snap))
        late_fields = ffva_fields()
        late_fields["EUR_REGIME"] = "ACCUM"
        late = mail(
            "FFVA_G8", 1, 6, late_fields, sent=9000, snap=snap,
            gmail_id="late-copy", msgid=snap + ":ALT",
        )
        rows, err = run(msgs + [late], False)
        self.assertEqual([r for r in rows if r["script"] == "FFVA_G8"], [])
        self.assertIn("reason=dup_conflict", err)
        self.assertNotIn("ACCUM", err)
        self.assertNotIn("DIST", err)

    def test_complete_snapshot_outranks_a_later_incomplete_one(self):
        early = complete("FFVA_G8", 6, ffva_fields())
        for msg in early:
            msg["body"] = msg["body"].replace("SENT_MS=1000", "SENT_MS=10")
            msg["body"] = msg["body"].replace(":1000", ":10")
        late = mail("FFVA_G8", 1, 6, {"EUR_REGIME": "ACCUM", "EUR_STATUS": "FRESH", "EUR_ROLL": "NO"}, sent=99)
        rows, err = run(early + [late], False)
        ffva = [r for r in rows if r["script"] == "FFVA_G8"]
        self.assertEqual(len(ffva), 8, err)
        self.assertTrue(all(r["estado"] == "DIST" and r["voto"] == "-1" for r in ffva))


if __name__ == "__main__":
    unittest.main()

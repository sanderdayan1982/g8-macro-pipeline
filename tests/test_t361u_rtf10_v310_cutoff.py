"""orden Sander 24-nov t361u: un correo RTF10 3.10.x con cierre D1
posterior al 23-nov se rechaza y no vota. El v3.10 con cierre <= 23-nov
sigue leyéndose como histórico. No se modifican tests existentes.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import votes_ingest as V  # noqa: E402

CUTOFF = date(2026, 11, 23)
AFTER = date(2026, 11, 24)
CCYS = list(V.CCYS)


def _v3_mails(session, script_version="3.10.0", alert_version="3"):
    bar = V.expected_bar_close_ms(session)
    snap = "RTF10_G8:inst:FX:EURUSD:%s:1000" % bar
    fields = {}
    for ccy in CCYS:
        fields["%s_DRIVER" % ccy] = "TP_SAFE"
        fields["%s_STATUS" % ccy] = "FRESH"
    keys = list(fields.items())
    n = 5
    out = []
    for i in range(1, n + 1):
        chunk = dict(keys[i - 1::n])
        bits = [
            "G8", "SCHEMA=1", "MODULE=RTF10_G8",
            "SCRIPT_VERSION=%s" % script_version,
            "TRANSPORT=G8_PARTS_V1", "TRANSPORT_REV=1.3",
            "PART=%s" % i, "PARTS=%s" % n,
            "MESSAGE_ID=%s:P%s" % (snap, i),
            "INSTANCE=i", "HOST=FX:EURUSD", "TF=1D",
            "BAR_CLOSE_MS=%s" % bar, "SENT_MS=1000",
            "SNAPSHOT_ID=%s" % snap, "BATCH_STATUS=DATA",
        ]
        if alert_version is not None:
            bits.insert(4, "ALERT_VERSION=%s" % alert_version)
        for key, val in chunk.items():
            bits.append("%s=%s" % (key, val))
        out.append({
            "id": "t361u-v3-p%s" % i,
            "date": "2026-11-24T22:05:00Z",
            "subject": "Alert: RTF10_G8",
            "body": "|".join(bits),
        })
    return out


def _index(rows):
    return {(r["script"], r["ccy"]): r for r in rows}


class VersionHelper(unittest.TestCase):
    def test_version_and_cutoff_predicates(self):
        self.assertTrue(V.is_rtf10_v310_version("3.10.0"))
        self.assertTrue(V.is_rtf10_v310_version("3.10.1"))
        self.assertTrue(V.is_rtf10_v310_version("3.10"))
        self.assertFalse(V.is_rtf10_v310_version("3.1"))
        self.assertFalse(V.is_rtf10_v310_version("3.11.0"))
        self.assertFalse(V.is_rtf10_v310_version("4.0.0"))
        self.assertFalse(V.is_rtf10_v310_version("3.100"))
        self.assertTrue(V.rtf10_v310_after_cutoff("3.10.0", AFTER))
        self.assertFalse(V.rtf10_v310_after_cutoff("3.10.0", CUTOFF))
        self.assertFalse(V.rtf10_v310_after_cutoff("3.10.0", date(2026, 11, 21)))
        self.assertFalse(V.rtf10_v310_after_cutoff("4.0.0", AFTER))


class CutoffRejectsAndDoesNotVote(unittest.TestCase):
    def test_v310_after_23nov_is_rejected_without_vote_rows(self):
        rejected = []
        rows = V.rows_from_messages(
            _v3_mails(AFTER), AFTER, False, rejected=rejected,
        )
        self.assertEqual([r for r in rows if r["script"] == "RTF10_G8"], [])
        self.assertEqual(len(rejected), 1)
        rec = rejected[0]
        self.assertEqual(rec["reason"], "RTF10_V310_AFTER_CUTOFF")
        self.assertEqual(rec["fecha_cierre"], "2026-11-24")
        self.assertEqual(rec["script"], "RTF10_G8")
        self.assertEqual(rec["version"], "3.10.0")

    def test_v310_on_23nov_still_reads_as_historical(self):
        rejected = []
        rows = V.rows_from_messages(
            _v3_mails(CUTOFF, alert_version="3"), CUTOFF, False, rejected=rejected,
        )
        self.assertEqual(rejected, [])
        eur = _index(rows)[("RTF10_G8", "EUR")]
        self.assertEqual(eur["estado"], "TP_SAFE")
        self.assertEqual(eur["voto"], "NA")
        self.assertEqual(eur["version"], "3.10.0")
        self.assertEqual(len([r for r in rows if r["script"] == "RTF10_G8"]), 8)

    def test_v310_dot_one_after_cutoff_also_rejected(self):
        rejected = []
        rows = V.rows_from_messages(
            _v3_mails(AFTER, script_version="3.10.1"), AFTER, False,
            rejected=rejected,
        )
        self.assertEqual([r for r in rows if r["script"] == "RTF10_G8"], [])
        self.assertEqual(rejected[0]["version"], "3.10.1")
        self.assertEqual(rejected[0]["reason"], V.RTF10_V310_AFTER_CUTOFF)

    def test_append_does_not_write_vote_rows_and_rejection_is_visible(self):
        tmp = tempfile.mkdtemp(prefix="g8-t361u-")
        votes = os.path.join(tmp, "votes.csv")
        rejected_path = os.path.join(tmp, "_ingest", "votes_rejected.jsonl")
        rejected = []
        rows = V.rows_from_messages(
            _v3_mails(AFTER), AFTER, False, rejected=rejected,
        )
        added = V.append_rows(votes, rows, rejected_path=rejected_path)
        self.assertEqual(added, 0)
        self.assertTrue(all(r["script"] != "RTF10_G8" for r in rows))
        # Persistencia visible del rechazo (mismo camino que run()).
        written = "2026-11-24T22:10:00Z"
        for rec in rejected:
            V.register_rejected(
                rejected_path,
                {"fecha_cierre": rec["fecha_cierre"], "script": rec["script"],
                 "ccy": rec.get("ccy") or ""},
                V.rtf10_v310_cutoff_deadline(),
                written,
                reason=rec["reason"],
            )
        recs = [json.loads(line) for line in Path(rejected_path).read_text(encoding="utf-8").splitlines() if line]
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["reason"], "RTF10_V310_AFTER_CUTOFF")
        self.assertEqual(recs[0]["fecha_cierre"], "2026-11-24")
        self.assertEqual(recs[0]["script"], "RTF10_G8")
        if os.path.exists(votes):
            body = Path(votes).read_text(encoding="utf-8")
            self.assertNotIn("RTF10_G8", body)

    def test_final_in_v4_regime_keeps_stale_and_records_rejection(self):
        # El STALE por falta de v4 no es un voto del correo v3.
        rejected = []
        rows = V.rows_from_messages(
            _v3_mails(AFTER), AFTER, True,
            first_rtf10_v4_session=CUTOFF,
            rejected=rejected,
        )
        self.assertEqual(len(rejected), 1)
        rtf = [r for r in rows if r["script"] == "RTF10_G8"]
        self.assertEqual(len(rtf), 8)
        self.assertTrue(all(r["estado"] == "STALE" and r["voto"] == "NA" for r in rtf))


if __name__ == "__main__":
    unittest.main(verbosity=2)

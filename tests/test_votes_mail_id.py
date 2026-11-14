"""id_correo is sha256(Gmail id)[:16]. The raw id is never stored.

Orden de Sander 14-nov-2026 (t160u). New file only.
Dedup and append-only keys do not use the Gmail id.
"""
import hashlib
import os
import re
import sys
import tempfile
import unittest
from datetime import date

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import votes_ingest as V  # noqa: E402

SESSION = date(2026, 11, 12)
BAR = V.expected_bar_close_ms(SESSION)
ID_RE = re.compile(r"^[0-9a-f]{16}(;[0-9a-f]{16})*$")


def digest(gmail_id):
    return hashlib.sha256(gmail_id.encode("utf-8")).hexdigest()[:16]


def xccy(ids):
    """Two-part XCCY snapshot. ids is one Gmail id per part, in part order."""
    n = 2
    sent = 1000
    snap = "XCCY_G8:inst:FX:EURUSD:%s:%s" % (BAR, sent)
    out = []
    for part, gmail_id in enumerate(ids, start=1):
        bits = [
            "G8", "SCHEMA=1", "MODULE=XCCY_G8", "SCRIPT_VERSION=2.8.1",
            "TRANSPORT=G8_PARTS_V1", "TRANSPORT_REV=1.3",
            "INSTANCE=i", "HOST=FX:EURUSD", "TF=1D",
            "BAR_CLOSE_MS=%s" % BAR, "SENT_MS=%s" % sent,
            "SNAPSHOT_ID=%s" % snap,
            "MESSAGE_ID=%s:P%s" % (snap, part),
            "PART=%s" % part, "PARTS=%s" % n,
            "BATCH_STATUS=DATA",
        ]
        if part == 1:
            bits.append("EUR_REGIME=GAP")
            bits.append("EUR_STATUS=FRESH")
        out.append({
            "id": gmail_id,
            "date": "2026-11-13T01:10:00Z",
            "subject": "Alert: synthetic",
            "body": "|".join(bits),
        })
    return out


class MailIdDigest(unittest.TestCase):
    def test_rows_store_the_hash_and_never_the_raw_id(self):
        raw = ("RAW-GMAIL-AAA", "RAW-GMAIL-BBB")
        rows = [r for r in V.rows_from_messages(xccy(raw), SESSION, False) if r["script"] == "XCCY_G8"]
        self.assertEqual(len(rows), 8)
        expected = ";".join(sorted(digest(item) for item in raw))
        for row in rows:
            self.assertEqual(row["id_correo"], expected)
            self.assertRegex(row["id_correo"], ID_RE)
            blob = "|".join(row.values())
            self.assertNotIn(raw[0], blob)
            self.assertNotIn(raw[1], blob)

    def test_hash_is_deterministic_and_order_independent(self):
        forward = [r for r in V.rows_from_messages(xccy(("id-a", "id-b")), SESSION, False)
                   if r["ccy"] == "EUR"][0]
        backward = [r for r in V.rows_from_messages(xccy(("id-b", "id-a")), SESSION, False)
                    if r["ccy"] == "EUR"][0]
        again = [r for r in V.rows_from_messages(xccy(("id-a", "id-b")), SESSION, False)
                 if r["ccy"] == "EUR"][0]
        self.assertEqual(forward["id_correo"], backward["id_correo"])
        self.assertEqual(forward["id_correo"], again["id_correo"])
        self.assertEqual(forward["id_correo"], ";".join(sorted((digest("id-a"), digest("id-b")))))

    def test_append_key_ignores_the_mail_id(self):
        rows = [r for r in V.rows_from_messages(xccy(("id-a", "id-b")), SESSION, False)
                if r["script"] == "XCCY_G8" and r["ccy"] == "EUR"]
        self.assertEqual(len(rows), 1)
        path = os.path.join(tempfile.mkdtemp(), "votes.csv")
        self.assertEqual(V.append_rows(path, rows), 1)
        other = dict(rows[0])
        other["id_correo"] = digest("different-id")
        self.assertEqual(V.append_rows(path, [other]), 0)
        self.assertEqual(len(V.read_existing_keys(path)), 1)


if __name__ == "__main__":
    unittest.main()

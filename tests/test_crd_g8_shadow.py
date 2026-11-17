"""CRD_G8 sombra. Correo sintético: solo estados. Orden Sander 17-nov t235u, D-7.

No hay cifras. Los campos que el Pine manda aparte del estado no entran en la fila.
"""
import unittest
from datetime import date

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import votes_ingest as V  # noqa: E402

SESSION = date(2026, 11, 12)
BAR = V.expected_bar_close_ms(SESSION)
STATES = {
    "USD": "SANO",
    "EUR": "NEUTRO",
    "GBP": "TENSO",
    "JPY": "ROTURA",
    "AUD": "SANO",
    "NZD": "NA",
    "CAD": "NEUTRO",
    "CHF": "TENSO",
}
HIDDEN = "HIDDEN"


def crd_mail(batch="DATA", part="1", message="P1"):
    bits = [
        "G8",
        "SCHEMA=1",
        "MODULE=CRD_G8",
        "SCRIPT_VERSION=1.0.0",
        "ALERT_VERSION=1",
        "TRANSPORT=G8_PARTS_V1",
        "TRANSPORT_REV=1.3",
        "INSTANCE=SYN",
        "HOST=FX:EURUSD",
        "TF=1D",
        "BAR_CLOSE_MS=%s" % BAR,
        "SENT_MS=1000",
        "EVENT=DAILY_SNAPSHOT",
        "SNAPSHOT_ID=CRD_G8:SYN:%s" % BAR,
        "MESSAGE_ID=CRD_G8:SYN:%s:%s" % (BAR, message),
        "PART=%s" % part,
        "PARTS=1",
        "BATCH_STATUS=%s" % batch,
        "STATUS_MODULE=SHADOW",
        "VOTE=0",
        "CRD_G8_STATE=SANO",
        "CRD_EUR_COVERAGE=%s" % HIDDEN,
        "CRD8=%s" % HIDDEN,
        "ISO8=%s" % HIDDEN,
        "CRD_EUR_ST=%s" % HIDDEN,
        "CRD_USD_BK_STATE=%s" % HIDDEN,
        "CRD_EUR_BK_STATE=%s" % HIDDEN,
    ]
    for ccy, state in STATES.items():
        bits.append("CRD_%s_STATE=%s" % (ccy, state))
        bits.append("CRD_%s_ISOLATED=NO" % ccy)
        bits.append("VOTE_%s=0" % ccy)
    return {
        "id": "crd-syn",
        "date": "2026-11-13T01:00:00Z",
        "subject": "Alert: CRD_G8",
        "body": "|".join(bits),
    }


def crd_rows(messages, write_missing):
    rows = V.rows_from_messages(messages, SESSION, write_missing)
    return [r for r in rows if r["script"] == "CRD_G8"]


class CrdShadow(unittest.TestCase):
    def test_states_vote_zero_and_empty_valor(self):
        rows = crd_rows([crd_mail()], True)
        self.assertEqual(sorted(r["ccy"] for r in rows), sorted(V.CCYS))
        self.assertNotIn("G8", {r["ccy"] for r in rows})
        by_ccy = {r["ccy"]: r for r in rows}
        for ccy, state in STATES.items():
            row = by_ccy[ccy]
            self.assertEqual(row["estado"], state, ccy)
            self.assertEqual(row["valor"], "", ccy)
            self.assertEqual(row["voto"], "0", ccy)
            self.assertEqual(row["stale"], "false", ccy)
            self.assertEqual(row["version"], "1.0.0", ccy)
            self.assertEqual(row["parts_expected"], "1", ccy)
            self.assertEqual(row["parts_received"], "1", ccy)
            self.assertEqual(row["family"], "CRD", ccy)
            self.assertNotIn(HIDDEN, row.values(), ccy)

    def test_error_batch_is_incomplete_only_on_the_final_pass(self):
        mail = crd_mail(batch="ERROR", part="0", message="ERROR")
        self.assertEqual(crd_rows([mail], False), [])
        rows = crd_rows([mail], True)
        self.assertEqual(len(rows), 8)
        self.assertTrue(all(r["estado"] == "INGEST_INCOMPLETE" for r in rows))
        self.assertTrue(all(r["voto"] == "NA" and r["valor"] == "" for r in rows))

    def test_absence_on_the_final_pass_is_missing(self):
        rows = crd_rows([], True)
        self.assertEqual(len(rows), 8)
        self.assertTrue(all(
            r["estado"] == "MISSING" and r["voto"] == "NA" and r["valor"] == ""
            for r in rows
        ))


if __name__ == "__main__":
    unittest.main()

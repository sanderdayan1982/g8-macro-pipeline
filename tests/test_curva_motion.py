"""CURVA_MOTION sale del correo de CURVA_G8. Solo estados. Orden Sander 17-nov t239u.

Sin cifras. report_date queda vacío. valor no se rellena.
"""
import os
import sys
import unittest
from datetime import date

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import votes_ingest as V  # noqa: E402

SESSION = date(2026, 11, 12)
BAR = V.expected_bar_close_ms(SESSION)
MOTIONS = {
    "USD": "BULL_STEEPENING",
    "EUR": "BEAR_STEEPENING",
    "GBP": "BULL_FLATTENING",
    "JPY": "BEAR_FLATTENING",
    "AUD": "QUIET",
    "NZD": "NA",
    "CAD": "QUIET",
    "CHF": "NA",
}


def curva_part(n, fields=None):
    bits = [
        "G8",
        "MODULE=CURVA_G8",
        "SCRIPT_VERSION=1.4.8",
        "TRANSPORT=G8_PARTS_V1",
        "TRANSPORT_REV=1.3",
        "BAR_CLOSE_MS=%s" % BAR,
        "SENT_MS=1000",
        "SNAPSHOT_ID=CURVA_G8:SYN:%s" % BAR,
        "MESSAGE_ID=CURVA_G8:SYN:%s:P%s" % (BAR, n),
        "PART=%s" % n,
        "PARTS=5",
        "BATCH_STATUS=DATA",
    ]
    if n == 1:
        for key, val in (fields or {}).items():
            bits.append("%s=%s" % (key, val))
    return {
        "id": "curva-p%s" % n,
        "date": "2026-11-13T01:00:00Z",
        "subject": "Alert: CURVA_G8",
        "body": "|".join(bits),
    }


def curva_messages():
    fields = {}
    for ccy, motion in MOTIONS.items():
        fields["%s_REGIME" % ccy] = "NORMAL"
        fields["%s_STATUS" % ccy] = "FRESH"
        fields["%s_MOTION" % ccy] = motion
    return [curva_part(n, fields if n == 1 else None) for n in range(1, 6)]


class CurvaMotion(unittest.TestCase):
    def test_motion_rows_keep_regime_rows(self):
        self.assertNotIn("CURVA_MOTION", V.ALLOWED_VALOR_MODULES)
        rows = V.rows_from_messages(curva_messages(), SESSION, True)
        regime = [r for r in rows if r["script"] == "CURVA_G8" and r["ccy"] == "EUR"]
        motion = {r["ccy"]: r for r in rows if r["script"] == "CURVA_MOTION"}
        self.assertEqual(len(regime), 1)
        self.assertEqual(regime[0]["estado"], "NORMAL")
        self.assertEqual(regime[0]["voto"], "NA")
        self.assertEqual(regime[0]["valor"], "")
        self.assertEqual(sorted(motion), sorted(V.CCYS))
        for ccy, label in MOTIONS.items():
            row = motion[ccy]
            self.assertEqual(row["estado"], label, ccy)
            self.assertEqual(row["voto"], "NA", ccy)
            self.assertEqual(row["valor"], "", ccy)
            self.assertEqual(row.get("report_date", ""), "", ccy)
            self.assertEqual(row["family"], "CURVA", ccy)

    def test_absence_is_missing_and_vote_stays_na(self):
        rows = [r for r in V.rows_from_messages([], SESSION, True) if r["script"] == "CURVA_MOTION"]
        self.assertEqual(len(rows), 8)
        self.assertTrue(all(
            r["estado"] == "MISSING" and r["voto"] == "NA" and r["valor"] == ""
            and r.get("report_date", "") == ""
            for r in rows
        ))


if __name__ == "__main__":
    unittest.main()

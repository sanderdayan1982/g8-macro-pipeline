"""rule_version 2: FRESH_PROXY counts as fresh for the stale flag.

Orden expresa de Sander 14-nov-2026 (t158u). New file only.
POS_G8 stays ALWAYS_NA. The estado column is not relabeled.
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


def pos_messages(status):
    """Complete POS_G8 snapshot. Catalog count is 6; every part declares PARTS=6."""
    n = V.MODULE["POS_G8"]["parts"]
    fields = {
        "USD_LF_STATE": "CROWD SHORT",
        "USD_LF_Z": "-2.665",
        "USD_LF_STATUS": status,
    }
    sent = 1000
    snap = "POS_G8:inst:FX:EURUSD:%s:%s" % (BAR, sent)
    out = []
    for part in range(1, n + 1):
        bits = [
            "G8", "SCHEMA=1", "MODULE=POS_G8",
            "SCRIPT_VERSION=%s" % V.MODULE["POS_G8"]["version"],
            "TRANSPORT=G8_PARTS_V1", "TRANSPORT_REV=1.3",
            "INSTANCE=i", "HOST=FX:EURUSD", "TF=1D",
            "BAR_CLOSE_MS=%s" % BAR, "SENT_MS=%s" % sent,
            "SNAPSHOT_ID=%s" % snap,
            "MESSAGE_ID=%s:P%s" % (snap, part),
            "PART=%s" % part, "PARTS=%s" % n,
            "BATCH_STATUS=DATA",
        ]
        if part == 1:
            for key, val in fields.items():
                bits.append("%s=%s" % (key, val))
        out.append({
            "id": "POS_G8-p%s" % part,
            "date": "2026-11-13T01:10:00Z",
            "subject": "Alert: POS_G8",
            "body": "|".join(bits),
        })
    return out


def usd_row(status):
    rows = [r for r in V.rows_from_messages(pos_messages(status), SESSION, False)
            if r["script"] == "POS_G8" and r["ccy"] == "USD"]
    assert len(rows) == 1
    return rows[0]


def module_messages(name, fields):
    n = V.MODULE[name]["parts"]
    sent = 1000
    snap = "%s:inst:FX:EURUSD:%s:%s" % (name, BAR, sent)
    out = []
    for part in range(1, n + 1):
        bits = [
            "G8", "SCHEMA=1", "MODULE=%s" % name,
            "SCRIPT_VERSION=%s" % V.MODULE[name]["version"],
            "TRANSPORT=G8_PARTS_V1", "TRANSPORT_REV=1.3",
            "INSTANCE=i", "HOST=FX:EURUSD", "TF=1D",
            "BAR_CLOSE_MS=%s" % BAR, "SENT_MS=%s" % sent,
            "SNAPSHOT_ID=%s" % snap,
            "MESSAGE_ID=%s:P%s" % (snap, part),
            "PART=%s" % part, "PARTS=%s" % n,
            "BATCH_STATUS=DATA",
        ]
        if part == 1:
            for key, val in fields.items():
                bits.append("%s=%s" % (key, val))
        out.append({
            "id": "%s-p%s" % (name, part),
            "date": "2026-11-13T01:10:00Z",
            "subject": "Alert: synthetic",
            "body": "|".join(bits),
        })
    return out


def one(name, fields, ccy="EUR"):
    rows = [r for r in V.rows_from_messages(module_messages(name, fields), SESSION, False)
            if r["script"] == name and r["ccy"] == ccy]
    assert len(rows) == 1, rows
    return rows[0]


class FreshProxy(unittest.TestCase):
    def test_pos_usd_fresh_proxy_is_not_stale_and_vote_stays_na(self):
        row = usd_row("FRESH_PROXY")
        self.assertEqual(row["stale"], "false")
        self.assertEqual(row["voto"], "NA")
        self.assertEqual(row["rule_version"], "2")
        self.assertEqual(row["estado"], "CROWD SHORT")
        self.assertNotEqual(row["estado"], "FRESH")

    def test_pos_usd_stale_status_stays_stale(self):
        row = usd_row("STALE")
        self.assertEqual(row["stale"], "true")
        self.assertEqual(row["voto"], "NA")
        self.assertEqual(row["estado"], "CROWD SHORT")
        self.assertEqual(row["rule_version"], "2")

    def test_directional_fresh_proxy_keeps_vote_na_and_stale_false(self):
        cases = (
            ("IYDT_2Y", {
                "Y2_EUR_SCORE": "1", "Y2_EUR_SCORE_BAND": "POS_STRONG", "Y2_EUR_STATUS": "FRESH_PROXY",
            }, "+1"),
            ("IYDT_10Y", {
                "Y10_EUR_SCORE": "1", "Y10_EUR_SCORE_BAND": "NEG_STRONG", "Y10_EUR_STATUS": "FRESH_PROXY",
            }, "-1"),
            ("FFVA_G8", {
                "EUR_REGIME": "DIST", "EUR_STATUS": "FRESH_PROXY", "EUR_ROLL": "NO",
            }, "-1"),
            ("POL_G8", {
                "EUR_STATE": "REPRICE", "EUR_NOW_DIRECTION": "HAWKISH", "EUR_REPRICE_COUNTED": "FULL",
                "EUR_CLASS_ELIGIBLE": "YES", "EUR_STATUS": "FRESH_PROXY", "EUR_HAWK_SCORE": "1",
            }, "+1"),
        )
        for name, fields, fresh_vote in cases:
            proxy = one(name, fields)
            self.assertEqual(proxy["stale"], "false", name)
            self.assertEqual(proxy["voto"], "NA", name)
            self.assertEqual(proxy["rule_version"], "2", name)
            fresh_fields = dict(fields)
            for key in list(fresh_fields):
                if key.endswith("_STATUS"):
                    fresh_fields[key] = "FRESH"
            fresh = one(name, fresh_fields)
            self.assertEqual(fresh["voto"], fresh_vote, name)
            self.assertEqual(fresh["stale"], "false", name)


if __name__ == "__main__":
    unittest.main()

"""t186u — cleared volume (stat_type 6) is shadow-only.

Synthetic statistics rows. No network, no Databento call.
The canonical settle/OI CSV stays the pre-t186u schema. Volume lives in a
sibling file. A missing stat_type 6 is empty, never 0, never carried forward.
"""
import gzip
import inspect
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import cme_options_collector as C  # noqa: E402

SESSION = "2026-07-02"
PARENT = "EUU.OPT"
DMAP = {
    "1": {"strike": "1.1", "expiry": "2026-08-07", "iclass": "C", "symbol": "EUUQ6"},
    "2": {"strike": "1.2", "expiry": "2026-08-07", "iclass": "C", "symbol": "EUUQ6"},
}
# Bytes the collector wrote before stat_type 6 existed (csv default CRLF,
# header without volume, empty oi left empty).
LEGACY_CANON = (
    "session,root,type,symbol,expiry,right,strike,settle,settle_flag,oi\r\n"
    "2026-07-02,EUU,OPT,EUUQ6,2026-08-07,C,1.1,0.0123,2,10\r\n"
    "2026-07-02,EUU,OPT,EUUQ6,2026-08-07,C,1.2,0.0200,2,\r\n"
)
CONSUMERS = (
    "scripts/cme_fxvol_shadow.py",
    "scripts/dashboard_alerts.py",
    "scripts/build_options_summary.py",
    "scripts/cme_options_shadow.py",
    "scripts/health_derivatives.py",
    "scripts/cme_options_backfill.py",
    "docs/index.html",
)


def _row(iid, stat_type, price="", quantity="", ts=SESSION + "T19:13:00Z", flags="2"):
    row = {
        "ts_ref": ts,
        "instrument_id": iid,
        "stat_type": stat_type,
        "price": price,
        "stat_flags": flags,
        "symbol": "EUUQ6",
    }
    if quantity is not None:
        row["quantity"] = quantity
    return row


def _base_stats():
    return [
        _row("1", C.STAT_SETTLE, price="0.0123"),
        _row("1", C.STAT_OI, quantity="10"),
        _row("2", C.STAT_SETTLE, price="0.0200"),
        _row("2", C.STAT_OI, quantity=""),
    ]


def _join(stats):
    settles, ois, volumes = C.split_session_stats(stats, SESSION)
    return C.contract_rows(settles, ois, volumes, DMAP, PARENT, SESSION)


class ClearedVolumeShadow(unittest.TestCase):
    def test_stat_type_constant_is_cleared_volume(self):
        self.assertEqual(C.STAT_VOLUME, "6")

    def test_volume_parsed_from_stat_type_6(self):
        stats = _base_stats() + [
            _row("1", "6", quantity="4321"),
            _row("1", C.STAT_VOLUME, quantity="15"),  # last row for the instrument wins
            _row("9", C.STAT_VOLUME, quantity="999"),  # no settlement: not a contract row
        ]
        recs, vols, unknown = _join(stats)
        self.assertEqual(unknown, 0)
        by_strike = {r["strike"]: r for r in vols}
        self.assertEqual(by_strike["1.1"]["volume"], "15")
        self.assertEqual(by_strike["1.2"]["volume"], "")
        self.assertEqual([r["settle"] for r in recs], ["0.0123", "0.0200"])
        self.assertEqual([r["oi"] for r in recs], ["10", ""])
        self.assertNotIn("volume", recs[0])
        self.assertEqual(list(recs[0].keys()), C.CANON_HEADER)

    def test_missing_stat_type_6_is_empty_not_zero_and_not_carried_forward(self):
        stats = _base_stats() + [
            _row("1", C.STAT_VOLUME, quantity="99", ts="2026-07-01T19:13:00Z"),
            _row("2", C.STAT_VOLUME, quantity=""),
            _row("2", C.STAT_VOLUME, quantity="0"),
            _row("2", C.STAT_VOLUME, quantity="   "),
        ]
        # instrument 2's last stat_type 6 is blank → unavailable.
        # instrument 1 has volume only on the previous session.
        recs, vols, _unknown = _join(stats)
        by_strike = {r["strike"]: r for r in vols}
        self.assertEqual(by_strike["1.1"]["volume"], "")
        self.assertEqual(by_strike["1.2"]["volume"], "")
        self.assertNotEqual(by_strike["1.1"]["volume"], "0")
        self.assertNotEqual(by_strike["1.2"]["volume"], "0")
        self.assertEqual(recs[0]["oi"], "10")
        bare = _row("1", C.STAT_VOLUME)
        del bare["quantity"]
        _recs, vols_bare, _u = _join(_base_stats() + [bare])
        self.assertEqual(vols_bare[0]["volume"], "")
        zero = _base_stats() + [_row("1", C.STAT_VOLUME, quantity="0")]
        _recs, vols_zero, _u = _join(zero)
        self.assertEqual({r["strike"]: r["volume"] for r in vols_zero}["1.1"], "0")

    def test_settle_oi_bytes_match_the_pre_volume_writer(self):
        without = _base_stats()
        with_vol = without + [_row("1", C.STAT_VOLUME, quantity="4321")]
        recs_a, vols_a, _u = _join(without)
        recs_b, vols_b, _u = _join(with_vol)
        self.assertEqual(recs_a, recs_b)
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp))
        canon = os.path.join(tmp, "canonical", SESSION, "EUU.csv")
        shadow = os.path.join(tmp, "volume_shadow", SESSION, "EUU.csv")
        C.write_csv(canon, C.CANON_HEADER, recs_a)
        with open(canon, "rb") as fh:
            self.assertEqual(fh.read(), LEGACY_CANON.encode("ascii"))
        C.write_csv(canon, C.CANON_HEADER, recs_b)
        with open(canon, "rb") as fh:
            self.assertEqual(fh.read(), LEGACY_CANON.encode("ascii"))
        C.write_csv(shadow, C.VOLUME_HEADER, vols_a, lineterminator="\n")
        with open(shadow, encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("session,root,type,symbol,expiry,right,strike,volume\n"))
        self.assertNotIn(",0\n", text)
        self.assertIn(",1.1,\n", text)
        C.write_csv(shadow, C.VOLUME_HEADER, vols_b, lineterminator="\n")
        with open(shadow, encoding="utf-8") as fh:
            self.assertIn(",1.1,4321\n", fh.read())
        self.assertNotEqual(os.path.dirname(canon), os.path.dirname(shadow))

    def test_volume_helpers_do_not_coerce_with_or_zero(self):
        src = "\n".join(inspect.getsource(fn) for fn in (
            C._volume_quantity, C.shadow_volume, C.split_session_stats, C.contract_rows))
        self.assertNotIn("or 0", src)

    def test_raw_archive_is_gzip_and_holds_no_secret(self):
        secret = "db-not-a-real-key"
        body = b"ts_recv,instrument_id,stat_type,quantity\n2026-07-02,1,6,15\n"
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp))
        path = C.archive_raw_response(
            tmp, "statistics", "EUU.OPT", SESSION, "2026-07-03T05:59:00", body)
        self.assertTrue(path.name.endswith(".csv.gz"))
        self.assertNotIn(":", path.name)
        self.assertNotIn(secret, path.name)
        self.assertNotIn(secret, path.read_bytes().decode("latin1"))
        with gzip.open(path, "rb") as fh:
            self.assertEqual(fh.read(), body)
        self.assertFalse(os.path.exists(str(path) + ".tmp"))
        with self.assertRaises(TypeError):
            C.archive_raw_response(tmp, "statistics", "EUU.OPT", SESSION, "x", "not-bytes")

    def test_workflow_uploads_raw_outside_git_and_consumers_ignore_volume(self):
        with open(os.path.join(ROOT, ".github", "workflows", "cme_options.yml"), encoding="utf-8") as fh:
            wf = fh.read()
        self.assertIn("G8_OPT_VOL_DIR: data/options/volume_shadow", wf)
        self.assertIn("G8_OPT_RAW_DIR: ${{ runner.temp }}/cme-options-raw", wf)
        # orden Sander 18-nov t245u
        self.assertNotIn("actions/upload-artifact", wf)
        self.assertNotIn("cme-options-raw-${{ github.run_id }}", wf)
        commit = wf.split("Commit & push if changed", 1)[1]
        self.assertNotIn("cme-options-raw", commit)
        self.assertNotIn("runner.temp", commit)
        with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as fh:
            ignore = fh.read()
        self.assertIn("data/options/raw/", ignore)
        self.assertIn("*.dbn", ignore)
        self.assertIn("*.dbn.zst", ignore)
        for rel in CONSUMERS:
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn("volume_shadow", text, rel)
            self.assertNotIn("STAT_VOLUME", text, rel)
            self.assertNotIn("G8_OPT_RAW_DIR", text, rel)


if __name__ == "__main__":
    unittest.main()

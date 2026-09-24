"""Pruebas de g8common.series — P6: vacío, truncado, regresión, revisiones, correcciones históricas,
salto implausible (T06: retener → confirmar → aceptar con trazabilidad; decisión manual), coherencia."""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from g8common import series as S  # noqa: E402

T0 = datetime(2026, 9, 24, 7, 0, tzinfo=timezone.utc)
PL = {"min": -1.0, "max": 10.0, "max_jump": 0.35}


def dv(rows, header="Date,Value"):
    return (header + "\n" + "".join("%s,%s\n" % (d, v) for d, v in rows)).encode()


def ohlcv(rows):
    return ("DATE,OPEN,HIGH,LOW,CLOSE,VOLUME\n" +
            "".join("%s,%.4f,%.4f,%.4f,%.4f,0\n" % (d, v, v, v, v) for d, v in rows)).encode()


BASE = [("2026-09-%02d" % d, 2.80 + d / 1000.0) for d in (14, 15, 16, 17, 18, 21, 22)]


class SeriesTests(unittest.TestCase):
    def test_parse_rejects_empty_and_garbage(self):
        for bad in (b"", b"   \n", b"Date,Value\n", b"<html>error</html>", b"Date,Value\n2026-09-22,abc\n",
                    b"Date,Value\n2026-09-22,1\n2026-09-22,2\n", b"Date,Value\n2026-09-22,nan\n"):
            with self.assertRaises(S.SeriesError):
                S.parse(bad)

    def test_superset_download_is_byte_identical(self):
        repo = S.parse(dv(BASE))
        src_bytes = dv(BASE + [("2026-09-23", 2.83)])
        r = S.merge("NZD_CASH_ON.csv", repo, S.parse(src_bytes), PL, None, {}, "run1", T0)
        self.assertEqual(r.status, S.PUBLISH)
        self.assertEqual(r.series.to_bytes(), src_bytes)
        self.assertEqual(r.new, ["20260923"])

    def test_ohlcv_crlf_preserved(self):
        rows = [("202609%02d" % d, 3.8 + d / 100.0) for d in (21, 22)]
        raw = ohlcv(rows).replace(b"\n", b"\r\n")
        s = S.parse(raw)
        self.assertEqual(s.to_bytes(), raw)

    def test_mixed_line_endings_keep_all_rows(self):
        s = S.parse(b"DATE,CLOSE\r\n20260917,1\r\n20260918,2\n")
        self.assertEqual(s.dates, ["20260917", "20260918"])

    def test_truncated_response_invalid(self):
        repo = S.parse(dv(BASE))
        src = S.parse(dv([BASE[0], BASE[-1]] + [("2026-09-23", 2.83)]))    # hueco en medio
        r = S.merge("f.csv", repo, src, PL, None, {}, "run1", T0)
        self.assertEqual(r.status, S.INVALID)
        self.assertIsNone(r.series)

    def test_short_recent_window_keeps_history(self):
        repo = S.parse(dv(BASE))
        src = S.parse(dv(BASE[-2:] + [("2026-09-23", 2.83)]))
        r = S.merge("f.csv", repo, src, PL, None, {}, "run1", T0)
        self.assertEqual(r.status, S.PUBLISH)
        self.assertEqual(len(r.series.rows), len(BASE) + 1)                  # nada se borra
        self.assertEqual(r.dropped_by_source, len(BASE) - 2)

    def test_regression_blocked(self):
        repo = S.parse(dv(BASE + [("2026-09-23", 2.83)]))
        r = S.merge("f.csv", repo, S.parse(dv(BASE)), PL, None, {}, "run1", T0)
        self.assertEqual(r.status, S.REGRESSION_BLOCKED)

    def test_same_value_different_format_no_change(self):
        repo = S.parse(dv([("2026-09-22", "4.90")]))
        r = S.merge("f.csv", repo, S.parse(dv([("2026-09-22", "4.9")])), PL, None, {}, "run1", T0)
        self.assertEqual(r.status, S.NOOP)

    def test_revision_inside_window_accepted(self):
        repo = S.parse(dv(BASE))
        rev = [(d, v) for d, v in BASE]
        rev[-1] = (rev[-1][0], 2.83)                                          # revisa la última (+0.008)
        r = S.merge("f.csv", repo, S.parse(dv(rev)), PL, 5, {}, "run1", T0)
        self.assertEqual(r.status, S.PUBLISH)
        self.assertEqual(r.revised, ["20260922"])

    def test_revision_detected_even_if_max_date_unchanged(self):
        repo = S.parse(dv(BASE))
        rev = list(BASE)
        rev[3] = (rev[3][0], 2.9)
        r = S.merge("f.csv", repo, S.parse(dv(rev)), PL, None, {}, "run1", T0)   # sin ventana documentada
        self.assertEqual(r.src_max, r.repo_max)
        self.assertEqual(r.status, S.HELD)                                    # detectada y en confirmación
        self.assertEqual(r.held[0][0], "20260917")

    def test_historical_correction_outside_window_needs_confirmation(self):
        repo = S.parse(dv(BASE))
        rev = list(BASE)
        rev[0] = (rev[0][0], 2.9)
        r = S.merge("f.csv", repo, S.parse(dv(rev)), PL, 2, {}, "run1", T0)
        self.assertEqual(r.status, S.HELD)
        self.assertIn("corrección histórica", r.held[0][2])

    def _jump(self):
        repo = S.parse(dv(BASE))
        src = S.parse(dv(BASE + [("2026-09-23", 3.60)]))                     # +0.778 > 0.35
        return repo, src

    def test_t06_jump_held_last_valid_kept_candidate_stored(self):
        repo, src = self._jump()
        r = S.merge("f.csv", repo, src, PL, None, {}, "run1", T0)
        self.assertEqual(r.status, S.HELD)
        self.assertIsNone(r.series)                                           # nada que publicar: último válido intacto
        self.assertEqual(len(r.candidates), 1)
        c = r.candidates[0]
        self.assertEqual((c["date"], c["value"], c["status"]), ("20260923", 3.6, S.Q_PENDING))

    def test_t06_confirmed_by_later_independent_download(self):
        repo, src = self._jump()
        r1 = S.merge("f.csv", repo, src, PL, None, {}, "run1", T0)
        q = {c["id"]: c for c in r1.candidates}
        r_same = S.merge("f.csv", repo, src, PL, None, q, "run1", T0 + timedelta(hours=2))
        self.assertEqual(r_same.status, S.HELD)                               # misma ejecución no confirma
        r_fast = S.merge("f.csv", repo, src, PL, None, q, "run2", T0 + timedelta(minutes=5))
        self.assertEqual(r_fast.status, S.HELD)                               # demasiado pronto (<30 min)
        r2 = S.merge("f.csv", repo, src, PL, None, q, "run2", T0 + timedelta(hours=9))
        self.assertEqual(r2.status, S.PUBLISH)
        self.assertEqual(r2.candidates[0]["status"], S.Q_ACCEPTED_CONFIRMED)
        self.assertEqual(r2.candidates[0]["confirmed_by_run"], "run2")
        self.assertIn("20260923", r2.series.rows)

    def test_t06_source_changes_value_supersedes(self):
        repo, src = self._jump()
        r1 = S.merge("f.csv", repo, src, PL, None, {}, "run1", T0)
        q = {c["id"]: c for c in r1.candidates}
        fixed = S.parse(dv(BASE + [("2026-09-23", 2.84)]))
        r2 = S.merge("f.csv", repo, fixed, PL, None, q, "run2", T0 + timedelta(hours=9))
        self.assertEqual(r2.status, S.PUBLISH)
        self.assertEqual([c["status"] for c in r2.candidates], [S.Q_SUPERSEDED])

    def test_t06_manual_accept_and_reject(self):
        repo, src = self._jump()
        r1 = S.merge("f.csv", repo, src, PL, None, {}, "run1", T0)
        cid = r1.candidates[0]["id"]
        q = {cid: r1.candidates[0]}
        acc = S.apply_manual_decisions(q, [{"id": cid, "decision": "accept", "by": "Sander", "reason": "subida real", "utc": "x"}])
        r2 = S.merge("f.csv", repo, src, PL, None, acc, "run1", T0)           # manual: vale incluso en la misma ejecución
        self.assertEqual(r2.status, S.PUBLISH)
        self.assertEqual(acc[cid]["decision"]["by"], "Sander")
        rej = S.apply_manual_decisions(q, [{"id": cid, "decision": "reject", "by": "Sander", "reason": "error", "utc": "x"}])
        r3 = S.merge("f.csv", repo, src, PL, None, rej, "run9", T0 + timedelta(days=3))
        self.assertEqual(r3.status, S.HELD)                                   # rechazo manual no se autoconfirma

    def test_family_hold_new_from(self):
        repo = S.parse(dv(BASE))
        src = S.parse(dv(BASE + [("2026-09-23", 2.83)]))
        r = S.merge("g.csv", repo, src, PL, None, {}, "run1", T0, hold_new_from="20260923")
        self.assertEqual(r.status, S.HELD)
        self.assertIsNone(r.series)

    def test_first_creation_range_only(self):
        rows = [("2026-01-%02d" % d, v) for d, v in ((5, 4.0), (6, 3.0), (7, 3.1))]   # salto histórico de 1.0
        r = S.merge("f.csv", None, S.parse(dv(rows)), PL, None, {}, "run1", T0)
        self.assertEqual(r.status, S.PUBLISH)

    def test_write_atomic(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "x.csv")
        S.write_atomic(p, b"a\n")
        S.write_atomic(p, b"b\n")
        self.assertEqual(open(p, "rb").read(), b"b\n")
        self.assertEqual(sorted(os.listdir(d)), ["x.csv"])


if __name__ == "__main__":
    unittest.main()

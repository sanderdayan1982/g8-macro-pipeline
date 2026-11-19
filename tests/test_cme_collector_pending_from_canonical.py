"""orden Sander 19-nov (t269u): el colector calcula las sesiones pendientes a partir
de las que ya existen en canonical/, sin state.json. Si una sesión ya está, no se
descarga ni se paga.

canonical/ sintético y Databento simulado: cualquier llamada de pago
(timeseries.get_range) hace fallar el test. Sin red.
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import cme_options_collector as C  # noqa: E402

ROOTS = [p.split(".")[0] for p in C.OPT_PARENTS + C.FUT_PARENTS]
HEADER = ",".join(C.CANON_HEADER)
# jueves 19-nov-2026 18:00 UTC → sesión objetivo = miércoles 18-nov
NOW = datetime(2026, 11, 19, 18, 0, tzinfo=timezone.utc)


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz else NOW.replace(tzinfo=None)


def write_session(out, sess, roots=ROOTS):
    d = Path(out) / sess
    d.mkdir(parents=True, exist_ok=True)
    for r in roots:
        (d / f"{r}.csv").write_text(HEADER + "\r\n" + f"{sess},{r},X,SYM,2026-12-04,C,1,0,2,\r\n")


class FakeDatabento:
    """Registra cada endpoint. Las llamadas de pago hacen fallar el test."""
    PAID = ("timeseries.",)

    def __init__(self, testcase):
        self.t, self.calls = testcase, []

    def __call__(self, auth, endpoint, params):
        self.calls.append(endpoint)
        if endpoint.startswith(self.PAID):
            self.t.fail("llamada de pago a Databento: %s %s" % (endpoint, params.get("start")))
        raise AssertionError("llamada inesperada: %s" % endpoint)

    def paid(self):
        return [c for c in self.calls if c.startswith(self.PAID)]


class PendingFromCanonical(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "canonical"
        self.out.mkdir()
        self.state = Path(self.tmp.name) / "state.json"
        # state.json viejo y desfasado: ya no debe influir
        self.state.write_text(json.dumps({"last_success": "2026-11-12"}))
        self.db = FakeDatabento(self)
        self.processed = []
        self.patches = [
            mock.patch.object(C, "OUT_DIR", self.out),
            mock.patch.object(C, "STATE_FILE", self.state),
            mock.patch.object(C, "datetime", _FixedDatetime),
            mock.patch.object(C, "get_api_key", lambda: "k"),
            mock.patch.object(C, "api_get", self.db),
            mock.patch.object(C, "notify", lambda m: None),
            mock.patch.object(sys, "argv", ["cme_options_collector.py"]),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def run_main(self, fake_process=False):
        def proc(auth, sess):
            self.processed.append(str(sess))
            return True
        buf = io.StringIO()
        ctx = mock.patch.object(C, "process_session", proc) if fake_process else mock.patch.object(C, "MAX_CATCHUP", C.MAX_CATCHUP)
        with ctx, redirect_stdout(buf):
            C.main()
        return buf.getvalue()

    def test_all_sessions_present_no_paid_download(self):
        for s in ("2026-11-12", "2026-11-13", "2026-11-16", "2026-11-17", "2026-11-18"):
            write_session(self.out, s)
        out = self.run_main()  # process_session real: cualquier descarga fallaría
        self.assertEqual(self.db.paid(), [])
        self.assertEqual(self.db.calls, [])  # ni siquiera consultas de disponibilidad
        self.assertIn("Nothing to do", out)

    def test_second_run_same_day_pays_nothing(self):
        for s in ("2026-11-13", "2026-11-16", "2026-11-17"):
            write_session(self.out, s)
        self.run_main(fake_process=True)
        self.assertEqual(self.processed, ["2026-11-18"])  # no 13/16/17 pese al state.json viejo
        write_session(self.out, "2026-11-18")  # lo que dejó el primer run
        self.processed.clear()
        self.run_main()
        self.assertEqual(self.db.paid(), [])
        self.assertEqual(self.processed, [])

    def test_incomplete_session_is_pending(self):
        write_session(self.out, "2026-11-17")
        write_session(self.out, "2026-11-18", roots=ROOTS[:-1])  # falta un producto
        self.run_main(fake_process=True)
        self.assertEqual(self.processed, ["2026-11-18"])
        self.assertFalse(C.session_complete(date(2026, 11, 18), self.out))
        self.assertTrue(C.session_complete(date(2026, 11, 17), self.out))

    def test_header_only_file_is_not_complete(self):
        write_session(self.out, "2026-11-18")
        (self.out / "2026-11-18" / "EUU.csv").write_text(HEADER + "\r\n")
        self.assertFalse(C.session_complete(date(2026, 11, 18), self.out))

    def test_empty_canonical_only_target(self):
        self.run_main(fake_process=True)
        self.assertEqual(self.processed, ["2026-11-18"])

    def test_missing_canonical_dir_behaves_as_empty(self):
        self.out.rmdir()
        self.assertEqual(C.present_sessions(self.out), [])
        self.run_main(fake_process=True)
        self.assertEqual(self.processed, ["2026-11-18"])

    def test_catchup_capped_at_max(self):
        write_session(self.out, "2026-10-01")
        (self.out / "not-a-date").mkdir()
        (self.out / "README.txt").write_text("x")
        self.run_main(fake_process=True)
        self.assertEqual(len(self.processed), C.MAX_CATCHUP)
        self.assertEqual(self.processed[0], "2026-10-02")
        for s in self.processed:
            self.assertLess(date.fromisoformat(s).weekday(), 5)

    def test_gap_before_latest_follows_latest(self):
        # como con state.json: se continúa desde la última sesión completa
        write_session(self.out, "2026-11-13")
        write_session(self.out, "2026-11-17")
        self.run_main(fake_process=True)
        self.assertEqual(self.processed, ["2026-11-18"])

    def test_state_still_written_for_health(self):
        write_session(self.out, "2026-11-17")
        self.run_main(fake_process=True)
        self.assertEqual(json.loads(self.state.read_text())["last_success"], "2026-11-18")


if __name__ == "__main__":
    unittest.main()

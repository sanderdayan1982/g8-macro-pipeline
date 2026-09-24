"""Lote 3 · equivalencia y fallos corregidos por descargador (SOFR, €STR, SONIA, TONA, CORRA, AONIA, BIS ×5).

Por cada descargador:
  E1 equivalencia con respuesta válida y día nuevo (original congelado vs nuevo: mismos bytes)
  E2 equivalencia el día siguiente (la ventana de 5 años avanza: mismos bytes)
  E3 sin novedad: el original reescribe lo mismo; el nuevo no escribe (mismos bytes)
  F1 503 persistente: 4 intentos 10/40/90 s, fichero intacto, código 1
  F2 respuesta truncada: el ORIGINAL la escribía (defecto demostrado); el nuevo conserva el fichero
  F3 respuesta vacía / sin datos: ambos conservan, código 1
  F4 respuesta más antigua que lo publicado: el original retrocedía; el nuevo bloquea
  F5 429 con Retry-After mayor que el presupuesto: aplazado y la siguiente ejecución no llama antes
  F6 transitorio y luego correcto: el original fallaba; el nuevo publica
  F7 salto implausible: se retiene, candidato guardado; confirmado en una ejecución posterior → publicado
"""
import json
import os
import shutil
import sys
import unittest
from datetime import timedelta

sys.path.insert(0, os.path.dirname(__file__))
import fetcher_harness as H  # noqa: E402
from g8fakes import Clock  # noqa: E402

SPECS = [
    ("fetch_estr", "ESTR.csv", H.ecb_csv, (), "window"),
    ("fetch_sofr", "SOFR.csv", H.fred_csv, (), "window"),
    ("fetch_sonia", "SONIA.csv", H.boe_csv, (), "window"),
    ("fetch_tona", "TONA.csv", H.boj_json, (), "month"),
    ("fetch_corra", "CORRA.csv", H.valet_json, (), "window"),
    ("fetch_aonia", "AONIA.csv", H.rba_f1, (), "all"),
    ("fetch_bis_ocr", "NZD_OCR.csv", H.bis_csv, (), "all"),
    ("fetch_bis_policy", "GB_POLICY.csv", H.bis_csv, ("GB",), "all"),
    ("fetch_bis_policy", "JP_POLICY.csv", H.bis_csv, ("JP",), "all"),
    ("fetch_bis_policy", "CH_POLICY.csv", H.bis_csv, ("CH",), "all"),
    ("fetch_bis_policy", "AU_POLICY.csv", H.bis_csv, ("AU",), "all"),
]


def next_bd(d):
    from datetime import datetime
    x = datetime.strptime(d, "%Y%m%d") + timedelta(days=1)
    while x.weekday() >= 5:
        x += timedelta(days=1)
    return x.strftime("%Y%m%d")


class Base(object):
    script = fname = wrap = None
    argv = ()
    window = "window"

    def setUp(self):
        self.rows = H.repo_rows(self.fname)
        self.orig_bytes = open(os.path.join(H.ROOT, "data", self.fname), "rb").read()
        self.roots = []

    def tearDown(self):
        for r in self.roots:
            shutil.rmtree(r, ignore_errors=True)

    def root(self, content=None):
        r = H.make_root({self.fname: self.orig_bytes if content is None else content})
        self.roots.append(r)
        return r

    def window_rows(self, rows, now):
        start = (now - timedelta(days=365 * 5))
        if self.window == "month":
            lo = start.strftime("%Y%m") + "01"
        elif self.window == "window":
            lo = start.strftime("%Y%m%d")
        else:
            lo = "00000000"
        return [r for r in rows if r[0] >= lo]

    def serve_rows(self, rows, now=H.NOW):
        body = self.wrap(self.window_rows(rows, now))
        return lambda url: (200, body)

    def both(self, serve, content=None, now=H.NOW):
        ro, rn = self.root(content), self.root(content)
        rc_o, _ = H.run_original(self.script, serve, ro, self.argv, now=now)
        rc_n, calls, clock = H.run_new(self.script, serve, rn, self.argv, now=now)
        return (rc_o, H.read(ro, self.fname)), (rc_n, H.read(rn, self.fname)), rn, calls, clock

    def new_row(self):
        d, v = self.rows[-1]
        return (next_bd(d), v)

    # ── equivalencia ─────────────────────────────────────────────────────────
    def test_E1_equivalence_new_day(self):
        rows = self.rows + [self.new_row()]
        (rco, bo), (rcn, bn), *_ = self.both(self.serve_rows(rows))
        self.assertEqual((rco, rcn), (0, 0))
        self.assertEqual(bo, bn)
        self.assertNotEqual(bn, self.orig_bytes)

    def test_E2_equivalence_following_day_window_moves(self):
        rows = self.rows + [self.new_row()]
        (rco, bo), (rcn, bn), *_ = self.both(self.serve_rows(rows))
        rows2 = rows + [(next_bd(rows[-1][0]), rows[-1][1])]
        now2 = H.NOW + timedelta(days=1)
        ro, rn = self.root(bo), self.root(bn)
        rco2, _ = H.run_original(self.script, self.serve_rows(rows2, now2), ro, self.argv, now=now2)
        rcn2, _, _ = H.run_new(self.script, self.serve_rows(rows2, now2), rn, self.argv, now=now2)
        self.assertEqual((rco2, rcn2), (0, 0))
        self.assertEqual(H.read(ro, self.fname), H.read(rn, self.fname))

    def test_E3_no_news_identical(self):
        (rco, bo), (rcn, bn), rn, *_ = self.both(self.serve_rows(self.rows))
        self.assertEqual(bo, bn)
        self.assertEqual(bn, self.orig_bytes)

    # ── fallos corregidos ────────────────────────────────────────────────────
    def test_F1_503_persistent_four_attempts_file_intact(self):
        (rco, bo), (rcn, bn), rn, calls, clock = self.both(lambda url: (503, b"busy"))
        self.assertEqual((rco, rcn), (1, 1))
        self.assertEqual(bo, self.orig_bytes)
        self.assertEqual(bn, self.orig_bytes)
        self.assertEqual(len(calls), 4)
        self.assertEqual(clock.slept, [10, 40, 90])

    def test_F2_truncated_response_kept(self):
        keep = self.window_rows(self.rows, H.NOW)
        rows = keep[:20] + keep[-3:] + [self.new_row()]           # hueco enorme en medio
        (rco, bo), (rcn, bn), rn, *_ = self.both(self.serve_rows(rows))
        self.assertNotEqual(bo, self.orig_bytes)                   # el original machacaba la historia
        self.assertLess(len(bo), len(self.orig_bytes))
        self.assertEqual(bn, self.orig_bytes)                      # el nuevo conserva lo publicado
        self.assertEqual(rcn, 1)

    def test_F3_empty_response(self):
        (rco, bo), (rcn, bn), *_ = self.both(lambda url: (200, b""))
        self.assertEqual((rco, rcn), (1, 1))
        self.assertEqual((bo, bn), (self.orig_bytes, self.orig_bytes))

    def test_F4_older_response_blocked(self):
        rows = self.rows[:-3]
        (rco, bo), (rcn, bn), *_ = self.both(self.serve_rows(rows))
        self.assertNotEqual(bo, self.orig_bytes)                   # el original retrocedía 3 días
        self.assertEqual(bn, self.orig_bytes)
        self.assertEqual(rcn, 1)

    def test_F5_retry_after_beyond_budget_deferred(self):
        rn = self.root()
        clock = Clock()
        rc, calls, _ = H.run_new(self.script, lambda url: (503, (b"", {"retry-after": "3600"})), rn, self.argv, clock=clock)
        self.assertEqual((rc, len(calls)), (1, 1))
        latest = json.load(open(os.path.join(rn, "data", "_ingest", "latest", "actions__%s.json" % self.job_name())))
        self.assertTrue(latest["not_before"])
        clock.t += 600                                              # 10 min después: aún no se puede llamar
        rc2, calls2, _ = H.run_new(self.script, self.serve_rows(self.rows + [self.new_row()]), rn, self.argv, clock=clock)
        self.assertEqual(calls2, [])
        self.assertEqual(H.read(rn, self.fname), self.orig_bytes)
        clock.t += 3600                                             # pasado Retry-After: vuelve a consultar
        rc3, calls3, _ = H.run_new(self.script, self.serve_rows(self.rows + [self.new_row()]), rn, self.argv, clock=clock)
        self.assertEqual((rc3, len(calls3)), (0, 1))

    def test_F6_transient_then_ok(self):
        body = self.wrap(self.window_rows(self.rows + [self.new_row()], H.NOW))
        seq = [(502, b""), (200, body)]
        (rco, bo), (rcn, bn), *_ = self.both(lambda url: seq.pop(0) if len(seq) > 1 else seq[0])
        self.assertEqual(rcn, 0)
        self.assertNotEqual(bn, self.orig_bytes)

    def test_F7_implausible_jump_held_then_confirmed(self):
        d, v = self.new_row()
        rows = self.rows + [(d, "%.4f" % (float(v) + 5.0))]
        rn = self.root()
        clock = Clock()
        rc, _, _ = H.run_new(self.script, self.serve_rows(rows), rn, self.argv, clock=clock)
        self.assertEqual(H.read(rn, self.fname), self.orig_bytes)
        q = json.load(open(os.path.join(rn, "data", "_ingest", "quarantine", self.fname + ".json")))
        (cid, c), = q["candidates"].items()
        self.assertEqual((c["date"], c["status"]), (d, "PENDING"))
        clock.t += 9 * 3600
        rc2, _, _ = H.run_new(self.script, self.serve_rows(rows), rn, self.argv, clock=clock)
        self.assertIn(d.encode(), H.read(rn, self.fname))
        q2 = json.load(open(os.path.join(rn, "data", "_ingest", "quarantine", self.fname + ".json")))
        self.assertEqual(q2["candidates"][cid]["status"], "ACCEPTED_CONFIRMED")

    def job_name(self):
        return self.script + ("_" + self.argv[0] if self.argv else "")


def _mk(spec):
    script, fname, wrap, argv, window = spec
    name = "Test_%s_%s" % (script, fname.replace(".", "_"))
    return type(name, (Base, unittest.TestCase), {"script": script, "fname": fname, "wrap": staticmethod(wrap),
                                                   "argv": argv, "window": window})


for _s in SPECS:
    _c = _mk(_s)
    globals()[_c.__name__] = _c

if __name__ == "__main__":
    unittest.main()

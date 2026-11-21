"""t296u — solve() con parada al converger y lectura CSV por índice: equivalencia con el método antiguo.

Datos sintéticos generados aquí (Black-76 con sonrisa de forma fija, niveles redondos). Ningún dato de
CME. Referencia: la bisección antigua de 200 vueltas fijas y la lectura con csv.DictReader, copiadas
tal cual en este fichero. Tolerancia 1e-9 en cada número.
"""
import csv
import json
import math
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import cme_fxvol_shadow as FX  # noqa: E402

TOL = 1e-9
HEADER = ["session", "root", "type", "symbol", "expiry", "right", "strike", "settle", "settle_flag", "oi"]


def old_solve(p, F, K, T, cp):
    """solve() antes de t296u: 200 vueltas fijas."""
    intr = max(0.0, F - K) if cp == "C" else max(0.0, K - F)
    if p <= intr:
        return None
    lo, hi = 1e-4, 5.0
    if FX.b76(F, K, T, hi, cp) < p:
        return None
    for _ in range(200):
        m = 0.5 * (lo + hi)
        if FX.b76(F, K, T, m, cp) < p:
            lo = m
        else:
            hi = m
    return 0.5 * (lo + hi)


def old_read(path):
    """read() antes de t296u: csv.DictReader."""
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        return list(csv.DictReader(fh))


def _compare(a, b, path, diffs):
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            diffs.append("%s claves %s != %s" % (path, sorted(a), sorted(b)))
            return
        for k in a:
            _compare(a[k], b[k], "%s.%s" % (path, k), diffs)
    elif isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            diffs.append("%s longitud %d != %d" % (path, len(a), len(b)))
            return
        for i, (x, y) in enumerate(zip(a, b)):
            _compare(x, y, "%s[%d]" % (path, i), diffs)
    elif isinstance(a, bool) or isinstance(b, bool):
        if a is not b:
            diffs.append("%s %r != %r" % (path, a, b))
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if abs(float(a) - float(b)) > TOL:
            diffs.append("%s %r != %r" % (path, a, b))
    elif a != b:
        diffs.append("%s %r != %r" % (path, a, b))


def _smile(m):
    # sonrisa sintética de forma fija (no calibrada a ningún mercado)
    x = math.log(m)
    return 1.0 + 0.8 * x * x - 0.15 * x


def write_canonical(repo, n_sessions=30):
    can = os.path.join(repo, "data", "options", "canonical")
    start = date(2026, 1, 5)
    days = []
    d = start
    while len(days) < n_sessions:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    base = {"EUR": 1.10, "JPY": 0.010, "GBP": 1.25, "AUD": 0.70, "CAD": 0.75, "CHF": 1.05}
    mults = [0.80, 0.85, 0.90, 0.93, 0.95, 0.97, 0.98, 0.99, 1.00, 1.01, 1.02, 1.03, 1.05, 1.07, 1.10, 1.15, 1.20]
    for i, sd in enumerate(days):
        sess = sd.isoformat()
        folder = os.path.join(can, sess)
        os.makedirs(folder)
        exps = [(sd + timedelta(days=12)).isoformat(), (sd + timedelta(days=40)).isoformat()]
        futs = [("H", date(2026, 3, 18)), ("M", date(2026, 6, 17)), ("U", date(2026, 9, 16))]
        for j, (ccy, (opt, fut)) in enumerate(FX.OPT.items()):
            level = base[ccy] * (1.0 + 0.002 * math.sin(i * 0.7 + j))
            sig = 0.08 + 0.01 * j + 0.005 * math.cos(i * 0.4)
            with open(os.path.join(folder, fut + ".csv"), "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(HEADER)
                for k, (code, fexp) in enumerate(futs):
                    px = level * (1.0 + 0.001 * k)
                    oi = "" if (k == 2 and i % 5 == 0) else str(100000 + 1000 * i - 30000 * k)
                    w.writerow([sess, fut, "FUT", fut + code + "6", fexp.isoformat(), "", "",
                                format(px, ".8f"), "1", oi])
                fh.write("\n")  # línea en blanco: DictReader la omite
            with open(os.path.join(folder, opt + ".csv"), "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(HEADER)
                for exp in exps:
                    tenor = (date.fromisoformat(exp) - sd).days / 365.0
                    fwd = level  # futuro siguiente al vencimiento
                    for m in mults:
                        strike = fwd * m
                        vol = sig * _smile(m)
                        for right in ("C", "P"):
                            price = FX.b76(fwd, strike, tenor, vol, right)
                            settle = "" if (m == 0.93 and right == "P" and i % 7 == 0) else format(price, ".10f")
                            w.writerow([sess, opt, "OPT", "SYN", exp, right, format(strike, ".8f"), settle, "1", "10"])
    return [d.isoformat() for d in days]


class SolveEquivalence(unittest.TestCase):
    def test_solve_matches_200_iterations_within_1e9(self):
        n = 0
        for F in (0.0065, 0.70, 1.10, 1.35):
            for T in (7 / 365.0, 30 / 365.0, 0.5, 2.0):
                for m in (0.5, 0.8, 0.95, 1.0, 1.05, 1.25, 2.0):
                    K = F * m
                    for s in (0.0002, 0.03, 0.10, 0.45, 1.5, 4.9):
                        for cp in ("C", "P"):
                            p = FX.b76(F, K, T, s, cp)
                            for q in (p, p * 0.999, p * 1.001):
                                a, b = FX.solve(q, F, K, T, cp), old_solve(q, F, K, T, cp)
                                if a is None or b is None:
                                    self.assertIs(a, b, (q, F, K, T, cp))
                                    continue
                                n += 1
                                self.assertLessEqual(abs(a - b), TOL, (q, F, K, T, cp, a, b))
        self.assertGreater(n, 1000)

    def test_out_of_bracket_cases_stay_none(self):
        self.assertIsNone(FX.solve(0.0, 1.0, 0.9, 0.1, "C"))          # bajo el intrínseco
        self.assertIsNone(FX.solve(10.0, 1.0, 1.0, 0.1, "C"))         # por encima de hi=5
        self.assertIsNone(old_solve(10.0, 1.0, 1.0, 0.1, "C"))

    def test_tolerance_constant(self):
        self.assertEqual(FX.SOLVE_TOL, 1e-12)


class ReadEquivalence(unittest.TestCase):
    def test_reader_by_index_matches_dictreader_on_used_columns(self):
        tmp = tempfile.mkdtemp()
        try:
            p = os.path.join(tmp, "X.csv")
            with open(p, "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(HEADER)
                w.writerow(["2026-01-05", "6E", "FUT", "6EH6", "2026-03-18", "", "", "1.10000000", "1", "123"])
                fh.write("\n")
                w.writerow(["2026-01-05", "EUU", "OPT", "SYN", "2026-01-20", "C", "1.1", "", "1", ""])
                w.writerow(["2026-01-05", "EUU", "OPT", "SYN", "2026-01-20", "P"])  # fila corta
            new, old = FX.read(p), old_read(p)
            self.assertEqual(len(new), len(old))
            for a, b in zip(new, old):
                self.assertEqual(set(a), set(FX.READ_COLUMNS))
                for k in FX.READ_COLUMNS:
                    self.assertEqual(a[k], b[k], k)
            self.assertIsNone(FX.read(os.path.join(tmp, "no.csv")))
        finally:
            shutil.rmtree(tmp)


class BuildEquivalence(unittest.TestCase):
    """build() completo sobre un canónico sintético: nuevo vs antiguo (solve 200 + DictReader)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.days = write_canonical(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def _build_old(self, session=None):
        saved = FX.solve, FX.read
        FX.solve, FX.read = old_solve, old_read
        try:
            return FX.build(self.tmp, session)
        finally:
            FX.solve, FX.read = saved

    def test_build_and_history_match_within_1e9(self):
        for session in (None, self.days[10], self.days[-3]):
            new = FX.build(self.tmp, session)
            old = self._build_old(session)
            diffs = []
            _compare(new[0], old[0], "out", diffs)
            _compare(new[1], old[1], "hist", diffs)
            self.assertEqual(new[2], old[2])
            self.assertEqual(diffs, [], "\n".join(diffs[:20]))
            ok = [c for c in FX.OPT if new[0]["currencies"][c]["status"] == "OK"]
            self.assertEqual(len(ok), 6, session)

    def test_output_schema_unchanged(self):
        new = FX.build(self.tmp)[0]
        old = self._build_old()[0]
        self.assertEqual(list(new), list(old))
        for c in new["currencies"]:
            self.assertEqual(list(new["currencies"][c]), list(old["currencies"][c]), c)
        self.assertEqual(json.dumps(new, sort_keys=True).count(":"), json.dumps(old, sort_keys=True).count(":"))

    def test_history_csv_identical(self):
        new = FX.build(self.tmp)
        old = self._build_old()
        self.assertEqual(FX.history_csv(new[2], new[1]), FX.history_csv(old[2], old[1]))


if __name__ == "__main__":
    unittest.main()

"""252 sesiones sintéticas: el percentil de IV30 existe y el régimen se calcula.

Orden expresa de Sander 14-nov-2026 (t161u). No lee data/options/canonical.
La cadena es Black-76 con vol constante 8 % sobre 252 sesiones. Con la ventana
llena el percentil de una serie plana es 100 y vol_regime es EXPANSION.
"""
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import cme_fxvol_shadow as FX  # noqa: E402

N_SESSIONS = 252
F = 1.10
VOL = 0.08
DTE = 40
FUT_EXP = "2099-12-15"
FUT_SYM = "6EZ9"
HEADER = "session,root,type,symbol,expiry,right,strike,settle,settle_flag,oi\n"
CCYS = ("EUR", "JPY", "GBP", "AUD", "CAD", "CHF")


def _sessions(n):
    d = date(2024, 1, 2)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _write_synthetic(repo, n=N_SESSIONS):
    """Árbol canonical mínimo que build() acepta. No usa datos publicados.

    Strike, precio y futuro son los mismos cada día. La fecha de la fila y el
    vencimiento (DTE 40) sí cambian con la sesión.
    """
    can = os.path.join(repo, "data", "options", "canonical")
    for s in _sessions(n):
        sd = date.fromisoformat(s)
        exp = (sd + timedelta(days=DTE)).isoformat()
        T = DTE / 365.0
        opt_lines = [HEADER]
        for i in range(-12, 13):
            k = round(F + i * 0.005, 4)
            cp = "C" if k >= F else "P"
            px = FX.b76(F, k, T, VOL, cp)
            if px <= 1e-8:
                continue
            opt_lines.append("%s,OPT,OPT,SYN,%s,%s,%.4f,%.8f,2,10\n" % (s, exp, cp, k, px))
        opt_body = "".join(opt_lines)
        fut_body = HEADER + "%s,FUT,FUT,%s,%s,,,%s,3,1000\n" % (s, FUT_SYM, FUT_EXP, format(F, ".8f"))
        day = os.path.join(can, s)
        os.makedirs(day, exist_ok=True)
        for opt, fut in FX.OPT.values():
            with open(os.path.join(day, opt + ".csv"), "w", encoding="utf-8", newline="") as fh:
                fh.write(opt_body)
            with open(os.path.join(day, fut + ".csv"), "w", encoding="utf-8", newline="") as fh:
                fh.write(fut_body)


class Pct252LongWindow(unittest.TestCase):
    def test_percentile_exists_and_regime_is_computed(self):
        with tempfile.TemporaryDirectory(prefix="fxvol252-") as repo:
            _write_synthetic(repo)
            out, _, days = FX.build(repo)
        self.assertEqual(len(days), N_SESSIONS)
        self.assertGreaterEqual(len(days), 252)
        for ccy in CCYS:
            cur = out["currencies"][ccy]
            self.assertEqual(cur["status"], "OK", ccy)
            self.assertGreaterEqual(cur["hist_n"], 252, ccy)
            self.assertIsInstance(cur["iv30_pct252"], float, ccy)
            self.assertEqual(cur["iv30_pct252"], 100.0, ccy)
            self.assertNotIn("iv30_pct252_days", cur, ccy)
            self.assertNotEqual(cur["vol_regime"], "UNAVAILABLE", ccy)
            self.assertEqual(cur["vol_regime"], "EXPANSION", ccy)
        self.assertEqual(out["currencies"]["NZD"]["vol_regime"], "UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()

"""A1 (2026-10-16, consenso Claude + Argon 4): el POS del dashboard clasifica igual que POS-G8 v1.2.6.

Umbrales LF por divisa (f_thr_fx), USD° 0.79/1.15, y AM por lado (f_thr_am) solo en SHADOW.
La misma tabla en colector, generador (aserción Rule 32) y twin-test.
"""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import pos_g8_cot_collector as C  # noqa: E402

PINE_FX = {"EUR": (1.51, 2.26), "GBP": (1.47, 2.06), "JPY": (1.44, 2.42), "AUD": (1.46, 2.30),
           "NZD": (1.56, 2.26), "CAD": (1.51, 2.36), "CHF": (1.47, 2.02), "USD": (0.79, 1.15)}
PINE_AM = {"EUR": [1.52, 2.12, 2.16, 2.72], "GBP": [2.00, 2.61, 1.41, 2.22], "JPY": [1.48, 1.96, 2.23, 2.76],
           "AUD": [1.79, 2.52, 1.69, 2.44], "NZD": [2.30, 3.00, 1.39, 2.17], "CAD": [1.88, 2.76, 1.69, 2.48],
           "CHF": [2.26, 3.00, 1.16, 1.79], "USD": [0.80, 1.55, 0.91, 1.17]}


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class PosSync(unittest.TestCase):
    def test_collector_matches_pine_v126(self):
        for ccy, (w, e) in PINE_FX.items():
            self.assertEqual(C.fx_thr(ccy), (w, e), ccy)
        self.assertEqual(C.AM_THR_SHADOW, PINE_AM)

    def test_generator_assertion_equals_collector(self):
        G = load("scripts/generators/pos_g8.py", "gen_pos_g8")
        self.assertEqual(G.EXPECTED_THR, C.THR)

    def test_twin_uses_same_table(self):
        src = (ROOT / "scripts/twin_test.py").read_text()
        for ccy, (w, e) in PINE_FX.items():
            self.assertRegex(src, r'"%s": \(%s, %s0?\)' % (ccy, w, e), ccy)

    def test_usd_uses_its_own_narrow_pair(self):
        rows = [{"lf_z": -1.0, "lf_net": 1, "am_z": -0.9} for _ in range(7)]
        u = C.usd_proxy(rows)
        self.assertEqual(u["lf_z"], 1.0)
        self.assertEqual(u["state"], "STRETCH+")          # 1.0 ≥ 0.79 (con 1.5/2.0 habría sido NEUTRAL)
        self.assertEqual(u["am_state_shadow"], "NEUTRAL")  # AM USD 0.9 < 0.91

    def test_crowd_is_per_currency(self):
        w, e = C.fx_thr("JPY")
        self.assertEqual(C.state(2.10, w, e), "STRETCH+")  # con el 2.0 global habría sido CROWD LONG
        w, e = C.fx_thr("CHF")
        self.assertEqual(C.state(-2.05, w, e), "CROWD SHORT")

    def test_am_asymmetric_sides(self):
        t = PINE_AM["GBP"]
        self.assertEqual(C.state_asym(-2.03, t), "STRETCH-")   # captura TV 16-oct: GBP AM Z −2.01 ⇒ STRETCH−
        self.assertEqual(C.state_asym(2.03, t), "STRETCH+")    # lado largo: 1.41 ≤ 2.03 < 2.22
        self.assertEqual(C.state_asym(None, t), "NA")

    def test_unknown_currency_falls_back(self):
        self.assertEqual(C.fx_thr("XXX"), (1.5, 2.0))


if __name__ == "__main__":
    unittest.main()

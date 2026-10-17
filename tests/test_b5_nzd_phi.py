"""B5 (SHADOW): no hay curva homogénea pre-2009; la corrección de Φ no sustituye ACM_G8_NZD.csv."""
import hashlib
import json
import os
import sys
import unittest

import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import acm_g8  # noqa: E402
import nzd_phi_shadow as B5  # noqa: E402


def _first_date(name):
    with open(os.path.join(ROOT, "data", name), encoding="utf-8") as fh:
        next(fh)
        return next(fh).split(",")[0]


class OfficialHistory(unittest.TestCase):
    def test_spliced_bonds_do_not_start_before_2009(self):
        for name in ("NZD_BOND_2Y.csv", "NZD_BOND_5Y.csv", "NZD_BOND_10Y.csv", "NZD_BOND_1Y.csv"):
            self.assertGreaterEqual(_first_date(name), "2009-01-29", name)
        self.assertLess(_first_date("NZD_CASH_ON.csv"), "1990-01-01")


class KendallPhi(unittest.TestCase):
    def test_formula_and_stationary_cap(self):
        Phi = np.diag([0.9, 0.5, -0.2])
        out = B5.correct_phi(Phi, 100)
        self.assertAlmostEqual(out[0, 0], 0.9 + (1 + 3 * 0.9) / 100, places=9)
        self.assertAlmostEqual(out[1, 1], 0.5 + (1 + 3 * 0.5) / 100, places=9)
        self.assertAlmostEqual(out[2, 2], -0.2 + (1 + 3 * -0.2) / 100, places=9)
        capped = B5.correct_phi(np.array([[0.99]]), 20)
        self.assertAlmostEqual(float(capped[0, 0]), 0.995, places=9)

    def test_default_estimate_ignores_the_hook(self):
        rng = np.random.default_rng(1)
        mats = list(acm_g8.GRID_MONTHS)
        level = np.cumsum(rng.normal(0, 0.0002, 90))
        cols = [0.03 + level + 0.002 * (m / 120.0) for m in mats]
        frame = pd.DataFrame(np.column_stack(cols), columns=mats)
        plain = acm_g8.acm_estimate(frame)
        explicit = acm_g8.acm_estimate(frame, phi_adjust=None)
        moved = acm_g8.acm_estimate(frame, phi_adjust=B5.correct_phi)
        np.testing.assert_allclose(plain["Phi"], explicit["Phi"])
        self.assertGreater(np.max(np.abs(moved["Phi"] - plain["Phi"])), 1e-6)

    def test_short_sample_monte_carlo_moves_toward_the_root(self):
        rng = np.random.default_rng(5)
        phi, n, reps = 0.95, 80, 300
        ols, corrected = [], []
        for _ in range(reps):
            x = np.zeros(n)
            shock = rng.normal(0, 1, n)
            for t in range(1, n):
                x[t] = phi * x[t - 1] + shock[t]
            x0 = np.column_stack([np.ones(n - 1), x[:-1]])
            slope = np.linalg.lstsq(x0, x[1:], rcond=None)[0][1]
            ols.append(slope)
            corrected.append(float(B5.correct_phi(np.array([[slope]]), n - 1)[0, 0]))
        ols_m, cor_m = float(np.mean(ols)), float(np.mean(corrected))
        self.assertLess(ols_m, phi)
        self.assertLess(abs(cor_m - phi), abs(ols_m - phi))


class ShadowLeavesThePublishedFile(unittest.TestCase):
    def test_measure_does_not_rewrite_the_csv(self):
        path = os.path.join(ROOT, "data", "ACM_G8_NZD.csv")
        with open(path, "rb") as fh:
            before = hashlib.sha256(fh.read()).hexdigest()
        summary = B5.measure()
        with open(path, "rb") as fh:
            after = hashlib.sha256(fh.read()).hexdigest()
        self.assertEqual(before, after)
        self.assertEqual(summary["mode"], "SHADOW")
        self.assertLess(summary["n_months"], 240)
        self.assertGreater(summary["max_abs_eig_shadow"], summary["max_abs_eig_ols"])
        d_y10 = (summary["last_y10_shadow"] - summary["last_y10_ols"]) * 100.0
        self.assertLess(abs(d_y10), abs(summary["last_d_tp_bp"]))
        shadow = os.path.join(ROOT, "data", B5.SHADOW_NAME)
        with open(shadow, encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["mode"], "SHADOW")
        self.assertNotIn("ACM_G8_NZD.csv", json.dumps(saved))


if __name__ == "__main__":
    unittest.main()

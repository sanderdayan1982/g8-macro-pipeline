"""B2 (SHADOW): el MDP diario no reestima betas y el martes clava el Z semanal."""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import metals_shadow_daily as S  # noqa: E402


def _daily(dates, px=100.0):
    idx = pd.DatetimeIndex(pd.to_datetime(dates))
    return pd.DataFrame({"PX": np.full(len(idx), px)}, index=idx)


class ShadowProjection(unittest.TestCase):
    def test_tuesday_matches_weekly_and_later_days_stay_causal(self):
        # Rejilla W-TUE con un hueco: el 13-oct queda a 5 días hábiles del 6-oct y antes del 20-oct.
        weekly = pd.to_datetime(["2026-10-06", "2026-10-20"])
        dates = ["2026-10-06", "2026-10-07", "2026-10-10", "2026-10-13", "2026-10-20", "2026-10-21"]
        daily = _daily(dates)
        y = np.ones(len(daily))
        X = np.ones((len(daily), 1))
        beta_pred = np.array([[0.2], [0.8]])
        beta_filt = np.array([[0.5], [9.0]])
        weekly_z = np.array([1.23456, -0.5])
        frame = S.project(daily, weekly, beta_pred, beta_filt, weekly_z, y, X, z_window=156)
        by_date = {row.DATE: row for row in frame.itertuples(index=False)}

        self.assertNotIn("20261013", by_date)   # 5 días hábiles después del último martes conocido: no se emite
        self.assertNotIn("20261010", by_date)   # sábado

        tue = by_date["20261006"]
        self.assertEqual(tue.BETA_STATE, "pred")
        self.assertAlmostEqual(tue.RESID, 0.8, places=6)   # y - x·β_{τ|τ-1}
        self.assertAlmostEqual(tue.MDP_Z, round(1.23456, 4), places=4)

        wed = by_date["20261007"]
        self.assertEqual(wed.BETA_STATE, "filt")
        self.assertAlmostEqual(wed.RESID, 0.5, places=6)   # β_{τ|τ} del martes ya visto, no el del 20-oct
        self.assertNotAlmostEqual(wed.RESID, 1.0 - 9.0, places=6)

        nxt = by_date["20261020"]
        self.assertEqual(nxt.BETA_STATE, "pred")
        self.assertAlmostEqual(nxt.MDP_Z, -0.5, places=4)

        after = by_date["20261021"]
        self.assertEqual(after.BETA_STATE, "filt")
        self.assertAlmostEqual(after.RESID, 1.0 - 9.0, places=6)

        meta = S.last_meta(frame, weekly)
        self.assertEqual(meta["mdp_as_of"], "2026-10-21")
        self.assertEqual(meta["cot_as_of"], "2026-10-20")
        self.assertEqual(meta["beta_state"], "filt")
        self.assertEqual(meta["mode"], "SHADOW")

    def test_weekend_is_not_emitted(self):
        weekly = pd.to_datetime(["2026-10-06"])
        daily = _daily(["2026-10-06", "2026-10-10"])  # sábado
        y = np.ones(2)
        X = np.ones((2, 1))
        frame = S.project(daily, weekly, np.array([[0.2]]), np.array([[0.5]]), np.array([1.0]), y, X, 156)
        self.assertEqual(list(frame["DATE"]), ["20261006"])

    def test_non_tuesday_z_is_a_causal_daily_zscore(self):
        # Dos martes seguidos: el miércoles 7-oct ya tiene residuos diarios previos y sigue dentro de los 4 días.
        weekly = pd.to_datetime(["2026-09-29", "2026-10-06"])
        dates = pd.bdate_range("2026-09-29", "2026-10-07")
        daily = _daily(dates)
        y = np.arange(len(daily), dtype=float)
        X = np.ones((len(daily), 1))
        beta = np.zeros((2, 1))
        frame = S.project(daily, weekly, beta, beta, np.array([0.25, -0.25]), y, X, z_window=3)
        wed = dates[-1]
        self.assertEqual(wed.strftime("%Y-%m-%d"), "2026-10-07")
        row = frame[frame["DATE"] == wed.strftime("%Y%m%d")].iloc[0]
        hist = y[:-1]
        expected = (y[-1] - hist.mean()) / hist.std(ddof=0)
        self.assertAlmostEqual(row.MDP_Z, round(float(expected), 4), places=4)
        self.assertNotAlmostEqual(row.MDP_Z, round(float(y[-1] / 0.25), 4), places=4)
        self.assertEqual(frame.iloc[0].MDP_Z, 0.25)  # el primer martes conserva el Z semanal

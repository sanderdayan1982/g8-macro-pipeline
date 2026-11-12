"""FLUJOS-01-PR2 — vectores de RV21 (con roll), VRP, régimen, Δ1d/Δ22d y driver.

No toca test_a3_rows_on_the_real_files. El oráculo de RV21 es statistics.stdev (N−1).
"""
import math
import os
import statistics
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import argos_rates as AR  # noqa: E402
import cme_fxvol_shadow as FX  # noqa: E402


def _session(front, prices):
    """prices: {symbol: settle}. front es el contrato de mayor OI ese día."""
    return {"fut": {"px_contract": front, "settles": dict(prices)}}


def _oracle(prices):
    rets = [math.log(prices[i] / prices[i - 1]) for i in range(len(prices) - 21, len(prices))]
    return round(statistics.stdev(rets) * math.sqrt(252) * 100.0, 2)


class Rv21Vectors(unittest.TestCase):
    def test_twenty_one_log_returns_match_sample_stdev(self):
        # 22 settlements del mismo contrato: 21 rendimientos, sin roll.
        prices = [1.10 * math.exp(0.001 * i + (0.0004 if i % 2 else -0.0002)) for i in range(22)]
        hist = [_session("6EZ6", {"6EZ6": p}) for p in prices]
        got, why = FX.rv21_field(hist)
        self.assertIsNone(why)
        self.assertEqual(got, _oracle(prices))

    def test_roll_day_is_excluded_and_short_tail_is_na(self):
        # 30 días del contrato viejo, el día del cambio, y solo 10 sesiones después.
        hist = []
        for i in range(30):
            hist.append(_session("6EU6", {"6EU6": 1.20 + i * 0.0001, "6EZ6": 1.10 + i * 0.0001}))
        hist.append(_session("6EZ6", {"6EZ6": 1.20}))  # día de cambio: excluido
        for i in range(10):
            hist.append(_session("6EZ6", {"6EZ6": 1.21 + i * 0.0002}))
        got, why = FX.rv21_field(hist)
        self.assertEqual(got, "NA")
        self.assertIn("<21", why)
        self.assertIn("día de cambio de contrato excluido", why)
        # 10 precios tras el roll → 9 rendimientos, no se usa el cruce 6EU6→6EZ6.
        self.assertIn("9 rendimientos válidos del contrato 6EZ6", why)

    def test_roll_outside_the_window_uses_only_the_new_contract(self):
        old = [1.50 + i * 0.01 for i in range(5)]  # saltos grandes: si entraran, la vol cambiaría
        new = [1.10 * math.exp(0.0005 * i) for i in range(30)]
        hist = [_session("6EU6", {"6EU6": p, "6EZ6": p}) for p in old]
        hist.append(_session("6EZ6", {"6EZ6": new[0]}))  # día de cambio, excluido
        hist.extend(_session("6EZ6", {"6EZ6": p}) for p in new[1:])
        got, why = FX.rv21_field(hist)
        self.assertIsNone(why)
        # El tramo válido son los 29 precios posteriores al día excluido (new[1:]).
        self.assertEqual(got, _oracle(new[1:]))

    def test_vrp_is_iv30_minus_rv21_in_percent(self):
        self.assertEqual(FX.vol_regime_field("NA", 1.5), "UNAVAILABLE")
        self.assertEqual(FX.vol_regime_field(10, "NA"), "UNAVAILABLE")
        self.assertEqual(FX.vol_regime_field(10, 0.2), "COMPRESSION")
        self.assertEqual(FX.vol_regime_field(10, -0.2), "NEUTRAL")
        self.assertEqual(FX.vol_regime_field(19.9, 0.01), "COMPRESSION")
        self.assertEqual(FX.vol_regime_field(20, 1), "NEUTRAL")
        self.assertEqual(FX.vol_regime_field(80, 1), "NEUTRAL")
        self.assertEqual(FX.vol_regime_field(80.1, -1), "EXPANSION")
        iv, rv = 5.46, 6.10
        self.assertEqual(round(iv - rv, 2), -0.64)

    def test_live_eur_matches_the_oracle_and_the_roll_session_is_na(self):
        out, hist, _ = FX.build(ROOT, "2026-11-06")
        eur = out["currencies"]["EUR"]
        symbol = eur["fut"]["px_contract"]
        prices = []
        for h in hist["EUR"]:
            fu = h.get("fut") or {}
            px = (fu.get("settles") or {}).get(symbol)
            if fu.get("px_contract") == symbol and px:
                prices.append(px)
            else:
                prices = []
        # El día de cambio es el primero en que el contrato ya es el front: se excluye.
        self.assertGreater(len(prices), 22)
        self.assertEqual(eur["rv21"], _oracle(prices[1:]))
        self.assertEqual(eur["vrp"], round(eur["iv30_atm"] - eur["rv21"], 2))
        self.assertEqual(eur["vol_regime"], "UNAVAILABLE")
        roll, _, _ = FX.build(ROOT, "2026-09-15")
        row = roll["currencies"]["EUR"]
        self.assertEqual(row["rv21"], "NA")
        self.assertEqual(row["vrp"], "NA")
        self.assertIn("día de cambio de contrato excluido", row["rv21_reason"])
        self.assertEqual(row["vol_regime"], "UNAVAILABLE")


class RatesComputed(unittest.TestCase):
    def test_d1_d22_and_driver_including_the_tie(self):
        real = [1.0] * 23
        be = [2.0] * 23
        real[-1] = 1.10
        be[-1] = 2.10  # |Δ| iguales a 21 obs y a 1 obs → REAL_LED
        legs = {"EUR": {"nom": [3.0 + i * 0.01 for i in range(23)], "real": real, "be": be}}
        block = AR.computed_block(legs)["EUR"]
        self.assertEqual(block["d1d"]["source"], "computed")
        self.assertEqual(block["d22d"]["source"], "computed")
        self.assertEqual(block["d1d"]["nominal_10y"], round((legs["EUR"]["nom"][-1] - legs["EUR"]["nom"][-2]) * 100, 2))
        self.assertEqual(block["d22d"]["real_10y"], round((real[-1] - real[0]) * 100, 2))
        self.assertEqual(block["driver"], "REAL_LED")
        be[-1] = 2.50
        self.assertEqual(AR.driver_field(real, be), "BE_LED")
        self.assertEqual(AR.driver_field(real, None), "UNAVAILABLE")
        self.assertEqual(AR.driver_field(real[:10], be), "UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()

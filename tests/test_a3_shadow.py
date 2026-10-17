"""A3 (SHADOW): relleno ACM a 8 días hábiles y diagnósticos de contagio sin tocar el Paso C.

La regla de hierro: no se resta la curva de un día contra la de otro. Una fila _FFILL
conserva la fecha del CSV y la curva de acm_input_asof. NZD no entra hasta B5.
"""
import json
import os
import unittest
from datetime import date, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
import sys
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import s01b_shadow_a3 as A3  # noqa: E402


def bday(k, start=date(2024, 1, 2)):
    d, left = start, k
    while left:
        d += timedelta(days=1)
        if d.weekday() < 5:
            left -= 1
    return d


DAY7 = date(2026, 3, 11)
DAY15 = date(2026, 3, 23)


def ladder(n_steps, slope):
    """n_steps+1 impresiones reales. ΔUSD = +1 pb por paso, Δi = slope pb. β = slope."""
    days = [bday(i) for i in range(n_steps + 1)]
    usd, own = [], []
    for i, day in enumerate(days):
        usd.append((day, 1.0 + 0.01 * i, "ACM_K3_300m"))
        own.append((day, 5.0 + slope * 0.01 * i, "ACM_K3_300m"))
    return own, usd, days


class IronRule(unittest.TestCase):
    def test_same_real_dates_subtract(self):
        rows_i = [(DAY7, 1.00, "ACM_K3_300m"), (DAY15, 1.10, "ACM_K3_300m")]
        rows_u = [(DAY7, 2.00, "ACM_K3_300m"), (DAY15, 2.03, "ACM_K3_300m")]
        local = A3.dtp22_local("EUR", DAY7, DAY15, rows_i, rows_u, beta=2.0)
        self.assertAlmostEqual(local, 10.0 - 2.0 * 3.0, places=9)

    def test_does_not_subtract_day7_curve_from_day15(self):
        # La fila USD del día 15 es un arrastre de la curva del día 7.
        # Un inner join por fecha de CSV restaría 20 − 1·50 = −30. Tiene que salir vacío.
        rows_i = [(DAY7, 1.00, "ACM_K3_300m"), (DAY15, 1.20, "ACM_K3_300m")]
        rows_u = [(DAY7, 2.00, "ACM_K3_300m"), (DAY15, 2.50, "ACM_K3_300m_FFILL")]
        forbidden = (1.20 - 1.00) * 100.0 - 1.0 * (2.50 - 2.00) * 100.0
        self.assertAlmostEqual(forbidden, -30.0, places=9)
        self.assertIsNone(A3.dtp22_local("EUR", DAY7, DAY15, rows_i, rows_u, beta=1.0))
        self.assertIsNone(A3.real_print_tp(rows_u, DAY15))

    def test_missing_usd_print_is_na(self):
        rows_i = [(DAY7, 1.0, "ACM_K3_300m"), (DAY15, 1.1, "ACM_K3_300m")]
        rows_u = [(DAY7, 2.0, "ACM_K3_300m")]
        self.assertIsNone(A3.dtp22_local("GBP", DAY7, DAY15, rows_i, rows_u, beta=1.0))

    def test_collapsed_input_dates_are_na(self):
        rows = [(DAY7, 1.0, "ACM_K3_300m")]
        self.assertIsNone(A3.dtp22_local("AUD", DAY7, DAY7, rows, rows, beta=1.0))

    def test_probe_beats_csv_date_and_ffill_walk(self):
        day14 = date(2026, 3, 20)
        rows = [
            (DAY7, 1.0, "ACM_K3_300m"),
            (day14, 1.5, "ACM_K3_300m"),
            (DAY15, 9.0, "ACM_K3_300m_FFILL"),
        ]
        self.assertEqual(A3.input_date(rows, DAY15, probe=(DAY7,)), DAY7)
        self.assertEqual(A3.input_date(rows, DAY15, probe=None), day14)
        self.assertAlmostEqual(A3.real_tp_asof(rows, DAY15, probe=(DAY7,)), 1.0, places=9)
        self.assertNotAlmostEqual(A3.real_tp_asof(rows, DAY15, probe=(DAY7,)), 9.0)

    def test_nzd_and_usd_never_emit(self):
        rows = [(DAY7, 1.0, "ACM_K3_300m"), (DAY15, 1.2, "ACM_K3_300m")]
        self.assertIsNone(A3.dtp22_local("NZD", DAY7, DAY15, rows, rows, beta=1.0))
        self.assertIsNone(A3.dtp22_local("USD", DAY7, DAY15, rows, rows, beta=1.0))
        nzd = A3.shadow_currency("NZD", DAY7, DAY15, rows, rows, [1.0] * 23, [0.0] * 22, [1.0], DAY15, min_steps=1)
        usd = A3.shadow_currency("USD", DAY7, DAY15, rows, rows, [1.0] * 23, [0.0] * 22, [1.0], DAY15, min_steps=1)
        self.assertEqual(nzd["status"], "EXCLUIDA_B5")
        self.assertEqual(usd["status"], "REFERENCIA")
        self.assertIsNone(nzd["dtp22_local"])
        self.assertIsNone(nzd["pct_dtp22_local"])


class BetaAndRho(unittest.TestCase):
    def test_beta_is_causal_and_excludes_the_measured_end(self):
        own, usd, days = ladder(61, slope=2.0)
        cut = days[-1]
        wild = bday(80)
        own.append((wild, 50.0, "ACM_K3_300m"))
        usd.append((wild, 1.0, "ACM_K3_300m"))
        beta, n = A3.beta_usd(own, usd, cut, min_steps=60)
        self.assertEqual(n, 60)
        self.assertAlmostEqual(beta, 2.0, places=9)
        leaked, _ = A3.beta_usd(own, usd, wild + timedelta(days=1), min_steps=60)
        self.assertNotAlmostEqual(leaked, 2.0, places=6)

    def test_short_sample_has_no_beta(self):
        own, usd, days = ladder(10, slope=2.0)
        beta, n = A3.beta_usd(own, usd, days[-1] + timedelta(days=1))
        self.assertIsNone(beta)
        self.assertEqual(n, 10)

    def test_rho_keeps_xxxusd_sign(self):
        # ΔTP crece y la divisa se fortalece (Δln > 0) → correlación positiva.
        # Si se invirtiera el signo de debilidad, este caso daría −1.
        tp = [((i * i) * 0.01) for i in range(23)]
        dtp = [(tp[i + 1] - tp[i]) * 100.0 for i in range(22)]
        stronger = A3.rho_tp_fx_22(tp, [v / 100.0 for v in dtp])[0]
        weaker = A3.rho_tp_fx_22(tp, [-v / 100.0 for v in dtp])[0]
        self.assertAlmostEqual(stronger, 1.0, places=9)
        self.assertAlmostEqual(weaker, -1.0, places=9)

    def test_rho_uses_real_curve_not_the_filled_row(self):
        flat = [1.0] * 23
        fx = [((i % 5) - 2) * 0.001 for i in range(22)]
        rho, n = A3.rho_tp_fx_22(flat, fx)
        self.assertIsNone(rho)
        self.assertEqual(n, 22)
        jumped = [1.0] + [9.0] * 22
        self.assertIsNotNone(A3.rho_tp_fx_22(jumped, fx)[0])

    def test_percentile_matches_s01b_and_skips_nzd(self):
        import s01b
        vals = [float(i) for i in range(1, 11)]
        self.assertAlmostEqual(A3.percentile(vals, 80), s01b.pct(vals, 80.0), places=9)
        history = {"EUR": vals, "NZD": [1000.0] * 10, "USD": [1000.0] * 10}
        self.assertEqual(A3.percentile_population(history, "NZD"), [])
        self.assertEqual(A3.percentile_population(history, "USD"), [])
        self.assertEqual(A3.percentile_population(history, "EUR"), vals)
        short = A3.contagion_percentile(history, "EUR", min_n=60)
        self.assertIsNone(short)
        long_hist = {"EUR": [float(i) for i in range(60)]}
        self.assertIsNotNone(A3.contagion_percentile(long_hist, "EUR"))


class EngineBoundary(unittest.TestCase):
    def test_ffill_limit_is_eight_in_both_panels(self):
        with open(os.path.join(ROOT, "scripts", "acm_g8.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertEqual(src.count("ffill(limit=FFILL_LIMIT)"), 2)
        self.assertNotIn("ffill(limit=5)", src)
        import acm_g8
        self.assertEqual(acm_g8.FFILL_LIMIT, 8)

    def test_nowcast_horizon_is_not_the_acm_fill(self):
        with open(os.path.join(ROOT, "sources", "nowcast_aud.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
        self.assertEqual(cfg["max_horizon_bd"], 10)

    def test_shadow_module_does_not_name_step_c_sinks(self):
        with open(os.path.join(ROOT, "scripts", "s01b_shadow_a3.py"), encoding="utf-8") as fh:
            src = fh.read()
        for token in ("state.json", "S01B.json", "events.jsonl", "evaluate_ccy", "dtp_population"):
            self.assertNotIn(token, src, token)

    def test_dashboard_states_the_new_fill(self):
        with open(os.path.join(ROOT, "docs", "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn("ffill ≤ 8 s.", html)
        self.assertIn("arrastra hasta 8 sesiones", html)
        self.assertNotIn("hasta 5 sesiones", html)


class BuildShadow(unittest.TestCase):
    def test_session_on_synthetic_files(self):
        import tempfile
        import s01b
        days = []
        d = date(2024, 1, 2)
        while len(days) < 90:
            if s01b.is_target_day(d):
                days.append(d)
            d += timedelta(days=1)
        session = days[-1]
        usd = [(day, 1.0 + float(i), "ACM_K3_300m") for i, day in enumerate(days)]
        eur = [(day, 5.0 + 2.0 * i, "ACM_K3_300m") for i, day in enumerate(days)]
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp))
        os.makedirs(os.path.join(tmp, "usd_factor"))
        state = os.path.join(tmp, "state.json")
        with open(state, "w", encoding="utf-8") as fh:
            fh.write('{"signal":"ON"}')
        self._acm(os.path.join(tmp, "ACM_G8_USD.csv"), usd)
        self._acm(os.path.join(tmp, "ACM_G8_EUR.csv"), eur)
        with open(os.path.join(tmp, "usd_factor", "canonical.csv"), "w", encoding="utf-8") as fh:
            fh.write("date,f,r_EUR,r_GBP,r_JPY,r_CHF,r_CAD,r_AUD,r_NZD,desfase_us\n")
            for i, day in enumerate(days):
                r = (i % 5 - 2) * 0.001
                fh.write("%s,0,%s,%s,%s,%s,%s,%s,%s,False\n" % (day.isoformat(), r, r, r, r, r, r, r))
        out = A3.build_shadow(tmp, session)
        self.assertEqual(out["mode"], "SHADOW")
        self.assertEqual(out["currencies"]["NZD"]["status"], "EXCLUIDA_B5")
        self.assertEqual(out["currencies"]["USD"]["status"], "REFERENCIA")
        eur_row = out["currencies"]["EUR"]
        self.assertEqual(eur_row["status"], "OK")
        self.assertAlmostEqual(eur_row["beta_usd"], 2.0, places=9)
        self.assertAlmostEqual(eur_row["dtp22_local"], 0.0, places=6)
        # Cada paso suma el mismo ΔTP: no hay varianza y ρ sale vacío. El signo se prueba aparte.
        self.assertIsNone(eur_row["rho_tp_fx_22"])
        self.assertEqual(eur_row["n_rho"], 22)
        with open(state, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), '{"signal":"ON"}')
        self.assertFalse(os.path.exists(os.path.join(tmp, "S01B.json")))

    def _acm(self, path, rows):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("DATE,Y10_FIT,RNY10,TP10,QUALITY\n")
            for d, tp, q in rows:
                fh.write("%s,1.0000,1.0000,%.6f,%s\n" % (d.strftime("%Y%m%d"), tp, q))


if __name__ == "__main__":
    unittest.main()

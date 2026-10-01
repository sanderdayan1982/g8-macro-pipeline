"""Acta P-1 · decisiones de tipo oficial verificadas por delante del BIS (policy_decisions.csv).

U1 sin fichero de decisiones: nada cambia (equivalencia con el comportamiento previo)
U2 decisión por delante del BIS: prolonga lunes-viernes hasta hoy; antes de la fecha efectiva, tipo anterior
U3 decisión con fecha efectiva futura: no se aplica
U4 decisión que el BIS ya cubre con otro valor: aviso de discrepancia, manda el BIS
U5 fichero inválido (país, tipo fuera de rango, sin fuente): DecisionError
I1 BIS caído (503) + decisión: se publica la prolongación y el rc sigue en 1 (el fallo del BIS sigue abierto)
I2 BIS correcto pero atrasado + decisión: se publica BIS + prolongación, rc 0
I4 decisión con salto implausible (> plaus_max_jump): se retiene como el resto de la ingesta
I3 BIS caído sin decisión aplicable: fichero intacto, rc 1
D1 regla §05 de coherencia: diferencial tipo a un día − oficial apartado > 15 bp de su mediana 2 obs → SUSPECT
"""
import os
import shutil
import sys
import unittest
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import fetcher_harness as H  # noqa: E402
import frozen_data  # noqa: E402

from g8common import policy_decisions as P  # noqa: E402


def write_dec(root, lines):
    os.makedirs(os.path.join(root, "data", "manual"), exist_ok=True)
    with open(os.path.join(root, "data", "manual", "policy_decisions.csv"), "w") as fh:
        fh.write("country,effective_date,rate,announced_date,source,verified\n")
        for l in lines:
            fh.write(l + "\n")


class Unit(unittest.TestCase):
    rows = [("20260917", 1.0), ("20260918", 1.0), ("20260922", 1.0)]

    def dec(self, c, eff, r):
        return {"country": c, "effective": eff, "rate": r, "source": "x"}

    def test_U1_no_file(self):
        root = H.make_root({})
        try:
            self.assertEqual(P.load(root), [])
        finally:
            shutil.rmtree(root)
        out, added, w = P.apply(self.rows, "JP", [], date(2026, 10, 1))
        self.assertEqual((out, added, w), (self.rows, 0, []))

    def test_U2_extends_weekdays(self):
        out, added, w = P.apply(self.rows, "JP", [self.dec("JP", date(2026, 9, 24), 1.25)], date(2026, 10, 1))
        self.assertEqual(out[-7:], [("20260923", 1.0), ("20260924", 1.25), ("20260925", 1.25), ("20260928", 1.25),
                                    ("20260929", 1.25), ("20260930", 1.25), ("20261001", 1.25)])
        self.assertEqual(added, 7)
        self.assertEqual(w, [])

    def test_U3_future_not_applied(self):
        out, added, _ = P.apply(self.rows, "JP", [self.dec("JP", date(2026, 10, 5), 1.5)], date(2026, 10, 1))
        self.assertEqual((out, added), (self.rows, 0))

    def test_U3b_other_country_ignored(self):
        out, added, _ = P.apply(self.rows, "JP", [self.dec("AU", date(2026, 9, 30), 4.6)], date(2026, 10, 1))
        self.assertEqual(added, 0)

    def test_U4_discrepancy_bis_rules(self):
        out, added, w = P.apply(self.rows, "JP", [self.dec("JP", date(2026, 9, 18), 1.25)], date(2026, 10, 1))
        self.assertEqual(added, 0)
        self.assertEqual(out, self.rows)
        self.assertTrue(w and "discrepancia" in w[0])

    def test_U5_invalid_file(self):
        for line in ("XX,2026-09-24,1.25,2026-09-18,src,v", "JP,2026-09-24,25,2026-09-18,src,v",
                     "JP,2026-09-24,1.25,2026-09-18,,v", "JP,24/09/2026,1.25,2026-09-18,src,v"):
            root = H.make_root({})
            try:
                write_dec(root, [line])
                with self.assertRaises(P.DecisionError):
                    P.load(root)
            finally:
                shutil.rmtree(root)

    def test_U6_comments_and_order(self):
        root = H.make_root({})
        try:
            write_dec(root, ["# comentario", "JP,2026-09-24,1.25,2026-09-18,src,v", "AU,2026-09-30,4.60,2026-09-29,src,v"])
            d = P.load(root)
            self.assertEqual([x["country"] for x in d], ["AU", "JP"])
        finally:
            shutil.rmtree(root)


class Integration(unittest.TestCase):
    fname = "JP_POLICY.csv"

    def setUp(self):
        self.orig = frozen_data.read_bytes(self.fname)
        self.rows = H.repo_rows(self.fname)
        self.root = H.make_root({self.fname: self.orig})
        last = datetime.strptime(self.rows[-1][0], "%Y%m%d").date()
        self.today = H.NOW.date()
        self.eff = self.today if last < self.today else None
        self.new = float(self.rows[-1][1]) + 0.25          # salto ≤ plaus_max_jump (0.5)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _published(self):
        return P.read_published(os.path.join(self.root, "data", self.fname))

    def test_I1_bis_down_with_decision(self):
        if self.eff is None:
            self.skipTest("datos congelados ya cubren la fecha del arnés")
        write_dec(self.root, ["JP,%s,%.2f,2026-09-18,src,v" % (self.eff.isoformat(), self.new)])
        rc, calls, _ = H.run_new("fetch_bis_policy", lambda url: (503, b"busy"), self.root, ("JP",))
        self.assertEqual(rc, 1)
        pub = self._published()
        self.assertEqual(pub[-1], (self.eff.strftime("%Y%m%d"), self.new))
        self.assertEqual(pub[:len(self.rows)], [(k, float(v)) for k, v in self.rows])

    def test_I2_bis_ok_but_late_with_decision(self):
        if self.eff is None:
            self.skipTest("datos congelados ya cubren la fecha del arnés")
        write_dec(self.root, ["JP,%s,%.2f,2026-09-18,src,v" % (self.eff.isoformat(), self.new)])
        rc, _, _ = H.run_new("fetch_bis_policy", lambda url: (200, H.bis_csv(self.rows)), self.root, ("JP",))
        self.assertEqual(rc, 0)
        self.assertEqual(self._published()[-1], (self.eff.strftime("%Y%m%d"), self.new))

    def test_I4_implausible_jump_held(self):
        if self.eff is None:
            self.skipTest("datos congelados ya cubren la fecha del arnés")
        write_dec(self.root, ["JP,%s,9.75,2026-09-18,src,v" % self.eff.isoformat()])
        H.run_new("fetch_bis_policy", lambda url: (503, b"busy"), self.root, ("JP",))
        self.assertNotEqual(self._published()[-1][1], 9.75)

    def test_I3_bis_down_no_decision(self):
        rc, _, _ = H.run_new("fetch_bis_policy", lambda url: (503, b"busy"), self.root, ("JP",))
        self.assertEqual(rc, 1)
        self.assertEqual(H.read(self.root, self.fname), self.orig)


class Coherence(unittest.TestCase):
    def test_D1_suspect_rule(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("da_p1", os.path.join(H.ROOT, "scripts", "dashboard_alerts.py"))
        da = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(da)
        days = [date(2026, 8, 1) + timedelta(days=i) for i in range(40)]
        rfr = [(d, 0.98 if i < 38 else 1.23) for i, d in enumerate(days)]
        pol = [(d, 1.0) for d in days]
        series = {"TONA.csv": rfr, "JP_POLICY.csv": pol}
        da.read_series = lambda name, *a, **k: series.get(name)
        da.FLOORS = [("JPY", "TONA.csv", "JP_POLICY.csv", "Policy Rate")]
        st, lines = {}, []
        da.check_policy_coherence(st, lines)
        self.assertEqual(st["policy_coherence"]["JPY"]["status"], "SUSPECT")
        pol[-2:] = [(days[-2], 1.25), (days[-1], 1.25)]
        da.check_policy_coherence(st, lines)
        self.assertEqual(st["policy_coherence"]["JPY"]["status"], "OK")
        self.assertTrue(any("SUSPECT → OK" in l for l in lines))


if __name__ == "__main__":
    unittest.main()

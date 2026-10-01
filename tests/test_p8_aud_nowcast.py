"""Acta P-8 · estimación diaria AUD 2Y/10Y (EST_AUD_V1, scripts/aud_nowcast.py) y sus consumidores.

N1 especificación solo en sources/nowcast_aud.json (el script no lleva betas ni drivers escritos)
N2 datos sintéticos exactos: recupera β y la estimación coincide con la verdad
N3 retardo: el driver de EE. UU. entra con el valor del día hábil ANTERIOR
N4 driver ausente → estado INPUT_MISSING, rc 1, sin filas nuevas y sin borrar el historial
N5 historial que solo crece: al llegar la RBA, las filas viejas quedan y se recalculan solo las posteriores
N6 datos reales: fuera de muestra (último año) la estimación mejora a «repetir el último dato RBA»
N7 s01b: contexto AUD con la estimación → CTX_EST + calidad EST; la sonda de ACM_FFILL solo mira datos oficiales
N8 §00 y §01 la etiquetan; el workflow la calcula antes de s01b y del brief; el agente puede repararla (no su config)
"""
import csv
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, timedelta

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "scripts", "tools"))
import aud_nowcast as N  # noqa: E402

try:
    import yaml
except ImportError:                                                      # pragma: no cover
    yaml = None


def bdays(start, n):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_ohlcv(path, series):
    with open(path, "w") as fh:
        fh.write("DATE,OPEN,HIGH,LOW,CLOSE,VOLUME\n")
        for d, v in series:
            fh.write("%s,%.6f,%.6f,%.6f,%.6f,0\n" % (d.strftime("%Y%m%d"), v, v, v, v))


def write_ry(path, series):
    with open(path, "w") as fh:
        fh.write("# QUALITY=TEST\nDATE,NOM10,REAL10,BE10\n")
        for d, v in series:
            fh.write("%s,%.6f,1.0,%.6f\n" % (d.strftime("%Y%m%d"), v, v - 1.0))


def write_dv(path, series):
    with open(path, "w") as fh:
        fh.write("Date,Value,Source\n")
        for d, v in series:
            fh.write("%s,%.6f,TEST\n" % (d.isoformat(), v))


class Synthetic(unittest.TestCase):
    """Mundo exacto: AU2Y = 0.8·bill + 0.3·US2Y(d−1); AU10Y = 0.5·bill + 0.6·US10Y(d−1)."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "data"))
        os.makedirs(os.path.join(self.root, "sources"))
        shutil.copy(os.path.join(ROOT, "sources", "nowcast_aud.json"), os.path.join(self.root, "sources"))
        rng = np.random.default_rng(7)
        self.days = bdays(date(2023, 1, 2), 900)
        bill = np.cumsum(rng.normal(0, 0.03, len(self.days))) + 4.0
        us2 = np.cumsum(rng.normal(0, 0.05, len(self.days))) + 4.0
        us10 = np.cumsum(rng.normal(0, 0.05, len(self.days))) + 4.5
        au2 = [0.8 * bill[i] + 0.3 * (us2[i - 1] if i else us2[0]) for i in range(len(self.days))]
        au10 = [0.5 * bill[i] + 0.6 * (us10[i - 1] if i else us10[0]) for i in range(len(self.days))]
        self.truth = {"NOM2Y": dict(zip(self.days, au2)), "NOM10": dict(zip(self.days, au10))}
        self.cut = len(self.days) - 5                                       # la RBA llega hasta aquí
        d = os.path.join(self.root, "data")
        write_ohlcv(os.path.join(d, "AUD_BILL_6M.csv"), zip(self.days, bill))
        write_ohlcv(os.path.join(d, "US_BILL_2Y.csv"), zip(self.days, us2))
        write_ry(os.path.join(d, "RY_G8_USD.csv"), zip(self.days, us10))
        write_dv(os.path.join(d, "AUD_NOM_2Y.csv"), list(zip(self.days, au2))[:self.cut])
        write_ry(os.path.join(d, "RY_G8_AUD.csv"), list(zip(self.days, au10))[:self.cut])

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def rows(self):
        with open(os.path.join(self.root, "data", "AUD_NOWCAST.csv")) as fh:
            return list(csv.DictReader(fh))

    def test_N2_exact_world(self):
        meta = N.run(self.root, today=self.days[-1] + timedelta(days=1))
        self.assertEqual(meta["status"], "OK")
        self.assertAlmostEqual(meta["targets"]["NOM2Y"]["beta"]["AU_BILL_6M"], 0.8, places=3)
        self.assertAlmostEqual(meta["targets"]["NOM2Y"]["beta"]["US_2Y"], 0.3, places=3)
        self.assertAlmostEqual(meta["targets"]["NOM10"]["beta"]["US_10Y"], 0.6, places=3)
        rows = self.rows()
        self.assertEqual(len(rows), 5)                                     # 5 días hábiles posteriores a la base
        for r in rows:
            d = date(int(r["DATE"][:4]), int(r["DATE"][4:6]), int(r["DATE"][6:]))
            self.assertAlmostEqual(float(r["NOM2Y"]), self.truth["NOM2Y"][d], places=3)
            self.assertAlmostEqual(float(r["NOM10"]), self.truth["NOM10"][d], places=3)
            self.assertEqual(r["MODEL"], "EST_AUD_V1")
        self.assertEqual([r["H_BD"] for r in rows], ["1", "2", "3", "4", "5"])

    def test_N3_us_driver_lag(self):
        s = [(date(2026, 9, 28), 1.0), (date(2026, 9, 29), 2.0), (date(2026, 9, 30), 3.0)]
        self.assertEqual(N.driver_value(s, 1, date(2026, 9, 30)), 2.0)   # día hábil anterior
        self.assertEqual(N.driver_value(s, 0, date(2026, 9, 30)), 3.0)

    def test_N4_missing_driver(self):
        N.run(self.root, today=self.days[-1] + timedelta(days=1))
        before = self.rows()
        os.remove(os.path.join(self.root, "data", "US_BILL_2Y.csv"))
        meta = N.run(self.root, today=self.days[-1] + timedelta(days=1))
        self.assertEqual(meta["status"], "INPUT_MISSING")
        self.assertTrue(any("US_2Y" in w for w in meta["warnings"]))
        self.assertEqual([r["DATE"] for r in self.rows()], [r["DATE"] for r in before])   # nada se borra

    def test_N5_history_only_grows(self):
        N.run(self.root, today=self.days[-1] + timedelta(days=1))
        first = {r["DATE"]: r for r in self.rows()}
        d = os.path.join(self.root, "data")                                # llega la RBA con 2 días más
        write_dv(os.path.join(d, "AUD_NOM_2Y.csv"), list(self.truth["NOM2Y"].items())[:self.cut + 2])
        write_ry(os.path.join(d, "RY_G8_AUD.csv"), list(self.truth["NOM10"].items())[:self.cut + 2])
        N.run(self.root, today=self.days[-1] + timedelta(days=1))
        second = {r["DATE"]: r for r in self.rows()}
        self.assertTrue(set(first) <= set(second))
        old = sorted(first)[:2]
        for k in old:                                                      # ya cubiertas por la RBA: quedan como registro
            self.assertEqual(second[k]["BASE_DATE"], first[k]["BASE_DATE"])
        for k in sorted(second)[2:]:
            self.assertNotEqual(second[k]["BASE_DATE"], first[k]["BASE_DATE"])


class Spec(unittest.TestCase):
    def test_N1_spec_lives_in_config(self):
        with open(os.path.join(ROOT, "scripts", "aud_nowcast.py"), encoding="utf-8") as fh:
            src = fh.read()
        for token in ("AUD_BILL_6M.csv", "US_BILL_2Y.csv", "RY_G8_USD.csv", "0.8954", "0.5406"):
            self.assertNotIn(token, src, token)
        cfg = load_json(os.path.join(ROOT, "sources", "nowcast_aud.json"))
        self.assertEqual(cfg["model"], "EST_AUD_V1")
        self.assertEqual({d["name"] for d in cfg["drivers"]["NOM10"]}, {"AU_BILL_6M", "US_10Y"})


class RealData(unittest.TestCase):
    def test_N6_beats_naive_out_of_sample(self):
        cfg = load_json(os.path.join(ROOT, "sources", "nowcast_aud.json"))
        data = os.path.join(ROOT, "data")
        for t in N.TARGETS:
            tc = cfg["targets"][t]
            target = N.read_series(os.path.join(data, tc["file"]), tc["col"])
            drv = [(N.read_series(os.path.join(data, d["file"]), d["col"]), d["lag_bd"]) for d in cfg["drivers"][t]]
            if len(target) < 800 or any(not s for s, _ in drv):
                self.skipTest("datos del repo insuficientes")
            split = target[-260][0]                                       # último año fuera de muestra
            fit = N.calibrate([x for x in target if x[0] < split], drv, cfg["calibration_years"], cfg["min_obs"])
            test = [x for x in target if x[0] >= split]
            err_m, err_0 = [], []
            for i in range(0, len(test) - 4, 5):                          # anclas semanales, horizonte 3 días
                base = test[i]
                sub = [x for x in target if x[0] <= base[0]]
                rows, _ = N.nowcast(sub, [(s, l) for s, l in drv], fit, 3)
                if len(rows) < 3:
                    continue
                actual = dict(test)[rows[2][0]] if rows[2][0] in dict(test) else None
                if actual is None:
                    continue
                err_m.append(rows[2][1] - actual)
                err_0.append(base[1] - actual)
            rm, r0 = np.sqrt(np.mean(np.square(err_m))), np.sqrt(np.mean(np.square(err_0)))
            self.assertGreater(len(err_m), 30, t)
            self.assertLess(rm, 0.9 * r0, "%s: estimación %.1f pb vs repetir %.1f pb" % (t, rm * 100, r0 * 100))


class S01bIntegration(unittest.TestCase):
    def test_N7_context_est_and_probe(self):
        import s01b
        cal = bdays(date(2026, 7, 1), 70)
        t, pos = cal[-1], len(cal) - 1
        acm = [(d, 1.0 + 0.001 * i, 3.0, 4.0 + 0.001 * i, "ACM_K3_380m") for i, d in enumerate(cal)]
        off = [(d, 4.0 + 0.001 * i) for i, d in enumerate(cal[:-5])]       # RBA: hasta 5 días antes de t
        ext = [(d, 4.2) for d in cal[-5:]]
        r = [0.0] * len(cal)
        f = [1.0] * len(cal)
        est = {"nominal": [d for d, _ in ext], "y2": [d for d, _ in ext]}
        row, _ = s01b.evaluate_ccy("AUD", t, pos, cal, acm, off + ext, off + ext, [], r, f, t, {"initialized": True}, est=est)
        self.assertIn("CTX_EST", row["flags"])
        self.assertEqual(row["context_last"]["nominal"]["quality"], "EST_AUD_V1")
        self.assertTrue(row["context_last"]["y2"]["estimate"])
        self.assertIsNotNone(row["d_nom"])
        self.assertEqual(row["acm_input_asof_t"], cal[-6].isoformat())   # sonda: último dato OFICIAL
        self.assertIn("ACM_FFILL", row["flags"])
        base, _ = s01b.evaluate_ccy("AUD", t, pos, cal, acm, off, off, [], r, f, t, {"initialized": True})
        for k in ("avail", "signal", "persist", "d_tp", "d_rny", "theta"):
            self.assertEqual(row.get(k), base.get(k), k)                 # detector intacto


class Consumers(unittest.TestCase):
    def test_N8_labels_workflow_and_gate(self):
        with open(os.path.join(ROOT, "scripts", "dashboard_alerts.py"), encoding="utf-8") as fh:
            self.assertIn('"NOM EST %s', fh.read())
        with open(os.path.join(ROOT, "docs", "index.html"), encoding="utf-8") as fh:
            h = fh.read()
        self.assertIn("RAW + 'AUD_NOWCAST.csv'", h)
        self.assertIn("cl.estimate", h)
        self.assertIn("AUD_NOWCAST: repo('AUD_NOWCAST.csv'", h)
        if yaml:
            with open(os.path.join(ROOT, ".github", "workflows", "daily_update.yml"), encoding="utf-8") as fh:
                wf = yaml.safe_load(fh)
            steps = wf["jobs"][list(wf["jobs"])[0]]["steps"]
            names = [s.get("name", "") for s in steps]
            i = next(k for k, n in enumerate(names) if n.startswith("AUD nowcast"))
            self.assertLess(i, next(k for k, n in enumerate(names) if n.startswith("S01B")))
            self.assertLess(i, next(k for k, n in enumerate(names) if n.startswith("Dashboard alerts")))
            self.assertIn("g8step.py --name aud_nowcast", steps[i]["run"])
        import agent_gate as G
        self.assertEqual(G.classify(["scripts/aud_nowcast.py"])["decision"], "AUTO")
        self.assertEqual(G.classify(["sources/nowcast_aud.json"])["decision"], "OWNER")


if __name__ == "__main__":
    unittest.main()

"""FXVOL-SHADOW v1.0 — vectores de 5 sesiones, casos ROLL/FLAT/INTERP/NA, idempotencia y tiempo.

Los vectores son la salida de la referencia sobre canonical a 10-nov-2026.
Si una comparación falla, el diff se imprime y el test se detiene: no se recalibra nada.
"""
import csv
import json
import os
import re
import sys
import time
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import cme_fxvol_shadow as FX  # noqa: E402

VECTORS = os.path.join(ROOT, "tests", "fixtures", "fxvol", "FXVOL_test_vectors.json")
SESSIONS = ["2026-08-17", "2026-09-15", "2026-10-30", "2026-11-02", "2026-11-06"]
CCYS = ["EUR", "JPY", "GBP", "AUD", "CAD", "CHF"]
JSON_PATH = os.path.join(ROOT, "data", "CME_FXVOL_shadow.json")
CSV_PATH = os.path.join(ROOT, "data", "options", "fxvol_history.csv")

TOL_KEYS = {
    "iv30_atm": 0.01,
    "rr25": 0.01,
    "bf25": 0.01,
    "iv_pct": 0.1,
    "rr_pct": 0.1,
    "d_oi_pct": 0.01,
    "px_chg_pct": 0.001,
}


def _tol(path):
    parent = path[-2] if len(path) >= 2 else ""
    if parent in ("d_iv", "d_rr"):
        return 0.01
    if path and path[-1] == "rr25_d5":
        return 0.01  # FLUJOS-01: misma tolerancia que d_rr (es ese cambio a 5 sesiones)
    return TOL_KEYS.get(path[-1] if path else "")


def _walk(exp, got, path, diffs, devs):
    if isinstance(exp, dict):
        if not isinstance(got, dict):
            diffs.append("%s tipo %r != %r" % (".".join(path), type(exp).__name__, type(got).__name__))
            return
        extra = sorted(set(got) - set(exp))
        missing = sorted(set(exp) - set(got))
        if extra or missing:
            diffs.append("%s claves extra=%s faltan=%s" % (".".join(path) or "<root>", extra, missing))
        for key in exp:
            if key in got:
                _walk(exp[key], got[key], path + (key,), diffs, devs)
        return
    if isinstance(exp, list):
        if exp != got:
            diffs.append("%s lista %r != %r" % (".".join(path), exp, got))
        return
    if isinstance(exp, bool) or isinstance(got, bool):
        if exp != got:
            diffs.append("%s %r != %r" % (".".join(path), exp, got))
        return
    if isinstance(exp, (int, float)) and isinstance(got, (int, float)):
        tol = _tol(path)
        if tol is None:
            if exp != got:
                diffs.append("%s %r != %r" % (".".join(path), exp, got))
            return
        dev = abs(float(exp) - float(got))
        devs.append(dev)
        if dev > tol + 1e-12:
            diffs.append("%s %r != %r (desv %s > %s)" % (".".join(path), exp, got, dev, tol))
        return
    if exp != got:
        diffs.append("%s %r != %r" % (".".join(path), exp, got))


def _load_vectors():
    with open(VECTORS, encoding="utf-8") as fh:
        doc = json.load(fh)
    return {s: doc[s] for s in SESSIONS}


class FxvolShadow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vectors = _load_vectors()
        cls.results = {}
        cls.max_dev = {}
        for session in SESSIONS:
            out, _, _ = FX.build(ROOT, session)
            diffs = []
            devs = []
            _walk(cls.vectors[session], out, (), diffs, devs)
            cls.results[session] = (out, diffs)
            cls.max_dev[session] = max(devs) if devs else 0.0
            print("FXVOL %s max_dev=%.6g diffs=%d" % (session, cls.max_dev[session], len(diffs)))

    def test_five_sessions_within_tolerance(self):
        failed = []
        for session in SESSIONS:
            _, diffs = self.results[session]
            if diffs:
                failed.append(session)
                print("DIFF %s" % session)
                for line in diffs[:40]:
                    print("  " + line)
        self.assertEqual(failed, [], "sesiones fuera de vector: %s" % failed)

    def test_roll_flat_interp_and_nzd(self):
        roll = self.results["2026-09-15"][0]
        for ccy in CCYS:
            fut = roll["currencies"][ccy]["fut"]
            self.assertEqual(fut["1d"]["quadrant"], "ROLL", ccy)
            self.assertEqual(fut["1w"]["quadrant"], "ROLL", ccy)
        flat = self.results["2026-11-02"][0]
        for ccy in CCYS:
            self.assertEqual(flat["currencies"][ccy]["mode"], "FLAT", ccy)
        edge = self.results["2026-10-30"][0]
        for ccy in CCYS:
            cur = edge["currencies"][ccy]
            self.assertEqual(cur["mode"], "INTERP", ccy)
            self.assertEqual(cur["dte1"], 7, ccy)
        for session in SESSIONS:
            nzd = self.results[session][0]["currencies"]["NZD"]
            self.assertEqual(nzd, {
                "status": "NA", "reason": "NO_CME_OPTIONS", "gate0": "NA",
                "iv30_pct252": "UNAVAILABLE",
                "rr25_d5": "UNAVAILABLE",
                "rv21": "UNAVAILABLE",
                "vrp": "UNAVAILABLE",
                "vol_regime": "UNAVAILABLE",
            })

    def test_additive_fields_do_not_recompute_existing_ones(self):
        for session in SESSIONS:
            out = self.results[session][0]
            for ccy in CCYS:
                cur = out["currencies"][ccy]
                self.assertEqual(cur["rr25_d5"], cur["d_rr"]["1w"], "%s %s" % (session, ccy))
                self.assertLess(cur["hist_n"], 252, "%s %s" % (session, ccy))
                self.assertEqual(cur["iv30_pct252"], "NA", "%s %s" % (session, ccy))
                self.assertEqual(cur["iv30_pct252_days"], cur["hist_n"], "%s %s" % (session, ccy))
                # FLUJOS-01-PR2 autorizado Sander 12-nov-2026 08:57
                exp = self.vectors[session]["currencies"][ccy]
                for key in ("iv30_atm", "rv21", "vrp", "vol_regime"):
                    self.assertEqual(cur[key], exp[key], "%s %s %s" % (session, ccy, key))
                if cur["rv21"] != "NA":
                    self.assertEqual(cur["vrp"], round(cur["iv30_atm"] - cur["rv21"], 2), "%s %s" % (session, ccy))

    def test_pct252_needs_252_observations(self):
        short, days = FX.iv30_pct252_field([1.0, 2.0, 3.0], 3.0)
        self.assertEqual((short, days), ("NA", 3))
        series = [float(i) for i in range(252)]
        pct, days = FX.iv30_pct252_field(series, series[-1])
        self.assertIsNone(days)
        self.assertEqual(pct, 100.0)
        self.assertEqual(pct, FX.pct_rank(series, series[-1]))
        older = [float(i) for i in range(300)]
        pct2, days2 = FX.iv30_pct252_field(older, 251.0)
        self.assertIsNone(days2)
        # solo las últimas 252 (48..299): valores <= 251 son 48..251 → 204/252
        self.assertEqual(pct2, round(100.0 * 204 / 252.0, 1))

    def test_idempotent_and_under_60s(self):
        t0 = time.perf_counter()
        self.assertEqual(FX.main([]), 0)
        elapsed = time.perf_counter() - t0
        print("FXVOL runtime_s=%.3f" % elapsed)
        self.assertLess(elapsed, 60.0, "ejecución completa %.3f s" % elapsed)
        with open(JSON_PATH, encoding="utf-8") as fh:
            first = json.load(fh)
        with open(CSV_PATH, "rb") as fh:
            csv_a = fh.read()
        self.assertEqual(FX.main([]), 0)
        with open(JSON_PATH, encoding="utf-8") as fh:
            second = json.load(fh)
        with open(CSV_PATH, "rb") as fh:
            csv_b = fh.read()
        self.assertRegex(first.pop("generated_utc"), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertRegex(second.pop("generated_utc"), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(first, second)
        self.assertEqual(csv_a, csv_b)
        self.assertTrue(csv_a.endswith(b"\n"))
        text = csv_a.decode("utf-8")
        rows = list(csv.reader(text.splitlines()))
        self.assertEqual(rows[0], FX.CSV_COLUMNS)
        self.assertEqual(len(rows) - 1, first["sessions_total"] * 6)
        # F1 y px_settle salen tal cual del CSV canónico (no re-redondeados).
        eur = next(r for r in rows[1:] if r[0] == first["session"] and r[1] == "EUR")
        self.assertRegex(eur[4], r"^-?\d+\.\d{4}$")
        with open(os.path.join(ROOT, "data", "options", "canonical", first["session"], "6E.csv"), newline="") as fh:
            futs = list(csv.DictReader(fh))
        top = max((x for x in futs if x["oi"] and x["settle"]), key=lambda x: int(x["oi"]))
        self.assertEqual(eur[13], next(x["settle"] for x in futs if x["symbol"] == eur[12]))
        oi_sum = sum(int(x["oi"]) for x in futs if x["oi"])
        self.assertEqual(eur[16], str(oi_sum))
        self.assertEqual(eur[17], top["symbol"])
        self.assertEqual(eur[18], top["settle"])

    def test_missing_session_does_not_write(self):
        before = {}
        for path in (JSON_PATH, CSV_PATH):
            if os.path.exists(path):
                with open(path, "rb") as fh:
                    before[path] = fh.read()
            else:
                before[path] = None
        self.assertEqual(FX.main(["--session", "1900-01-01"]), 1)
        for path in (JSON_PATH, CSV_PATH):
            if os.path.exists(path):
                with open(path, "rb") as fh:
                    after = fh.read()
            else:
                after = None
            self.assertEqual(after, before[path])

    def test_workflow_and_dashboard_markers(self):
        with open(os.path.join(ROOT, ".github", "workflows", "daily_update.yml"), encoding="utf-8") as fh:
            yml = fh.read()
        c2 = yml.index("name: Shadow CME options C2")
        fx = yml.index("name: Shadow CME FXVOL (IV30 / RR25 / BF25 / OI futuros)")
        nxt = yml.index("name: Shadow CHF nowcast")
        self.assertLess(c2, fx)
        self.assertLess(fx, nxt)
        self.assertNotIn("\n      - name:", yml[c2:fx])
        self.assertIn(
            "python scripts/tools/g8step.py --name cme_fxvol_shadow --phase post --cap 40 --min 5 -- python scripts/cme_fxvol_shadow.py",
            yml,
        )
        commit = yml.split("data/options/shadow_history.json", 1)[1]
        self.assertTrue(commit.startswith(" \\\n            data/CME_FXVOL_shadow.json \\\n            data/options/fxvol_history.csv \\"))
        with open(os.path.join(ROOT, "docs", "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn('id="opt-fxvol"', html)
        self.assertIn("IV30-G8 — Section 06b module · v1 (SHADOW)", html)
        self.assertIn("CME_FXVOL: Object.assign(", html)
        self.assertIn("IV30-G8 · VOL IMPLÍCITA CME · SKEW 25Δ · OI FUTUROS", html)
        self.assertIn("<th>Pct 252</th>", html)
        self.assertIn("<th>ΔRR 5d</th>", html)
        self.assertIn("<th>RV21</th>", html)
        self.assertIn("<th>VRP</th>", html)
        self.assertIn("<th>Régimen</th>", html)
        surface = html.index('id="opt-surface"')
        fxdiv = html.index('id="opt-fxvol"')
        footer = html.index('id="section-options"')
        # el div nuevo está dentro de §06, después de opt-surface
        self.assertLess(footer, surface)
        self.assertLess(surface, fxdiv)
        self.assertLess(fxdiv, html.index("</section>", fxdiv))
        mod = html.index("IV30-G8 — Section 06b module · v1 (SHADOW)")
        old = html.index("OPTIONS SURFACE — Section 06 module · v2")
        brief = html.index("00 — BRIEF module")
        self.assertLess(old, mod)
        self.assertLess(mod, brief)
        # El dato no se llama CVOL. Solo queda la frase obligatoria que lo distingue.
        self.assertIn("No es el CVOL oficial", html)
        script = html[mod:brief].replace("No es el CVOL oficial", "")
        self.assertNotRegex(script, re.compile(r"\bCVOL\b"))


if __name__ == "__main__":
    unittest.main()

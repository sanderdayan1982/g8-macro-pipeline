"""Acta P-9 · auditoría 6-oct-2026: nada más viejo que mensual, ACM al día, agente que se repara solo.

F1 CHF: sin respaldo congelado (2025-07) en §01/§04/§00; el respaldo mensual OCDE solo si es del mes actual o anterior
F2 §00: un TP con más de 35 días (o el fichero CHF sin QUALITY) sale «TP NO DISPONIBLE»
F3 metales: FEDFUNDS (mensual) retirado; respaldo = EFFR diario del NY Fed (fuente primaria)
F4 §05 sin la fila muerta USD_TP_MIRROR
A1 la pasada intradía LATE recalcula ACM (8 divisas) y la estimación AUD después de sus entradas
G1 compuerta: arreglo del dashboard AUTO si no toca funciones de cálculo; OWNER si las toca o toca health.js; exige acta
R1 relanzamientos: lista cerrada, inputs válidos, motivo obligatorio, sin duplicados, máximo 3, no relanza lo ocupado
V1 la prueba de renderizado en Chromium forma parte de validate_smoke.sh (Validate y compuerta)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "scripts", "tools"))
import agent_gate as G  # noqa: E402
import agent_recovery as R  # noqa: E402


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


class Freshness(unittest.TestCase):
    def test_F1_no_frozen_chf(self):
        h = read("docs/index.html")
        self.assertNotIn("data.CHF.asOf = '2025-07 · SNB frozen'", h)
        self.assertNotIn("data.CHF.frozen = true", h)
        self.assertIn("monthsBehind(raw.chfFred[raw.chfFred.length - 1].d) <= 1", h)
        self.assertIn("delete data.CHF;", h)
        dl = read("docs/js/data-loader.js")
        self.assertNotIn("SNB cube frozen 2025-07)'; out[key].label = 'CHF ACM 10Y TP (FROZEN)'", dl)
        self.assertIn("(Date.now() - last.getTime()) / 864e5 > 35", dl)

    def test_F2_brief_tp_older_than_a_month_is_unavailable(self):
        import dashboard_alerts as A
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        shutil.copytree(os.path.join(ROOT, "data"), os.path.join(tmp, "data"),
                        ignore=shutil.ignore_patterns("options", "futures", "_ingest", "s01b"))
        with open(os.path.join(tmp, "data", "ACM_G8_CHF.csv"), "w") as fh:     # fichero congelado: sin QUALITY
            fh.write("DATE,Y10_FIT,RNY10,TP10\n20250728,0.40,0.30,0.10\n20250731,0.41,0.30,0.11\n")
        with patch.object(A, "DATA", os.path.join(tmp, "data")):
            book = A.build_book({})
        chf = [r for r in book if r["ccy"] == "CHF"][0]
        self.assertIsNone(chf.get("tp"))
        self.assertTrue(any(f.startswith("TP NO DISPONIBLE") for f in chf["flags"]), chf["flags"])
        self.assertFalse(any("FROZEN" in f for f in chf["flags"]))

    def test_F3_metals_daily_effr_fallback(self):
        src = read("scripts/metals_fairvalue_g8.py")
        self.assertNotIn('"FEDFUNDS"', src)
        self.assertNotIn("'FEDFUNDS'", read("scripts/metals_provenance.py"))
        import metals_fairvalue_g8 as M
        payload = json.dumps({"refRates": [{"effectiveDate": "2006-01-%02d" % (i % 28 + 1), "percentRate": 4.0}
                                           for i in range(10)] +
                                          [{"effectiveDate": str(d.date()), "percentRate": 3.88}
                                           for d in __import__("pandas").bdate_range("2024-01-01", periods=600)]})
        with patch.object(M, "_http_get", return_value=payload):
            s = M.fetch_nyfed_effr()
        self.assertGreater(len(s), 500)
        self.assertEqual(M.MP.FREQUENCIES["NYFED_EFFR"], "daily")

    def test_F4_dead_mirror_row_removed(self):
        self.assertNotIn("USD_TP_MIRROR: {", read("docs/index.html"))


class IntradayAcm(unittest.TestCase):
    def test_A1_late_pass_runs_acm_after_inputs(self):
        import refresh_feeds as RF
        late = [j[0] for j in RF.GROUPS["LATE"]]
        self.assertLess(late.index("real_yields"), late.index("acm_g8"))
        self.assertLess(late.index("acm_g8"), late.index("aud_nowcast"))
        acm = [j for j in RF.GROUPS["LATE"] if j[0] == "acm_g8"][0]
        self.assertEqual(acm[2:], ("USD", "EUR", "JPY", "GBP", "CAD", "AUD"))
        self.assertIn(("acm_nzd", "acm_g8.py", "NZD"), RF.GROUPS["LATE"])
        self.assertIn(("acm_chf", "acm_g8.py", "CHF"), RF.GROUPS["LATE"])


@unittest.skipUnless(shutil.which("git"), "sin git")
class GateDashboard(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.git("init", "-q")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        os.makedirs(os.path.join(self.d, "docs", "js"))
        os.makedirs(os.path.join(self.d, "docs", "actas"))
        self.html = ("<script>\nfunction zScore(series, win) {\n  var w = series.slice(-win);\n  return w[0];\n}\n"
                     "function cell(v) { return '<td>' + v + '</td>'; }\n</script>\n")
        self.write("docs/index.html", self.html)
        self.write("docs/js/health.js", "function x(){ return 1; }\n")
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD").strip()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def git(self, *a):
        return subprocess.run(["git"] + list(a), cwd=self.d, capture_output=True, text=True, check=True).stdout

    def write(self, rel, text):
        with open(os.path.join(self.d, rel), "w") as fh:
            fh.write(text)

    def gate(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "tools", "agent_gate.py"), "--base", self.base],
                           cwd=self.d, capture_output=True, text=True)
        return r.returncode, json.loads(r.stdout)

    def commit(self, msg="c"):
        self.git("add", ".")
        self.git("commit", "-qm", msg)

    def test_G1_presentation_fix_is_auto_with_acta(self):
        self.write("docs/index.html", self.html.replace("'<td>' + v + '</td>'", "'<td class=\"n\">' + v + '</td>'"))
        self.commit()
        rc, out = self.gate()
        self.assertEqual(rc, 3)                                             # sin acta → OWNER
        self.assertIn("dashboard repair needs an acta", out["reasons"])
        self.write("docs/actas/ACTA_AGENTE_20261006.md", "# arreglo\n")
        self.commit()
        rc, out = self.gate()
        self.assertEqual((rc, out["decision"]), (0, "AUTO"), out)

    def test_G1_protected_calculation_goes_to_owner(self):
        self.write("docs/index.html", self.html.replace("return w[0];", "return w[1];"))
        self.write("docs/actas/ACTA_AGENTE_20261006.md", "# arreglo\n")
        self.commit()
        rc, out = self.gate()
        self.assertEqual(rc, 3)
        self.assertTrue(any("protected calculation changed" in x and "zScore" in x for x in out["reasons"]), out)

    def test_G1_health_js_is_owner(self):
        self.assertEqual(G.classify(["docs/js/health.js"])["decision"], "OWNER")
        self.assertEqual(G.classify(["docs/js/charts.js"])["decision"], "AUTO")


class Recovery(unittest.TestCase):
    def test_R1_validation(self):
        ok, rej = R.validate([
            {"workflow": "cme_options.yml", "reason": "push rechazado"},
            {"workflow": "intraday_fetch.yml", "inputs": {"group": "ASIA"}, "reason": "RBA tarde"},
            {"workflow": "maintenance_agent.yml", "reason": "x"},                # fuera de lista
            {"workflow": "intraday_fetch.yml", "inputs": {"group": "MARS"}, "reason": "x"},
            {"workflow": "daily_update.yml"},                                   # sin motivo
            {"workflow": "cme_options.yml", "reason": "otra vez"},               # duplicado
            {"workflow": "daily_update.yml", "reason": "ok"},
            {"workflow": "usd_factor.yml", "reason": "cuarto"},                 # sobre el límite
        ])
        self.assertEqual([x["workflow"] for x in ok], ["cme_options.yml", "intraday_fetch.yml", "daily_update.yml"])
        whys = [r["why"] for r in rej]
        self.assertIn("workflow fuera de la lista de recuperación", whys)
        self.assertIn("valor de input no permitido", whys)
        self.assertIn("falta el motivo (evidencia)", whys)
        self.assertIn("duplicado", whys)
        self.assertIn("límite de 3 relanzamientos por pasada", whys)

    def test_R1_dispatch_skips_busy_and_builds_command(self):
        calls = []

        class P:
            def __init__(self, out="", rc=0):
                self.stdout, self.returncode = out, rc

        def fake(cmd, **kw):
            calls.append(cmd)
            if cmd[:3] == ["gh", "run", "list"]:
                return P(json.dumps([{"status": "in_progress"}]) if "cme_options.yml" in cmd else "[]")
            return P()
        ok, _ = R.validate([{"workflow": "cme_options.yml", "reason": "a"},
                            {"workflow": "intraday_fetch.yml", "inputs": {"group": "LATE"}, "reason": "b"}])
        done = R.dispatch(ok, run=fake)
        self.assertEqual([d["result"] for d in done], ["SKIPPED_BUSY", "DISPATCHED"])
        self.assertIn(["gh", "workflow", "run", "intraday_fetch.yml", "--ref", "main", "-f", "group=LATE"], calls)


class Render(unittest.TestCase):
    def test_V1_render_check_in_shared_smoke(self):
        sm = read("scripts/tools/validate_smoke.sh")
        self.assertIn("node scripts/tools/dashboard_render_check.cjs", sm)
        self.assertTrue(os.path.exists(os.path.join(ROOT, "scripts", "tools", "dashboard_render_check.cjs")))
        wf = read(".github/workflows/maintenance_agent.yml")
        self.assertIn("scripts/tools/agent_recovery.py .agent/recovery.json", wf)
        self.assertIn(".agent/recovery.json", wf.split("upload-artifact")[1][:400])


if __name__ == "__main__":
    unittest.main()

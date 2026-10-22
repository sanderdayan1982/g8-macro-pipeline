"""test_u3_audit_fixes.py — Pruebas de regresión de la auditoría integral G8:
1. Intocabilidad de motores cuantitativos y funciones JS protegidas (PROTECTED_JS).
2. Respaldo oficial BIS WS_CBPOL bulk ZIP en fetch_bis_ocr.py (con verificación de solapamiento >= 20 obs).
3. Optimización de carga en charts.js (omisíón de secciones .legacy-hidden) y ruta G8NET en data-loader.js.
4. Coherencia de display-gate [REVIEW] entre §04/§05 (metals.js) y §00 (dashboard_alerts.py / brief.json).
5. Diagnóstico priorizado (1º Diaria -> 2º Semanal -> 3º Mensual) y autorrecuperación en agent_doctor.py + agent_recovery.py.
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

import agent_doctor as AD  # noqa: E402
import agent_gate as AG  # noqa: E402
import agent_recovery as AR  # noqa: E402
import dashboard_alerts as DA  # noqa: E402
import fetch_bis_ocr as FBO  # noqa: E402


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


class AuditFixesTest(unittest.TestCase):
    def test_1_engines_and_protected_js_untouched(self):
        import ast
        import hashlib
        frozen_files = [
            "scripts/acm_g8.py",
            "scripts/s01b.py",
            "scripts/usd_factor.py",
            "scripts/book_risk.py",
            "scripts/nzd_tp_synth.py",
            "scripts/real_yields_g8.py",
            "scripts/aud_nowcast.py",
            "scripts/kalman_core.py",
            "sources/nowcast_aud.json",
        ]
        expected_metals_ast = {
            "asof_weekly": "710c0541c78e4157480bebdcd5bdb2ffbaff899c30d7cd12d91dbf2ce5f7ee0e",
            "load_slope": "684844895cc4be3ae286437e888d6a1f8d1b01e5644aafb3ddf2d1dd5482b1d6",
            "forward_returns": "8befbd22911e00b29c1d954d8c7e07531b31701fa8362d41b3a90ee29b1d7ea8",
            "evaluate_delta": "2157aae522bd200d83e2242ec3716f0fea0702d701d3000221cbefe897ad47ed",
            "calibrate_delta": "f32662c7168d26e05b9c7b3d4fa8f4a861f0938703fa862856b9359d850892ee",
            "cot_activation": "54498f07ed1a6fa30e4660d418613230d9623809ac6b7085025c887cf7061055",
            "build_panel_xau": "830932632b9ab6073e69d640cbb97ab77233e5533508ee607671e308c5329de4",
            "build_panel_xag": "e9efa74214b7bb87aebe8710e657bfb840fd7bbd5513e20319d55f767dbc6ef6",
            "build_metal": "79e8ded371c865421e9ac3ad7973440195bd51e7c9a460e833a3488dac5ed8c4",
        }
        metals_src = read("scripts/metals_fairvalue_g8.py")
        metals_tree = ast.parse(metals_src)
        actual_metals_ast = {
            n.name: hashlib.sha256(ast.get_source_segment(metals_src, n).encode("utf-8")).hexdigest()
            for n in metals_tree.body
            if isinstance(n, ast.FunctionDef) and n.name in expected_metals_ast
        }
        self.assertEqual(actual_metals_ast, expected_metals_ast)
        if shutil.which("git"):
            r = subprocess.run(
                ["git", "diff", "--name-only", "HEAD", "--"] + frozen_files,
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(r.stdout.strip(), "", "Motor o fichero congelado modificado: %s" % r.stdout)

            for js_file, names in AG.PROTECTED_JS.items():
                base_src = subprocess.run(
                    ["git", "show", "HEAD:%s" % js_file],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout
                cur_src = read(js_file)
                base_fns = AG.js_functions(base_src, names)
                cur_fns = AG.js_functions(cur_src, names)
                for fn in names:
                    self.assertEqual(
                        base_fns.get(fn),
                        cur_fns.get(fn),
                        "Función de cálculo protegida %s modificada en %s" % (fn, js_file),
                    )

    def test_2_fetch_bis_ocr_uses_bulk_fallback_when_api_fails(self):
        src = read("scripts/fetch_bis_ocr.py")
        self.assertIn("from g8common import bis_bulk as _bulk", src)
        self.assertIn("_bulk.verify_overlap(backup, published)", src)

    def test_3_frontend_performance_and_escaping(self):
        html = read("docs/index.html")
        self.assertIn('<html lang="es">', html)
        self.assertNotIn("xlsx.full.min.js", html)
        self.assertEqual(html.count(".attr-q-synth{"), 1)
        self.assertIn("function escAttr(s)", html)
        self.assertIn(">IPC a/a</span>", html)
        self.assertNotIn("(§09)", html)

        charts = read("docs/js/charts.js")
        self.assertIn("function isVisibleSection(id)", charts)
        self.assertIn("const wantLegacy =", charts)

        dl = read("docs/js/data-loader.js")
        self.assertIn("global.G8NET.fetchText(`data/${filename}`)", dl)

    def test_4_dashboard_alerts_metals_display_gate_review(self):
        breaches = DA.metals_display_breaches({
            "var_ratio": 1.38,
            "halflife": 195.0,
            "ic_8w": 0.04,
            "hit_8w": 0.49,
        })
        self.assertEqual(len(breaches), 4)

        clean = DA.metals_display_breaches({
            "var_ratio": 0.65,
            "halflife": 52.0,
            "ic_8w": 0.18,
            "hit_8w": 0.58,
        })
        self.assertEqual(clean, [])

        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        shutil.copytree(
            os.path.join(ROOT, "data"),
            os.path.join(tmp, "data"),
            ignore=shutil.ignore_patterns("options", "futures", "_ingest", "s01b"),
        )
        with patch.object(DA, "DATA", os.path.join(tmp, "data")):
            st = {}
            lines = []
            DA.check_metals(st, lines)
            self.assertEqual(st["metals"]["XAU"]["quality"], "REVIEW")
            self.assertEqual(st["metals"]["XAG"]["quality"], "REVIEW")
            self.assertEqual(st["metals"]["XAU"]["display_quality"], "REVIEW")
            self.assertEqual(st["metals"]["XAG"]["display_quality"], "REVIEW")
            self.assertTrue(len(st["metals"]["XAU"]["gate_breaches"]) >= 1)

        state = json.loads(read("data/MFV_G8_state.json"))
        self.assertEqual(state["metals"]["XAU"]["quality"], "REVIEW")
        self.assertEqual(state["metals"]["XAG"]["quality"], "REVIEW")
        self.assertTrue(read("data/MFV_G8_XAU.csv").startswith("# QUALITY=REVIEW |"))
        self.assertTrue(read("data/MFV_G8_XAG.csv").startswith("# QUALITY=REVIEW |"))
        brief = json.loads(read("data/alerts/brief.json"))
        metals_bullets = [b["text"] for b in brief.get("bullets", []) if "Metales MDP" in b.get("sec", "")]
        self.assertEqual(len(metals_bullets), 1)
        self.assertIn("[REVIEW]", metals_bullets[0])

    def test_5_agent_doctor_priority_and_recovery(self):
        self.assertIn("feed_recovery.yml", AR.ALLOWED)
        ok, rej = AR.validate([{"workflow": "feed_recovery.yml", "reason": "NZD_OCR fallo puntual API BIS"}])
        self.assertEqual(len(ok), 1)
        self.assertEqual(rej, [])

        diag = AD.collect_diagnosis()
        self.assertIn("priority_counts", diag)
        self.assertIn("incidents", diag)
        ranks = [inc["priority_rank"] for inc in diag["incidents"]]
        self.assertEqual(ranks, sorted(ranks), "Las incidencias deben estar ordenadas 1º Diaria -> 2º Semanal -> 3º Mensual")

        md = AD.format_markdown(diag)
        self.assertIn("1º DIARIA", md)
        tg = AD.format_telegram_summary(diag)
        self.assertTrue(
            tg.startswith("⚡ Frescura G8")
            or tg.startswith("✓ Monitor de frescura")
            or tg.startswith("⚡ <b>Frescura G8</b>")
            or tg.startswith("✓ <b>Monitor de frescura G8</b>")
        )

    def test_6_agent_schedule_and_netlify_economy(self):
        from datetime import date
        import watch_cadence as WC
        self.assertEqual(WC.ACTIVE_WEEKDAYS, (0, 2, 4))
        self.assertTrue(WC.active(date(2026, 10, 12)))   # Lunes
        self.assertFalse(WC.active(date(2026, 10, 13)))  # Martes
        self.assertTrue(WC.active(date(2026, 10, 14)))   # Miércoles
        self.assertFalse(WC.active(date(2026, 10, 15)))  # Jueves
        self.assertTrue(WC.active(date(2026, 10, 16)))   # Viernes
        self.assertFalse(WC.active(date(2026, 10, 17)))  # Sábado
        self.assertFalse(WC.active(date(2026, 10, 18)))  # Domingo
        self.assertTrue(WC.active(date(2026, 10, 19)))   # Lunes siguiente

        maint_wf = read(".github/workflows/maintenance_agent.yml")
        self.assertIn("cron: '17 17 * * 1,3,5'", maint_wf)
        self.assertIn("--max-turns 30", maint_wf)

        watch_wf = read(".github/workflows/ingest_watch.yml")
        self.assertIn("cron: '13 17 * * 1,3,5'", watch_wf)

        netlify = read("netlify.toml")
        self.assertIn("':!docs/actas' ':!docs/*.md'", netlify)
        self.assertIn('for = "/proxy/*"', netlify)
        self.assertIn("max-age=3600", netlify)

        health_js = read("docs/js/health.js")
        self.assertIn("age > 80", health_js)
        self.assertIn("function pollIfVisible()", health_js)
        self.assertIn("if (document.hidden) return;", health_js)


if __name__ == "__main__":
    unittest.main()

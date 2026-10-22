"""U2 — los seis hallazgos del dictamen sobre 6c387bea dejan de ser solo cadenas, y la web enseña
las sombras diarias B2 (metales) y A3 (§01-b) sin tocar los ficheros congelados.

briefSec, b2Line y las celdas A3 se ejecutan con node sobre los JSON reales; la distancia JPY se
recalcula; las sesiones se comparan; y los siete hashes siguen.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import date

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
HTML = os.path.join(ROOT, "docs", "index.html")
FROZEN = [
    ("scripts/cme_options_collector.py", "61276d17636d921913e383127b47176e946adc5eab59420416f4f3a244a477f9"),
    ("scripts/build_options_summary.py", "f0fdf61d1047d3d899b5037f3047dc194329b4a4d5de42b59bed1346e304e019"),
    (".github/workflows/cme_options.yml", "281dfed01663e6147cd32fdde02c1bc88688ccdee0f6a54789128205e0fab6ab"),
    ("scripts/dashboard_alerts.py", "0c0aba148a00b8cb7dd950f651f28e708605e0bcc720cd353bb9397370874322"),
    ("scripts/s01b.py", "0c43d6dd54677974bf4ffde880de049396f4b0d9cdd1c6e39964d796ef132ee5"),
    ("scripts/usd_factor.py", "d96bbaa366637a5d1238eac2af1e03096561ff63d92fc3bc246919bad804d3d3"),
    ("scripts/book_risk.py", "78514c2daa319d08454a99dae8225f7c2208da1fcd34cab13eb2547d72ef9d50"),
]


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _brief_sec_source(html):
    match = re.search(r"function briefSec\(sec, numbering\) \{\n(?:.*\n)*?\}", html)
    if not match:
        raise AssertionError("briefSec no está en docs/index.html")
    return match.group(0)


def _run_brief_sec(html, secs, numbering=None):
    src = _brief_sec_source(html)
    program = (
        src
        + "\nconst secs = "
        + json.dumps(secs)
        + ";\nprocess.stdout.write(JSON.stringify(secs.map(s => briefSec(s, " + json.dumps(numbering) + "))));\n"
    )
    out = subprocess.check_output(["node", "-e", program], text=True)
    return json.loads(out)


def _registry_sections(html):
    block = re.search(r"var FEED_REGISTRY = \{.*?\n\};", html, re.S)
    if not block:
        raise AssertionError("FEED_REGISTRY no está")
    found = {}
    for file_name, section in re.findall(
        r"repo\(\s*'([^']+)'[\s\S]*?,\s*\d+\s*,\s*'([^']+)'\s*\)",
        block.group(0),
    ):
        found[file_name] = section
    return found


class BriefSec(unittest.TestCase):
    def test_real_bullets_drop_section_09_and_keep_the_visible_numbers(self):
        html = _read(HTML)
        brief = json.loads(_read(os.path.join(ROOT, "data", "alerts", "brief.json")))
        secs = [b["sec"] for b in brief["bullets"]]
        painted = _run_brief_sec(html, secs, brief.get('section_numbering'))
        legacy = ['§09 COT', '§04 Term premium', '§06/§07 Metales MDP', '§10 Factor USD',
                  '§02 Floor spreads', '§01-b CTF']
        by_src = dict(zip(legacy, _run_brief_sec(html, legacy)))
        self.assertEqual(by_src["§09 COT"], "COT · fuera de la página")
        self.assertEqual(by_src["§04 Term premium"], "§03 Term premium")
        self.assertEqual(by_src["§06/§07 Metales MDP"], "§04/§05 Metales MDP")
        self.assertEqual(by_src["§10 Factor USD"], "§07 Factor USD")
        self.assertEqual(by_src["§02 Floor spreads"], "§02 Floor spreads")
        self.assertEqual(by_src["§01-b CTF"], "§01-b CTF")
        for text in painted:
            for stale in ("§08", "§09", "§10"):
                self.assertNotIn(stale, text, text)
        self.assertNotIn("§09", " ".join(painted))
        self.assertEqual(painted, secs if brief.get('section_numbering') == '00-07' else _run_brief_sec(html, secs))


class OptionsInversion(unittest.TestCase):
    def test_jpy_shadow_inverts_once_onto_the_user_put(self):
        html = _read(HTML)
        shadow = json.loads(_read(os.path.join(ROOT, "data", "CME_OPTIONS_C1_shadow.json")))
        jpy = shadow["currencies"]["JPY"]
        k = jpy["call_wall"]["k"]
        ref = jpy["ref"]
        self.assertGreater(k, ref)
        native = jpy["call_wall"]["dist_pct"]
        self.assertEqual(native, round((k - ref) / ref * 100, 2))
        inverted = (1 / k - 1 / ref) / (1 / ref) * 100
        self.assertLess(inverted, 0)
        self.assertNotEqual(round(inverted, 2), round(native, 2))
        self.assertIn("call: swap ? block.put_wall : block.call_wall", html)
        self.assertIn("put: swap ? block.call_wall : block.put_wall", html)
        dist = re.search(r"function fmtC2Dist\(ccy, wall, ref\) \{[\s\S]*?\n\}", html).group(0)
        self.assertIn("(uk - ur) / ur * 100", dist)
        self.assertIn("CME (XXX/USD). En este JSON los niveles y dist_pct son nativos", shadow["convention"])
        self.assertIn("No volver a invertir.", shadow["convention"])

    def test_section_06_marks_shadow_and_refuses_a_different_session(self):
        html = _read(HTML)
        self.assertIn("\">C2 · sesión ' + shadow.session + ' (CME_OPTIONS_C1_shadow.json)</span>", html)
        self.assertIn("String(shadow.session) === String(doc.latest_session)", html)
        self.assertIn("SHADOW omitida", html)


class FloorsAndRegistry(unittest.TestCase):
    def test_section_02_prints_as_of_and_drops_a_stale_shadow(self):
        html = _read(HTML)
        self.assertIn("CB_EVENT_48H_shadow.json · as-of ", html)
        self.assertIn("function shadowAgeOk", html)
        self.assertIn("sombra atrasada", html)
        self.assertIn("CB_EVENT_48H (0..2 d.h. del calendario del banco)", html)
        self.assertIn("useShadow ? shadow.bd : bdBetween", html)

    def test_feed_registry_uses_the_visible_numbers(self):
        sections = _registry_sections(_read(HTML))
        self.assertEqual(sections["GB_POLICY.csv"], "02")
        self.assertEqual(sections["JP_POLICY.csv"], "02")
        self.assertEqual(sections["ACM_TP_10Y.csv"], "03")
        self.assertEqual(sections["ACM_G8_NZD.csv"], "03")
        self.assertEqual(sections["ACM_G8_CHF.csv"], "03")
        self.assertEqual(sections["MFV_G8_XAU.csv"], "04")
        self.assertEqual(sections["MFV_G8_XAG.csv"], "05")
        self.assertEqual(sections["OPTIONS_SURFACE.json"], "06")
        self.assertEqual(sections["USD_FACTOR.json"], "07")
        self.assertEqual(sections["pos_g8_cot.json"], "00")
        self.assertFalse(set(sections.values()) & {"08", "09", "10"})

    def test_section_06_session_matches_on_the_real_files(self):
        shadow = json.loads(_read(os.path.join(ROOT, "data", "CME_OPTIONS_C1_shadow.json")))
        surface = json.loads(_read(os.path.join(ROOT, "data", "OPTIONS_SURFACE.json")))
        self.assertEqual(str(shadow["session"]), str(surface["latest_session"]))

    def test_seven_frozen_hashes_stay(self):
        for rel, expected in FROZEN:
            with open(os.path.join(ROOT, rel), "rb") as fh:
                self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), expected, rel)


def _block(html, head, indent=""):
    match = re.search(re.escape(head) + r"[\s\S]*?\n" + indent + r"\}", html)
    if not match:
        raise AssertionError(head)
    return match.group(0)


def _node(program):
    return json.loads(subprocess.check_output(["node", "-e", program], text=True))


class MetalsDailyB2(unittest.TestCase):
    def test_b2_line_reads_the_real_shadow_state(self):
        html = _read(HTML)
        doc = json.loads(_read(os.path.join(ROOT, "data", "MFV_G8_shadow_state.json")))
        self.assertEqual(doc["mode"], "SHADOW")
        src = _block(html, "function b2Line(m) {")
        out = _node(src + "\nconst doc = " + json.dumps(doc) +
                    ";\nprocess.stdout.write(JSON.stringify({XAU: b2Line(doc.metals.XAU), XAG: b2Line(doc.metals.XAG)}));")
        for metal in ("XAU", "XAG"):
            m = doc["metals"][metal]
            z = "%+.2f" % m["mdp_z"]
            self.assertEqual(out[metal], "DIARIO (B2): MDP-Z %s · precio/regresores %s · ancla COT %s · β %s"
                             % (z, m["mdp_as_of"], m["cot_as_of"], m["beta_state"]))

    def test_b2_is_loaded_apart_and_the_weekly_headline_stays(self):
        html = _read(HTML)
        self.assertIn("getText(RAW + 'MFV_G8_shadow_state.json')", html)
        self.assertIn("getText(RAW + 'MFV_G8_state.json')", html)
        self.assertIn('id="mfv-b2-xau"', html)
        self.assertIn('id="mfv-b2-xag"', html)
        refresh = _block(html, "function refreshMetals() {")
        self.assertLess(refresh.index("renderDailyB2()"), refresh.index("renderMetal('XAU'"))
        self.assertNotIn("await renderDailyB2", html)


class S01BShadowA3(unittest.TestCase):
    def _cells(self, a3, as_of):
        html = _read(HTML)
        same = re.search(r"function a3SameSession\(j\) \{.*\}", html).group(0)
        cell = _block(html, "function a3Cell(c) {", " " * 16)
        helpers = ("function nz(x) { return x != null && isFinite(x); }\n"
                   "function esc(s) { return String(s == null ? '' : s); }\n")
        program = (helpers + "var a3Shadow = " + json.dumps(a3) + ";\n" + same + "\n" + cell +
                   "\nconst j = {as_of: " + json.dumps(as_of) + "};\nconst ok = a3SameSession(j);\n"
                   "const rows = {}; ['USD','EUR','GBP','JPY','AUD','NZD','CAD','CHF'].forEach(function (c) { rows[c] = a3Cell(c); });\n"
                   "process.stdout.write(JSON.stringify({ok: ok, rows: rows}));")
        return _node(program)

    def test_a3_rows_on_the_real_files(self):
        a3 = json.loads(_read(os.path.join(ROOT, "data", "S01B_shadow_a3.json")))
        s01b = json.loads(_read(os.path.join(ROOT, "data", "S01B.json")))
        self.assertEqual(a3["mode"], "SHADOW")
        self.assertEqual(a3["t"], s01b["as_of"])
        out = self._cells(a3, s01b["as_of"])
        self.assertTrue(out["ok"])
        self.assertEqual(out["rows"]["NZD"], "A3 · EXCLUIDA_B5")
        eur = a3["currencies"]["EUR"]
        self.assertEqual(out["rows"]["EUR"], "A3 · local: %+.1f pb (β %.2f) · ρ22: %+.2f"
                         % (eur["dtp22_local"], eur["beta_usd"], eur["rho_tp_fx_22"]))
        for c in ("EUR", "GBP", "JPY", "AUD", "CAD", "CHF"):
            self.assertIn("local: ", out["rows"][c])
            self.assertIn("ρ22: ", out["rows"][c])

    def test_a3_is_dropped_when_the_session_differs(self):
        a3 = json.loads(_read(os.path.join(ROOT, "data", "S01B_shadow_a3.json")))
        out = self._cells(a3, "1999-01-05")
        self.assertFalse(out["ok"])
        html = _read(HTML)
        self.assertIn("(a3ok ? a3Row(c) : '')", html)
        self.assertIn("A3 SHADOW omitida", html)

    def test_writer_session_is_the_last_target_day_of_the_canonical(self):
        import s01b_shadow_a3 as A3
        s01b = json.loads(_read(os.path.join(ROOT, "data", "S01B.json")))
        self.assertEqual(A3.last_session(os.path.join(ROOT, "data")), date.fromisoformat(s01b["as_of"]))
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp))
        dest = os.path.join(tmp, A3.SHADOW_NAME)
        A3.write_shadow({"mode": "SHADOW", "t": "2026-10-16"}, dest)
        self.assertEqual(json.loads(_read(dest))["t"], "2026-10-16")
        self.assertFalse(os.path.exists(dest + ".tmp"))

    def test_daily_job_writes_and_stages_a3(self):
        wf = _read(os.path.join(ROOT, ".github", "workflows", "daily_update.yml"))
        step = wf[wf.index("- name: Shadow S01B A3"):]
        step = step[:step.index("\n\n")]
        self.assertIn("continue-on-error: true", step)
        self.assertIn("--phase post --cap 40", step)
        self.assertIn("python scripts/s01b_shadow_a3.py", step)
        commit = wf[wf.index("- name: Commit updated data files"):]
        self.assertIn("data/S01B_shadow_a3.json", commit[:commit.index("git diff --staged")])


if __name__ == "__main__":
    unittest.main()

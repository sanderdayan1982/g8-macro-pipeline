"""U2 — los seis hallazgos del dictamen sobre 6c387bea dejan de ser solo cadenas.

briefSec se ejecuta con node, la distancia JPY se recalcula, y los siete hashes siguen.
"""
import hashlib
import json
import os
import re
import subprocess
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HTML = os.path.join(ROOT, "docs", "index.html")
FROZEN = [
    ("scripts/cme_options_collector.py", "61276d17636d921913e383127b47176e946adc5eab59420416f4f3a244a477f9"),
    ("scripts/build_options_summary.py", "f0fdf61d1047d3d899b5037f3047dc194329b4a4d5de42b59bed1346e304e019"),
    (".github/workflows/cme_options.yml", "281dfed01663e6147cd32fdde02c1bc88688ccdee0f6a54789128205e0fab6ab"),
    ("scripts/dashboard_alerts.py", "56e46654e7cc86b0be45e9d5d1792476169db3426d38d12aacb93d50056b5ebb"),
    ("scripts/s01b.py", "0c43d6dd54677974bf4ffde880de049396f4b0d9cdd1c6e39964d796ef132ee5"),
    ("scripts/usd_factor.py", "d96bbaa366637a5d1238eac2af1e03096561ff63d92fc3bc246919bad804d3d3"),
    ("scripts/book_risk.py", "78514c2daa319d08454a99dae8225f7c2208da1fcd34cab13eb2547d72ef9d50"),
]


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _brief_sec_source(html):
    match = re.search(r"function briefSec\(sec\) \{\n(?:.*\n)*?\}", html)
    if not match:
        raise AssertionError("briefSec no está en docs/index.html")
    return match.group(0)


def _run_brief_sec(html, secs):
    src = _brief_sec_source(html)
    program = (
        src
        + "\nconst secs = "
        + json.dumps(secs)
        + ";\nprocess.stdout.write(JSON.stringify(secs.map(briefSec)));\n"
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
        painted = _run_brief_sec(html, secs)
        by_src = dict(zip(secs, painted))
        self.assertEqual(by_src["§09 COT"], "COT · fuera de la página")
        self.assertEqual(by_src["§04 Term premium"], "§03 Term premium")
        self.assertEqual(by_src["§06/§07 Metales MDP"], "§04/§05 Metales MDP")
        self.assertEqual(by_src["§10 Factor USD"], "§07 Factor USD")
        self.assertEqual(by_src["§02 Floor spreads"], "§02 Floor spreads")
        self.assertEqual(by_src["§01-b CTF"], "§01-b CTF")
        for text in painted:
            for stale in ("§06", "§08", "§09", "§10"):
                self.assertNotIn(stale, text, text)
        self.assertNotIn("§09", " ".join(painted))


class OptionsInversion(unittest.TestCase):
    def test_jpy_shadow_inverts_once_onto_the_user_put(self):
        html = _read(HTML)
        shadow = json.loads(_read(os.path.join(ROOT, "data", "CME_OPTIONS_C1_shadow.json")))
        jpy = shadow["currencies"]["JPY"]
        k = jpy["call_wall"]["k"]
        ref = jpy["ref"]
        self.assertEqual(k, 0.0065)
        self.assertEqual(ref, 0.006353)
        native = jpy["call_wall"]["dist_pct"]
        self.assertEqual(native, 2.31)
        inverted = (1 / k - 1 / ref) / (1 / ref) * 100
        self.assertEqual(round(inverted, 2), -2.26)
        self.assertNotEqual(round(inverted, 2), round(native, 2))
        self.assertIn("call: swap ? block.put_wall : block.call_wall", html)
        self.assertIn("put: swap ? block.call_wall : block.put_wall", html)
        dist = re.search(r"function fmtC2Dist\(ccy, wall, ref\) \{[\s\S]*?\n\}", html).group(0)
        self.assertIn("(uk - ur) / ur * 100", dist)
        self.assertIn("CME (XXX/USD). En este JSON los niveles y dist_pct son nativos", shadow["convention"])
        self.assertIn("No volver a invertir.", shadow["convention"])

    def test_section_06_marks_shadow_and_refuses_a_different_session(self):
        html = _read(HTML)
        self.assertIn('title="CME_OPTIONS_C1_shadow.json · sesión \' + shadow.session + \'">SHADOW</span>', html)
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

    def test_seven_frozen_hashes_stay(self):
        for rel, expected in FROZEN:
            with open(os.path.join(ROOT, rel), "rb") as fh:
                self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), expected, rel)


if __name__ == "__main__":
    unittest.main()

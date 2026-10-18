"""U1 — numeración 00–07, sombras pintadas en la web y cron diario.

No toca los siete ficheros congelados de tests/test_exclusions.py.
"""
import hashlib
import json
import os
import re
import subprocess
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HTML = os.path.join(ROOT, "docs", "index.html")
YML = os.path.join(ROOT, ".github", "workflows", "daily_update.yml")

TITLES = [
    "00 — BRIEF — LECTURA DE 8 SEGUNDOS",
    "01 — LONG-END ATTRIBUTION MATRIX G8",
    "02 — MONEY MARKET FLOOR SPREADS · TIPOS OFICIALES G8",
    "03 — TERM PREMIUM ACM 10Y G8 (TIME SERIES)",
    "04 — GOLD FAIR VALUE / MDP (KALMAN TVP / MMT)",
    "05 — SILVER FAIR VALUE / MDP (KALMAN TVP / DUAL)",
    "06 — OPTIONS SURFACE · CME FX WALLS (GATE 0)",
    "07 — FACTOR USD · RESIDUALES · LIBRO (CONTEXT)",
]
IDS = (
    "section-brief", "section-attribution", "section-floors", "section-acm",
    "section-gold", "section-silver", "section-options", "section-factor",
)
FROZEN = [
    ("scripts/cme_options_collector.py", "61276d17636d921913e383127b47176e946adc5eab59420416f4f3a244a477f9"),
    ("scripts/build_options_summary.py", "f0fdf61d1047d3d899b5037f3047dc194329b4a4d5de42b59bed1346e304e019"),
    (".github/workflows/cme_options.yml", "281dfed01663e6147cd32fdde02c1bc88688ccdee0f6a54789128205e0fab6ab"),
    ("scripts/dashboard_alerts.py", "1d8d24fb0ebc6284cf66ebadbf4151e3ac567b368de947a27236eec8d784befe"),
    ("scripts/s01b.py", "0c43d6dd54677974bf4ffde880de049396f4b0d9cdd1c6e39964d796ef132ee5"),
    ("scripts/usd_factor.py", "d96bbaa366637a5d1238eac2af1e03096561ff63d92fc3bc246919bad804d3d3"),
    ("scripts/book_risk.py", "78514c2daa319d08454a99dae8225f7c2208da1fcd34cab13eb2547d72ef9d50"),
]
SHADOW_OUT = (
    "data/CB_EVENT_48H_shadow.json",
    "data/CME_OPTIONS_C1_shadow.json",
    "data/options/shadow_history.json",
    "data/CHF_NOWCAST_shadow.json",
    "data/CHF_SPOT_2Y_daily_shadow.csv",
    "data/CHF_SPOT_5Y_daily_shadow.csv",
)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _js_function(name):
    match = re.search(r"function " + re.escape(name) + r"\([^\n]*\) \{[\s\S]*?\n\}", _read(HTML))
    if not match:
        raise AssertionError("Missing dashboard function: " + name)
    return match.group(0)


def _node(program):
    return json.loads(subprocess.check_output(["node", "-e", program], text=True))


class U1DashboardIntegration(unittest.TestCase):
    def test_titles_are_consecutive_00_to_07(self):
        titles = re.findall(r'<h2 class="section-title">(.*?)</h2>', _read(HTML))
        self.assertEqual(titles, TITLES)
        self.assertEqual([t[:2] for t in titles], [f"{i:02d}" for i in range(8)])

    def test_nav_keys_and_kept_ids(self):
        html = _read(HTML)
        nav = re.search(r'<nav class="desk-nav" id="desk-nav".*?</nav>', html, re.S).group(0)
        self.assertEqual(re.findall(r'data-key="([^"]+)"', nav), list("01234567") + ["s"])
        self.assertIn('href="#publication-health" data-key="s"', nav)
        self.assertIn("teclas 0–7 · s", nav)
        for section_id in IDS:
            self.assertIn(f'id="{section_id}"', html)

    def test_page_reads_the_three_shadow_files(self):
        html = _read(HTML)
        for name in ("CB_EVENT_48H_shadow.json", "CHF_NOWCAST_shadow.json", "CME_OPTIONS_C1_shadow.json"):
            self.assertIn(name, html)
        self.assertIn("days_to_next_cb_meeting", html)
        self.assertIn("nowcast 2Y:", html)
        for token in ("dist_pct", "pct_oi_1d", "near_pin_05pct", "chip_15pct_strike",
                      "roll_window_7d", "map_status", "nearest_call_wall", "nearest_put_wall"):
            self.assertIn(token, html)
        self.assertIn("bdBetween", html)

    def test_brief_renumbers_visible_sections(self):
        labels = ["§09 COT", "§04 Term premium", "§06/§07 Metales MDP",
                  "§08 Opciones", "§10 Factor USD", "§02 Floor spreads", "§01-b CTF"]
        out = _node(_js_function("briefSec") + "\nprocess.stdout.write(JSON.stringify(" +
                    json.dumps(labels) + ".map(briefSec)));")
        self.assertEqual(out, ["COT · fuera de la página", "§03 Term premium", "§04/§05 Metales MDP",
                               "§06 Opciones", "§07 Factor USD", "§02 Floor spreads", "§01-b CTF"])

    def test_jpy_distance_and_side_execute_the_display_functions(self):
        program = "var INV = {JPY: true, CAD: true, CHF: true};\n"
        program += _js_function("userWalls") + "\n" + _js_function("fmtC2Dist")
        program += """
const native = {call_wall: {k: 0.0065, dist_pct: 2.31}, put_wall: {k: 0.006, dist_pct: -5.56}};
const user = userWalls('JPY', native);
process.stdout.write(JSON.stringify({
    put: fmtC2Dist('JPY', user.put, 0.006353),
    putIsNativeCall: user.put === native.call_wall,
    callIsNativePut: user.call === native.put_wall,
    native: fmtC2Dist('EUR', native.call_wall, 0.006353)
}));
"""
        self.assertEqual(_node(program), {"put": "-2.26%", "putIsNativeCall": True,
                                          "callIsNativePut": True, "native": "+2.31%"})

    def test_cb_shadow_age_with_a_fixed_clock(self):
        program = """
const RealDate = Date;
var Date = class extends RealDate {
    constructor(...args) { super(...(args.length ? args : ['2026-03-03T12:00:00Z'])); }
    static now() { return new RealDate('2026-03-03T12:00:00Z').getTime(); }
};
""" + _js_function("shadowAgeOk")
        program += "\nprocess.stdout.write(JSON.stringify(" + json.dumps(
            ["2026-03-03", "2026-03-02", "2026-03-01", "2026-03-04", "2026-02-30", "bad", ""]
        ) + ".map(as_of => shadowAgeOk({as_of}))));"
        self.assertEqual(_node(program), [True, True, False, False, False, False, False])

    def test_missing_daily_mdp_is_not_a_zero_signal(self):
        out = _node(_js_function("b2Line") + "\nprocess.stdout.write(JSON.stringify(" +
                    "[null, '', 0, 1.25].map(mdp_z => b2Line({mdp_z}))));")
        self.assertIn("MDP-Z —", out[0])
        self.assertIn("MDP-Z —", out[1])
        self.assertIn("MDP-Z +0.00", out[2])
        self.assertIn("MDP-Z +1.25", out[3])

    def test_policy_cells_recompute_days_when_shadow_expires(self):
        program = """
const RealDate = Date;
var Date = class extends RealDate {
    constructor(...args) { super(...(args.length ? args : ['2026-10-17T22:00:00Z'])); }
    static now() { return new RealDate('2026-10-17T22:00:00Z').getTime(); }
};
var CB_OF = {JPY: 'JP'};
var _cbNext = {JP: {date: '2026-10-30', source: 'official', note: 'next decision'}};
var _cbShadow = {as_of: '2026-10-01', currencies: {JPY: {days_to_next_cb_meeting: 99, CB_EVENT_48H: true}}};
"""
        program += "\n".join(_js_function(n) for n in ('bdBetween', 'shadowAgeOk', 'shadowDays', 'policyCells'))
        program += """
const stale = policyCells({ccy: 'JPY'}, null);
_cbShadow.as_of = '2026-10-17';
const current = policyCells({ccy: 'JPY'}, null);
process.stdout.write(JSON.stringify({stale, current}));
"""
        out = _node(program)
        self.assertIn("as-of 2026-10-01", out['stale'])
        self.assertIn("sombra atrasada", out['stale'])
        self.assertIn("10 d.h.", out['stale'])
        self.assertNotIn("99 d.h.", out['stale'])
        self.assertIn("as-of 2026-10-17", out['current'])
        self.assertIn("99 d.h.", out['current'])

    def test_daily_update_runs_shadows_and_stages_outputs(self):
        yml = _read(YML)
        for cmd in ("python scripts/cb_event_shadow.py", "python scripts/cme_options_shadow.py",
                    "python scripts/chf_nowcast_shadow.py"):
            self.assertIn(cmd, yml)
        commit = yml.split("Commit updated data files", 1)[1]
        self.assertIn("git add data/", commit)
        for name in SHADOW_OUT:
            self.assertIn(name, commit)

    def test_seven_frozen_hashes_stay(self):
        for rel, expected in FROZEN:
            with open(os.path.join(ROOT, rel), "rb") as fh:
                self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), expected, rel)


if __name__ == "__main__":
    unittest.main()

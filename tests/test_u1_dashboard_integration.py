"""U1 — numeración 00–07, sombras pintadas en la web y cron diario.

No toca los siete ficheros congelados de tests/test_exclusions.py.
"""
import hashlib
import os
import re
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
    ("scripts/dashboard_alerts.py", "56e46654e7cc86b0be45e9d5d1792476169db3426d38d12aacb93d50056b5ebb"),
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
        html = _read(HTML)
        self.assertIn("§06/§07", html)
        self.assertIn("§04/§05", html)
        self.assertIn("function briefSec", html)

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

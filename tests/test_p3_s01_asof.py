"""P-3 — §01: fecha real del dato + marca de atraso (AUD: RBA F2 semanal) y calendario G8 sin
desfase de zona horaria. Las funciones reales de docs/index.html se ejecutan en Node con
TZ=Africa/Malabo (Bata, UTC+1) y con TZ=UTC; la fecha de hoy se fija en el arnés."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HTML = os.path.join(ROOT, "docs", "index.html")
NODE = shutil.which("node")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def func(src, name):
    """Source of a top-level `function name(...) {...}` (brace-balanced)."""
    i = src.index("function " + name + "(")
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        depth += {"{": 1, "}": -1}.get(src[k], 0)
        if depth == 0:
            return src[i:k + 1]
    raise AssertionError(name)


def block(src, start, end):
    i = src.index(start)
    return src[i:src.index(end, i) + len(end)]


HARNESS = r"""
const RealDate = Date;
const FIXED = new RealDate(process.env.TODAY + 'T12:00:00').getTime();   // local noon
global.Date = class extends RealDate {
  constructor(...a) { if (a.length) super(...a); else super(FIXED); }
  static now() { return FIXED; }
};
global.window = {};
eval(require('fs').readFileSync(process.env.SRC, 'utf8'));
window.G8DQM = { businessDays: calBusinessDays, computeStaleness: computeStaleness };
global.G8DQM = window.G8DQM;
const C = JSON.parse(process.env.CASE);
const out = {};
out.bd = C.bd.map(d => calBusinessDays(d));
out.lag = C.lag.map(x => lagTag(x[0], x[1], x[2]));
out.asof = C.asof.map(d => asOfLine(d));
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node no disponible")
class S01AsOf(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        html = read(HTML)
        parts = [
            block(html, "var G8_HOLIDAYS = {", "};"),
            func(html, "isG8Holiday"), func(html, "localDay"), func(html, "calBusinessDays"), func(html, "computeStaleness"),
            block(html, "var PUB_NOTE = {", "};"),
            func(html, "nextDow"), func(html, "lagTag"), func(html, "asOfLine"),
        ]
        cls.tmp = tempfile.mkdtemp()
        cls.src = os.path.join(cls.tmp, "s01.js")
        with open(cls.src, "w", encoding="utf-8") as f:
            f.write("\n".join(parts))
        cls.h = os.path.join(cls.tmp, "h.js")
        with open(cls.h, "w", encoding="utf-8") as f:
            f.write(HARNESS)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_js(self, tz, today, bd=(), lag=(), asof=()):
        case = {"bd": list(bd), "lag": list(lag), "asof": list(asof)}
        env = dict(os.environ, TZ=tz, TODAY=today, SRC=self.src, CASE=json.dumps(case))
        p = subprocess.run([NODE, self.h], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def test_business_days_same_in_bata_and_utc(self):
        # 23-sep (JP holiday) → jue 1-oct: 24, 25, 28, 29, 30, 1 = 6 días hábiles
        for tz in ("Africa/Malabo", "UTC", "America/New_York"):
            o = self.run_js(tz, "2026-10-01", bd=["2026-09-23", "20260923", "2026-09-18", "2026-09-30", "2026-10-01"])
            self.assertEqual(o["bd"], [6, 6, 6, 1, 0], tz)

    def test_aud_weekly_lag_mark_with_publication_note(self):
        o = self.run_js("Africa/Malabo", "2026-10-01", lag=[["AUD", "2026-09-23", 8]])
        tag = o["lag"][0]
        self.assertIn("⧗ 09-23 · 6d", tag)
        self.assertIn("attr-z-md", tag)                  # dentro de presupuesto: ámbar, no rojo
        self.assertIn("viernes", tag)
        self.assertIn("próxima publicación prevista 2026-10-02", tag)

    def test_beyond_budget_turns_red_with_status(self):
        o = self.run_js("UTC", "2026-10-01", lag=[["GBP", "2026-09-18", 4]])
        self.assertIn("attr-z-hi", o["lag"][0])
        self.assertIn("DEGRADED", o["lag"][0])

    def test_fresh_leg_has_no_mark(self):
        o = self.run_js("UTC", "2026-10-01", lag=[["EUR", "2026-10-01", 4], ["USD", "2026-09-30", 4]])
        self.assertEqual(o["lag"], ["", ""])

    def test_asof_line_shows_market_date_and_flags_acm_ahead(self):
        o = self.run_js("UTC", "2026-10-01", asof=[
            {"nomAsOf": "2026-09-23", "acmAsOf": "2026-09-30"},
            {"nomAsOf": "2026-10-01", "acmAsOf": "2026-09-30"},
            {"nomAsOf": "2026-09-29", "acmAsOf": "2026-09-29"},
        ])
        aud, eur, usd = o["asof"]
        self.assertTrue(aud.startswith("dato 2026-09-23"))
        self.assertIn("ACM 09-30 ⚠ curva 09-23", aud)
        self.assertEqual(eur, "dato 2026-10-01 · ACM 09-30")
        self.assertEqual(usd, "dato 2026-09-29")


class S01Static(unittest.TestCase):
    def test_build_uses_lag_tag_and_asof_line(self):
        html = read(HTML)
        self.assertIn("lagTag(c, d.nomAsOf, d.lagMax)", html)
        self.assertIn("asOfLine(d)", html)
        self.assertIn("businessDays: calBusinessDays", html)
        self.assertNotIn("G8_HOLIDAYS[dt.toISOString()", html)


if __name__ == "__main__":
    unittest.main()

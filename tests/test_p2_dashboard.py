"""P-2 — dashboard: ruta Netlify propia /proxy/raw/* como respaldo de raw.githubusercontent.com
(tras el directo, antes de los proxies públicos); §00 por G8NET.fetchChain; sin red → última copia
en caché con su hora. La capa G8NET real de docs/index.html se ejecuta en Node con fetch y
localStorage simulados."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HTML = os.path.join(ROOT, "docs", "index.html")
REDIRECTS = os.path.join(ROOT, "docs", "_redirects")
RAW = "https://raw.githubusercontent.com/sanderdayan1982/g8-macro-pipeline/main/data/alerts/brief.json"
NODE = shutil.which("node")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def g8net_source():
    html = read(HTML)
    m = re.search(r"<script>\s*/\*[^*]*?═+\s*G8NET — shared network layer.*?</script>", html, re.S)
    assert m, "bloque G8NET no encontrado"
    return m.group(0)[len("<script>"):-len("</script>")]


HARNESS = r"""
const SCEN = JSON.parse(process.env.SCEN);
const store = {};
global.localStorage = {
  getItem: k => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
  removeItem: k => { delete store[k]; },
  key: i => Object.keys(store)[i],
  get length() { return Object.keys(store).length; },
};
global.window = { location: { protocol: SCEN.protocol || 'https:' } };
global.performance = { now: () => 0 };
const calls = [];
global.fetch = async (u) => {
  calls.push(u);
  const hit = (SCEN.ok || []).find(p => u.indexOf(p) === 0);
  if (!hit) throw new Error('net down');
  return { ok: true, status: 200, text: async () => SCEN.body, arrayBuffer: async () => new ArrayBuffer(0) };
};
for (const [k, v] of Object.entries(SCEN.seed || {})) store['g8net_' + k] = JSON.stringify(v);
eval(require('fs').readFileSync(process.env.SRC, 'utf8'));
(async () => {
  const r = await window.G8NET.fetchChain(SCEN.urls, SCEN.opts);
  process.stdout.write(JSON.stringify({ r, calls, store }));
})();
"""


@unittest.skipUnless(NODE, "node no disponible")
class G8NetP2(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.src = os.path.join(cls.tmp, "g8net.js")
        with open(cls.src, "w", encoding="utf-8") as f:
            f.write(g8net_source())
        cls.harness = os.path.join(cls.tmp, "h.js")
        with open(cls.harness, "w", encoding="utf-8") as f:
            f.write(HARNESS)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_js(self, **scen):
        scen.setdefault("urls", [RAW])
        scen.setdefault("opts", {"corsOk": True})
        scen.setdefault("body", '{"generated_utc":"2026-10-01T21:30:00Z","pad":"' + "x" * 40 + '"}')
        env = dict(os.environ, SCEN=json.dumps(scen), SRC=self.src)
        out = subprocess.run([NODE, self.harness], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_order_direct_then_netlify_raw_then_public(self):
        o = self.run_js(ok=[])
        self.assertFalse(o["r"]["ok"])
        c = o["calls"]
        self.assertEqual(c[0], RAW)
        self.assertEqual(c[1], "/proxy/raw/sanderdayan1982/g8-macro-pipeline/main/data/alerts/brief.json")
        self.assertTrue(all(x.startswith("https://") and "raw.githubusercontent.com" not in x.split("?")[0] for x in c[2:]))
        self.assertEqual(len(c), 5)   # directo + netlify-raw + 3 públicos

    def test_netlify_raw_wins_when_direct_fails(self):
        o = self.run_js(ok=["/proxy/raw/"])
        self.assertTrue(o["r"]["ok"])
        self.assertEqual(o["r"]["via"], "netlify-raw")
        self.assertEqual(len(o["calls"]), 2)
        self.assertFalse(o["r"].get("stale"))

    def test_no_backup_route_on_file_protocol(self):
        o = self.run_js(ok=[], protocol="file:")
        self.assertFalse(any(x.startswith("/proxy/") for x in o["calls"]))

    def test_other_hosts_unchanged(self):
        o = self.run_js(ok=[], urls=["https://data.snb.ch/api/x.csv"], opts={})
        self.assertEqual(o["calls"][0], "/proxy/snb/api/x.csv")
        self.assertFalse(any("/proxy/raw/" in x for x in o["calls"]))

    def test_offline_returns_old_cache_with_time_only_if_staleOk(self):
        seed = {RAW: {"ts": 1759300000000, "text": '{"generated_utc":"2026-09-30T21:30:00Z"}', "via": "direct"}}
        o = self.run_js(ok=[], seed=seed, opts={"corsOk": True, "noCache": True, "staleOk": True})
        r = o["r"]
        self.assertTrue(r["ok"])
        self.assertTrue(r["stale"])
        self.assertEqual(r["cachedTs"], 1759300000000)
        self.assertIn("sin red", r["via"])
        self.assertTrue(r["error"])
        # sin staleOk: fallo ruidoso, la copia antigua no se usa
        o2 = self.run_js(ok=[], seed=seed, opts={"corsOk": True})
        self.assertFalse(o2["r"]["ok"])

    def test_offline_without_cache_fails_loud(self):
        o = self.run_js(ok=[], opts={"corsOk": True, "noCache": True, "staleOk": True})
        self.assertFalse(o["r"]["ok"])

    def test_noCache_goes_live_and_refreshes_cache(self):
        seed = {RAW: {"ts": 4102444800000, "text": '{"old":1,"pad":"' + "y" * 40 + '"}', "via": "direct"}}
        o = self.run_js(ok=[RAW], seed=seed, opts={"corsOk": True, "noCache": True, "staleOk": True})
        self.assertFalse(o["r"].get("cached"))
        self.assertIn("generated_utc", json.loads(o["store"]["g8net_" + RAW])["text"])

    def test_stale_never_beats_live_later_tier(self):
        alt = "https://example.org/brief.json"
        seed = {RAW: {"ts": 1, "text": '{"old":1,"pad":"' + "y" * 40 + '"}', "via": "direct"}}
        o = self.run_js(ok=[alt], seed=seed, urls=[RAW, alt], opts={"corsOk": True, "noCache": True, "staleOk": True})
        self.assertFalse(o["r"].get("stale"))
        self.assertEqual(o["r"]["tier"], 1)


class StaticP2(unittest.TestCase):
    def test_redirect_route(self):
        lines = [l.split() for l in read(REDIRECTS).splitlines() if l.strip() and not l.startswith("#")]
        self.assertIn(["/proxy/raw/*", "https://raw.githubusercontent.com/:splat", "200"], lines)

    def test_brief_uses_fetchChain_not_single_fetch(self):
        html = read(HTML)
        m = re.search(r"00 — BRIEF module.*?</script>", html, re.S)
        block = m.group(0)
        self.assertIn("G8NET.fetchChain([RAW_B], { corsOk: true, noCache: true, staleOk: true })", block)
        self.assertNotIn("fetch(RAW_B", block)
        self.assertNotIn("AbortSignal.timeout(10000)", block)
        self.assertIn("SIN RED", block)


if __name__ == "__main__":
    unittest.main()

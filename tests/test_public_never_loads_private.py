"""La página pública nunca pide nada privado (orden Sander 20-nov t287u).

Complementa tests/test_private_panel_guard.py (sin modificarlo): lo que Netlify sirve
(docs/ sin actas ni .md, más docs/_redirects y netlify.toml) no puede referenciar
pdata/, el host del panel privado, Cloudflare Access ni el raw del repo privado.
"""
import os
import re
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PRIVATE = re.compile(
    r"pdata/"
    r"|g8-panel-privado"
    r"|\.pages\.dev"
    r"|cloudflareaccess\.com"
    r"|raw\.githubusercontent\.com/sanderdayan1982/g8-macro-data"
    r"|api\.github\.com/repos/sanderdayan1982/g8-macro-data",
    re.I,
)


def _served():
    out = []
    base = os.path.join(ROOT, "docs")
    for dp, dns, fns in os.walk(base):
        dns[:] = [d for d in dns if d != "actas"]
        for n in fns:
            if not n.endswith(".md"):
                out.append(os.path.join(dp, n))
    toml = os.path.join(ROOT, "netlify.toml")
    if os.path.exists(toml):
        out.append(toml)
    return out


class PublicNeverLoadsPrivate(unittest.TestCase):
    def test_served_files_have_no_private_reference(self):
        hits = []
        for p in _served():
            with open(p, encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    m = PRIVATE.search(line)
                    if m:
                        hits.append("%s:%d (%s)" % (os.path.relpath(p, ROOT), i, m.group(0)))
        self.assertEqual(hits, [], "referencia privada en lo que publica Netlify: %s" % "; ".join(hits))

    def test_detector_bites(self):
        for s in ("fetch('./pdata/X.json')", "https://g8-panel-privado.pages.dev/",
                  "https://x.cloudflareaccess.com/", "https://raw.githubusercontent.com/sanderdayan1982/g8-macro-data/main/a"):
            self.assertTrue(PRIVATE.search(s), s)
        for s in ("https://raw.githubusercontent.com/sanderdayan1982/g8-macro-pipeline/main/data/",
                  "public/CME_FXVOL_public.json", "plan g8-macro-data"):
            self.assertFalse(PRIVATE.search(s), s)


if __name__ == "__main__":
    unittest.main()

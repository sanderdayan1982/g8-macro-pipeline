"""La versión privada del dashboard no se construye ni se publica desde este repo.

orden Sander 19-nov t262u. La página pública (Netlify, docs/) sigue igual; la
privada se construye y se publica solo desde el repo privado. Este test falla si
aparece aquí cualquier pieza de esa construcción o publicación.
"""
import os
import re
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PRIVATE_REPO = "g8-macro-data"

# Piezas del build privado: nunca en el árbol público.
FORBIDDEN_FILES = (
    "panel/private_config.json",
    "panel/build_private_panel.py",
    "panel/book_panel.js",
    "wrangler.toml",
    "wrangler.json",
    "wrangler.jsonc",
    "docs/_headers",
    "docs/priv/book_panel.js",
)
FORBIDDEN_DIRS = ("docs/pdata", "docs/priv")

# Huellas de publicación en Cloudflare Pages.
DEPLOY = re.compile(r"wrangler|pages\s+deploy|pages-action|CLOUDFLARE_API_TOKEN|CLOUDFLARE_ACCOUNT_ID", re.I)
# Huellas de la versión construida dentro de la página publicada.
BUILT = re.compile(r"G8PRIV_LIBRO|\./pdata/|priv/book_panel\.js|SHOW_06B_EXT\s*=\s*true")
# Pasos de construcción/publicación.
BUILD_STEP = re.compile(r"build_private_panel|netlify\s+deploy|pages\s+deploy|wrangler|publish\s*[:=]", re.I)


def _read(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _workflows():
    d = os.path.join(ROOT, ".github", "workflows")
    if not os.path.isdir(d):
        return []
    return [os.path.join(d, n) for n in sorted(os.listdir(d)) if n.endswith((".yml", ".yaml"))]


def _site_files():
    """Ficheros que Netlify sirve: docs/ sin actas ni .md."""
    out = []
    base = os.path.join(ROOT, "docs")
    for dp, dns, fns in os.walk(base):
        dns[:] = [d for d in dns if d != "actas"]
        for n in fns:
            if not n.endswith(".md"):
                out.append(os.path.join(dp, n))
    return out


class PrivatePanelGuard(unittest.TestCase):
    def test_no_private_build_files(self):
        hits = [p for p in FORBIDDEN_FILES if os.path.exists(os.path.join(ROOT, p))]
        hits += [d + "/" for d in FORBIDDEN_DIRS if os.path.isdir(os.path.join(ROOT, d))]
        self.assertEqual(hits, [], "piezas del panel privado en el repo público: %s" % ", ".join(hits))

    def test_no_cloudflare_deploy_workflow(self):
        hits = [os.path.basename(p) for p in _workflows() if DEPLOY.search(_read(p))]
        self.assertEqual(hits, [], "workflow de publicación en Cloudflare en el repo público: %s" % ", ".join(hits))

    def test_private_repo_not_in_build_or_publish_step(self):
        hits = []
        for p in _workflows():
            text = _read(p)
            if PRIVATE_REPO in text and BUILD_STEP.search(text):
                hits.append(os.path.basename(p))
        toml = os.path.join(ROOT, "netlify.toml")
        if os.path.exists(toml):
            text = _read(toml)
            if PRIVATE_REPO in text or "build_private_panel" in text or DEPLOY.search(text):
                hits.append("netlify.toml")
        self.assertEqual(hits, [], "el repo privado aparece en un paso de build/publicación: %s" % ", ".join(hits))

    def test_published_site_is_not_the_private_build(self):
        hits = []
        for p in _site_files():
            if BUILT.search(_read(p)):
                hits.append(os.path.relpath(p, ROOT))
        self.assertEqual(hits, [], "la versión privada está en lo que publica Netlify: %s" % ", ".join(hits))

    def test_detectors_bite(self):
        # Los patrones detectan lo que dicen detectar (sin tocar el árbol).
        self.assertTrue(DEPLOY.search("uses: cloudflare/wrangler-action@v4"))
        self.assertTrue(DEPLOY.search("command: pages deploy site --project-name=x"))
        self.assertTrue(BUILT.search("fetchChain(['./pdata/X.json'])"))
        self.assertTrue(BUILT.search("var SHOW_06B_EXT = true;"))
        self.assertTrue(BUILD_STEP.search("python3 build_private_panel.py"))
        self.assertFalse(BUILT.search("var SHOW_06B_EXT = false;"))


if __name__ == "__main__":
    unittest.main()

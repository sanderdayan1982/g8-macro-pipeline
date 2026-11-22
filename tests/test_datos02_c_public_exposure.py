"""DATOS-02 C (t318u/t319u) — el árbol público no lleva futuros canónicos ni by_ccy.

Tests nuevos. No modifica tests existentes ni scripts/usd_factor.py.
"""
import copy
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts", "tools"))
import strip_usd_factor_public as S  # noqa: E402

SCRIPT = os.path.join(ROOT, "scripts", "tools", "strip_usd_factor_public.py")


def _ls_files():
    out = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True)
    return [line for line in out.splitlines() if line]


def _sample():
    return {
        "schema": "USD_FACTOR/1.0", "as_of": "2026-11-20",
        "currencies": [{"ccy": "EUR", "E21": 0.1}],
        "diag": {
            "fix": "14:15 CET",
            "twins": {
                "futures": {"available": True, "window": 252, "note": "x",
                            "by_ccy": {"EUR": {"n": 1, "corr": 0.0, "corr_ex_us": 0.0}}},
                "afe": {"available": True, "corr_weekly": 0.5},
            },
        },
    }


def _yaml_lines(name):
    """Líneas del YAML sin comentarios (# hasta fin de línea)."""
    with open(os.path.join(ROOT, ".github", "workflows", name), encoding="utf-8") as fh:
        raw = fh.read().splitlines()
    return [re.sub(r"(^|\s)#.*$", "", line) for line in raw]


class PublishedUsdFactor(unittest.TestCase):
    def test_published_usd_factor_json_has_no_by_ccy(self):
        with open(os.path.join(ROOT, "data", "USD_FACTOR.json"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn('"by_ccy"', text)
        fu = json.loads(text)["diag"]["twins"]["futures"]
        self.assertEqual(fu, {"available": False, "note": "privado / licencia"})

    def test_no_futures_canonical_csv_in_versioned_tree(self):
        self.assertEqual([p for p in _ls_files() if p.startswith("data/futures/canonical/")], [])

    def test_acta_t192u_not_in_public_tree(self):
        self.assertNotIn("docs/actas/ACTA_T192U.md", _ls_files())

    def test_index_html_has_no_databento_monthly_cost_figure(self):
        with open(os.path.join(ROOT, "docs", "index.html"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIsNone(re.search(r"~\$[0-9]", text))


class StripScript(unittest.TestCase):
    def test_strip_only_touches_futures_and_is_idempotent(self):
        src = _sample()
        out, changed = S.strip(copy.deepcopy(src))
        self.assertTrue(changed)
        self.assertEqual(out["diag"]["twins"]["futures"], {"available": False, "note": "privado / licencia"})
        expected = copy.deepcopy(src)
        expected["diag"]["twins"]["futures"] = {"available": False, "note": "privado / licencia"}
        self.assertEqual(out, expected)
        again, changed2 = S.strip(copy.deepcopy(out))
        self.assertFalse(changed2)
        self.assertEqual(again, out)

    def test_cli_rewrites_file_then_noops_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "USD_FACTOR.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(_sample(), indent=1, ensure_ascii=False))
            subprocess.check_call([sys.executable, SCRIPT, "--path", path], stdout=subprocess.DEVNULL)
            with open(path, "rb") as fh:
                first = fh.read()
            self.assertNotIn(b"by_ccy", first)
            subprocess.check_call([sys.executable, SCRIPT, "--path", path], stdout=subprocess.DEVNULL)
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(), first)

    def test_missing_file_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = subprocess.call([sys.executable, SCRIPT, "--path", os.path.join(tmp, "nope.json")],
                                 stdout=subprocess.DEVNULL)
            self.assertEqual(rc, 0)


class WorkflowOrder(unittest.TestCase):
    def _assert_strip_after_factor_before_add(self, name):
        lines = _yaml_lines(name)
        factor = next(i for i, l in enumerate(lines) if "scripts/usd_factor.py" in l and "python" in l)
        strip = next(i for i, l in enumerate(lines) if "strip_usd_factor_public.py" in l and i > factor)
        between = lines[factor + 1:strip]
        self.assertFalse([l for l in between if re.search(r"git (add|commit)|--commit|git_push", l)], name)
        first_add = next(i for i, l in enumerate(lines) if re.search(r"\bgit add\b", l))
        self.assertLess(strip, first_add, name)

    def test_usd_factor_yml(self):
        self._assert_strip_after_factor_before_add("usd_factor.yml")

    def test_feed_recovery_yml(self):
        self._assert_strip_after_factor_before_add("feed_recovery.yml")


if __name__ == "__main__":
    unittest.main()

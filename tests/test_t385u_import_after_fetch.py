"""t385u: importar el módulo de fetch del clon solo después de fetch_ff_only.

El 25-nov 14:00 WAT, sovereign-10y-daily importó fetch_sovereign_10y_daily
desde un clon anterior a #140 (sin es_already_has_expected), hizo
fetch_ff_only (el clon ya tenía la función) y falló con AttributeError
sobre el módulo ya cargado.

orden Sander 25-nov t385u: arreglo permanente; no se modifican tests existentes.
"""
from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VPS = ROOT / "vps"

STALE = "def run(*a, **k):\n    return {'ok': True}\n"
FRESH = (
    "def es_already_has_expected(*a, **k):\n"
    "    return True, {'expected_obs': 'ok'}\n"
    "def run(*a, **k):\n"
    "    return {'ok': True}\n"
)
MOD = "fetch_sovereign_10y_daily"


def _write(dirpath, body):
    scripts = Path(dirpath) / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / ("%s.py" % MOD)).write_text(body, encoding="utf-8")
    return scripts


def _purge():
    sys.modules.pop(MOD, None)


class ImportBeforeFetchRepro(unittest.TestCase):
    def setUp(self):
        self._paths = []
        _purge()

    def tearDown(self):
        _purge()
        for p in self._paths:
            while p in sys.path:
                sys.path.remove(p)

    def test_stale_import_then_update_raises_attributeerror(self):
        """Reproduce the 25-nov AttributeError: import, then update, then use."""
        with tempfile.TemporaryDirectory() as td:
            scripts = _write(td, STALE)
            self._paths.append(str(scripts))
            sys.path.insert(0, str(scripts))
            _purge()
            mod = importlib.import_module(MOD)
            self.assertFalse(hasattr(mod, "es_already_has_expected"))
            _write(td, FRESH)
            with self.assertRaises(AttributeError):
                getattr(mod, "es_already_has_expected")()

    def test_update_then_import_sees_attribute(self):
        with tempfile.TemporaryDirectory() as td:
            _write(td, STALE)
            scripts = _write(td, FRESH)
            self._paths.append(str(scripts))
            sys.path.insert(0, str(scripts))
            _purge()
            mod = importlib.import_module(MOD)
            has, ev = mod.es_already_has_expected()
            self.assertTrue(has)
            self.assertEqual(ev.get("expected_obs"), "ok")


class FetchBeforeImportInRunners(unittest.TestCase):
    def _order(self, filename, fetch_mod):
        src = (VPS / filename).read_text(encoding="utf-8")
        start = src.find("\ndef run(")
        if start < 0:
            start = src.find("def run(")
        self.assertGreaterEqual(start, 0, "def run missing in %s" % filename)
        rest = src[start:]
        if rest.startswith("\n"):
            rest = rest[1:]
        nxt = None
        for token in ("\ndef ", "\nclass ", "\nif __name__"):
            i = rest.find(token, 1)
            if i != -1 and (nxt is None or i < nxt):
                nxt = i
        body = rest if nxt is None else rest[:nxt]
        i_ff = body.find("fetch_ff_only")
        i_imp = body.find("import %s" % fetch_mod)
        self.assertGreaterEqual(i_ff, 0, "fetch_ff_only missing in %s run()" % filename)
        self.assertGreaterEqual(i_imp, 0, "import %s missing in %s run()" % (fetch_mod, filename))
        self.assertLess(
            i_ff,
            i_imp,
            "%s must call fetch_ff_only before importing %s" % (filename, fetch_mod),
        )

    def test_sovereign_10y_daily_fetches_before_import(self):
        self._order("sovereign_10y_daily.py", "fetch_sovereign_10y_daily")

    def test_sovereign_10y_es_1130_fetches_before_import(self):
        self._order("sovereign_10y_es_1130.py", "fetch_sovereign_10y_daily")

    def test_snb_daily_fetches_before_import(self):
        self._order("snb_daily.py", "fetch_chf_snb1a")

    def test_swpt_weekly_fetches_before_import(self):
        self._order("swpt_weekly.py", "fetch_swpt")


if __name__ == "__main__":
    unittest.main()

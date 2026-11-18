"""Guardián del árbol público (t248u + t249u + t250u).

Empareja basename y claves JSON en cualquier nivel. No busca palabras sueltas.
El barrido de iv30, rr25, bf25, oi, settle, d_oi y percentiles FXVOL sobre
todo git ls-files sigue encontrando ficheros que ya estaban en main; no se
afirma aquí para no reescribirlos.
"""
import json
import os
import subprocess
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FIGURE_NAMES = {
    "OPTIONS_SURFACE.json",
    "CME_OPTIONS_C1_shadow.json",
    "CME_FXVOL_shadow.json",
}
SHARED_KEYS = {
    "walls", "front_oi", "front_metrics", "pin_k", "call_k", "put_k",
}
BRIEF_KEYS = SHARED_KEYS | {
    "pin", "strikes_c", "strikes_p", "call", "put", "oi", "top",
}


def _ls_files():
    out = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True)
    return [line for line in out.splitlines() if line]


def _blob(path):
    return subprocess.check_output(["git", "show", f"HEAD:{path}"], cwd=ROOT)


def _keys(obj, found):
    if isinstance(obj, dict):
        for key, value in obj.items():
            found.add(str(key))
            _keys(value, found)
    elif isinstance(obj, list):
        for item in obj:
            _keys(item, found)


class CmePublicGuardian(unittest.TestCase):
    def test_versioned_tree_has_none_of_the_three_figure_names(self):
        hits = [path for path in _ls_files() if path.rsplit("/", 1)[-1] in FIGURE_NAMES]
        self.assertEqual(hits, [])

    def test_brief_json_has_none_of_the_walls_keys(self):
        self.assertIn("data/alerts/brief.json", _ls_files())
        found = set()
        _keys(json.loads(_blob("data/alerts/brief.json")), found)
        self.assertEqual(sorted(found & BRIEF_KEYS), [])

    def test_state_json_has_none_of_the_walls_keys(self):
        self.assertIn("data/alerts/state.json", _ls_files())
        found = set()
        _keys(json.loads(_blob("data/alerts/state.json")), found)
        self.assertEqual(sorted(found & SHARED_KEYS), [])


if __name__ == "__main__":
    unittest.main()

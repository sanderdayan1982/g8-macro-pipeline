"""Orden Sander 17-nov t236u. El workflow de sombra cubre las familias que sube el Mac.

No ejecuta el workflow. Lee mac/push_nzd_to_github.py (FAMILIES) y el YAML.
Los ficheros que el propio job escribe quedan fuera de `paths`.
"""
import fnmatch
import os
import sys
import unittest

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "mac"))
import push_nzd_to_github as P  # noqa: E402

WF = os.path.join(ROOT, ".github", "workflows", "mac_nzd_chf_shadow.yml")
OUTPUTS = (
    "data/CHF_NOWCAST_shadow.json",
    "data/CHF_SPOT_2Y_daily_shadow.csv",
    "data/CHF_SPOT_5Y_daily_shadow.csv",
    "data/argos_rates.json",
)


def _mac_paths():
    """Ficheros que el Mac puede subir: la lista obligatoria y un IIB de la glob."""
    out = []
    for _fam, patterns, required, _header, _key in P.FAMILIES:
        for name in required:
            out.append("data/" + name)
        if "NZD_IIB_*.csv" in patterns:
            out.append("data/NZD_IIB_2035.csv")
    return out


def _matches(path, pattern):
    return fnmatch.fnmatch(path, pattern)


class MacShadowWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(WF, encoding="utf-8") as fh:
            cls.doc = yaml.safe_load(fh)
            fh.seek(0)
            cls.text = fh.read()
        trigger = cls.doc[True] if True in cls.doc else cls.doc["on"]
        cls.paths = trigger["push"]["paths"]

    def test_paths_cover_every_mac_family(self):
        self.assertEqual([row[0] for row in P.FAMILIES], ["NZ-B2", "CH-CURVA", "CH-DIARIO", "JP-TONA"])
        missing = []
        for target in _mac_paths():
            if any(_matches(target, pat) for pat in self.paths):
                continue
            missing.append(target)
        self.assertEqual(missing, [])

    def test_own_commit_is_outside_the_path_filter(self):
        hit = [name for name in OUTPUTS if any(_matches(name, pat) for pat in self.paths)]
        self.assertEqual(hit, [])
        self.assertIn("[skip netlify]", self.text)
        self.assertIn("group: g8-shared-data-alerts", self.text)
        self.assertIn(
            "python scripts/tools/g8step.py --name chf_nowcast_shadow --phase post --cap 40 --min 5 -- python scripts/chf_nowcast_shadow.py",
            self.text,
        )
        self.assertIn("TZ=UTC python scripts/argos_rates.py", self.text)
        self.assertIn("ref: main", self.text)


if __name__ == "__main__":
    unittest.main()

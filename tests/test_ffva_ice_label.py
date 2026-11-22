"""DATOS-02 B: etiqueta ICE en la fila USD/DX del FFVA sin cambiar el voto en vivo."""
import csv
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import votes_ingest as V  # noqa: E402


class FfvaIceLabel(unittest.TestCase):
    def test_usd_row_labeled_ice_out_of_volume_tests(self):
        self.assertEqual(
            V.FFVA_ROW_SOURCE_LABELS.get("USD"),
            "FUENTE=ICE · fuera de las pruebas de volumen",
        )
        with open(os.path.join(ROOT, "sources", "registry.csv"), encoding="utf-8") as fh:
            rows = {r["feed_id"]: r for r in csv.DictReader(fh)}
        self.assertIn("FUENTE=ICE · fuera de las pruebas de volumen", rows["ICE_DX"]["notes"])

    def test_ffva_usd_live_vote_unchanged(self):
        for regime, expected in (("ACCUM", "+1"), ("SC", "+1"), ("DIST", "-1"), ("LIQ", "-1"), ("FLAT_OI", "0")):
            voto, stale = V._vote("FFVA_G8", "USD", {"USD_ROLL": "NO"}, regime, "FRESH", False)
            self.assertEqual((voto, stale), (expected, False), regime)
        voto_stale, stale_flag = V._vote("FFVA_G8", "USD", {"USD_ROLL": "NO"}, "STALE", "FRESH", False)
        self.assertEqual((voto_stale, stale_flag), ("NA", True))
        voto_roll, roll_stale = V._vote("FFVA_G8", "USD", {"USD_ROLL": "YES"}, "ACCUM", "FRESH", False)
        self.assertEqual((voto_roll, roll_stale), ("NA", False))


if __name__ == "__main__":
    unittest.main()

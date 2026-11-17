"""Orden Sander 17-nov-2026 t234u. valor público solo si el módulo está en la lista blanca.

No reescribe data/votes.csv. Las filas con fecha_cierre anterior al corte no se juzgan.
Sondas sintéticas: el carácter «1», sin cifras de mercado.
"""
import csv
import os
import sys
import unittest
from datetime import date

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import votes_ingest as V  # noqa: E402

# Corte de esta orden. La fusión aún no tiene fecha de GitHub; las filas ya
# publicadas son anteriores y no se reescriben.
FUSION = "2026-11-17"
VOTES = os.path.join(ROOT, "data", "votes.csv")

# Fuera aunque el campo parezca público. Orden t234u.
HELD_OUT = ("RISK_G8", "POL_G8", "FFVA_G8", "XCCY_G8", "XCCY_OIS_SHADOW", "CRD_G8")

# Una sonda por módulo del catálogo. «1» no es un dato de mercado.
PROBES = {
    "RISK_G8": {"REGIME": "X", "SCORE": "1"},
    "XCCY_G8": {"EUR_REGIME": "X"},
    "XCCY_OIS_SHADOW": {"EUR_REGIME": "X"},
    "PSI_G8": {"EUR_HEALTH": "X", "EUR_P_HEALTHY_BP": "1"},
    "CURVA_G8": {"EUR_REGIME": "X", "EUR_SPREAD_BP": "1"},
    "RTF10_G8": {"EUR_DRIVER": "X"},
    "POL_G8": {"EUR_STATE": "X", "EUR_HAWK_SCORE": "1"},
    "IYDT_2Y": {"Y2_EUR_SCORE_BAND": "X", "Y2_EUR_SCORE": "1"},
    "IYDT_10Y": {"Y10_EUR_SCORE_BAND": "X", "Y10_EUR_SCORE": "1"},
    "FFVA_G8": {"EUR_REGIME": "X", "EUR_DIRECTIONAL_INTENSITY": "1"},
    "TOT_G8": {"TOT_EUR_REGIME": "X", "TOT_EUR_SCORE": "1"},
    "EQF_G8": {"EQF_EUR_REGIME": "X", "EQF_EUR_SCORE": "1"},
    "POS_G8": {"EUR_LF_STATE": "X", "EUR_LF_Z": "1"},
}


class AllowedValorModules(unittest.TestCase):
    def test_held_out_names_are_not_whitelisted(self):
        for name in HELD_OUT:
            self.assertNotIn(name, V.ALLOWED_VALOR_MODULES, name)
        for name in V.ALLOWED_VALOR_MODULES:
            self.assertFalse(name.startswith("XCCY"), name)
            self.assertNotIn("CRD", name)

    def test_estado_valor_is_empty_outside_the_whitelist(self):
        names = [row[0] for row in V.MODULES]
        self.assertEqual(sorted(PROBES), sorted(names))
        for name in names:
            raw_valor = V._estado_valor_from_mail(name, "EUR", PROBES[name])[1]
            valor = V._estado_valor(name, "EUR", PROBES[name])[1]
            if name in V.ALLOWED_VALOR_MODULES:
                self.assertEqual(valor, raw_valor, name)
            else:
                self.assertEqual(valor, "", name)
                if raw_valor != "":
                    self.assertNotEqual(raw_valor, valor, name)
        self.assertEqual(V._estado_valor("CRD_G8", "EUR", {"CRD_STATE": "X"})[1], "")
        self.assertEqual(V._public_valor("XCCY_OTHER", "1"), "")
        self.assertEqual(V._public_valor("CRD_G8", "1"), "")

    def test_e21_whitelist_keeps_a_synthetic_number(self):
        self.assertIn("E21", V.ALLOWED_VALOR_MODULES)
        session = date(2026, 11, 12)
        doc = {
            "version": "1",
            "as_of": "2026-11-12",
            "generated_utc": "2026-11-12T18:00:00Z",
            "currencies": [{"ccy": "EUR", "E21": "1"}],
        }
        rows = [r for r in V.rows_from_factor(doc, session) if r["ccy"] == "EUR"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["valor"], "1")
        self.assertEqual(rows[0]["voto"], "+1")

    def test_votes_csv_on_or_after_fusion_has_empty_valor_outside_whitelist(self):
        with open(VOTES, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                if (row.get("fecha_cierre") or "") < FUSION:
                    continue
                if row.get("script") in V.ALLOWED_VALOR_MODULES:
                    continue
                self.assertEqual(row.get("valor") or "", "", row.get("script"))


if __name__ == "__main__":
    unittest.main()

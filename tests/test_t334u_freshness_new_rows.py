"""t334u: filas nuevas de frescura (SNB1A, DE/ES 10Y, spread). Las reglas previas no se alteran."""
import csv
import hashlib
import os
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NEW = ("CHF_SPOT_SNB1A", "DE10Y_PAR", "ES10Y_DAILY", "ES_DE_10Y_SPREAD")
# SHA-256 de sources/freshness_rules.csv y freshness_params.csv en origin/main
# (22-nov-2026, tip 8023181c) — las reglas existentes no se tocan.
# orden Sander 23-nov: huellas actualizadas por la división CAD_CURVE en BILLS/BONDS, PR #125
MAIN_RULES_SHA256 = "ee3294402ea17ffae13b88a16b11e9f0f9774e36cd231c4a9e3be0821a15954c"
# orden Sander 24-nov t373u: exigibilidad de SOFR tras el turno US de las 16:47
# Huella de freshness_params.csv actualizada: solo cambia la fila SOFR (g_min 60→270, g_basis, notes).
MAIN_PARAMS_SHA256 = "c505dcc5788403dd626af201cee7c1ad9a35fd8f3cd65d21f125baf274a44591"


def _rows(name):
    with open(os.path.join(ROOT, "sources", name), encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _prefix_without_new(name, new_ids):
    raw = open(os.path.join(ROOT, "sources", name), "rb").read()
    kept = []
    header = None
    for i, line in enumerate(raw.splitlines(keepends=True)):
        if i == 0:
            header = line
            kept.append(line)
            continue
        rid = line.split(b",", 1)[0].decode("utf-8")
        if rid not in new_ids:
            kept.append(line)
    return b"".join(kept)


class NewFreshnessRows(unittest.TestCase):
    def test_new_rules_and_params_exist(self):
        rules = {r["rule_id"]: r for r in _rows("freshness_rules.csv")}
        params = {r["rule_id"]: r for r in _rows("freshness_params.csv")}
        for k in NEW:
            self.assertIn(k, rules, k)
            self.assertIn(k, params, k)
        self.assertEqual(rules["CHF_SPOT_SNB1A"]["frequency"], "daily")
        self.assertEqual(rules["CHF_SPOT_SNB1A"]["pub_local"], "08:31")
        self.assertIn("J30M0", rules["CHF_SPOT_SNB1A"]["notes"])
        self.assertEqual(rules["DE10Y_PAR"]["calendar"], "TARGET")
        self.assertEqual(rules["ES10Y_DAILY"]["pub_offset_bd"], "2")
        self.assertEqual(params["ES_DE_10Y_SPREAD"]["model"], "derived")
        self.assertEqual(rules["ES_DE_10Y_SPREAD"]["files"], "ES_DE_10Y_SPREAD_DAILY.csv")
        outs = {r["file"]: r for r in _rows("freshness_outputs.csv")}
        self.assertIn("DE_10Y_PAR_DAILY.csv", outs["ES_DE_10Y_SPREAD_DAILY.csv"]["inputs"])
        self.assertIn("ES_10Y_DAILY.csv", outs["ES_DE_10Y_SPREAD_DAILY.csv"]["inputs"])

    def test_existing_rules_unchanged_versus_main(self):
        # t336u, orden expresa Sander 22-nov: el candado ignora la fila nueva SWPT y restaura
        # SWPT en los feeds de NOT_INGESTED; el resto de reglas no se toca.
        # t336u, orden expresa Sander 22-nov: regla nueva, no modifica ninguna existente
        # orden Sander 23-nov: huellas actualizadas por la división CAD_CURVE en BILLS/BONDS, PR #125
        prefix = _prefix_without_new("freshness_rules.csv", set(NEW) | {"SWPT", "CHF_CURVE_MONTH_END"})
        prefix = prefix.replace(b"NOT_INGESTED,RRPONTSYD,,", b"NOT_INGESTED,RRPONTSYD;SWPT,,", 1)
        self.assertEqual(hashlib.sha256(prefix).hexdigest(), MAIN_RULES_SHA256)
        pfx = _prefix_without_new("freshness_params.csv", set(NEW) | {"SWPT", "CHF_CURVE_MONTH_END"})
        self.assertEqual(hashlib.sha256(pfx).hexdigest(), MAIN_PARAMS_SHA256)

    def test_swpt_has_weekly_rule_and_left_not_ingested(self):
        # t336u, orden expresa Sander 22-nov: SWPT ya no vive en NOT_INGESTED; regla semanal propia.
        rules = {r["rule_id"]: r for r in _rows("freshness_rules.csv")}
        holding = [r["rule_id"] for r in _rows("freshness_rules.csv") if "SWPT" in (r.get("feeds") or "").split(";")]
        self.assertEqual(holding, ["SWPT"])
        self.assertEqual(rules["SWPT"]["frequency"], "weekly:THU")
        self.assertNotIn("SWPT", (rules["NOT_INGESTED"].get("feeds") or "").split(";"))
        self.assertIn("RRPONTSYD", (rules["NOT_INGESTED"].get("feeds") or "").split(";"))

    def test_cad_ry_rule_not_touched(self):
        prefix = _prefix_without_new("freshness_rules.csv", set(NEW))
        old = next(r for r in csv.DictReader(prefix.decode("utf-8").splitlines()) if r["rule_id"] == "CAD_RY")
        new = next(r for r in _rows("freshness_rules.csv") if r["rule_id"] == "CAD_RY")
        self.assertEqual(old, new)

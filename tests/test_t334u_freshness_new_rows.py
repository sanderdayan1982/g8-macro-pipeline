"""t334u: filas nuevas de frescura (SNB1A, DE/ES 10Y, spread). Las reglas previas no se alteran."""
import csv
import os
import subprocess
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NEW = ("CHF_SPOT_SNB1A", "DE10Y_PAR", "ES10Y_DAILY", "ES_DE_10Y_SPREAD")


def _rows(name):
    with open(os.path.join(ROOT, "sources", name), encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


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
        src = subprocess.check_output(
            ["git", "show", "origin/main:sources/freshness_rules.csv"], cwd=ROOT, text=True)
        old = list(csv.DictReader(src.splitlines()))
        new = _rows("freshness_rules.csv")
        old_ids = [r["rule_id"] for r in old]
        new_ids = [r["rule_id"] for r in new]
        self.assertEqual(old_ids, [i for i in new_ids if i not in NEW])
        for a, b in zip(old, new):
            self.assertEqual(a, b, a["rule_id"])

    def test_swpt_still_only_on_existing_not_ingested_rule(self):
        rules = _rows("freshness_rules.csv")
        holding = [r["rule_id"] for r in rules if "SWPT" in (r.get("feeds") or "").split(";")]
        self.assertEqual(holding, ["NOT_INGESTED"])
        self.assertFalse(any(r["rule_id"] == "SWPT" for r in rules))

    def test_cad_ry_rule_not_touched(self):
        src = subprocess.check_output(
            ["git", "show", "origin/main:sources/freshness_rules.csv"], cwd=ROOT, text=True)
        old = next(r for r in csv.DictReader(src.splitlines()) if r["rule_id"] == "CAD_RY")
        new = next(r for r in _rows("freshness_rules.csv") if r["rule_id"] == "CAD_RY")
        self.assertEqual(old, new)

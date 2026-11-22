"""t334u: filas nuevas de frescura (SNB1A, DE/ES 10Y, spread). Las reglas previas no se alteran."""
import csv
import hashlib
import os
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NEW = ("CHF_SPOT_SNB1A", "DE10Y_PAR", "ES10Y_DAILY", "ES_DE_10Y_SPREAD")
# SHA-256 de sources/freshness_rules.csv y freshness_params.csv en origin/main
# (22-nov-2026, tip 8023181c) — las reglas existentes no se tocan.
MAIN_RULES_SHA256 = "423df2966a37572ee7a66f2b28ade5b8a4a43676a7b3c6eca132673465177130"
MAIN_PARAMS_SHA256 = "b38608cdf8bddf18b7fb8fdc4e0f123706c465bbbb55dff202b39a6c8058e876"


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
        prefix = _prefix_without_new("freshness_rules.csv", set(NEW))
        self.assertEqual(hashlib.sha256(prefix).hexdigest(), MAIN_RULES_SHA256)
        pfx = _prefix_without_new("freshness_params.csv", set(NEW))
        self.assertEqual(hashlib.sha256(pfx).hexdigest(), MAIN_PARAMS_SHA256)

    def test_swpt_still_only_on_existing_not_ingested_rule(self):
        rules = _rows("freshness_rules.csv")
        holding = [r["rule_id"] for r in rules if "SWPT" in (r.get("feeds") or "").split(";")]
        self.assertEqual(holding, ["NOT_INGESTED"])
        self.assertFalse(any(r["rule_id"] == "SWPT" for r in rules))

    def test_cad_ry_rule_not_touched(self):
        prefix = _prefix_without_new("freshness_rules.csv", set(NEW))
        old = next(r for r in csv.DictReader(prefix.decode("utf-8").splitlines()) if r["rule_id"] == "CAD_RY")
        new = next(r for r in _rows("freshness_rules.csv") if r["rule_id"] == "CAD_RY")
        self.assertEqual(old, new)

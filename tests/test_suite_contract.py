"""A2: el código del dashboard cumple sources/suite_contract.json.

El contrato es el dueño de los parámetros compartidos con la Suite G8.
Si un umbral, un corte de la §02, un suelo, un par o el calendario COT se aparta, este test falla.
"""
import ast
import csv
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "sources" / "suite_contract.json"


def _literal_assign(nodes, name):
    for node in nodes:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                return ast.literal_eval(node.value)
            if isinstance(target, ast.Tuple):
                ids = [elt.id for elt in target.elts if isinstance(elt, ast.Name)]
                if name in ids:
                    return ast.literal_eval(node.value)[ids.index(name)]
    raise KeyError(name)


def _assign(path, name):
    return _literal_assign(ast.parse(path.read_text(encoding="utf-8")).body, name)


def _assign_in(path, func, name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func:
            return _literal_assign(node.body, name)
    raise KeyError(func)


def _pairs_from_priority(priority):
    out = []
    for i, base in enumerate(priority):
        for quote in priority[i + 1:]:
            out.append("%s/%s" % (base, quote))
    return out


class SuiteContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = json.loads(CONTRACT.read_text(encoding="utf-8"))

    def test_pos_tables_match_the_three_owners(self):
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))
        import importlib.util
        import pos_g8_cot_collector as col
        spec = importlib.util.spec_from_file_location("gen_pos_a2", ROOT / "scripts" / "generators" / "pos_g8.py")
        gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen)
        twin_thr = _assign_in(ROOT / "scripts" / "twin_test.py", "_pos_g8", "THR")
        pos = self.c["pos"]
        fx = {k: {"warn": v["warn"], "ext": v["ext"]} for k, v in pos["fx_ccy"].items()}
        metals = pos["metals"]
        self.assertEqual(col.PINE_REF, self.c["pine_ref"])
        self.assertEqual(col.Z_CAP, pos["z_cap"])
        self.assertEqual(col.WIN, pos["windows_weeks"])
        self.assertEqual(col.THR["fx"], pos["fx_fallback"])
        self.assertEqual(col.THR["fx_ccy"], fx)
        self.assertEqual(col.THR["XAU"], metals["XAU"])
        self.assertEqual(col.THR["XAG"], metals["XAG"])
        am = {k: pos["am_thresholds_shadow"][k] for k in pos["fx_ccy"]}
        self.assertEqual(col.AM_THR_SHADOW, am)
        self.assertEqual(gen.EXPECTED_THR["fx"], pos["fx_fallback"])
        self.assertEqual(gen.EXPECTED_THR["fx_ccy"], fx)
        self.assertEqual(gen.EXPECTED_THR["XAU"], metals["XAU"])
        self.assertEqual(gen.EXPECTED_THR["XAG"], metals["XAG"])
        self.assertEqual(gen.EXPECTED_WIN, pos["windows_weeks"])
        self.assertEqual(gen.PINE_VERSION, "v1_2_6")
        for ccy, thr in fx.items():
            self.assertEqual(twin_thr[ccy], (thr["warn"], thr["ext"]), ccy)
        for name, thr in metals.items():
            self.assertEqual(twin_thr[name], (thr["warn"], thr["ext"]), name)

    def test_section_02_cuts_match_alerts_and_the_page(self):
        alerts = ROOT / "scripts" / "dashboard_alerts.py"
        sec = self.c["section_02"]
        self.assertEqual(_assign(alerts, "FLOOR_TIGHT_BP"), sec["tight_if_bp_gt"])
        self.assertEqual(_assign(alerts, "FLOOR_PRESS_BP"), sec["pressure_if_bp_gt"])
        self.assertEqual(sec["ample_if_bp_le"], sec["tight_if_bp_gt"])
        self.assertEqual(sec["tight_if_bp_le"], sec["pressure_if_bp_gt"])
        src = alerts.read_text(encoding="utf-8")
        self.assertIn(
            'return "PRESSURE" if bp > FLOOR_PRESS_BP else "TIGHT" if bp > FLOOR_TIGHT_BP else "AMPLE"',
            src,
        )
        html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        i = html.index('fls-press">PRESSURE')
        window = html[i - 120:i + 220]
        self.assertIn("last > 10", window)
        self.assertIn("last > 2", window)
        self.assertIn("AMPLE", window)

    def test_policy_floors_and_ecb_dfr_series(self):
        floors = _assign(ROOT / "scripts" / "dashboard_alerts.py", "FLOORS")
        got = [{"ccy": a, "rfr": b, "floor_file": c, "anchor": d} for a, b, c, d in floors]
        want = [{k: row[k] for k in ("ccy", "rfr", "floor_file", "anchor")} for row in self.c["policy_floors"]]
        self.assertEqual(got, want)
        src = (ROOT / "scripts" / "fetch_floor_spreads.py").read_text(encoding="utf-8")
        self.assertIn("D.U2.EUR.4F.KR.DFR.LEV", src)
        self.assertIn('write_ohlcv("FLOOR_EUR.csv", out)', src)
        self.assertIn("series_id=IORB", src)
        self.assertIn('write_ohlcv("FLOOR_USD.csv", out)', src)
        eur = next(row for row in self.c["policy_floors"] if row["ccy"] == "EUR")
        self.assertEqual(eur["anchor"], "DFR")
        self.assertEqual(eur["floor_file"], "FLOOR_EUR.csv")
        self.assertIn("D.U2.EUR.4F.KR.DFR.LEV", eur["source"])

    def test_twenty_eight_pairs_and_cme_inversion(self):
        spec = self.c["pairs"]
        built = _pairs_from_priority(spec["base_priority"])
        self.assertEqual(len(built), 28)
        self.assertEqual(built, spec["list"])
        legs = set()
        for pair in spec["list"]:
            a, b = pair.split("/")
            legs.add(frozenset((a, b)))
        self.assertEqual(len(legs), 28)
        self.assertEqual(len(spec["currencies"]), 8)
        alerts = ROOT / "scripts" / "dashboard_alerts.py"
        self.assertEqual(_assign(alerts, "PAIR"), spec["options_operator_quote"])
        self.assertEqual(set(_assign(alerts, "INV")), set(spec["cme_inverted_vs_operator"]))
        for ccy, quote in spec["options_operator_quote"].items():
            self.assertIn(quote, spec["list"], ccy)
            self.assertIn(ccy, quote.replace("/", ""))
        native = _assign(ROOT / "scripts" / "usd_factor.py", "CCYS")
        self.assertEqual(native, spec["native_usd_per_unit"])

    def test_cot_calendar_is_friday_release_and_saturday_retry(self):
        cal = self.c["cot_calendar"]
        yml = (ROOT / cal["workflow"]).read_text(encoding="utf-8")
        for cron in cal["dashboard_cron_utc"]:
            self.assertIn("cron: '%s'" % cron, yml)
        self.assertIn("report as of Tue", yml)
        with (ROOT / "sources" / "schedules.csv").open(encoding="utf-8") as fh:
            rows = {r["pass_id"]: r for r in csv.DictReader(fh)}
        self.assertEqual([rows[i]["cron_utc"] for i in cal["schedule_ids"]], cal["dashboard_cron_utc"])
        self.assertEqual(rows["COT1"]["runner"], "g8_port_run.yml")
        self.assertEqual(rows["COT2"]["runner"], "g8_port_run.yml")


if __name__ == "__main__":
    unittest.main()

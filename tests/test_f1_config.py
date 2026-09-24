"""F1 — la configuración de frescura cubre TODOS los feeds e insumos y cada fuente tiene al menos dos
consultas previstas después de su publicación esperada, en horario de verano y de invierno."""
import csv
import glob
import os
import sys
import unittest
from datetime import date, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from g8common import schedule as SC  # noqa: E402


def rows(name):
    with open(os.path.join(ROOT, "sources", name), encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


RULES = rows("freshness_rules.csv")
PASSES = rows("schedules.csv")
CALS = SC.load_calendars(ROOT)
HORIZON = {"daily": 24, "monthly:BD1": 24}


class F1Config(unittest.TestCase):
    def test_every_registered_production_feed_has_exactly_one_rule(self):
        reg = rows("registry.csv")
        feeds = [r["feed_id"] for r in reg if r["status"].startswith("EXISTS") or r["status"] == "NOT_INGESTED"]
        self.assertEqual(sorted(r["feed_id"] for r in reg if r["status"] == "NOT_INGESTED"), ["RRPONTSYD", "SWPT"])
        seen = {}
        for r in RULES:
            for f in filter(None, r["feeds"].split(";")):
                seen.setdefault(f, []).append(r["rule_id"])
        self.assertEqual(sorted(set(feeds) - set(seen)), [], "feeds sin regla")
        self.assertEqual({f: v for f, v in seen.items() if len(v) > 1}, {}, "feed en varias reglas")
        self.assertEqual(sorted(set(seen) - set(feeds)), [], "regla con feed inexistente")
        self.assertEqual(len(feeds), 54)                        # 51 feeds + CFTC_TFF (alias) + RRPONTSYD/SWPT (sin ingestión)

    def test_every_data_file_is_covered(self):
        files = {os.path.basename(p) for p in glob.glob(os.path.join(ROOT, "data", "*.csv")) + glob.glob(os.path.join(ROOT, "data", "*.json"))}
        covered = set()
        for r in RULES:
            covered.update(os.path.basename(x) for x in r["files"].split(";") if x)
        extra = {"BOOK_RISK.json", "S01B.json", "USD_FACTOR.json", "OPTIONS_SURFACE.json", "pos_g8_cot.json", "MFV_G8_state.json"}
        self.assertEqual(sorted(files - covered - extra), [], "ficheros de data/ sin regla")
        self.assertIn("BOOK_RISK.json", files)                 # derivado de USD_FACTOR (libro), sin frescura propia

    def test_calendars_match_observed_publication_days(self):
        """Contraste con los datos reales ene–sep 2026: ningún día hábil sin dato ni dato en festivo."""
        checks = [("US_REPO", "SOFR.csv"), ("US", "US_BILL_3M.csv"), ("TARGET", "ESTR.csv"), ("GB", "SONIA.csv"),
                  ("JP", "TONA.csv"), ("JP", "JPY_BILL_2Y.csv"), ("CA", "CORRA.csv"), ("AU", "AONIA.csv"),
                  ("NZ", "NZD_BOND_10Y.csv"), ("CH", "CHF_SARON.csv")]
        for cal, f in checks:
            have = set()
            with open(os.path.join(ROOT, "data", f), encoding="utf-8") as fh:
                for line in fh:
                    c = line.split(",")[0].strip().replace("-", "")
                    if len(c) == 8 and c.isdigit():
                        have.add(date(int(c[:4]), int(c[4:6]), int(c[6:])))
            d = date(2026, 1, 5)
            while d <= date(2026, 9, 16):
                self.assertEqual(SC.is_bd(CALS, cal, d), d in have, "%s %s %s" % (cal, f, d))
                d += timedelta(days=1)

    def test_two_query_opportunities_after_publication_both_dst_regimes(self):
        start, end = date(2026, 10, 1), date(2027, 9, 30)      # cubre invierno y verano en todas las zonas
        checked = 0
        for r in RULES:
            if not r["pub_local"] or not r["query_groups"] or r["frequency"] not in ("daily", "monthly:BD1") and not r["frequency"].startswith("weekly:"):
                continue
            h = HORIZON.get(r["frequency"], 48)
            for c in SC.coverage(r, PASSES, CALS, start, end, horizon_h=h):
                n = len(c["passes"])
                self.assertGreaterEqual(n, 2, "%s publicado %s (%s UTC): solo %d consulta(s) en %d h" % (
                    r["rule_id"], c["pub_day"], c["pub_utc"].strftime("%H:%M"), n, h))
                checked += 1
        self.assertGreater(checked, 3000)

    def test_unknown_publication_time_has_two_daily_opportunities(self):
        per_group = {}
        for p in PASSES:
            per_group.setdefault(p["group"], 0)
            per_group[p["group"]] += 1
        for r in RULES:
            if r["pub_local"] or r["frequency"] != "daily":
                continue
            groups = [g for g in r["query_groups"].split("|") if g]
            self.assertGreaterEqual(sum(per_group.get(g, 0) for g in groups), 2, r["rule_id"])

    def test_passes_dst_example_sofr(self):
        r = next(x for x in RULES if x["rule_id"] == "SOFR")
        s = SC.coverage(r, PASSES, CALS, date(2026, 7, 15), date(2026, 7, 15))[0]
        w = SC.coverage(r, PASSES, CALS, date(2027, 1, 15), date(2027, 1, 15))[0]
        self.assertEqual(s["pub_utc"].strftime("%H:%M"), "12:00")   # EDT
        self.assertEqual(w["pub_utc"].strftime("%H:%M"), "13:00")   # EST
        self.assertEqual(s["passes"][0][1], "EU")
        self.assertEqual(w["passes"][0][1], "EU")

    def test_tona_first_pass_after_bojs_0850(self):
        r = next(x for x in RULES if x["rule_id"] == "TONA")
        c = SC.coverage(r, PASSES, CALS, date(2026, 10, 5), date(2026, 10, 5))[0]   # lunes JP
        self.assertEqual(c["pub_utc"].strftime("%a %H:%M"), "Sun 23:50")
        self.assertEqual((c["passes"][0][1], c["passes"][0][0].strftime("%a %H:%M")), ("ASIA1", "Mon 00:23"))


if __name__ == "__main__":
    unittest.main()

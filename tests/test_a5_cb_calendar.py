"""A5 (2026-10-16): calendario oficial de bancos centrales, §03 fundida en §02, §09 fuera de la web, USD° sin redondeo."""
import csv
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import health_monitor as HM  # noqa: E402
import pos_g8_cot_collector as C  # noqa: E402

CAL = ROOT / "data/manual/cb_meetings.csv"


def rows():
    with CAL.open(encoding="utf-8") as fh:
        return list(csv.DictReader(l for l in fh if l.strip() and not l.startswith("#")))


class Calendar(unittest.TestCase):
    def test_well_formed_official_sources(self):
        r = rows()
        self.assertEqual({x["country"] for x in r}, set(HM.CB_COUNTRIES))
        for x in r:
            datetime.strptime(x["decision_date"], "%Y-%m-%d")
            self.assertTrue(x["source"].startswith("https://"), x)
            self.assertTrue(x["verified"])
        keys = [(x["country"], x["decision_date"]) for x in r]
        self.assertEqual(len(keys), len(set(keys)), "fechas duplicadas")

    def test_known_next_decisions_as_of_build(self):
        """Fechas verificadas el 2026-10-16 en las webs oficiales (acta A5)."""
        r = rows()
        nxt = {}
        for x in sorted(r, key=lambda y: y["decision_date"]):
            if x["decision_date"] >= "2026-10-17":
                nxt.setdefault(x["country"], x["decision_date"])
        self.assertEqual(nxt, {"US": "2026-10-28", "CA": "2026-10-28", "NZ": "2026-10-28", "EA": "2026-10-29",
                               "JP": "2026-10-30", "AU": "2026-11-03", "GB": "2026-11-05", "CH": "2026-12-10"})

    def test_consistent_with_policy_evidence_next_review(self):
        import json
        ev = json.loads((ROOT / "sources/policy_evidence.json").read_text())["policies"]
        cc = {"GB_POLICY.csv": "GB", "JP_POLICY.csv": "JP", "CH_POLICY.csv": "CH", "AU_POLICY.csv": "AU", "NZD_OCR.csv": "NZ"}
        dates = {(x["country"], x["decision_date"]) for x in rows()}
        for f, c in cc.items():
            d = datetime.fromisoformat(ev[f]["next_review_at"]).date()
            # la evidencia caduca al empezar la reunión (BoJ: 1.er día); la decisión es ese día o el siguiente
            self.assertTrue((c, d.isoformat()) in dates or (c, (d + timedelta(days=1)).isoformat()) in dates, (f, d))

    def test_health_flags_exhausted_calendar(self):
        items = {x["file"]: x for x in HM.cb_calendar_items(ROOT, datetime(2026, 10, 16, tzinfo=timezone.utc))}
        self.assertTrue(all(x["status"] == "CURRENT" for x in items.values()))
        late = {x["file"]: x for x in HM.cb_calendar_items(ROOT, datetime(2027, 1, 5, tzinfo=timezone.utc))}
        self.assertEqual(late["manual/cb_meetings.csv:AU"]["status"], "LATE")   # RBA 2027 aún sin publicar
        self.assertEqual(late["manual/cb_meetings.csv:US"]["status"], "CURRENT")


class Web(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / "docs/index.html").read_text(encoding="utf-8")

    def test_sections_removed_and_merged(self):
        self.assertNotIn('id="section-cot"', self.html)
        self.assertNotIn('id="section-policy"', self.html)
        self.assertIn("Próxima reunión", self.html)
        self.assertIn("manual/cb_meetings.csv", self.html)

    def test_render_check_accepts_uppercase_header(self):
        src = (ROOT / "scripts/tools/dashboard_render_check.cjs").read_text(encoding="utf-8")
        self.assertIn("/pr[oó]xima reuni[oó]n/i", src)

    def test_dead_monitor_code_pruned(self):
        for name in ("function runAll(", "function healthScore(", "function syncHeader(", "function checkFeed("):
            self.assertNotIn(name, self.html)
        self.assertIn("function computeStaleness(", self.html)


class UsdUnrounded(unittest.TestCase):
    def test_usd_uses_unrounded_z(self):
        # siete Z de −0,7886: redondeadas a −0,79 darían USD 0,79 (STRETCH+); sin redondear 0,7886 < 0,79 (NEUTRAL)
        rows_ = [{"lf_z": -0.79, "_z_lf": -0.7886, "lf_net": 1, "am_z": 0.0, "_z_am": 0.0} for _ in range(7)]
        u = C.usd_proxy(rows_)
        self.assertEqual(u["state"], "NEUTRAL")
        self.assertEqual(u["lf_z"], 0.79)


if __name__ == "__main__":
    unittest.main()

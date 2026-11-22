"""t336u: CHF_CURVE_MONTH_END_CONTROL.csv tiene regla mensual propia (rendeiduebd).

El control de fin de mes no es una serie diaria de SNB1A. Calendario oficial SNB del
cubo rendeiduebd: publicación el 1.er día hábil CH a las 14:30 Europe/Zurich;
observación esperada = último día hábil del mes anterior (p. ej. 2026-10-30 vigente
en nov-2026). Test nuevo; no se editan tests existentes.
t336u, orden expresa Sander 22-nov.
"""
import csv
import os
import sys
import unittest
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from g8common import freshness as FR  # noqa: E402
from g8common import schedule as SC  # noqa: E402

RULES, PARAMS, OUTPUTS, CALS = FR.load_config(ROOT)
OUT = {o["file"]: o for o in OUTPUTS}
UTC = timezone.utc


def T(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%MZ").replace(tzinfo=UTC)


def _rows(name):
    with open(os.path.join(ROOT, "sources", name), encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


class MonthEndControlRule(unittest.TestCase):
    def test_own_monthly_rule_and_left_daily_snb1a(self):
        r, p = RULES["CHF_CURVE_MONTH_END"], PARAMS["CHF_CURVE_MONTH_END"]
        self.assertEqual(r["frequency"], "monthly:BD1")
        self.assertEqual(r["pub_local"], "14:30")
        self.assertEqual(r["timezone"], "Europe/Zurich")
        self.assertEqual(r["calendar"], "CH")
        self.assertEqual(p["model"], "prev_month_end")
        files_daily = (RULES["CHF_SPOT_SNB1A"].get("files") or "").split(";")
        self.assertNotIn("CHF_CURVE_MONTH_END_CONTROL.csv", files_daily)
        self.assertIn("CHF_CURVE_METH_BREAK.json", files_daily)
        self.assertIn("CHF_SPOT_1Y_DAILY.csv", files_daily)
        self.assertEqual(OUT["CHF_CURVE_MONTH_END_CONTROL.csv"]["rule_id"], "CHF_CURVE_MONTH_END")
        self.assertEqual(r["files"], "CHF_CURVE_MONTH_END_CONTROL.csv")
        self.assertFalse((r.get("feeds") or "").strip())  # no duplicar el feed CHF_SPOT_SNB1A

    def test_november_2026_expects_last_ch_business_day_of_october(self):
        pubs = FR.publications(
            RULES["CHF_CURVE_MONTH_END"], PARAMS["CHF_CURVE_MONTH_END"],
            "CH", "Europe/Zurich", CALS, T("2026-11-22T21:43Z"),
        )
        d, due, _ = pubs[0]
        self.assertEqual(d, date(2026, 10, 30))
        self.assertEqual(due.astimezone(ZoneInfo("Europe/Zurich")).strftime("%Y-%m-%d %H:%M"), "2026-11-02 14:30")

    def test_current_when_october_month_end_is_present_in_november(self):
        # AL DÍA CON LA FUENTE: 2026-10-30 es la obs vigente hasta el 1.er hábil de diciembre.
        o = OUT["CHF_CURVE_MONTH_END_CONTROL.csv"]
        ev = FR.evaluate(o, RULES[o["rule_id"]], PARAMS[o["rule_id"]], CALS,
                         T("2026-11-22T21:43Z"), {date(2026, 10, 30)})
        self.assertEqual(ev["expected_obs"], "2026-10-30")
        self.assertEqual(ev["have_max"], "2026-10-30")
        self.assertEqual(ev["missing"], [])
        self.assertIn(ev["state"], ("CURRENT", "NO_PUBLICATION"))
        self.assertNotIn(ev["state"], FR.LATE_STATES)

    def test_december_first_business_day_expects_november_month_end(self):
        pubs = FR.publications(
            RULES["CHF_CURVE_MONTH_END"], PARAMS["CHF_CURVE_MONTH_END"],
            "CH", "Europe/Zurich", CALS, T("2026-12-01T14:00Z"),
        )
        d, due, _ = pubs[0]
        self.assertEqual(d, date(2026, 11, 30))
        self.assertEqual(due.astimezone(ZoneInfo("Europe/Zurich")).strftime("%Y-%m-%d %H:%M"), "2026-12-01 14:30")
        o = OUT["CHF_CURVE_MONTH_END_CONTROL.csv"]
        # 14:00Z está dentro del margen g=60 min (exig 14:30Z): DUE, due_obs=11-30.
        ev_due = FR.evaluate(o, RULES[o["rule_id"]], PARAMS[o["rule_id"]], CALS,
                             T("2026-12-01T14:00Z"), {date(2026, 10, 30)})
        self.assertEqual((ev_due["state"], ev_due["due_obs"]), ("DUE", "2026-11-30"))
        ev_late = FR.evaluate(o, RULES[o["rule_id"]], PARAMS[o["rule_id"]], CALS,
                              T("2026-12-01T14:31Z"), {date(2026, 10, 30)})
        self.assertIn(ev_late["state"], FR.LATE_STATES)
        self.assertEqual(ev_late["expected_obs"], "2026-11-30")
        ev_ok = FR.evaluate(o, RULES[o["rule_id"]], PARAMS[o["rule_id"]], CALS,
                            T("2026-12-01T14:00Z"), {date(2026, 11, 30)})
        self.assertIn(ev_ok["state"], ("CURRENT", "NO_PUBLICATION"))

    def test_snb1a_daily_fields_other_than_files_unchanged(self):
        # Solo se quita el fichero de control de `files`; el resto de la regla diaria no se toca.
        prev = next(r for r in csv.DictReader(
            __import__("subprocess").check_output(
                ["git", "show", "HEAD:sources/freshness_rules.csv"], text=True
            ).splitlines()
        ) if r["rule_id"] == "CHF_SPOT_SNB1A")
        cur = RULES["CHF_SPOT_SNB1A"]
        for k in prev:
            if k == "files":
                continue
            self.assertEqual(cur[k], prev[k], k)
        self.assertEqual(
            [f for f in prev["files"].split(";") if f != "CHF_CURVE_MONTH_END_CONTROL.csv"],
            (cur.get("files") or "").split(";"),
        )


if __name__ == "__main__":
    unittest.main()

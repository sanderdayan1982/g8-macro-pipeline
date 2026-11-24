"""orden Sander 24-nov t373u: exigibilidad de SOFR tras el turno US de las 16:47.

SOFR conserva pub_local 08:00 America/New_York (hora oficial NY Fed) y pasa a g_min 270:
exigible a las 12:30 ET = 17:30Z (EST) / 16:30Z (EDT), siempre después del turno US
de intraday_fetch (cron 47 15 UTC = 16:47 WAT) con margen. Mismo mecanismo que ES (t371u).
"""
import os
import sys
import unittest
from datetime import date, datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from g8common import freshness as FR  # noqa: E402

RULES, PARAMS, OUTPUTS, CALS = FR.load_config(ROOT)
OUT = {o["file"]: o for o in OUTPUTS}
UTC = timezone.utc


def T(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%MZ").replace(tzinfo=UTC)


def ev(now, have):
    o = OUT["SOFR.csv"]
    return FR.evaluate(o, RULES[o["rule_id"]], PARAMS[o["rule_id"]], CALS, now, set(have))


class SofrExigibility(unittest.TestCase):
    # orden Sander 24-nov t373u
    def test_catalog_pub_local_0800_ny_and_g_min_270(self):
        r, p = RULES["SOFR"], PARAMS["SOFR"]
        self.assertEqual(r["pub_local"], "08:00")
        self.assertEqual(r["timezone"], "America/New_York")
        self.assertEqual(r["pub_offset_bd"], "1")
        self.assertEqual(p["model"], "bd_offset")
        self.assertEqual(p["g_min"], "270")

    def test_winter_not_late_before_us_turn_and_margin(self):
        # Martes 24-nov-2026 (EST): obs. del 23-nov publicada ~08:00 ET; repo con 20-nov.
        have = {date(2026, 11, 20)}
        for now in ("2026-11-24T14:32Z",   # corte de las 15:32 WAT (el falso LATE)
                    "2026-11-24T15:47Z",   # turno US (16:47 WAT)
                    "2026-11-24T17:29Z"):  # 18:29 WAT, aún no exigible
            e = ev(T(now), have)
            self.assertNotIn(e["state"], FR.LATE_STATES, now)
            self.assertEqual(e.get("expected_obs"), "2026-11-20", now)

    def test_winter_overdue_after_new_deadline(self):
        e = ev(T("2026-11-24T17:30Z"), {date(2026, 11, 20)})  # 12:30 ET = 18:30 WAT
        self.assertEqual(e["state"], "OVERDUE")
        self.assertEqual(e.get("expected_obs"), "2026-11-23")

    def test_winter_current_once_turn_brings_it(self):
        e = ev(T("2026-11-24T17:30Z"), {date(2026, 11, 20), date(2026, 11, 23)})
        self.assertEqual(e["state"], "CURRENT")

    def test_summer_deadline_still_after_us_turn(self):
        # Miércoles 15-jul-2026 (EDT): exigible 12:30 EDT = 16:30Z, 43 min tras el turno US.
        have = {date(2026, 7, 13)}
        e = ev(T("2026-07-15T15:47Z"), have)
        self.assertNotIn(e["state"], FR.LATE_STATES)
        e = ev(T("2026-07-15T16:29Z"), have)
        self.assertNotIn(e["state"], FR.LATE_STATES)
        e = ev(T("2026-07-15T16:30Z"), have)
        self.assertEqual(e["state"], "OVERDUE")
        self.assertEqual(e.get("expected_obs"), "2026-07-14")

    def test_deadline_after_us_turn_in_both_seasons(self):
        r, p = RULES["SOFR"], PARAMS["SOFR"]
        for now, turn in ((T("2026-11-24T23:00Z"), T("2026-11-24T15:47Z")),
                          (T("2026-07-15T23:00Z"), T("2026-07-15T15:47Z"))):
            pubs = FR.publications(r, p, r["calendar"], r["timezone"], CALS, now)
            _obs, _due, exig = pubs[0]
            self.assertGreaterEqual((exig - turn).total_seconds(), 40 * 60, now)


if __name__ == "__main__":
    unittest.main()

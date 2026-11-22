"""t340u: el spread ES−DE muestra la fecha de su última sesión común; no LATE falso en ES T+1–T+2.

Las reglas de frescura DE10Y_PAR / ES10Y_DAILY / ES_DE_10Y_SPREAD ya existen (t334u)
y no se modifican. Este test comprueba su comportamiento y el del fichero de spread.
"""
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import fetch_sovereign_10y_daily as S  # noqa: E402
from g8common import freshness as FR  # noqa: E402

RULES, PARAMS, OUTPUTS, CALS = FR.load_config(ROOT)
OUT = {o["file"]: o for o in OUTPUTS}
UTC = timezone.utc


def T(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%MZ").replace(tzinfo=UTC)


class LastCommonSession(unittest.TestCase):
    def test_last_valued_spread_date_is_last_common_session(self):
        de = {date(2026, 11, 18): 2.50, date(2026, 11, 19): 2.51, date(2026, 11, 20): 2.52}
        es = {date(2026, 11, 18): 3.10, date(2026, 11, 19): 3.11}  # 20-nov solo en DE
        sp = S.same_session_spread(es, de)
        valued = [d for d, (_l, _r, s) in sp.items() if s is not None]
        self.assertEqual(max(valued), date(2026, 11, 19))
        self.assertIsNone(sp[date(2026, 11, 20)][2])
        text = S.render_spread(sp)
        last_valued = None
        for line in text.splitlines()[1:]:
            parts = line.split(",")
            if parts[3].strip():
                last_valued = parts[0]
        self.assertEqual(last_valued, "2026-11-19")

    def test_freshness_expected_obs_is_last_common_session(self):
        o = OUT["ES_DE_10Y_SPREAD_DAILY.csv"]
        # have_dates del fichero incluye filas en blanco (diseño t331u B3); have_max puede
        # ser posterior. La fecha exigible es la mínima de las últimas obs. de las patas.
        def input_max_at(fname, _now):
            if fname == "DE_10Y_PAR_DAILY.csv":
                return date(2026, 11, 20)
            if fname == "ES_10Y_DAILY.csv":
                return date(2026, 11, 18)
            return None

        ev = FR.evaluate(
            o, RULES[o["rule_id"]], PARAMS[o["rule_id"]], CALS,
            T("2026-11-23T13:00Z"),  # 14:00 WAT
            {date(2026, 11, 18), date(2026, 11, 19), date(2026, 11, 20)},
            input_max_at=input_max_at,
        )
        self.assertEqual(ev["expected_obs"], "2026-11-18")
        self.assertEqual(ev["state"], "CURRENT")

    def test_es_tplus2_ceiling_is_not_late_before_next_exigency(self):
        # Viernes 20-nov publicó obs. del 18-nov (T+2). Lunes 23-nov 14:00 WAT:
        # la obs. del 19-nov aún no es exigible (sin pub_local, exig = medianoche del día siguiente).
        o = OUT["ES_10Y_DAILY.csv"]
        ev = FR.evaluate(
            o, RULES[o["rule_id"]], PARAMS[o["rule_id"]], CALS,
            T("2026-11-23T13:00Z"),
            {date(2026, 11, 18)},
        )
        self.assertNotIn(ev["state"], FR.LATE_STATES)
        self.assertEqual(ev.get("expected_obs"), "2026-11-18")

    def test_de_t0_friday_obs_current_before_monday_pub_window(self):
        # Antes de las 12:29 Europe/Berlin del lunes, la obs. del viernes sigue vigente.
        o = OUT["DE_10Y_PAR_DAILY.csv"]
        ev = FR.evaluate(
            o, RULES[o["rule_id"]], PARAMS[o["rule_id"]], CALS,
            T("2026-11-23T10:00Z"),  # 11:00 WAT, antes de 12:29 CET
            {date(2026, 11, 20)},
        )
        self.assertNotIn(ev["state"], FR.LATE_STATES)
        self.assertEqual(ev["expected_obs"], "2026-11-20")
        self.assertEqual(ev["have_max"], "2026-11-20")

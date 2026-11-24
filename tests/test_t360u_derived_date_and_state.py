"""orden Sander 24-nov t360u / t363u / t364u (PR A): fecha de un derivado =
última fila completa de las entradas motoras (o de todas si no hay motoras);
estado = peor estado operativo de las entradas, cada una con su calendario.
AUD_NOWCAST sigue EST y no vota. Sin pub_local ni g_min.
"""
import os
import sys
import unittest
from datetime import date, datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import fetch_sovereign_10y_daily as S  # noqa: E402
from g8common import freshness as FR  # noqa: E402

RULES, PARAMS, OUTPUTS, CALS = FR.load_config(ROOT)
OUT = {o["file"]: o for o in OUTPUTS}
UTC = timezone.utc


def T(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%MZ").replace(tzinfo=UTC)


class DerivedDateAndState(unittest.TestCase):
    # orden Sander 24-nov t360u
    def test_complete_obs_dates_ignore_partial_spread_rows(self):
        de = {date(2026, 11, 18): 2.50, date(2026, 11, 19): 2.51, date(2026, 11, 20): 2.52,
              date(2026, 11, 23): 2.53}
        es = {date(2026, 11, 18): 3.10, date(2026, 11, 19): 3.11}
        text = S.render_spread(S.same_session_spread(es, de))
        complete = FR.complete_obs_dates_from_bytes(text.encode("utf-8"))
        all_dates = FR.obs_dates_from_bytes(text.encode("utf-8"))
        self.assertEqual(max(complete), date(2026, 11, 19))
        self.assertEqual(max(all_dates), date(2026, 11, 23))
        self.assertNotIn(date(2026, 11, 20), complete)
        self.assertNotIn(date(2026, 11, 23), complete)

    def test_spread_pending_with_date_last_complete_session(self):
        # orden Sander 24-nov t360u
        # Con ES en PENDING, el spread sale PENDING con fecha 19-nov (filas 20/23 solo DE).
        o = OUT["ES_DE_10Y_SPREAD_DAILY.csv"]
        de_obs = {date(2026, 11, 18), date(2026, 11, 19), date(2026, 11, 20), date(2026, 11, 23)}
        es_obs = {date(2026, 11, 18), date(2026, 11, 19)}
        ev = FR.evaluate(
            o, RULES[o["rule_id"]], PARAMS[o["rule_id"]], CALS,
            T("2026-11-24T09:00Z"),
            {date(2026, 11, 18), date(2026, 11, 19), date(2026, 11, 20), date(2026, 11, 23)},
            input_max_at=lambda f, _n: date(2026, 11, 23) if "DE_" in f else date(2026, 11, 19),
            input_states={"DE_10Y_PAR_DAILY.csv": "CURRENT", "ES_10Y_DAILY.csv": "PENDING_TIME_UNKNOWN"},
            input_obs={"DE_10Y_PAR_DAILY.csv": de_obs, "ES_10Y_DAILY.csv": es_obs},
            input_dates={"DE_10Y_PAR_DAILY.csv": date(2026, 11, 23), "ES_10Y_DAILY.csv": date(2026, 11, 19)},
        )
        self.assertEqual(ev["have_max"], "2026-11-19")
        self.assertEqual(ev["state"], "PENDING_TIME_UNKNOWN")
        self.assertEqual(ev.get("last_common_session"), "2026-11-19")

    def test_driver_sets_date_monthly_current_does_not_worsen(self):
        # orden Sander 24-nov t364u (b): ACM_G8_CHF, motora CHF_NOM_10Y.
        # Spots mensuales al día (NO_PUBLICATION, 30-oct) no atrasan la fecha ni el estado.
        o = OUT["ACM_G8_CHF.csv"]
        nom = "CHF_NOM_10Y.csv"
        spots = [f"CHF_SPOT_{i}Y.csv" for i in range(1, 11)]
        have = {date(2026, 10, 30), date(2026, 11, 20), date(2026, 11, 23)}
        nom_obs = {date(2026, 11, 20), date(2026, 11, 23)}
        spot_obs = {date(2026, 10, 30)}
        input_obs = {nom: nom_obs, **{f: spot_obs for f in spots}}
        input_states = {nom: "CURRENT", **{f: "NO_PUBLICATION" for f in spots}}
        ev = FR.evaluate(
            o, RULES[o["rule_id"]], PARAMS[o["rule_id"]], CALS,
            T("2026-11-24T11:00Z"),
            have,
            input_max_at=lambda f, _n: date(2026, 11, 23) if f == nom else date(2026, 10, 30),
            input_states=input_states,
            input_obs=input_obs,
            input_dates={nom: date(2026, 11, 23), **{f: date(2026, 10, 30) for f in spots}},
        )
        self.assertEqual(ev["have_max"], "2026-11-23")
        self.assertEqual(ev["state"], "CURRENT")
        self.assertNotEqual(ev["state"], "BEHIND_INPUTS")
        self.assertEqual(ev.get("last_common_session"), "2026-11-23")

    def test_inherit_operational_pending_within_b1_sla(self):
        # orden Sander 24-nov t364u (c): ACM_G8_NZD / RY_G8_NZD heredan el
        # estado operativo PENDING (DUE) mientras las entradas B2 siguen
        # dentro de la gracia B1 (hasta 25-nov 05:04 WAT = 04:04Z).
        o = OUT["ACM_G8_NZD.csv"]
        inputs = [x for x in o["inputs"].split(";") if x]
        have = {date(2026, 11, 20)}
        obs = {date(2026, 11, 19), date(2026, 11, 20)}
        meta = {f: {"cause": "SIN_PASADA_PROGRAMADA", "overdue_since": "2026-11-24T04:04:00Z",
                    "calendar": "NZ", "missing": ["2026-11-23"], "expected_obs": "2026-11-23"}
                for f in inputs}
        states = {f: "OVERDUE" for f in inputs}

        ev = FR.evaluate(
            o, RULES[o["rule_id"]], PARAMS[o["rule_id"]], CALS,
            T("2026-11-24T12:00Z"),
            have,
            input_max_at=lambda f, _n: date(2026, 11, 20),
            input_states=states, input_obs={f: obs for f in inputs},
            input_dates={f: date(2026, 11, 20) for f in inputs},
            input_meta=meta,
        )
        self.assertEqual(ev["have_max"], "2026-11-20")
        self.assertEqual(ev["state"], "DUE")  # operativo PENDING

        ev_after = FR.evaluate(
            o, RULES[o["rule_id"]], PARAMS[o["rule_id"]], CALS,
            T("2026-11-25T04:04Z"),
            have,
            input_max_at=lambda f, _n: date(2026, 11, 20),
            input_states=states, input_obs={f: obs for f in inputs},
            input_dates={f: date(2026, 11, 20) for f in inputs},
            input_meta=meta,
        )
        self.assertEqual(ev_after["state"], "OVERDUE")
        self.assertEqual(ev_after.get("cause"), "SIN_PASADA_PROGRAMADA")
        self.assertEqual(ev_after.get("missing"), ["2026-11-23"])

        o_ry = OUT["RY_G8_NZD.csv"]
        ry_inputs = [x for x in o_ry["inputs"].split(";") if x]
        ry_meta = {f: meta[inputs[0]] for f in ry_inputs}
        ev_ry = FR.evaluate(
            o_ry, RULES[o_ry["rule_id"]], PARAMS[o_ry["rule_id"]], CALS,
            T("2026-11-24T12:00Z"),
            have,
            input_max_at=lambda f, _n: date(2026, 11, 20),
            input_states={f: "OVERDUE" for f in ry_inputs},
            input_obs={f: obs for f in ry_inputs},
            input_dates={f: date(2026, 11, 20) for f in ry_inputs},
            input_meta=ry_meta,
        )
        self.assertEqual(ev_ry["state"], "DUE")

    def test_mfv_no_publication_accepted(self):
        # orden Sander 24-nov t364u (d): NO_PUBLICATION se acepta en MFV XAU/XAG.
        o = OUT["MFV_G8_XAU.csv"]
        ev = FR.evaluate(
            o, RULES[o["rule_id"]], PARAMS[o["rule_id"]], CALS,
            T("2026-11-24T11:00Z"),
            {date(2026, 11, 24)},
            input_max_at=lambda f, _n: date(2026, 11, 17),
            input_states={"pos_g8_cot.json": "NO_PUBLICATION"},
            input_obs={"pos_g8_cot.json": {date(2026, 11, 17)}},
            input_dates={"pos_g8_cot.json": date(2026, 11, 17)},
        )
        self.assertEqual(ev["state"], "NO_PUBLICATION")
        self.assertEqual(ev["have_max"], "2026-11-24")


if __name__ == "__main__":
    unittest.main()

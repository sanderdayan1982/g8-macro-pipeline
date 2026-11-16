"""FAMILIAS-01 C — estados FXVOL en sombra.

Fixtures sintéticas. Ningún número de CME. Con FXVOL_STATES_PUBLISH en False
el JSON público no gana claves.
"""
import json
import os
import re
import sys
import unittest
from datetime import date, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import cme_fxvol_shadow as FX  # noqa: E402
import fxvol_states as S  # noqa: E402

KEYS_PATH = os.path.join(ROOT, "tests", "fixtures", "familias01_c", "main_build_keys.txt")
NEW_FIELDS = S.PUBLISHED_FIELDS
FORBIDDEN = (
    "vol_state",
    "premium_state",
    "skew_state",
    "skew_turn",
    "sk_z",
    "vrp_pct",
    "states_prov",
    "states_reason",
    "SK-TURN",
    "CALL-BID",
    "PUT-BID",
)


def bdays(n, start="2024-01-02"):
    cursor = date.fromisoformat(start)
    out = []
    while len(out) < n:
        if cursor.weekday() < 5:
            out.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return out


def rows(n, iv=5.0, rr=1.0, bf=0.1, px0=100.0, contract="SYN", start="2024-01-02"):
    made = []
    for index, session in enumerate(bdays(n, start)):
        made.append({
            "session": session,
            "status": "OK",
            "iv30_atm": iv(index) if callable(iv) else iv,
            "rr25": rr(index) if callable(rr) else rr,
            "bf25": bf(index) if callable(bf) else bf,
            "px_contract": contract,
            "px_settle": px0 + index * 0.05,
        })
    return made


def replay_vol(observations):
    state, arm, count = "NORMAL", None, 0
    published = []
    for item in observations:
        if item is None:
            published.append("UNAVAILABLE")
            state, arm, count = "NORMAL", None, 0
            continue
        pct, d1w = item
        desired = S.desired_vol(state, pct, d1w)
        state, arm, count = S.step_hysteresis(state, arm, count, desired)
        published.append(state)
    return published


def replay_premium(pcts):
    state, arm, count = S.DOT, None, 0
    published = []
    for pct in pcts:
        if pct is None:
            published.append("UNAVAILABLE")
            state, arm, count = S.DOT, None, 0
            continue
        state, arm, count = S.step_hysteresis(state, arm, count, S.desired_premium(state, pct))
        published.append(state)
    return published


def replay_skew(zs):
    state, arm, count = S.DOT, None, 0
    published = []
    for z in zs:
        if z is None:
            published.append("UNAVAILABLE")
            state, arm, count = S.DOT, None, 0
            continue
        state, arm, count = S.step_hysteresis(state, arm, count, S.desired_skew(state, z))
        published.append(state)
    return published


def key_paths(obj, prefix=""):
    if not isinstance(obj, dict):
        return []
    found = []
    for key, value in obj.items():
        path = prefix + "." + key if prefix else key
        found.append(path)
        found.extend(key_paths(value, path))
    return found


class Familias01C(unittest.TestCase):
    def test_t_c1_borders_inclusive_and_strict(self):
        # IV_pct: 20 entra en QUIET, 30 no sale; 80 entra en BID, 70 no sale; 95/85 igual.
        self.assertEqual(S.desired_vol("NORMAL", 20, 0), "QUIET")
        self.assertEqual(S.desired_vol("NORMAL", 20.1, 0), "NORMAL")
        self.assertEqual(S.desired_vol("QUIET", 30, 0), "QUIET")
        self.assertEqual(S.desired_vol("QUIET", 30.1, 0), "NORMAL")
        self.assertEqual(S.desired_vol("NORMAL", 80, 0), "BID")
        self.assertEqual(S.desired_vol("NORMAL", 79.9, 0), "NORMAL")
        self.assertEqual(S.desired_vol("BID", 70, 0), "BID")
        self.assertEqual(S.desired_vol("BID", 69.9, 0), "NORMAL")
        self.assertEqual(S.desired_vol("NORMAL", 95, 0.01), "STRESS")
        self.assertEqual(S.desired_vol("NORMAL", 94.9, 0.01), "BID")
        self.assertEqual(S.desired_vol("STRESS", 85, 0), "STRESS")
        self.assertEqual(S.desired_vol("STRESS", 84.9, 0), "BID")
        self.assertEqual(S.desired_vol("STRESS", 69.9, 0), "NORMAL")

        lows = [0.0] * 20 + [5.0] * 80
        self.assertEqual(S.pct_rank(lows, 0.0), 20.0)
        self.assertEqual(S.pct_rank([0.0] * 30 + [5.0] * 70, 0.0), 30.0)
        self.assertEqual(S.pct_rank([0.0] * 70 + [5.0] * 30, 0.0), 70.0)
        self.assertEqual(S.pct_rank([0.0] * 80 + [5.0] * 20, 0.0), 80.0)
        self.assertEqual(S.pct_rank([0.0] * 85 + [5.0] * 15, 0.0), 85.0)
        self.assertEqual(S.pct_rank([0.0] * 95 + [5.0] * 5, 0.0), 95.0)

        # VRP_pct: 90 entra, 80 no sale.
        self.assertEqual(S.desired_premium(S.DOT, 90), "EVENT")
        self.assertEqual(S.desired_premium(S.DOT, 89.9), S.DOT)
        self.assertEqual(S.desired_premium("EVENT", 80), "EVENT")
        self.assertEqual(S.desired_premium("EVENT", 79.9), S.DOT)
        self.assertEqual(S.pct_rank([0.0] * 90 + [1.0] * 10, 0.0), 90.0)
        self.assertEqual(S.pct_rank([0.0] * 80 + [1.0] * 20, 0.0), 80.0)

        # SK_z: ±1,5 entra, ±1,0 no sale.
        self.assertEqual(S.desired_skew(S.DOT, 1.5), "CALL-BID")
        self.assertEqual(S.desired_skew(S.DOT, 1.499), S.DOT)
        self.assertEqual(S.desired_skew("CALL-BID", 1.0), "CALL-BID")
        self.assertEqual(S.desired_skew("CALL-BID", 0.999), S.DOT)
        self.assertEqual(S.desired_skew(S.DOT, -1.5), "PUT-BID")
        self.assertEqual(S.desired_skew(S.DOT, -1.499), S.DOT)
        self.assertEqual(S.desired_skew("PUT-BID", -1.0), "PUT-BID")
        self.assertEqual(S.desired_skew("PUT-BID", -0.999), S.DOT)

        # Un cierre no cambia; el segundo sí. En el borde y justo al otro lado.
        self.assertEqual(replay_vol([(20, 0)])[-1], "NORMAL")
        self.assertEqual(replay_vol([(20, 0), (20, 0)])[-1], "QUIET")
        self.assertEqual(replay_vol([(20.1, 0), (20.1, 0)])[-1], "NORMAL")
        self.assertEqual(replay_premium([90])[-1], S.DOT)
        self.assertEqual(replay_premium([90, 90])[-1], "EVENT")
        self.assertEqual(replay_premium([89.9, 89.9])[-1], S.DOT)
        self.assertEqual(replay_skew([1.5])[-1], S.DOT)
        self.assertEqual(replay_skew([1.5, 1.5])[-1], "CALL-BID")
        self.assertEqual(replay_skew([-1.5, -1.5])[-1], "PUT-BID")
        self.assertEqual(replay_skew([1.499, 1.499])[-1], S.DOT)
        held = replay_vol([(10, 0), (10, 0), (30, 0), (30, 0)])
        self.assertEqual(held[-1], "QUIET")
        left = replay_vol([(10, 0), (10, 0), (30.1, 0), (30.1, 0)])
        self.assertEqual(left[-1], "NORMAL")

    def test_t_c2_stress_needs_positive_one_week_change(self):
        self.assertEqual(S.desired_vol("NORMAL", 96, 0), "BID")
        self.assertEqual(S.desired_vol("NORMAL", 96, -0.01), "BID")
        self.assertEqual(S.desired_vol("BID", 96, 0), "BID")
        self.assertNotEqual(replay_vol([(96, 0), (96, 0)])[-1], "STRESS")
        self.assertEqual(replay_vol([(96, 0), (96, 0)])[-1], "BID")
        self.assertEqual(replay_vol([(96, 0.01), (96, 0.01)])[-1], "STRESS")

    def test_t_c3_hysteresis_two_closes_and_vol_sequence(self):
        sequence = [
            (10, 0), (10, 0),
            (40, 0), (40, 0),
            (85, 0), (85, 0),
            (96, 0.5), (96, 0.5),
            (75, 0), (75, 0),
            (50, 0), (50, 0),
        ]
        got = replay_vol(sequence)
        self.assertEqual(got[0], "NORMAL")
        self.assertEqual(
            got,
            [
                "NORMAL", "QUIET",
                "QUIET", "NORMAL",
                "NORMAL", "BID",
                "BID", "STRESS",
                "STRESS", "BID",
                "BID", "NORMAL",
            ],
        )
        # La salida de STRESS con percentil ≥ 70 confirma BID, no NORMAL.
        self.assertEqual(S.desired_vol("STRESS", 75, 0), "BID")
        self.assertEqual(S.desired_vol("STRESS", 50, 0), "NORMAL")

    def test_t_c4_skew_turn_needs_sign_change_and_p90(self):
        ones = [1.0] * 60
        self.assertEqual(S.turn_label(1.0, ones, 1.0, -1.0)[0], "SK-TURN")
        self.assertEqual(S.turn_label(1.0, ones, 1.0, 1.0)[0], S.DOT)
        small = [1.0] * 59 + [0.5]
        self.assertEqual(S.turn_label(0.5, small, -1.0, 1.0)[0], S.DOT)

        quiet = rows(71, rr=1.0)
        quiet[-1]["rr25"] = -4.0
        same = rows(71, rr=1.0)
        same[-1]["rr25"] = 4.0
        turned = S.compute_fxvol_states({"EUR": quiet})["EUR"][-1]
        unturned = S.compute_fxvol_states({"EUR": same})["EUR"][-1]
        self.assertEqual(turned["skew_turn"], "SK-TURN")
        self.assertEqual(unturned["skew_turn"], S.DOT)

    def test_t_c5_r11_flips_jpy_cad_chf_once(self):
        self.assertEqual(S.r11_level("JPY", 1.25), -1.25)
        self.assertEqual(S.r11_level("CAD", 1.25), -1.25)
        self.assertEqual(S.r11_level("CHF", 1.25), -1.25)
        self.assertEqual(S.r11_level("EUR", 1.25), 1.25)
        self.assertEqual(S.r11_level("GBP", 1.25), 1.25)
        self.assertEqual(S.r11_level("AUD", 1.25), 1.25)
        # Aplicar la función otra vez deshace el giro: en la serie solo entra una vez.
        self.assertEqual(S.r11_level("JPY", S.r11_level("JPY", 1.25)), 1.25)

        made = rows(80, rr=lambda i: (i - 40) * 0.05)
        out = S.compute_fxvol_states({ccy: [dict(row) for row in made] for ccy in ("EUR", "JPY", "CAD", "CHF", "GBP")})
        eur = out["EUR"][-1]["sk_z"]
        self.assertIsInstance(eur, float)
        self.assertGreater(eur, 0)
        for ccy in ("JPY", "CAD", "CHF"):
            self.assertAlmostEqual(out[ccy][-1]["sk_z"], -eur, places=6)
        self.assertAlmostEqual(out["GBP"][-1]["sk_z"], eur, places=6)

    def test_t_c6_provisional_window_at_59_60_251_252(self):
        def last(n, **kwargs):
            return S.compute_fxvol_states({"EUR": rows(n, rr=lambda i: 0.01 * (i + 1), **kwargs)})["EUR"][-1]

        short = last(59)
        self.assertEqual(short["vol_state"], "UNAVAILABLE")
        self.assertFalse(short["states_prov"]["vol"])
        ready = last(60)
        self.assertNotEqual(ready["vol_state"], "UNAVAILABLE")
        self.assertTrue(ready["states_prov"]["vol"])
        almost = last(251)
        self.assertTrue(almost["states_prov"]["vol"])
        full = last(252)
        self.assertFalse(full["states_prov"]["vol"])
        self.assertNotEqual(full["vol_state"], "UNAVAILABLE")

        self.assertEqual(last(59)["skew_state"], "UNAVAILABLE")
        self.assertFalse(last(59)["states_prov"]["skew"])
        self.assertNotEqual(last(60)["skew_state"], "UNAVAILABLE")
        self.assertTrue(last(60)["states_prov"]["skew"])
        self.assertTrue(last(251)["states_prov"]["skew"])
        self.assertFalse(last(252)["states_prov"]["skew"])

        # 252 VRP: la primera aparece al tener 22 settles (índice 21). n = sesiones − 21.
        self.assertEqual(last(80)["premium_state"], "UNAVAILABLE")
        self.assertFalse(last(80)["states_prov"]["premium"])
        self.assertNotEqual(last(81)["premium_state"], "UNAVAILABLE")
        self.assertTrue(last(81)["states_prov"]["premium"])
        self.assertTrue(last(272)["states_prov"]["premium"])
        self.assertFalse(last(273)["states_prov"]["premium"])
        self.assertNotEqual(last(273)["premium_state"], "UNAVAILABLE")

        # 252 |dSK|: el primer dSK es la sesión 6 (índice 5). n = sesiones − 5.
        self.assertEqual(last(64)["skew_turn"], "UNAVAILABLE")
        self.assertFalse(last(64)["states_prov"]["turn"])
        self.assertNotEqual(last(65)["skew_turn"], "UNAVAILABLE")
        self.assertTrue(last(65)["states_prov"]["turn"])
        self.assertTrue(last(256)["states_prov"]["turn"])
        self.assertFalse(last(257)["states_prov"]["turn"])

        self.assertIn("[PROV]", ready["states_reason"])
        # IV a 252 sesiones ya no es provisional; VRP llega a 252 observaciones
        # más tarde (21 rendimientos de arranque). Las cuatro columnas, a la vez.
        complete = last(273)
        self.assertFalse(any(complete["states_prov"].values()))
        self.assertNotIn("[PROV]", complete["states_reason"])

    def test_t_c7_missing_quote_is_unavailable_and_resets(self):
        base = rows(70, rr=lambda i: 0.01 * (i + 1))
        hole_iv = [dict(row) for row in base]
        hole_iv.append(dict(base[-1], session=bdays(71)[-1], status="NA", iv30_atm=None))
        hole_iv.append(dict(base[-1], session=bdays(72)[-1]))
        series = S.compute_fxvol_states({"EUR": hole_iv})["EUR"]
        self.assertEqual(series[-3]["vol_state"], "BID")
        gap = series[-2]
        for key in ("vol_state", "premium_state", "skew_state", "skew_turn"):
            self.assertEqual(gap[key], "UNAVAILABLE", key)
        self.assertEqual(gap["sk_z"], "UNAVAILABLE")
        self.assertEqual(gap["vrp_pct"], "UNAVAILABLE")
        self.assertNotEqual(gap["sk_z"], 0)
        self.assertNotEqual(gap["sk_z"], series[-3]["sk_z"])
        self.assertIn("falta IV", gap["states_reason"])
        # Al día siguiente se parte de NORMAL / «·», no del BID previo.
        self.assertEqual(series[-1]["vol_state"], "NORMAL")
        self.assertNotEqual(series[-1]["vol_state"], "BID")

        for field, token in (("rr25", "falta RR"), ("bf25", "falta BF")):
            punched = [dict(row) for row in base]
            punched[-1] = dict(punched[-1], status="NA", **{field: None})
            got = S.compute_fxvol_states({"EUR": punched})["EUR"][-1]
            self.assertEqual(got["vol_state"], "UNAVAILABLE", field)
            self.assertIn(token, got["states_reason"])
            self.assertNotEqual(got["sk_z"], 0)

        # Un cero presente no es un dato ausente.
        zeros = rows(60, bf=0.0, iv=0.0, rr=lambda i: 0.01 * (i + 1))
        self.assertNotEqual(S.compute_fxvol_states({"EUR": zeros})["EUR"][-1]["vol_state"], "UNAVAILABLE")

        # La sesión ausente no cuenta en la ventana ni se rellena.
        full = rows(60)
        self.assertNotEqual(S.compute_fxvol_states({"EUR": full})["EUR"][-1]["vol_state"], "UNAVAILABLE")
        gapped = [dict(row) for row in full]
        gapped[10] = dict(gapped[10], status="NA", iv30_atm=None, rr25=None, bf25=None)
        self.assertEqual(S.compute_fxvol_states({"EUR": gapped})["EUR"][-1]["vol_state"], "UNAVAILABLE")

        # Δ1W pide las dos puntas. Hoy está, hace 5 sesiones no: la columna de vol
        # queda UNAVAILABLE y no se rellena el hueco. La prima, si tiene VRP, sigue.
        lagged = rows(90, rr=lambda i: 0.01 * (i + 1))
        lagged[-6] = dict(lagged[-6], status="NA", iv30_atm=None)
        lagged_last = S.compute_fxvol_states({"EUR": lagged})["EUR"][-1]
        self.assertEqual(lagged_last["vol_state"], "UNAVAILABLE")
        self.assertIn("falta dIV1w", lagged_last["states_reason"])
        self.assertNotEqual(lagged_last["premium_state"], "UNAVAILABLE")
        self.assertIn("falta dSK", lagged_last["states_reason"])

        # Sin 21 rendimientos del mismo contrato, la prima no se clasifica.
        # El vol, que no usa el futuro, sí.
        no_rv = rows(70, rr=lambda i: 0.01 * (i + 1))
        for item in no_rv[-25:]:
            item["px_settle"] = None
            item["px_contract"] = None
        no_rv_last = S.compute_fxvol_states({"EUR": no_rv})["EUR"][-1]
        self.assertEqual(no_rv_last["premium_state"], "UNAVAILABLE")
        self.assertIn("falta RV21", no_rv_last["states_reason"])
        self.assertNotEqual(no_rv_last["vol_state"], "UNAVAILABLE")

        text = (
            "session,ccy,status,reason,iv30_atm,rr25,bf25,px_contract,px_settle\n"
            "2024-01-02,EUR,NA,hueco,,,,,\n"
        )
        parsed = S.parse_history_csv(text)["EUR"][0]
        self.assertIsNone(S._num(parsed["iv30_atm"]))
        self.assertIsNone(S._num(parsed["rr25"]))
        self.assertIsNone(S._num(parsed["bf25"]))
        self.assertIsNone(S._num(""))

    def test_t_c8_nzd_is_na_and_leaves_the_rest(self):
        eur = rows(70, rr=lambda i: 0.01 * (i + 1))
        nzd = [{
            "session": eur[-1]["session"],
            "status": "OK",
            "iv30_atm": 99.0,
            "rr25": 3.0,
            "bf25": 1.0,
            "px_contract": "SYN",
            "px_settle": 1.0,
        }]
        out = S.compute_fxvol_states({"EUR": eur, "NZD": nzd})
        self.assertEqual(out["NZD"][0]["vol_state"], "NA")
        self.assertEqual(out["NZD"][0]["premium_state"], "NA")
        self.assertEqual(out["NZD"][0]["skew_state"], "NA")
        self.assertEqual(out["NZD"][0]["skew_turn"], "NA")
        self.assertEqual(out["NZD"][0]["sk_z"], "NA")
        self.assertEqual(out["NZD"][0]["vrp_pct"], "NA")
        self.assertEqual(out["NZD"][0]["states_reason"], "NO_CME_OPTIONS")
        self.assertNotEqual(out["EUR"][-1]["vol_state"], "NA")
        self.assertNotEqual(out["EUR"][-1]["vol_state"], "UNAVAILABLE")
        # Sin fila NZD en la entrada también sale NA, y no rompe a la divisa que sí está.
        only = S.compute_fxvol_states({"EUR": eur})
        self.assertEqual(only["NZD"][0]["vol_state"], "NA")
        self.assertEqual(only["EUR"][-1]["vol_state"], out["EUR"][-1]["vol_state"])

    def test_t_c9_stale_on_the_second_business_day(self):
        # Viernes 2026-01-02. Lunes es el 1.er hábil; martes, el 2.º.
        friday = "2026-01-02"
        made = rows(40, start="2025-11-10")
        made[-1]["session"] = friday
        fresh = S.compute_fxvol_states({"EUR": made}, asof=friday)["EUR"][-1]
        monday = S.compute_fxvol_states({"EUR": made}, asof="2026-01-05")["EUR"][-1]
        tuesday = S.compute_fxvol_states({"EUR": made}, asof="2026-01-06")["EUR"][-1]
        self.assertNotEqual(fresh["vol_state"], "STALE")
        self.assertNotEqual(monday["vol_state"], "STALE")
        self.assertEqual(monday["vol_state"], fresh["vol_state"])
        for key in ("vol_state", "premium_state", "skew_state", "skew_turn"):
            self.assertEqual(tuesday[key], "STALE", key)
        self.assertIn("STALE", tuesday["states_reason"])
        self.assertEqual(S.business_days_after(friday, "2026-01-05"), 1)
        self.assertEqual(S.business_days_after(friday, "2026-01-06"), 2)

    def test_t_c10_appending_a_session_does_not_repaint(self):
        base = rows(80, rr=lambda i: 0.02 * i)
        before = S.compute_fxvol_states({"EUR": base})["EUR"]
        extra = dict(base[-1])
        extra["session"] = bdays(81)[-1]
        extra["iv30_atm"] = 80.0
        extra["rr25"] = -3.0
        after = S.compute_fxvol_states({"EUR": base + [extra]})["EUR"]
        self.assertEqual(len(after), len(before) + 1)
        self.assertEqual(after[:-1], before)

    def test_t_c11_existing_keys_stay_byte_identical(self):
        doc = {
            "method": {"model": "Black-76", "pct_window": [60, 252]},
            "currencies": {"EUR": {"vol_regime": "NEUTRAL", "iv_pct": 33.3, "vrp": 0.25}},
        }
        blob = json.dumps(doc, sort_keys=True, ensure_ascii=False)
        untouched = json.loads(blob)
        self.assertIs(FX.FXVOL_STATES_PUBLISH, False)
        if FX.FXVOL_STATES_PUBLISH:
            S.publish_states(untouched, {"EUR": [_sample_state()]})
        self.assertEqual(json.dumps(untouched, sort_keys=True, ensure_ascii=False), blob)

        published = json.loads(blob)
        S.publish_states(published, {"EUR": [_sample_state()]})
        eur = published["currencies"]["EUR"]
        self.assertEqual(eur["vol_regime"], "NEUTRAL")
        self.assertEqual(eur["iv_pct"], 33.3)
        self.assertEqual(eur["vrp"], 0.25)
        for key in NEW_FIELDS:
            self.assertIn(key, eur)
        self.assertIn("states", published["method"])
        self.assertEqual(published["method"]["model"], "Black-76")
        self.assertEqual(published["method"]["states"]["vote"], 0)
        self.assertIs(published["method"]["states"]["blocked_entry"], False)
        # vol_regime no se recalcula: los bordes de siempre siguen donde estaban.
        self.assertEqual(FX.vol_regime_field(19.9, 0.01), "COMPRESSION")
        self.assertEqual(FX.vol_regime_field(20, 1), "NEUTRAL")
        self.assertEqual(FX.vol_regime_field(80.1, -1), "EXPANSION")
        self.assertEqual(FX.vol_regime_field("NA", 1), "UNAVAILABLE")

    def test_t_c12_public_docs_and_outputs_omit_state_fields(self):
        roots = [
            os.path.join(ROOT, "docs"),
            os.path.join(ROOT, "data", "CME_FXVOL_shadow.json"),
            os.path.join(ROOT, "data", "options", "fxvol_history.csv"),
        ]
        hits = []
        for path in roots:
            if os.path.isdir(path):
                for dirpath, _, names in os.walk(path):
                    for name in names:
                        hits.extend(_scan(os.path.join(dirpath, name)))
            else:
                hits.extend(_scan(path))
        self.assertEqual(hits, [])
        with open(os.path.join(ROOT, "docs", "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn("var SHOW_06B_EXT = false;", html)

    def test_t_c13_thresholds_match_the_signed_table(self):
        self.assertEqual(S.VOL_QUIET_ENTER, 20)
        self.assertEqual(S.VOL_QUIET_EXIT, 30)
        self.assertEqual(S.VOL_BID_ENTER, 80)
        self.assertEqual(S.VOL_BID_EXIT, 70)
        self.assertEqual(S.VOL_STRESS_ENTER, 95)
        self.assertEqual(S.VOL_STRESS_EXIT, 85)
        self.assertEqual(S.VRP_EVENT_ENTER, 90)
        self.assertEqual(S.VRP_EVENT_EXIT, 80)
        self.assertEqual(S.SK_Z_ENTER, 1.5)
        self.assertEqual(S.SK_Z_EXIT, 1.0)
        self.assertEqual(S.P_ABS_DSK, 90)
        self.assertEqual(S.HIST_MIN, 60)
        self.assertEqual(S.HIST_FULL, 252)
        self.assertEqual(S.LB_5, 5)
        self.assertEqual(S.RV_RETURNS, 21)
        self.assertEqual(S.HYSTERESIS_CLOSES, 2)
        self.assertEqual(S.METHOD_STATES["thresholds"]["STRESS_delta_gt"], 0)
        self.assertTrue(S.METHOD_STATES["header"].startswith("[PROV]"))
        self.assertEqual(S.R11_FLIP, frozenset(("JPY", "CAD", "CHF")))

    def test_t_c14_publish_flag_false_keeps_main_keys(self):
        self.assertIs(FX.FXVOL_STATES_PUBLISH, False)
        with open(KEYS_PATH, encoding="utf-8") as fh:
            expected = {line.strip() for line in fh if line.strip()}
        out, _, _ = FX.build(ROOT)
        got = set(key_paths(out))
        self.assertEqual(got - expected, set())
        self.assertEqual(expected - got, set())
        blob = json.dumps(out, ensure_ascii=False)
        for token in NEW_FIELDS:
            self.assertNotIn('"%s"' % token, blob)
        self.assertNotIn("states", out["method"])


def _sample_state():
    return {
        "session": "2024-06-03",
        "vol_state": "NORMAL",
        "premium_state": S.DOT,
        "skew_state": S.DOT,
        "skew_turn": S.DOT,
        "sk_z": 0.1,
        "vrp_pct": 10.0,
        "states_prov": {"vol": False, "premium": False, "skew": False, "turn": False},
        "states_reason": "",
    }


def _scan(path):
    """Campo nuevo como token. Un nombre de fichero que lo contiene de paso no cuenta."""
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except (UnicodeDecodeError, OSError):
        return []
    hits = []
    for token in FORBIDDEN:
        if re.search(r"(?<![A-Za-z0-9_])" + re.escape(token) + r"(?![A-Za-z0-9_])", text):
            hits.append("%s: %s" % (path, token))
    return hits


class Familias01CProxy(unittest.TestCase):
    def test_proxy_gate_is_classified_and_tagged(self):
        made = rows(70, rr=lambda i: 0.01 * (i + 1))
        chf = S.compute_fxvol_states({"CHF": made}, gate0={"CHF": "PROXY"})["CHF"][-1]
        eur = S.compute_fxvol_states({"EUR": made}, gate0={"EUR": "CLEAN"})["EUR"][-1]
        self.assertNotEqual(chf["vol_state"], "UNAVAILABLE")
        self.assertIn("[PROXY]", chf["states_reason"])
        self.assertNotIn("[PROXY]", eur["states_reason"])
        self.assertEqual(chf["vol_state"], eur["vol_state"])


if __name__ == "__main__":
    unittest.main()

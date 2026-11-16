"""FAMILIAS-01 M1 — crédito FRED en sombra (T-CR1..T-CR14).

Fixtures sintéticas. Ningún valor ICE. Con CRD_PUBLISH en False no se escribe
data/credit/ y el registro no lleva cifras ni el nombre del régimen.
"""
import csv
import io
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import credit_fred_shadow as M  # noqa: E402

REGIMES = ("SANO", "NEUTRO", "TENSO", "ROTURA", "AISLADO")
NA_CCYS = ("GBP", "JPY", "AUD", "NZD", "CAD", "CHF")


def bdays(n, start="2024-01-02"):
    cursor = date.fromisoformat(start)
    out = []
    while len(out) < n:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor += timedelta(days=1)
    return out


def bdays_ending(end, n):
    cursor = end
    out = []
    while len(out) < n:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor -= timedelta(days=1)
    out.reverse()
    return out


def sessions_of(values, start="2024-01-02"):
    days = bdays(len(values), start)
    return [{"date": day, "oas_bp": value} for day, value in zip(days, values)]


def varying(n, base=100.0):
    return [base + (index % 5) for index in range(n)]


def csv_of(pairs, column="SYNTH"):
    lines = ["observation_date,%s" % column]
    for day, token in pairs:
        lines.append("%s,%s" % (day.isoformat(), token))
    return "\n".join(lines) + "\n"


def replay(points):
    """Misma histéresis que la historia: (crd, d1w) → estado publicado."""
    state, arm, count = "NEUTRO", None, 0
    published = []
    for crd, d1w in points:
        state, arm, count = M.advance(state, arm, count, crd, d1w)
        published.append(state)
    return published


def compress(states):
    out = []
    for state in states:
        if not out or out[-1] != state:
            out.append(state)
    return out


class CreditFredShadow(unittest.TestCase):
    def test_t_cr1_borders_are_inclusive_on_entry_and_strict_on_exit(self):
        down = lambda value: math.nextafter(value, -math.inf)
        up = lambda value: math.nextafter(value, math.inf)
        want = M.desired_state

        self.assertEqual(want("NEUTRO", M.SANO_ENTER, 0.0), "SANO")
        self.assertEqual(want("NEUTRO", down(M.SANO_ENTER), 0.0), "NEUTRO")
        self.assertEqual(want("SANO", M.SANO_EXIT, 0.0), "SANO")
        self.assertEqual(want("SANO", down(M.SANO_EXIT), 0.0), "NEUTRO")
        self.assertEqual(want("SANO", 0.4, 0.0), "SANO")
        self.assertEqual(want("NEUTRO", 0.4, 0.0), "NEUTRO")

        self.assertEqual(want("NEUTRO", M.TENSO_ENTER, 0.0), "TENSO")
        self.assertEqual(want("NEUTRO", up(M.TENSO_ENTER), 0.0), "NEUTRO")
        self.assertEqual(want("TENSO", M.TENSO_EXIT, 0.0), "TENSO")
        self.assertEqual(want("TENSO", up(M.TENSO_EXIT), 0.0), "NEUTRO")
        self.assertEqual(want("TENSO", -0.8, 0.0), "TENSO")
        self.assertEqual(want("NEUTRO", -0.8, 0.0), "NEUTRO")

        self.assertEqual(want("NEUTRO", M.ROTURA_ENTER, -0.1), "ROTURA")
        self.assertEqual(want("NEUTRO", up(M.ROTURA_ENTER), -0.1), "TENSO")
        self.assertEqual(want("ROTURA", M.ROTURA_EXIT, 0.0), "ROTURA")
        self.assertEqual(want("ROTURA", up(M.ROTURA_EXIT), 0.0), "TENSO")
        self.assertEqual(want("ROTURA", M.TENSO_EXIT, 0.0), "TENSO")
        self.assertEqual(want("ROTURA", up(M.TENSO_EXIT), 0.0), "NEUTRO")

        self.assertEqual(want("SANO", -2.5, -0.2), "ROTURA")
        self.assertEqual(want("SANO", -1.5, 0.0), "TENSO")
        self.assertEqual(want("TENSO", 0.8, 0.0), "SANO")

    def test_t_cr2_rotura_does_not_enter_when_delta_1w_is_zero(self):
        held = replay([(-2.5, 0.0)] * 4)
        self.assertNotIn("ROTURA", held)
        self.assertEqual(held[-1], "TENSO")
        self.assertNotIn("ROTURA", replay([(-3.0, None)] * 4))
        self.assertEqual(M.desired_state("NEUTRO", M.ROTURA_ENTER, 0.0), "TENSO")
        self.assertEqual(M.desired_state("NEUTRO", M.ROTURA_ENTER, -0.0), "TENSO")
        self.assertEqual(M.desired_state("TENSO", -2.5, -1e-9), "ROTURA")
        # Con Δ1W < 0 sí entra, y hacen falta los dos cierres: el primero no publica ROTURA.
        entered = replay([(-2.5, -0.1), (-2.5, -0.1)])
        self.assertEqual(entered, ["NEUTRO", "ROTURA"])

    def test_t_cr3_hysteresis_needs_two_closes_along_the_signed_sequence(self):
        # Dos cierres en cada flecha. Un cierre solo no cambia el publicado.
        points = [
            (0.6, 0.0), (0.6, 0.0),       # NEUTRO → SANO
            (0.1, 0.0), (0.1, 0.0),       # SANO → NEUTRO
            (-1.2, 0.0), (-1.2, 0.0),     # NEUTRO → TENSO
            (-2.2, -0.4), (-2.2, -0.4),   # TENSO → ROTURA
            (-1.2, 0.0), (-1.2, 0.0),     # ROTURA → TENSO (crd <= -0.7)
            (0.0, 0.0), (0.0, 0.0),       # TENSO → NEUTRO
        ]
        published = replay(points)
        self.assertEqual(published[0], "NEUTRO")
        self.assertEqual(published[2], "SANO")
        self.assertEqual(published[4], "NEUTRO")
        self.assertEqual(published[6], "TENSO")
        self.assertEqual(published[8], "ROTURA")
        self.assertEqual(published[10], "TENSO")
        self.assertEqual(
            compress(published),
            ["NEUTRO", "SANO", "NEUTRO", "TENSO", "ROTURA", "TENSO", "NEUTRO"],
        )
        self.assertEqual(
            compress(published)[1:],
            ["SANO", "NEUTRO", "TENSO", "ROTURA", "TENSO", "NEUTRO"],
        )

    def test_t_cr4_session_uses_the_previous_print_never_oas_of_d(self):
        utc = timezone.utc
        self.assertEqual(M.session_date(datetime(2026, 6, 3, 21, 30, tzinfo=utc)), date(2026, 6, 3))
        self.assertEqual(M.session_date(datetime(2026, 6, 3, 22, 30, tzinfo=utc)), date(2026, 6, 3))
        self.assertEqual(M.session_date(datetime(2026, 6, 3, 23, 30, tzinfo=utc)), date(2026, 6, 4))

        days = bdays(80)
        session = days[-1]
        prior = days[:-1]
        pairs = [(day, "1.%02d" % (index % 4)) for index, day in enumerate(prior)]
        pairs.append((session, "9.00"))
        parsed = M.sessions_from_parsed(M.parse_fred_csv(csv_of(pairs)))
        used = M.evaluate_history(parsed, session)
        without = M.walk_sessions([row for row in parsed if row["date"] < session])[-1]
        with_d = M.walk_sessions(parsed)[-1]
        self.assertEqual(used["oas_asof"], prior[-1].isoformat())
        self.assertEqual(used["oas_hy_bp"], without["oas_hy_bp"])
        self.assertNotEqual(used["oas_hy_bp"], with_d["oas_hy_bp"])
        self.assertEqual(used["oas_z"], without["oas_z"])
        self.assertNotEqual(used["oas_z"], with_d["oas_z"])
        same_day = [row for row in parsed if row["date"] == session][0]
        self.assertNotEqual(used["oas_hy_bp"], same_day["oas_bp"])

    def test_t_cr5_holiday_gap_is_excluded_and_not_filled(self):
        june = [
            (date(2024, 6, 3), "1.00"),
            (date(2024, 6, 4), "."),
            (date(2024, 6, 5), ""),
            (date(2024, 6, 6), "1.50"),
            (date(2024, 6, 8), "9.99"),
        ]
        sessions = M.sessions_from_parsed(M.parse_fred_csv(csv_of(june)))
        by_date = {row["date"]: row["oas_bp"] for row in sessions}
        self.assertNotIn(date(2024, 6, 4), by_date)
        self.assertNotIn(date(2024, 6, 5), by_date)
        self.assertNotIn(date(2024, 6, 8), by_date)
        self.assertEqual(by_date[date(2024, 6, 3)], 100.0)
        self.assertEqual(by_date[date(2024, 6, 6)], 150.0)
        self.assertNotIn(125.0, by_date.values())
        self.assertNotEqual(by_date[date(2024, 6, 6)], 100.0)

        # Viernes Santo ausente (2024-03-29, 2025-04-18 en el archivo real, solo la fecha).
        # No se inserta una sesión y no se rellena con el cierre anterior.
        span = M.sessions_from_parsed(M.parse_fred_csv(csv_of([
            (date(2024, 3, 28), "1.00"),
            (date(2024, 4, 1), "2.00"),
        ])))
        self.assertEqual([row["date"] for row in span], [date(2024, 3, 28), date(2024, 4, 1)])
        self.assertEqual([row["oas_bp"] for row in span], [100.0, 200.0])
        self.assertTrue(M.is_us_oas_holiday(date(2024, 3, 29)))
        self.assertTrue(M.is_us_oas_holiday(date(2025, 4, 18)))

    def test_t_cr6_missing_business_day_is_unavailable_without_carry_or_zero(self):
        with open(os.path.join(ROOT, "scripts", "credit_fred_shadow.py"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertNotIn("or 0", source)

        parsed = M.sessions_from_parsed(M.parse_fred_csv(csv_of([
            (date(2024, 6, 3), "1.00"),
            (date(2024, 6, 5), "1.40"),
        ])))
        self.assertEqual([row["date"] for row in parsed], [date(2024, 6, 3), date(2024, 6, 4), date(2024, 6, 5)])
        self.assertIsNone(parsed[1]["oas_bp"])
        self.assertEqual(parsed[2]["oas_bp"], 140.0)

        original = M.z252

        def fake_z(values):
            if len(values) < M.HIST_MIN:
                return None, len(values)
            return -1.0, min(len(values), M.HIST_FULL)

        days = bdays(62)
        built = [{"date": day, "oas_bp": 50.0} for day in days]
        hole = days[-1] + timedelta(days=1)
        while hole.weekday() >= 5:
            hole += timedelta(days=1)
        nxt = hole + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        built.append({"date": hole, "oas_bp": None})
        built.append({"date": nxt, "oas_bp": 50.0})
        try:
            M.z252 = fake_z
            path = M.walk_sessions(built)
        finally:
            M.z252 = original
        self.assertEqual(path[61]["crd_state"], "SANO")
        self.assertEqual(path[62]["oas_asof"], hole.isoformat())
        self.assertEqual(path[62]["crd_state"], "UNAVAILABLE")
        self.assertIsNone(path[62]["oas_hy_bp"])
        self.assertIsNone(path[62]["crd"])
        self.assertIsNone(path[62]["oas_z"])
        self.assertNotEqual(path[62]["crd"], 0)
        self.assertIn("MISSING_OAS", path[62]["crd_reason"])
        self.assertEqual(path[63]["crd_state"], "NEUTRO")
        self.assertNotEqual(path[63]["crd_state"], "SANO")

        flat = M.walk_sessions(sessions_of([100.0] * 70))
        self.assertTrue(all(row["crd"] is None for row in flat))
        self.assertTrue(all(row["oas_z"] is None for row in flat))
        self.assertTrue(all(row["crd_state"] == "UNAVAILABLE" for row in flat))
        self.assertIn("ZERO_DISP", flat[-1]["crd_reason"])

        fetched = {
            M.SERIES_USD_HY: (503, "body 1.2345 must not be copied"),
            M.SERIES_USD_IG: (200, csv_of([(date(2024, 6, 3), "1.00")])),
            M.SERIES_EUR_HY: (200, csv_of([(date(2024, 6, 3), "1.00")])),
        }
        doc, _rows = M.run_shadow(fetched, date(2024, 6, 6))
        usd = doc["regions"]["USD"]
        self.assertEqual(usd["crd_state"], "UNAVAILABLE")
        self.assertIn("HTTP_503", usd["crd_reason"])
        self.assertNotIn("1.2345", usd["crd_reason"])
        self.assertIsNone(usd["oas_hy_bp"])
        self.assertIsNone(usd["crd"])
        self.assertIsNone(usd["crd_d1w"])
        self.assertIsNone(usd["oas_z"])

    def test_t_cr7_stale_when_older_than_two_us_business_days(self):
        asof = date(2024, 6, 3)  # lunes
        hist = [{"date": day, "oas_bp": 100.0 + (index % 5)} for index, day in enumerate(bdays_ending(asof, 70))]
        age2 = M.evaluate_history(hist, date(2024, 6, 5))   # martes y miércoles: 2
        age3 = M.evaluate_history(hist, date(2024, 6, 6))   # 3
        self.assertEqual(M.us_business_days_after(asof, date(2024, 6, 5)), 2)
        self.assertEqual(M.us_business_days_after(asof, date(2024, 6, 6)), 3)
        self.assertIn(age2["crd_state"], M.REGIMES)
        self.assertNotEqual(age2["crd_state"], "STALE")
        self.assertEqual(age3["crd_state"], "STALE")
        self.assertNotIn(age3["crd_state"], ("SANO", "TENSO", "ROTURA", "NEUTRO"))
        self.assertIn("STALE", age3["crd_reason"])

        # Viernes Santo no cuenta como día hábil: jueves 28-mar → martes 2-abr son 2, no 3.
        good_friday_end = date(2024, 3, 28)
        hist_gf = [
            {"date": day, "oas_bp": 100.0 + (index % 5)}
            for index, day in enumerate(bdays_ending(good_friday_end, 70))
        ]
        self.assertEqual(M.us_business_days_after(good_friday_end, date(2024, 4, 2)), 2)
        fresh = M.evaluate_history(hist_gf, date(2024, 4, 2))
        stale = M.evaluate_history(hist_gf, date(2024, 4, 3))
        self.assertNotEqual(fresh["crd_state"], "STALE")
        self.assertEqual(stale["crd_state"], "STALE")

    def test_t_cr8_prov_follows_the_z252_window(self):
        def last(n):
            return M.walk_sessions(sessions_of(varying(n)))[-1]

        short = last(59)
        at60 = last(60)
        at251 = last(251)
        at252 = last(252)
        longer = last(300)
        self.assertEqual(short["n"], 59)
        self.assertEqual(short["crd_state"], "UNAVAILABLE")
        self.assertIs(short["crd_prov"], False)
        self.assertNotIn("[PROV]", short["crd_reason"])
        self.assertIn("n<60", short["crd_reason"])
        self.assertIsNone(short["crd"])

        self.assertEqual(at60["n"], 60)
        self.assertNotEqual(at60["crd_state"], "UNAVAILABLE")
        self.assertIs(at60["crd_prov"], True)
        self.assertIn("[PROV]", at60["crd_reason"])

        self.assertEqual(at251["n"], 251)
        self.assertIs(at251["crd_prov"], True)
        self.assertIn("[PROV]", at251["crd_reason"])

        self.assertEqual(at252["n"], 252)
        self.assertIs(at252["crd_prov"], False)
        self.assertNotIn("[PROV]", at252["crd_reason"])
        self.assertIn("[PARCIAL-A]", at252["crd_reason"])

        self.assertEqual(longer["n"], 252)
        self.assertIs(longer["crd_prov"], False)

    def test_t_cr9_oas_up_means_crd_down_and_the_sign_is_applied_once(self):
        values = varying(80)
        values.append(values[-1] + 80.0)
        path = M.walk_sessions(sessions_of(values))
        previous, last = path[-2], path[-1]
        self.assertGreater(last["oas_hy_bp"], previous["oas_hy_bp"])
        self.assertLess(last["crd"], previous["crd"])
        self.assertEqual(last["crd"], -last["oas_z"])
        self.assertGreater(last["d_oas_1w"], 0.0)
        self.assertLess(last["crd_d1w"], 0.0)
        window = values[-M.HIST_FULL:]
        n = len(window)
        mean = sum(window) / float(n)
        var = sum((value - mean) ** 2 for value in window) / float(n)
        z = (window[-1] - mean) / math.sqrt(var)
        self.assertAlmostEqual(last["oas_z"], z)
        self.assertAlmostEqual(last["crd"], -z)

    def test_t_cr10_states_use_the_first_value_so_a_revision_does_not_repaint(self):
        base = sessions_of(varying(80, base=120.0))
        before = M.walk_sessions(base)
        first = [
            {"date": row["date"].isoformat(), "value_bp": row["oas_bp"]}
            for row in base
        ]
        vintage = M.merge_vintage([], "SYN", first, "2026-06-01T00:00:00Z")
        revised = [dict(row) for row in first]
        revised[10] = dict(revised[10], value_bp=revised[10]["value_bp"] + 400.0)
        vintage = M.merge_vintage(vintage, "SYN", revised, "2026-06-02T00:00:00Z")
        downloaded = [dict(row) for row in base]
        downloaded[10] = dict(downloaded[10], oas_bp=downloaded[10]["oas_bp"] + 400.0)
        kept = M.apply_first_known(downloaded, vintage, "SYN")
        after = M.walk_sessions(kept)
        repainted = M.walk_sessions(downloaded)
        self.assertEqual([row["crd_state"] for row in after], [row["crd_state"] for row in before])
        self.assertEqual([row["crd"] for row in after], [row["crd"] for row in before])
        self.assertNotEqual(repainted[-1]["crd"], before[-1]["crd"])
        self.assertEqual(kept[10]["oas_bp"], base[10]["oas_bp"])

    def test_t_cr11_a_revision_appends_a_row_and_does_not_rewrite(self):
        first_seen = "2026-06-01T00:00:00Z"
        original_rows = M.merge_vintage(
            [], "SYN", [{"date": "2024-06-03", "value_bp": 100.0}], first_seen,
        )
        snapshot = dict(original_rows[0])
        revised = M.merge_vintage(
            original_rows, "SYN",
            [{"date": "2024-06-03", "value_bp": 140.0}],
            "2026-06-02T01:00:00Z",
        )
        self.assertEqual(original_rows[0], snapshot)
        self.assertEqual(len(revised), 2)
        self.assertEqual(revised[0]["value_bp"], 100.0)
        self.assertEqual(revised[0]["rev"], 0)
        self.assertEqual(revised[0]["first_seen_utc"], first_seen)
        self.assertEqual(revised[1]["rev"], 1)
        self.assertEqual(revised[1]["value_bp"], 140.0)
        self.assertNotEqual(revised[1]["first_seen_utc"], first_seen)
        again = M.merge_vintage(
            revised, "SYN",
            [{"date": "2024-06-03", "value_bp": 140.0}],
            "2026-06-03T00:00:00Z",
        )
        self.assertEqual(len(again), 2)
        third = M.merge_vintage(
            again, "SYN",
            [{"date": "2024-06-03", "value_bp": 180.0}],
            "2026-06-04T00:00:00Z",
        )
        self.assertEqual([row["rev"] for row in third], [0, 1, 2])
        self.assertEqual(third[0]["value_bp"], 100.0)
        text_before = M.render_vintage(original_rows)
        text_after = M.render_vintage(revised)
        self.assertEqual(text_before.splitlines()[1], text_after.splitlines()[1])
        self.assertEqual(len(text_after.splitlines()), len(text_before.splitlines()) + 1)

    def test_t_cr12_flag_off_writes_nothing_and_prints_no_ice_figure(self):
        self.assertIs(M.CRD_PUBLISH, False)
        self.assertEqual(M.CRD_SHADOW_PATH, os.path.join("data", "credit", "CRD_shadow.json"))
        self.assertEqual(M.OAS_VINTAGE_PATH, os.path.join("data", "credit", "oas_vintage.csv"))

        lines = []
        marker = "7.7777"

        def fake(url, series_id):
            self.assertEqual(url, M.fred_url(series_id))
            self.assertTrue(url.startswith("https://fred.stlouisfed.org/graph/fredgraph.csv?id="))
            return 200, csv_of([(date(2024, 6, 3), marker)])

        before = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)
        rc = M.main(
            fetch=fake,
            now=datetime(2024, 6, 6, 21, 30, tzinfo=timezone.utc),
            root=ROOT,
            stdout=lines.append,
        )
        after = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)
        self.assertEqual(rc, 0)
        self.assertEqual(before, after)
        self.assertFalse(os.path.exists(os.path.join(ROOT, M.CRD_SHADOW_PATH)))
        self.assertFalse(os.path.exists(os.path.join(ROOT, M.OAS_VINTAGE_PATH)))
        text = "\n".join(lines)
        self.assertNotIn(marker, text)
        self.assertNotIn("777.77", text)
        self.assertNotRegex(text, r"\d+\.\d+")
        for name in REGIMES:
            self.assertNotIn(name, text)
        self.assertIn("publish=off", text)
        self.assertIn("vote=0", text)
        self.assertIn("blocked_entry=no", text)
        self.assertNotIn("LOG_BLOCKED", text)

        tmp = tempfile.mkdtemp()
        try:
            doc, vintage = M.run_shadow(
                {sid: (200, csv_of([(date(2024, 6, 3), marker)])) for sid in M.SERIES_IDS},
                date(2024, 6, 6),
            )
            self.assertIn("regions", doc)
            self.assertFalse(M.persist(tmp, doc, vintage, publish=False))
            self.assertEqual(os.listdir(tmp), [])
            self.assertTrue(M.persist(tmp, doc, vintage, publish=True))
            self.assertTrue(os.path.exists(os.path.join(tmp, M.CRD_SHADOW_PATH)))
            self.assertTrue(os.path.exists(os.path.join(tmp, M.OAS_VINTAGE_PATH)))
        finally:
            shutil.rmtree(tmp)
        self.assertFalse(os.path.exists(os.path.join(ROOT, "data", "credit")))

        with open(os.path.join(ROOT, ".github", "workflows", "daily_update.yml"), encoding="utf-8") as fh:
            yml = fh.read()
        self.assertIn("name: Shadow CRD FRED (FAMILIAS-01 M1)", yml)
        step = yml.split("name: Shadow CRD FRED (FAMILIAS-01 M1)", 1)[1].split("name: Commit updated data files", 1)[0]
        self.assertIn("continue-on-error: true", step)
        self.assertIn("if: always()", step)
        self.assertIn(
            "python scripts/tools/g8step.py --name credit_fred_shadow --phase post --cap 40 --min 5 -- python scripts/credit_fred_shadow.py",
            step,
        )
        self.assertNotIn("CRD_shadow.json", yml)
        self.assertNotIn("oas_vintage.csv", yml)
        for command in (
            "python scripts/cb_event_shadow.py",
            "python scripts/cme_options_shadow.py",
            "python scripts/cme_fxvol_shadow.py",
            "python scripts/chf_nowcast_shadow.py",
            "python scripts/s01b_shadow_a3.py",
        ):
            self.assertIn(command, yml)

        blob = _public_surface()
        self.assertNotIn(marker, blob)
        self.assertNotIn("oas_hy_bp", blob)
        for name in ("SANO", "TENSO", "ROTURA", "AISLADO"):
            self.assertNotIn(name, blob)

    def test_t_cr13_aislado_and_dd_are_unavailable_and_the_other_ccys_are_na(self):
        days = bdays(70)
        hy = csv_of([(day, "1.%02d" % (index % 4)) for index, day in enumerate(days)])
        ig_low = csv_of([(day, "0.50") for day in days], column="IG")
        ig_high = csv_of([(day, "4.00") for day in days], column="IG")
        session = days[-1] + timedelta(days=1)
        while session.weekday() >= 5 or M.is_us_oas_holiday(session):
            session += timedelta(days=1)

        def run(ig_text):
            fetched = {
                M.SERIES_USD_HY: (200, hy),
                M.SERIES_USD_IG: (200, ig_text),
                M.SERIES_EUR_HY: (200, hy),
            }
            doc, _rows = M.run_shadow(fetched, session)
            return doc

        low = run(ig_low)
        high = run(ig_high)
        usd = low["regions"]["USD"]
        eur = low["regions"]["EUR"]
        self.assertEqual(usd["crd_isolated"], "UNAVAILABLE")
        self.assertEqual(eur["crd_isolated"], "UNAVAILABLE")
        self.assertEqual(usd["dd"], "NA")
        self.assertEqual(eur["dd"], "NA")
        self.assertEqual(low["method"]["crd"]["aislado"], "UNAVAILABLE")
        self.assertEqual(low["method"]["crd"]["dd_p10"], "NA")
        self.assertNotEqual(usd["crd_state"], "AISLADO")
        self.assertEqual(eur["oas_ig_bp"], "NA")
        self.assertEqual(low["regions"]["USD"]["crd"], high["regions"]["USD"]["crd"])
        self.assertEqual(low["regions"]["USD"]["crd_state"], high["regions"]["USD"]["crd_state"])
        self.assertNotEqual(low["regions"]["USD"]["oas_ig_bp"], high["regions"]["USD"]["oas_ig_bp"])
        for ccy in NA_CCYS:
            region = low["regions"][ccy]
            self.assertEqual(region["crd_reason"], "NO_FRED_SPREAD", ccy)
            for key in M.PUBLIC_FIELDS:
                if key == "crd_reason":
                    continue
                self.assertEqual(region[key], "NA", "%s %s" % (ccy, key))

    def test_t_cr14_thresholds_match_the_signed_table(self):
        self.assertEqual(M.SANO_ENTER, 0.5)
        self.assertEqual(M.SANO_EXIT, 0.3)
        self.assertEqual(M.TENSO_ENTER, -1.0)
        self.assertEqual(M.TENSO_EXIT, -0.7)
        self.assertEqual(M.ROTURA_ENTER, -2.0)
        self.assertEqual(M.ROTURA_EXIT, -1.5)
        self.assertEqual(M.ROTURA_D1W_LT, 0)
        self.assertEqual(M.HIST_MIN, 60)
        self.assertEqual(M.HIST_FULL, 252)
        self.assertEqual(M.DELTA_LAG, 5)
        self.assertEqual(M.DELTA_SPAN, 257)
        self.assertEqual(M.HYSTERESIS_CLOSES, 2)
        self.assertEqual(M.STALE_US_BUSINESS_DAYS, 2)
        self.assertEqual(M.VOTE, 0)
        self.assertIs(M.BLOCKED_ENTRY, False)
        self.assertIs(M.CRD_PUBLISH, False)
        self.assertEqual(M.DD_P10, "NA")
        self.assertEqual(M.AISLADO, "UNAVAILABLE")
        self.assertEqual(M.Z_DDOF, 0)
        self.assertEqual(M.SERIES_USD_HY, "BAMLH0A0HYM2")
        self.assertEqual(M.SERIES_EUR_HY, "BAMLHE00EHYIOAS")
        self.assertEqual(M.SERIES_USD_IG, "BAMLC0A0CM")
        thresholds = M.METHOD_CRD["thresholds"]
        self.assertEqual(thresholds["SANO_enter_ge"], 0.5)
        self.assertEqual(thresholds["SANO_exit_lt"], 0.3)
        self.assertEqual(thresholds["TENSO_enter_le"], -1.0)
        self.assertEqual(thresholds["TENSO_exit_gt"], -0.7)
        self.assertEqual(thresholds["ROTURA_enter_le"], -2.0)
        self.assertEqual(thresholds["ROTURA_exit_gt"], -1.5)
        self.assertEqual(thresholds["ROTURA_d1w_lt"], 0)
        self.assertEqual(thresholds["dd_p10"], "NA")
        self.assertEqual(M.METHOD_CRD["vote"], 0)
        self.assertIs(M.METHOD_CRD["blocked_entry"], False)
        self.assertEqual(M.METHOD_CRD["hysteresis_closes"], 2)
        self.assertEqual(M.METHOD_CRD["z_window"], 252)
        self.assertEqual(M.METHOD_CRD["delta_sessions"], 5)
        self.assertEqual(M.METHOD_CRD["delta_span"], 257)
        self.assertEqual(M.METHOD_CRD["stale_older_than_us_business_days"], 2)
        self.assertEqual(M.METHOD_CRD["precedence"], ["ROTURA", "TENSO", "SANO", "NEUTRO"])
        self.assertTrue(M.METHOD_CRD["header"].startswith("[PROV]"))
        self.assertEqual(M.METHOD_CRD["partial"], "[PARCIAL-A]")
        self.assertEqual(M.NA_CCYS, NA_CCYS)
        self.assertEqual(M.NA_REASON, "NO_FRED_SPREAD")


def _public_surface():
    chunks = []
    top = os.path.join(ROOT, "data")
    for name in os.listdir(top):
        path = os.path.join(top, name)
        if os.path.isfile(path) and name.endswith((".json", ".csv", ".html", ".md")):
            with open(path, encoding="utf-8", errors="replace") as fh:
                chunks.append(fh.read())
    credit = os.path.join(top, "credit")
    if os.path.isdir(credit):
        for dirpath, _, names in os.walk(credit):
            for name in names:
                with open(os.path.join(dirpath, name), encoding="utf-8", errors="replace") as fh:
                    chunks.append(fh.read())
    with open(os.path.join(ROOT, "docs", "index.html"), encoding="utf-8") as fh:
        chunks.append(fh.read())
    js = os.path.join(ROOT, "docs", "js")
    if os.path.isdir(js):
        for name in os.listdir(js):
            if name.endswith(".js"):
                with open(os.path.join(js, name), encoding="utf-8") as fh:
                    chunks.append(fh.read())
    return "\n".join(chunks)


if __name__ == "__main__":
    unittest.main()

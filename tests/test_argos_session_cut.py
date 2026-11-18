"""Corte de argos_rates a la última sesión 1D cerrada (17:00 America/New_York).

Festivos: sources/calendars.csv, calendario US. Datos sintéticos en un directorio
temporal; no toca data/ del repo.
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import argos_rates as AR  # noqa: E402
from g8common.schedule import load_calendars  # noqa: E402

NY = ZoneInfo("America/New_York")


def utc(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ymd(d):
    return d.strftime("%Y%m%d")


def ending(end, n):
    out = []
    d = end
    while len(out) < n:
        out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


def write(dirpath, name, text):
    with open(os.path.join(dirpath, name), "w", encoding="utf-8") as fh:
        fh.write(text)


def ohlcv(rows):
    lines = ["DATE,OPEN,HIGH,LOW,CLOSE,VOLUME"]
    for d, v in rows:
        lines.append("%s,%.4f,%.4f,%.4f,%.4f,0" % (ymd(d), v, v, v, v))
    return "\n".join(lines) + "\n"


def ry(rows):
    lines = ["# QUALITY=TEST", "DATE,NOM10,REAL10,BE10"]
    for d, n, r, b in rows:
        lines.append("%s,%.4f,%.4f,%.4f" % (ymd(d), n, r, b))
    return "\n".join(lines) + "\n"


def acm(rows):
    lines = ["DATE,Y10_FIT,RNY10,TP10,QUALITY"]
    for d, y in rows:
        lines.append("%s,%.4f,%.4f,%.4f,TEST" % (ymd(d), y, y * 0.6, y * 0.4))
    return "\n".join(lines) + "\n"


def nowcast(rows):
    lines = ["DATE,NOM2Y,NOM10,BE10,REAL10,H_BD,ERR2Y_BP,ERR10_BP,ERRBE_BP,BASE_DATE,MODEL"]
    for d, nom in rows:
        lines.append("%s,1.0000,%.4f,1.0000,1.0000,1,1,1,1,%s,TEST" % (ymd(d), nom, ymd(d)))
    return "\n".join(lines) + "\n"


def s01b(as_of, d_2y):
    return json.dumps({
        "schema": "s01b-1",
        "as_of": as_of,
        "status": "CONTEXT",
        "currencies": {
            "JPY": {
                "d_rny": 1, "d_tp": 1, "d_fit": 1, "resid": 1, "d_2y": d_2y,
                "d_be": 1, "d_fx": 0.1, "theta": 1, "signal": "OFF", "persist": 1,
                "flags": [], "avail": "OK", "emitter": False, "acm_asof_t": as_of,
            }
        },
        "events": [],
    })


def build_dir(root, session, later=None, s01b_as_of=None, s01b_d2y=1):
    """40 observaciones hasta `session`. `later` es una fila de la mañana siguiente."""
    os.makedirs(root, exist_ok=True)
    days = ending(session, 40)
    write(root, "TONA.csv", ohlcv([(d, 0.50) for d in days] + ([(later, 9.99)] if later else [])))
    write(root, "JP_POLICY.csv", ohlcv([(d, 0.25) for d in days]))
    write(root, "AONIA.csv", ohlcv([(d, 4.00) for d in days] + ([(later, 9.50)] if later else [])))
    write(root, "AU_POLICY.csv", ohlcv([(d, 3.60) for d in days]))
    write(root, "RY_G8_JPY.csv", ry([(d, 1.11, 0.50, 0.61) for d in days] + ([(later, 9.99, 8.00, 1.99)] if later else [])))
    write(root, "RY_G8_AUD.csv", ry([(d, 4.00, 2.00, 2.00) for d in days]))
    write(root, "ACM_G8_JPY.csv", acm([(d, 1.11) for d in days]))
    write(root, "ACM_G8_AUD.csv", acm([(d, 4.00) for d in days]))
    nc = [(d, 4.00) for d in days]
    if later:
        nc.append((later, 8.88))
    write(root, "AUD_NOWCAST.csv", nowcast(nc))
    write(root, "S01B.json", s01b(s01b_as_of or session.isoformat(), s01b_d2y))


def run(now, folder, apply_cut):
    doc = AR.parse(AR.render(now, data_dir=folder, apply_cut=apply_cut))
    return doc


def blob(doc):
    return json.dumps(doc, indent=1, ensure_ascii=False) + "\n"


class ClosedSessionClock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cals = load_calendars(ROOT)
        cls.us = cls.cals["US"]

    def test_winter_before_and_after_close(self):
        # 2026-01-15 es jueves. 17:00 EST = 22:00 UTC.
        before = datetime(2026, 1, 15, 16, 59, tzinfo=NY)
        after = datetime(2026, 1, 15, 17, 0, tzinfo=NY)
        self.assertEqual(before.astimezone(timezone.utc).hour, 21)
        self.assertEqual(after.astimezone(timezone.utc).hour, 22)
        self.assertEqual(AR.closed_session(before, self.cals), date(2026, 1, 14))
        self.assertEqual(AR.closed_session(after, self.cals), date(2026, 1, 15))
        # 21:30 UTC en enero todavía es antes de las 17:00 EST.
        self.assertEqual(AR.closed_session(utc(2026, 1, 15, 21, 30), self.cals), date(2026, 1, 14))

    def test_summer_before_and_after_close(self):
        # 2026-07-16 es jueves. 17:00 EDT = 21:00 UTC.
        before = datetime(2026, 7, 16, 16, 59, tzinfo=NY)
        after = datetime(2026, 7, 16, 17, 0, tzinfo=NY)
        self.assertEqual(before.astimezone(timezone.utc).hour, 20)
        self.assertEqual(after.astimezone(timezone.utc).hour, 21)
        self.assertEqual(AR.closed_session(before, self.cals), date(2026, 7, 15))
        self.assertEqual(AR.closed_session(after, self.cals), date(2026, 7, 16))
        # La misma hora UTC que en invierno ya es después del cierre de verano.
        self.assertEqual(AR.closed_session(utc(2026, 7, 16, 21, 30), self.cals), date(2026, 7, 16))

    def test_monday_early_morning_is_friday(self):
        # Lunes 2026-11-16 07:00 EST, antes del cierre.
        now = datetime(2026, 11, 16, 7, 0, tzinfo=NY)
        self.assertEqual(now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "2026-11-16T12:00:00Z")
        self.assertEqual(AR.closed_session(now, self.cals), date(2026, 11, 13))

    def test_us_holiday_from_calendars_csv(self):
        self.assertIn("2026-11-11", self.us)
        # Miércoles festivo, después de las 17:00 EST: la sesión cerrada es el martes.
        holiday = datetime(2026, 11, 11, 18, 0, tzinfo=NY)
        self.assertEqual(AR.closed_session(holiday, self.cals), date(2026, 11, 10))
        # Jueves por la mañana: el miércoles no es hábil.
        thursday = datetime(2026, 11, 12, 8, 0, tzinfo=NY)
        self.assertEqual(AR.closed_session(thursday, self.cals), date(2026, 11, 10))

    def test_year_without_us_holidays_is_loud(self):
        years = {item[:4] for item in self.us}
        self.assertNotIn("2035", years)
        with self.assertRaises(SystemExit):
            AR.closed_session(datetime(2035, 6, 14, 18, 0, tzinfo=NY), self.cals)


class ClosedSessionRows(unittest.TestCase):
    def test_early_morning_row_is_excluded(self):
        session = date(2026, 11, 13)
        later = date(2026, 11, 16)
        now = "2026-11-16T12:00:00Z"
        with tempfile.TemporaryDirectory() as tmp:
            build_dir(tmp, session, later=later, s01b_as_of="2026-11-16", s01b_d2y=4321)
            cut = run(now, tmp, True)
            raw = run(now, tmp, False)
        self.assertEqual(cut["as_of"], "2026-11-13")
        self.assertEqual(cut["generated_at"], now)
        self.assertEqual(cut["currencies"]["JPY"]["nominal_10y"], 1.11)
        self.assertEqual(cut["currencies"]["JPY"]["date"], "2026-11-13")
        self.assertEqual(cut["currencies"]["AUD"]["nominal_10y"], 4.0)
        self.assertEqual(cut["floors"]["JPY"]["date"], "2026-11-13")
        self.assertEqual(cut["floors"]["JPY"]["spread_bp"], 25.0)
        self.assertEqual(cut["floors"]["AUD"]["date"], "2026-11-13")
        self.assertEqual(cut["floors"]["AUD"]["spread_bp"], 40.0)
        self.assertEqual(cut["currencies"]["JPY"]["s01b"], "UNAVAILABLE")
        self.assertNotIn("4321", json.dumps(cut))
        self.assertNotIn("9.99", json.dumps(cut["currencies"]["JPY"]))
        self.assertNotIn("8.88", json.dumps(cut["currencies"]["AUD"]))
        self.assertEqual(raw["currencies"]["JPY"]["nominal_10y"], 9.99)
        self.assertEqual(raw["currencies"]["JPY"]["date"], "2026-11-16")
        self.assertEqual(raw["currencies"]["AUD"]["nominal_10y"], 8.88)
        self.assertEqual(raw["floors"]["JPY"]["spread_bp"], 974.0)
        self.assertEqual(raw["floors"]["JPY"]["date"], "2026-11-16")
        self.assertEqual(raw["floors"]["AUD"]["date"], "2026-11-16")
        self.assertEqual(raw["currencies"]["JPY"]["s01b"]["d_2y"], 4321)

    def test_byte_identical_after_close_without_later_rows(self):
        cases = (
            (date(2026, 11, 13), "2026-11-13T22:30:00Z"),
            (date(2026, 7, 16), "2026-07-16T21:30:00Z"),
        )
        for session, now in cases:
            with self.subTest(now=now):
                self.assertEqual(AR.closed_session(now), session)
                self.assertEqual(now[:10], session.isoformat())
                with tempfile.TemporaryDirectory() as tmp:
                    build_dir(tmp, session, later=None, s01b_as_of=session.isoformat(), s01b_d2y=7)
                    cut = run(now, tmp, True)
                    raw = run(now, tmp, False)
                self.assertEqual(cut["as_of"], session.isoformat())
                self.assertEqual(blob(cut), blob(raw))

    def test_day_17_nzd_and_chf_nom_stay_out_of_session_16(self):
        """Un impreso del 17 no entra en la sesión cerrada del 16. Serie sintética."""
        session = date(2026, 11, 16)
        later = date(2026, 11, 17)
        now = "2026-11-17T21:00:00Z"
        self.assertEqual(AR.closed_session(now), session)
        days = ending(session, 70)
        sentinel = 9.17
        prior = 1.16

        def nzd_bond():
            lines = ["Date,Value"]
            for d in days:
                lines.append("%s,%.2f" % (d.isoformat(), prior))
            lines.append("%s,%.2f" % (later.isoformat(), sentinel))
            return "\n".join(lines) + "\n"

        def chf_nom():
            lines = ["Date,Value,Source"]
            for d in days:
                lines.append("%s,%.2f,syn" % (d.isoformat(), prior))
            lines.append("%s,%.2f,syn" % (later.isoformat(), sentinel))
            return "\n".join(lines) + "\n"

        def chf_acm():
            lines = ["DATE,Y10_FIT,RNY10,TP10,QUALITY"]
            for d in days:
                lines.append("%s,1.0000,0.6000,0.4000,ACM_K3" % ymd(d))
            return "\n".join(lines) + "\n"

        with tempfile.TemporaryDirectory() as tmp:
            build_dir(tmp, session, later=None)
            write(tmp, "NZD_BOND_10Y.csv", nzd_bond())
            write(tmp, "CHF_NOM_10Y.csv", chf_nom())
            write(tmp, "ACM_G8_CHF.csv", chf_acm())
            cut = run(now, tmp, True)
            raw = run(now, tmp, False)
        self.assertEqual(cut["as_of"], "2026-11-16")
        self.assertEqual(cut["currencies"]["NZD"]["nominal_10y"], prior)
        self.assertEqual(cut["currencies"]["CHF"]["nominal_10y"], prior)
        self.assertNotIn("%.2f" % sentinel, json.dumps(cut["currencies"]["NZD"]))
        self.assertNotIn("%.2f" % sentinel, json.dumps(cut["currencies"]["CHF"]))
        self.assertEqual(raw["currencies"]["NZD"]["nominal_10y"], sentinel)
        self.assertEqual(raw["currencies"]["CHF"]["nominal_10y"], sentinel)

    def test_floor_usd_row_after_the_session_stays_out(self):
        """orden Sander t241u. FLOOR_USD del día siguiente no entra en la sesión cerrada.

        El reloj ya es el 18, antes de las 17:00 America/New_York: la sesión
        cerrada sigue siendo el 17. Serie sintética; el 9.18 no es un IORB.
        """
        session = date(2026, 11, 17)
        later = date(2026, 11, 18)
        now = "2026-11-18T12:00:00Z"
        self.assertEqual(AR.closed_session(now), session)
        days = ending(session, 40)
        prior = 3.50
        sentinel = 9.18
        with tempfile.TemporaryDirectory() as tmp:
            build_dir(tmp, session, later=None)
            write(tmp, "SOFR.csv", ohlcv([(d, 3.53) for d in days]))
            write(tmp, "FLOOR_USD.csv", ohlcv([(d, prior) for d in days] + [(later, sentinel)]))
            cut = run(now, tmp, True)
            raw = run(now, tmp, False)
        self.assertEqual(cut["as_of"], "2026-11-17")
        self.assertEqual(cut["floors"]["USD"]["policy_rate"], prior)
        self.assertEqual(cut["floors"]["USD"]["date"], "2026-11-17")
        self.assertNotIn("%.2f" % sentinel, json.dumps(cut["floors"]["USD"]))
        self.assertEqual(raw["floors"]["USD"]["policy_rate"], sentinel)


if __name__ == "__main__":
    unittest.main()

"""t191u — publica fut_roll_window.imm (tercer miércoles, ISO).

Fixture sintético: un directorio de sesión vacío. No abre el canónico,
no llama a la red y no reescribe el JSON publicado.
RadarNews (g8-radar-news, executionWindows.js immFor) lee la clave
fut_roll_window.imm y solo acepta YYYY-MM-DD.
"""
import os
import re
import sys
import tempfile
import unittest
from datetime import date

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import cme_fxvol_shadow as FX  # noqa: E402

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Día 1 del mes → tercer miércoles. Cubre los siete arranques de semana.
# Miércoles → día 15. Jueves → día 21. Febrero 2024 es bisiesto.
THIRD_WEDNESDAY = {
    (2024, 5): "2024-05-15",   # miércoles
    (2025, 1): "2025-01-15",   # miércoles
    (2021, 9): "2021-09-15",   # miércoles, mes de contrato
    (2024, 2): "2024-02-21",   # jueves, febrero bisiesto
    (2018, 3): "2018-03-21",   # jueves, mes de contrato
    (2026, 1): "2026-01-21",   # jueves
    (2024, 1): "2024-01-17",   # lunes
    (2024, 10): "2024-10-16",  # martes
    (2026, 12): "2026-12-16",  # martes, mes de contrato de la sesión publicada
    (2024, 3): "2024-03-20",   # viernes
    (2024, 6): "2024-06-19",   # sábado
    (2024, 9): "2024-09-18",   # domingo
    (2024, 12): "2024-12-18",  # domingo
}


def _published(session):
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "data", "options", "canonical", session))
        out, _, _ = FX.build(tmp, session)
    return out["fut_roll_window"]


class ThirdWednesday(unittest.TestCase):
    def test_third_wednesday_for_every_month_start_weekday(self):
        for (year, month), expected in THIRD_WEDNESDAY.items():
            got = FX.third_wed(year, month)
            self.assertEqual(got.isoformat(), expected, "%04d-%02d" % (year, month))
            self.assertEqual(got.weekday(), 2, expected)
            self.assertEqual(got.day, int(expected[8:10]))
            wednesdays = [
                day for day in range(1, got.day + 1)
                if date(year, month, day).weekday() == 2
            ]
            self.assertEqual(wednesdays, [got.day - 14, got.day - 7, got.day])

    def test_month_starting_wednesday_is_the_15th(self):
        for year, month in ((2024, 5), (2025, 1), (2021, 9)):
            self.assertEqual(date(year, month, 1).weekday(), 2)
            self.assertEqual(FX.third_wed(year, month), date(year, month, 15))

    def test_month_starting_thursday_is_the_21st(self):
        for year, month in ((2024, 2), (2018, 3), (2026, 1)):
            self.assertEqual(date(year, month, 1).weekday(), 3)
            self.assertEqual(FX.third_wed(year, month), date(year, month, 21))


class FutRollWindowImm(unittest.TestCase):
    def test_roll_window_keeps_start_end_active_and_returns_imm(self):
        # Sesión publicada 2026-11-12: ventana ya escrita start/end/active.
        start, end, active, imm = FX.roll_window(date(2026, 11, 12))
        self.assertEqual(start, date(2026, 12, 7))
        self.assertEqual(end, date(2026, 12, 19))
        self.assertIs(active, False)
        self.assertEqual(imm, date(2026, 12, 16))
        # El día del IMM y los dos bordes de la ventana no cambian la fecha.
        for day, flag in ((date(2026, 12, 7), True), (date(2026, 12, 16), True), (date(2026, 12, 19), True)):
            a, b, act, w = FX.roll_window(day)
            self.assertEqual((a, b, w), (start, end, imm), day.isoformat())
            self.assertIs(act, flag)
        # El día siguiente al cierre pasa al contrato de marzo.
        a, b, act, w = FX.roll_window(date(2026, 12, 20))
        self.assertEqual(w, date(2027, 3, 17))
        self.assertEqual(a, date(2027, 3, 8))
        self.assertEqual(b, date(2027, 3, 20))
        self.assertIs(act, False)

    def test_contract_month_that_starts_on_wednesday_or_thursday(self):
        # 2021-09-01 es miércoles. El IMM es el 15. La ventana de junio ya cerró.
        start, end, active, imm = FX.roll_window(date(2021, 9, 1))
        self.assertEqual(imm, date(2021, 9, 15))
        self.assertEqual(start, date(2021, 9, 6))
        self.assertEqual(end, date(2021, 9, 18))
        self.assertIs(active, False)
        on_imm = FX.roll_window(date(2021, 9, 15))
        self.assertEqual(on_imm[3], date(2021, 9, 15))
        self.assertIs(on_imm[2], True)
        # 2018-03-01 es jueves. El IMM es el 21.
        start, end, active, imm = FX.roll_window(date(2018, 3, 1))
        self.assertEqual(imm, date(2018, 3, 21))
        self.assertEqual(start, date(2018, 3, 12))
        self.assertEqual(end, date(2018, 3, 24))
        self.assertIs(active, False)

    def test_published_imm_is_present_and_iso(self):
        window = _published("2026-11-12")
        self.assertEqual(list(window), ["start", "end", "active", "imm"])
        self.assertEqual(window["start"], "2026-12-07")
        self.assertEqual(window["end"], "2026-12-19")
        self.assertIs(window["active"], False)
        self.assertIsInstance(window["imm"], str)
        self.assertRegex(window["imm"], ISO_DATE)
        self.assertEqual(window["imm"], "2026-12-16")
        parsed = date.fromisoformat(window["imm"])
        self.assertEqual(parsed.isoformat(), window["imm"])
        self.assertEqual(parsed.weekday(), 2)

        wednesday = _published("2021-09-15")
        self.assertEqual(wednesday["imm"], "2021-09-15")
        self.assertRegex(wednesday["imm"], ISO_DATE)
        self.assertEqual(wednesday["start"], "2021-09-06")
        self.assertEqual(wednesday["end"], "2021-09-18")
        self.assertIs(wednesday["active"], True)

        thursday = _published("2018-03-21")
        self.assertEqual(thursday["imm"], "2018-03-21")
        self.assertRegex(thursday["imm"], ISO_DATE)
        self.assertEqual(thursday["start"], "2018-03-12")
        self.assertEqual(thursday["end"], "2018-03-24")
        self.assertIs(thursday["active"], True)


if __name__ == "__main__":
    unittest.main()

"""Tests nuevos de la regla de feed muerto y el proxy NZD_CASH_ON → NZD_OCR (t323u).

No modifican tests existentes. Un dato PROXY no se hace pasar por overnight oficial.
"""
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import feed_proxy as FP  # noqa: E402
from g8common import schedule as SC  # noqa: E402


def _mini_cash(official_lines, proxy_lines=()):
    lines = ["Date,Value,Status,Source,ProxyDate"]
    lines.extend(official_lines)
    lines.extend(proxy_lines)
    return "\n".join(lines) + "\n"


class FeedProxyUnit(unittest.TestCase):
    def setUp(self):
        self.cals = SC.load_calendars(ROOT)
        self.subs = [s for s in FP.load_substitutes(ROOT) if s.feed_id == "NZD_CASH_ON"]
        self.assertEqual(len(self.subs), 1)
        self.sub = self.subs[0]
        self.assertEqual(self.sub.approval_status, "active")
        self.assertEqual(self.sub.substitute_feed, "NZD_OCR")

    def test_substitutes_table_has_all_registry_feeds_and_one_active(self):
        subs = FP.load_substitutes(ROOT)
        # t333u, orden expresa Sander 22-nov: +CHF_SPOT_SNB1A, DE10Y_PAR, ES10Y_DAILY, ES_DE_10Y_SPREAD (125+4=129).
        self.assertEqual(len(subs), 129)
        active = [s for s in subs if s.approval_status == "active"]
        self.assertEqual([s.feed_id for s in active], ["NZD_CASH_ON"])
        # ninguno propone un sustituto público para un feed con licencia
        for s in subs:
            if s.approval_status == "licensed_no_public":
                self.assertEqual(s.substitute_feed, "")

    def test_official_view_strips_proxy_rows(self):
        text = _mini_cash(
            ["2026-11-12,2.8"],
            ["2026-11-13,2.75,PROXY,NZD_OCR,2026-11-13"],
        )
        b = FP.official_csv_bytes(text)
        self.assertEqual(b, b"Date,Value\n2026-11-12,2.8\n")
        self.assertEqual(FP.official_dates_from_bytes(text), [date(2026, 11, 12)])
        self.assertEqual(FP.all_dates_from_bytes(text), [date(2026, 11, 12), date(2026, 11, 13)])

    def test_apply_from_13_nov_fills_nz_business_days_with_ocr(self):
        cash = "Date,Value\n2026-11-09,2.81\n2026-11-12,2.8\n"
        ocr = "DATE,OPEN,HIGH,LOW,CLOSE,VOLUME\n20261112,2.7500,2.7500,2.7500,2.7500,0\n20261113,2.7500,2.7500,2.7500,2.7500,0\n"
        new_b, ev, st = FP.apply_one(
            ROOT, self.sub, self.cals, date(2026, 11, 22),
            file_text=cash, sub_text=ocr, force_from=date(2026, 11, 13),
        )
        self.assertIsNotNone(new_b)
        text = new_b.decode()
        self.assertIn("2026-11-12,2.8,,,\n", text)  # fila oficial intacta
        for d in ("2026-11-13", "2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20"):
            self.assertIn("%s,2.75,PROXY,NZD_OCR,2026-11-13" % d, text)
        self.assertNotIn("2026-11-14,", text)  # fin de semana
        self.assertNotIn("2026-11-15,", text)
        self.assertEqual(st.last_official, "2026-11-12")
        self.assertEqual(st.last_any, "2026-11-20")
        self.assertTrue(any(e.kind == "proxy" for e in ev))

    def test_no_rewrite_when_official_is_current(self):
        # 23-sep oficial, reloj 24-sep: no hay que escribir PROXY ni cambiar la cabecera
        cash = "Date,Value\n2026-09-23,2.78\n"
        new_b, ev, st = FP.apply_one(
            ROOT, self.sub, self.cals, date(2026, 9, 24),
            file_text=cash, sub_text="DATE,CLOSE\n20260923,2.75\n",
        )
        self.assertIsNone(new_b)
        self.assertEqual(st.last_official, "2026-09-23")

    def test_recovery_when_official_b2_returns(self):
        cash = _mini_cash(
            ["2026-11-12,2.8"],
            [
                "2026-11-13,2.75,PROXY,NZD_OCR,2026-11-13",
                "2026-11-16,2.75,PROXY,NZD_OCR,2026-11-13",
            ],
        )
        # B2 vuelve a imprimir el 13-nov (oficial 2.81) y aún no el 16
        official_plus = "Date,Value\n2026-11-12,2.8\n2026-11-13,2.81\n"
        new_b, ev, st = FP.apply_one(
            ROOT, self.sub, self.cals, date(2026, 11, 22),
            file_text=cash, official_text=official_plus,
            sub_text="DATE,CLOSE\n20261113,2.7500,2.7500,2.7500,2.7500,0\n",
        )
        text = (new_b or official_plus.encode()).decode()
        self.assertIn("2026-11-13,2.81,,,\n", text)
        self.assertNotIn("2026-11-13,2.75,PROXY", text)
        self.assertIn("2026-11-16,2.75,PROXY,NZD_OCR,2026-11-13", text)
        self.assertTrue(any(e.kind == "recovered" for e in ev))

    def test_late_thresholds_2_and_3_bd(self):
        # último oficial 12-nov, lag 2, el 18-nov (mié) esperado=16, late = 13 y 16 = 2
        cash = "Date,Value\n2026-11-12,2.8\n"
        st, _, _ = FP.inspect_feed(ROOT, self.sub, self.cals, date(2026, 11, 18), file_text=cash)
        self.assertEqual(st.last_official, "2026-11-12")
        self.assertEqual(st.late_bd, 2)
        st3, _, _ = FP.inspect_feed(ROOT, self.sub, self.cals, date(2026, 11, 19), file_text=cash)
        self.assertGreaterEqual(st3.late_bd, 3)

    def test_published_cash_file_has_proxy_rows_from_13_nov(self):
        # t343u, orden expresa Sander 23-nov: test desacoplado de datos vivos
        text = Path(ROOT, "tests", "fixtures", "t344u", "nzd", "NZD_CASH_ON.csv").read_text()
        self.assertTrue(text.startswith("Date,Value,Status,Source,ProxyDate"))
        self.assertIn("2026-11-12,2.8,,,\n", text)
        self.assertIn("2026-11-13,2.75,PROXY,NZD_OCR,2026-11-13", text)
        self.assertIn("2026-11-19,2.75,PROXY,NZD_OCR,2026-11-13", text)
        # RBNZ B2 oficial 20-nov = 2.84 (página B2, released 23-nov-2026); 13-19 siguen PROXY
        self.assertIn("2026-11-20,2.84,,,\n", text)
        self.assertNotIn("2026-11-20,2.75,PROXY", text)

    def test_proxy_does_not_look_like_official_to_health(self):
        # t343u, orden expresa Sander 23-nov: test desacoplado de datos vivos
        raw = Path(ROOT, "tests", "fixtures", "t344u", "nzd", "NZD_CASH_ON.csv").read_bytes()
        off = FP.official_dates_from_bytes(raw)
        all_d = FP.all_dates_from_bytes(raw)
        self.assertEqual(max(off), date(2026, 11, 20))
        self.assertEqual(max(all_d), date(2026, 11, 20))
        self.assertIn(date(2026, 11, 12), off)
        self.assertIn(date(2026, 11, 20), off)
        for d in (date(2026, 11, 13), date(2026, 11, 16), date(2026, 11, 17),
                  date(2026, 11, 18), date(2026, 11, 19)):
            self.assertNotIn(d, off)
            self.assertIn(d, all_d)


    def test_publisher_strips_proxy_before_merge_and_reads_ocr_from_repo(self):
        """t329u: en el Mac no hay NZD_OCR local. Las filas PROXY del tip no son primaria."""
        cash = (
            "Date,Value,Status,Source,ProxyDate\n"
            "2026-11-12,2.8,,,\n"
            "2026-11-13,2.75,PROXY,NZD_OCR,2026-11-13\n"
        )
        official = FP.official_csv_bytes(cash)
        text = official.decode("utf-8")
        self.assertIn("2026-11-12,2.8", text)
        self.assertNotIn("PROXY", text)
        self.assertNotIn("2026-11-13", text)
        ocr = "Date,Value\n2026-10-28,2.75\n2026-11-13,2.75\n"
        # apply_one con OCR "del repo" (no local) conserva y amplía el proxy
        new_b, ev, st = FP.apply_one(
            ROOT, self.subs[0], self.cals, date(2026, 11, 20),
            file_text=cash, official_text=official.decode("utf-8"), sub_text=ocr,
        )
        self.assertIsNotNone(new_b)
        out = new_b.decode("utf-8")
        self.assertIn("2026-11-12,2.8", out)
        self.assertIn("2026-11-13,2.75,PROXY,NZD_OCR,2026-11-13", out)
        self.assertTrue(any(e.kind == "proxy" for e in ev))
        # si se pasara el CSV con PROXY como official_text, se "recuperaría" mal
        bad_off = cash
        new_bad, ev_bad, _ = FP.apply_one(
            ROOT, self.subs[0], self.cals, date(2026, 11, 20),
            file_text=cash, official_text=bad_off, sub_text=ocr,
        )
        # official_rows ignora PROXY aunque el texto las traiga, así que tampoco debe recuperar
        if new_bad is not None:
            self.assertNotIn("recovered", [e.kind for e in ev_bad])
            self.assertIn("2026-11-13,2.75,PROXY", new_bad.decode("utf-8"))


class IngestWatchHook(unittest.TestCase):
    def test_no_substitutes_file_is_noop(self):
        tmp = tempfile.mkdtemp()
        try:
            alerts, info = {}, []
            FP.check_for_ingest_watch(tmp, date(2026, 11, 22), alerts, info)
            self.assertEqual(alerts, {})
            self.assertEqual(info, [])
        finally:
            shutil.rmtree(tmp)



if __name__ == "__main__":
    unittest.main()

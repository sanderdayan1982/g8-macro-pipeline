"""P-3 (opción A, autorizada 1-oct): QUALITY …_FFILL en las filas ACM construidas con un tramo arrastrado
(AUD: letras F1 diarias, bonos F2 semanales). Solo procedencia: valores intactos, s01b emite igual,
brief.json lo declara en flags."""
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import acm_g8  # noqa: E402
import s01b  # noqa: E402
import dashboard_alerts as alerts  # noqa: E402


def bdays(start, n):
    return pd.bdate_range(start, periods=n)


class DailyPanelFfill(unittest.TestCase):
    def panel(self):
        d = bdays("2026-09-14", 13)                                       # 14-sep … 30-sep
        bills = pd.Series(np.linspace(4.3, 4.6, len(d)), index=d)
        bonds = pd.Series(np.linspace(4.9, 5.2, 8), index=d[:8])          # F2: hasta mié 23-sep
        with patch.dict(acm_g8.DAILY_FILES, {"XXX": {}}), \
             patch.dict(acm_g8.DAILY_FRED_EXTRA, {}, clear=False), \
             patch.dict(acm_g8.DAILY_DV_FILES, {}, clear=False), \
             patch.dict(acm_g8.DAILY_SOURCE_EXTRA, {"XXX": {0.25: lambda: bills, 10: lambda: bonds}}), \
             patch.object(acm_g8, "persist_extra", lambda *a, **k: None), \
             patch.object(pd.Timestamp, "today", classmethod(lambda cls: pd.Timestamp("2026-10-01"))):
            return acm_g8.build_daily_panel("XXX"), bonds

    def test_carried_rows_marked_and_values_unchanged(self):
        p, bonds = self.panel()
        ff = p.attrs["ffilled"]
        self.assertEqual(str(p.index[-1].date()), "2026-09-30")          # ffill(limit=5) intacto
        self.assertFalse(ff.loc[:"2026-09-23"].any())
        self.assertTrue(ff.loc["2026-09-24":].all())
        self.assertEqual(int(ff.sum()), 5)
        self.assertTrue((p.loc["2026-09-24":, 10] == bonds.iloc[-1]).all())

    def test_tag_ffill_appends_suffix_only_on_marked_rows(self):
        p, _ = self.panel()
        q = np.array(["ACM_K3_380m"] * len(p), dtype=object)
        out, n = acm_g8.tag_ffill(q, p.index, p.attrs["ffilled"])
        self.assertEqual(n, 5)
        self.assertEqual(list(out[:8]), ["ACM_K3_380m"] * 8)
        self.assertEqual(list(out[8:]), ["ACM_K3_380m_FFILL"] * 5)
        self.assertEqual(list(q), ["ACM_K3_380m"] * len(p))               # entrada no mutada

    def test_tag_ffill_without_mask_is_noop(self):
        q = np.array(["ACM_K3_380m"] * 3, dtype=object)
        out, n = acm_g8.tag_ffill(q, bdays("2026-09-28", 3), None)
        self.assertEqual((list(out), n), (list(q), 0))


class S01bGatingUnchanged(unittest.TestCase):
    def test_suffix_does_not_change_emission_or_badge(self):
        for base in ("ACM_K3_380m", "ACM_K3_200m", "ACM_K3_SHORT_SAMPLE_136m", "ACM_K3_NOWCAST_PARALLEL", ""):
            self.assertEqual(s01b.quality_ok(base + "_FFILL" if base else base), s01b.quality_ok(base), base)
        self.assertEqual(s01b.quality_ok("ACM_K3_380m_FFILL"), (True, ""))


class BriefFlag(unittest.TestCase):
    def test_acm_quality_reads_ffill_from_last_row(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        with open(os.path.join(tmp, "ACM_G8_AUD.csv"), "w", encoding="utf-8") as fh:
            fh.write("DATE,Y10_FIT,RNY10,TP10,QUALITY\n20260923,5.19,3.92,1.27,ACM_K3_380m\n"
                     "20260930,5.19,3.92,1.27,ACM_K3_380m_FFILL\n")
        with patch.object(alerts, "DATA", tmp):
            self.assertIn("FFILL", alerts._acm_quality("ACM_G8_AUD.csv"))

    def test_build_book_declares_flag(self):
        with open(os.path.join(ROOT, "scripts", "dashboard_alerts.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn('r["flags"].append("TP curva arrastrada (ffill)")', src)


if __name__ == "__main__":
    unittest.main()

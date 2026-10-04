"""Recovery guards: forecast horizon and source failures never silently replace inputs."""
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import io
import zipfile
from datetime import datetime, timezone
import openpyxl
import fetch_gbp_real as GBP
from g8common import bis_bulk as BIS, series as S

class BoERecovery(unittest.TestCase):
    def zip(self, values=(5.424033836234473,1.992255268983954,3.431778567250519), when=datetime(2026,10,1), nested=False):
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w') as z:
            for kind,value in zip(('nominal','real','inflation'),values):
                w=openpyxl.Workbook();s=w.active;s.title='4. spot curve'
                s.append([None,'UK '+kind+' spot curve']);s.append(['Maturity']);s.append(['years:',5,10]);s.append(['#VALUE!']);s.append([when,0,value])
                b=io.BytesIO();w.save(b);w.close();z.writestr(GBP.NAMES[kind],b.getvalue())
        if not nested:return buf.getvalue()
        outer=io.BytesIO()
        with zipfile.ZipFile(outer,'w') as z:z.writestr('latest.zip',buf.getvalue())
        return outer.getvalue()

    def test_official_october_excerpt_and_nested_layout(self):
        # Official three 10Y values downloaded in Actions run 37224657436; workbook shape reproduced.
        for nested in (False,True):
            rows=GBP.parse(self.zip(nested=nested),date(2026,10,4))
            self.assertEqual(rows[0][0],'20261001')
            self.assertAlmostEqual(rows[0][2],1.992255268983954)
            self.assertAlmostEqual(rows[0][3],3.431778567250519)

    def test_invalid_identity_and_future_dates_rejected(self):
        for blob in (self.zip((5,1,2)), self.zip(when=datetime(2026,10,5))):
            with self.assertRaises(ValueError):GBP.parse(blob,date(2026,10,4))

    def test_month_rollover_preserves_entire_history(self):
        old=S.parse(GBP.render([('20260929',5.3807,1.9942,3.3865),('20260930',5.4028,1.9964,3.4064)]))
        new=S.parse(GBP.render(GBP.parse(self.zip(),date(2026,10,4))))
        merged=S.merge('RY_G8_GBP.csv',old,new,{'min':-3,'max':8,'max_jump':0.6},now_utc=datetime(2026,10,4,tzinfo=timezone.utc),measures=['NOM10','REAL10','BE10'])
        self.assertEqual(merged.status,'PUBLISH');self.assertEqual(len(merged.series.rows),len(old.rows)+1)
        self.assertTrue(all(merged.series.rows[d]==r for d,r in old.rows.items()))

    def test_old_iadb_cannot_regress_workbook_date(self):
        old=S.parse(GBP.render([('20261001',5.4,2,3.4)]));new=S.parse(GBP.render([('20260930',5.3,2,3.3)]))
        r=S.merge('RY_G8_GBP.csv',old,new,now_utc=datetime(2026,10,4,tzinfo=timezone.utc))
        self.assertEqual(r.status,'REGRESSION_BLOCKED');self.assertIsNone(r.series)

    def test_iadb_missing_leg_and_bad_identity_rejected(self):
        with self.assertRaises(ValueError):GBP.parse_iadb('DATE,IUDMNZC\n01 Oct 2026,5\n',date(2026,10,4))
        with self.assertRaises(ValueError):GBP.parse_iadb('DATE,IUDMNZC,IUDMRZC,IUDMIZC\n01 Oct 2026,5,1,1\n',date(2026,10,4))

class BISRecovery(unittest.TestCase):
    def test_flat_export_labels_filter_frequency_country_units(self):
        text='FREQ:Frequency,REF_AREA:Reference area,TIME_PERIOD:Time period or range,OBS_VALUE:Observation Value,UNIT_MEASURE:Unit of measure,UNIT_MULT:Unit Multiplier\nD: Daily,JP: Japan,2026-09-29,1.25,368: Per cent per year,0: Units\nM: Monthly,JP: Japan,2026-09,9,368: Per cent per year,0: Units\nD: Daily,AU: Australia,2026-09-29,4.6,368: Per cent per year,0: Units\n'
        self.assertEqual(BIS.parse_csv(io.StringIO(text),'JP','2026-09-01','2026-10-04'),[('20260929',1.25)])
        with self.assertRaises(ValueError):BIS.parse_csv(io.StringIO(text.replace('0: Units','1: Tens')),'JP','2026-09-01','2026-10-04')

    def test_overlap_gate_rejects_unverified_alternative(self):
        old=[('202609%02d'%n,1.25) for n in range(1,21)]
        self.assertEqual(BIS.verify_overlap(old,old),20)
        with self.assertRaises(ValueError):BIS.verify_overlap(old[:19],old)
        with self.assertRaises(ValueError):BIS.verify_overlap(old[:-1]+[('20260920',1.5)],old)

class RecoverySchedule(unittest.TestCase):
    def test_recovery_is_not_a_new_periodic_watch(self):
        import yaml
        w=yaml.safe_load((ROOT/'.github/workflows/feed_recovery.yml').read_text())
        on=w.get('on',w.get(True));self.assertNotIn('schedule',on)
        self.assertEqual(on['push']['branches'],['main'])
        text=(ROOT/'scripts/tools/refresh_brief_snapshot.py').read_text()
        self.assertNotIn('save_state(',text);self.assertNotIn('tg_send(',text)

class FrequencyLimit(unittest.TestCase):
    def test_quarterly_inputs_have_no_active_value(self):
        data=json.loads((ROOT/'data/manual/manual_inputs.json').read_text())
        for key in ('CHF_BE_MANUAL','NZD_BE_MANUAL'):
            self.assertTrue(data[key]['disabled'])
            self.assertIsNone(data[key]['value'])
            self.assertIsNone(data[key]['date'])

    def test_browser_cannot_restore_quarterly_fallback(self):
        text=(ROOT/'docs/index.html').read_text()
        self.assertNotIn('BE_CONST.',text)
        self.assertNotIn('function manualConst(',text)
        self.assertNotIn('function constBE(',text)
        self.assertNotIn('MANUAL_DEFAULTS',text)
        self.assertNotIn("v: 0.399, date: '2026-06-13'",text)
        self.assertNotIn("CHF_BE_MANUAL: manualConst",text)
        self.assertNotIn("NZD_BE_MANUAL: manualConst",text)
        self.assertIn("quarterly fallback prohibited",text)
        self.assertFalse((ROOT/'scripts/fetch_snb_forecast.py').exists())

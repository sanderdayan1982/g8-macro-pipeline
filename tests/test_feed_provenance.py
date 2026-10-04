import ast
import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import metals_fairvalue_g8 as M
import metals_provenance as P
import health_derivatives as H
from health_evidence import read_json

class Provenance(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.data=self.root/'data';self.data.mkdir()
        self.now=datetime(2026,10,4,20,tzinfo=timezone.utc)
        self.e=P.Evidence();self.e.anchor=date(2026,9,29)

    def inputs(self, metal):
        self.e.metal=metal
        keys=['price:XAU','BE10','DGS10','EFFR','NFA_DEBT','WRESBAL','WTREGEN','DTWEXBGS','REAL10'] if metal=='XAU' else ['price:XAG','price:XAU','REAL10','DTWEXBGS']
        for k in keys:
            self.e.record(k,pd.Series([1.,2.],index=pd.to_datetime(['2026-09-29','2026-10-01'])),'fixture','daily')

    def create_publication(self):
        for metal in ('XAU','XAG'):
            self.inputs(metal)
            (self.data/f'MFV_G8_{metal}.csv').write_text('DATE,PX\n20260929,1\n')
            (self.data/f'MFV_G8_walkforward_{metal}.csv').write_text('delta,score\n0.9,1\n')
        (self.data/'MFV_G8_state.json').write_text(json.dumps({'metals':{m:{'cot_as_of':'20260929'} for m in ('XAU','XAG')}}))
        proof=P.manifest(self.data,self.e,['XAU','XAG'])
        proof['generated_utc']='2026-10-04T19:00:00Z'
        (self.data/'_ingest/metals_provenance.json').write_text(json.dumps(proof))

    def test_quarterly_debt_never_requested_on_daily_failure(self):
        with patch.object(M,'_fetch_debt_to_penny',return_value=None),patch.object(M,'fetch_fred') as fred:
            with self.assertRaises(RuntimeError): M._fetch_debt_public()
            fred.assert_not_called()
        with patch.object(M,'_http_get') as http:
            for name in ('GFDEBTN','FDHBFIN'):
                with self.assertRaises(ValueError): M.fetch_fred(name)
            with self.assertRaises(ValueError): M.fetch_fred('DGS10',freq='q')
            http.assert_not_called()

    def test_old_annual_input_cannot_return(self):
        (self.data/'OFFICIAL_GOLD_DEMAND.csv').write_text('DATE,CUM_TONNES\n20260831,1\n')
        with patch.object(M,'DATA_DIR',str(self.data)):
            with self.assertRaises(RuntimeError): M.load_official_demand()

    def test_records_original_date_without_modifying_series(self):
        self.e.metal='XAU'
        raw=pd.Series([1.],index=pd.to_datetime(['2026-09-25']))
        self.assertIs(self.e.record('NFA_DEBT',raw,'Treasury','daily'),raw)
        self.assertEqual(self.e.inputs['XAU']['NFA_DEBT']['used_observation'],'2026-09-25')
        self.assertEqual(len(raw),1)

    def test_rejects_stale_and_unverified_inputs(self):
        self.inputs('XAU');self.e.validate('XAU')
        self.e.inputs['XAU']['NFA_DEBT']['used_observation']='2026-08-31'
        with self.assertRaises(ValueError): self.e.validate('XAU')
        self.inputs('XAU');self.e.inputs['XAU']['price:XAU']['publication_frequency']='unverified'
        with self.assertRaises(ValueError): self.e.validate('XAU')

    def test_partial_run_restores_every_previous_output(self):
        before={n:('original '+n).encode() for n in P.OUTPUTS}
        for n,v in before.items(): (self.data/n).write_bytes(v)
        def fail(name,*args):
            (self.data/f'MFV_G8_{name}.csv').write_text('partial valid CSV')
            return False
        with patch.object(M,'DATA_DIR',str(self.data)),patch.object(M,'build_metal',side_effect=fail),patch.object(sys,'argv',['metals']),contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(M.main(),1)
        self.assertEqual(P.snapshot(self.data),before)
        self.assertEqual(read_json(self.data/'_ingest/metals_provenance.json')['status'],'ERROR')

    def test_bound_outputs_and_weekly_deadline(self):
        self.create_publication()
        item={'file':'MFV_G8_XAU.csv','status':'UNKNOWN'}
        H.metals(self.root,self.now,item,read_json);self.assertEqual(item['status'],'CURRENT')
        H.metals(self.root,datetime(2026,10,11,tzinfo=timezone.utc),item,read_json);self.assertEqual(item['status'],'LATE')
        (self.data/'MFV_G8_XAU.csv').write_text('DATE,PX\n20260929,999\n')
        H.metals(self.root,self.now,item,read_json);self.assertEqual(item['status'],'ERROR')

    def test_new_failure_invalidates_previous_verified_manifest(self):
        self.create_publication()
        p=self.data/'_ingest/latest/actions__job_metals.json';p.parent.mkdir()
        p.write_text(json.dumps({'written_utc':'2026-10-04T19:30:00Z','failed':[{'name':'metals_fairvalue'}]}))
        item={'file':'MFV_G8_state.json','status':'UNKNOWN'}
        H.metals(self.root,self.now,item,read_json);self.assertEqual(item['status'],'ERROR')

    def test_option_weekend_wait_and_overdue_are_distinct(self):
        # Real canonical files are read only; the clock and summary are fixed fixtures.
        doc={'latest_session':'2026-10-01','latest':{'EUR':{'ref':1}}}
        state={'last_success':'2026-10-01'}
        def read(p): return state if p.name=='state.json' else doc
        import build_options_summary as B
        folder=self.data/'options/canonical/2026-10-01';folder.mkdir(parents=True)
        for pair in B.CCY.values():
            for code in pair: (folder/(code+'.csv')).write_text('session\n2026-10-01\n')
        item={'file':'OPTIONS_SURFACE.json','status':'UNKNOWN'}
        with patch.object(B,'session_metrics',return_value=doc['latest']):
            H.options(self.root,self.now,item,read);self.assertEqual(item['status'],'PENDING')
            H.options(self.root,datetime(2026,10,5,22,tzinfo=timezone.utc),item,read);self.assertEqual(item['status'],'LATE')
            (folder/'6E.csv').unlink()
            H.options(self.root,self.now,item,read);self.assertEqual(item['status'],'ERROR')

    def test_cash_download_must_follow_latest_publication(self):
        import shutil
        shutil.copytree(ROOT/'sources',self.root/'sources')
        (self.data/'NZD_CASH_ON.csv').write_text('Date,Value\n2026-10-01,2.84\n')
        report={'fetch_detail':{'nzd':{'rc':0,'finished_utc':'2026-10-04T18:30:08Z','files':{'NZD_CASH_ON.csv':{'status':'WRITTEN','max_date':'20261001'},'NZD_BOND_10Y.csv':{'max_date':'20261001'}}}},'families':{'NZ-B2':{'download_kind':'FRESH','status':'NOOP','files':{'NZD_CASH_ON.csv':{'status':'NOOP','src_max':'20261001'}}}}}
        item={'file':'NZD_CASH_ON.csv','status':'UNKNOWN'}
        H.cash(self.root,self.now,item,lambda p:report);self.assertEqual(item['status'],'CURRENT')
        H.cash(self.root,datetime(2026,10,5,3,tzinfo=timezone.utc),item,lambda p:report);self.assertEqual(item['status'],'UNKNOWN')
        # HTTP success after release time does not prove the workbook was updated.
        report['fetch_detail']['nzd']['finished_utc']='2026-10-05T03:00:00Z'
        H.cash(self.root,datetime(2026,10,5,4,tzinfo=timezone.utc),item,lambda p:report);self.assertEqual(item['status'],'UNKNOWN')
        report['fetch_detail']['nzd']['files']['NZD_BOND_10Y.csv']['max_date']='20261002'
        H.cash(self.root,datetime(2026,10,5,4,tzinfo=timezone.utc),item,lambda p:report);self.assertEqual(item['status'],'CURRENT')

    def test_model_math_and_calibration_unchanged(self):
        expected={'asof_weekly': '21719fd555bbca49d5a3282bdf59b037e9b86ee39e4302ca980f395f7f6ba9e8', 'load_slope': 'e1701e96bd0ab9b52a1b4877a2cb38402804b6509b760fb961aad81a62fbb4a4', 'forward_returns': '038777f6d89c8b38399985d676a6e3b3c46e7ad63ebf150d1f8a840f35094506', 'evaluate_delta': '6589da9050cc724411c2c1a43923d6cc1779e0e549f7e80c087abd15725576e0', 'calibrate_delta': 'd940dc3a21f7df4e6b216679f8f298bf21d301374c66bdab88e70d4dbfe468e3', 'cot_activation': 'ebb3884f8aacc54c6a37f08dbc44054d08f05e8f72252cba87fbd8030464a9f6', 'build_panel_xau': 'e6e4ac5fbc5ced916c6e9a44fb6f592cd3019caa080c90f43a45595c5bdae7e5', 'build_panel_xag': '8245f9f45e96b2e75491d8850a6addd711f0ecdbbdd0080faec4be5c171bd7f0', 'build_metal': '5dd4126338c8702440da957704fceec1bc55337e1dde5ef46f74ed893f547f5b'}
        tree=ast.parse((ROOT/'scripts/metals_fairvalue_g8.py').read_text())
        actual={n.name:hashlib.sha256(ast.dump(n,include_attributes=False).encode()).hexdigest() for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in expected}
        self.assertEqual(actual,expected)

    def test_valid_review_signal_is_published_without_changing_quality(self):
        def review(name, tuesdays, delta, bridge, log):
            (self.data/f'MFV_G8_{name}.csv').write_text('DATE,PX\n20260929,2\n')
            (self.data/f'MFV_G8_walkforward_{name}.csv').write_text('delta,score\n0.9,-1\n')
            bridge[name]={'quality':'REVIEW','cot_as_of':'20260929'}
            return False  # existing build_metal reports quality, not execution success
        with patch.object(M,'DATA_DIR',str(self.data)),patch.object(M,'build_metal',side_effect=review),patch.object(P.Evidence,'validate',return_value={}),patch.object(sys,'argv',['metals']),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(M.main(),0)
        self.assertEqual(read_json(self.data/'MFV_G8_state.json')['metals']['XAU']['quality'],'REVIEW')
        self.assertEqual(read_json(self.data/'_ingest/metals_provenance.json')['status'],'VERIFIED')

import sys
import tempfile
import unittest
from datetime import datetime, timezone, date
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import health_evidence as E
import dashboard_alerts as D

class Evidence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'data').mkdir()
        self.now = datetime(2026,10,4,21,tzinfo=timezone.utc)
        self.policy = E.read_json(ROOT / 'sources/policy_evidence.json')['policies']

    def item(self, name, contents, **kw):
        (self.root / 'data' / name).write_text(contents)
        out = dict(file=name,status='UNKNOWN',**kw)
        E.refine(self.root,self.now,out,self.policy)
        return out

    def test_future_announcement_cannot_hide_stale_effective_rate(self):
        x=self.item('FLOOR_USD.csv','DATE,CLOSE\n20260901,3\n20261005,4\n',have_max='2026-10-05',expected_obs='2026-10-01')
        self.assertEqual(x['status'],'LATE')
        self.assertEqual(x['have_max'],'2026-09-01')
        self.assertEqual(x['announced_max'],'2026-10-05')

    def test_valid_current_rate_ignores_announced_change(self):
        x=self.item('FLOOR_USD.csv','DATE,CLOSE\n20261004,3\n20261005,4\n',have_max='2026-10-05',expected_obs='2026-10-01')
        self.assertEqual(x['status'],'CURRENT')
        self.assertEqual(x['have_max'],'2026-10-04')

    def test_policy_mismatch_and_expiry(self):
        self.assertEqual(self.item('GB_POLICY.csv','DATE,CLOSE\n20261001,3.75\n')['status'],'CURRENT')
        self.assertEqual(self.item('GB_POLICY.csv','DATE,CLOSE\n20261001,4\n')['status'],'ERROR')
        self.now=datetime(2026,10,12,tzinfo=timezone.utc)
        self.assertEqual(self.item('GB_POLICY.csv','DATE,CLOSE\n20261001,3.75\n')['status'],'UNKNOWN')

    def test_next_meeting_invalidates_evidence_even_if_recent(self):
        self.policy['GB_POLICY.csv']['next_review_at']='2026-10-04T20:30:00Z'
        self.assertEqual(self.item('GB_POLICY.csv','DATE,CLOSE\n20261001,3.75\n')['status'],'UNKNOWN')

    def test_retired_input_cannot_reappear_silently(self):
        x={'file':'OFFICIAL_GOLD_DEMAND.csv','status':'UNKNOWN'}
        E.refine(self.root,self.now,x,self.policy)
        self.assertEqual(x['status'],'EXCLUDED')
        self.assertEqual(self.item('OFFICIAL_GOLD_DEMAND.csv','DATE,CUM_TONNES\n20260831,1\n')['status'],'ERROR')

    def test_brief_does_not_report_not_yet_effective_policy(self):
        class Clock:
            @staticmethod
            def now(tz): return self.now
        state={}
        with patch.object(D,'datetime',Clock), patch.object(D,'POLICY',[('USD','FLOOR_USD.csv','IORB')]), patch.object(D,'read_series',return_value=[(date(2026,10,4),3.9),(date(2026,10,5),4.0)]):
            D.check_policy(state,[])
        self.assertEqual(state['policy']['USD'],{'v':3.9,'date':'2026-10-04'})

    def test_browser_policy_filters_future_rows(self):
        import shutil, subprocess
        if not shutil.which('node'): self.skipTest('node required')
        js = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const NativeDate=Date;
class Clock extends NativeDate { constructor(...a){super(...(a.length?a:['2026-10-04T21:00:00Z']));} static now(){return new NativeDate('2026-10-04T21:00:00Z').getTime();} }
const ctx={window:{},Date:Clock,console:{log(){},error(){}},fetch:async()=>({ok:true,text:async()=>''}),Papa:{parse:()=>({data:[{date:20261004,close:3.9},{date:20261005,close:4.0}]})}};
vm.createContext(ctx);vm.runInContext(fs.readFileSync('docs/js/data-loader.js','utf8'),ctx);
ctx.window.G8DataLoader.loadAllPolicy().then(out=>{assert.equal(out.us_policy.rows.length,1);assert.equal(out.us_policy.rows[0].close,3.9);}).catch(e=>{console.error(e);process.exit(1);});
'''
        subprocess.run(['node','-e',js],cwd=ROOT,check=True)

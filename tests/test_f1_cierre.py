"""F1 OWNER: actual panel, alert, cadence and trusted workflow regressions."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from urllib.parse import urlparse
from types import SimpleNamespace
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import dashboard_alerts as D
import health_evidence as E
from tools import agent_gate as G, agent_recovery as R
from tools import maintenance_due as MD
from tools.watch_cadence import nominal_day


def workflow():
    return yaml.safe_load((ROOT/'.github/workflows/maintenance_agent.yml').read_text())


class Alerts(unittest.TestCase):
    def test_constant_windows_clear_both_bands_but_missing_data_retains_them(self):
        for prev in ('HI', 'LO'):
            for n in (60, 252, 300):
                vals = [0.0] * n
                self.assertIsNone(D.zscore(vals))
                self.assertEqual(D.band_z(D.zscore(vals), prev, vals), '')
            self.assertEqual(D.band_z(None, prev, [0.0]*59), prev)
            self.assertEqual(D.band_z(None, prev), prev)  # scalar MDP missing is not known flat
            self.assertEqual(D.band_z(2.5, prev, list(range(252))), prev)

    def test_actual_aud_floor_evaluation_clears_stale_hi_and_brief_label(self):
        st = {'floors': {'AUD': {'badge': 'AMPLE', 'zband': 'HI', 'last': 0.0}}}
        observations = [(date(2026, 10, 1), 4.6)]*252
        with patch.object(D, 'FLOORS', [('AUD','AONIA.csv','AU_POLICY.csv','Cash Rate')]), \
             patch.object(D, 'read_series', return_value=observations):
            D.check_floors(st, [])
        self.assertEqual(st['floors']['AUD']['zband'], '')
        with tempfile.TemporaryDirectory() as tmp, patch.object(D,'BRIEF_PATH',str(Path(tmp)/'brief.json')), \
             patch.object(D,'read_series',return_value=[]), patch.object(D,'read_json',return_value={}):
            D.build_brief(st, [])
            doc=json.loads((Path(tmp)/'brief.json').read_text())
        floor = next(x for x in doc['bullets'] if x['sec']=='§02 Floor spreads')
        self.assertNotIn('AUD',floor['text'])
        self.assertNotIn('z extremo',floor['text'])
        self.assertEqual(doc['section_numbering'],'00-07')
        self.assertNotIn('ffva',doc['gates'])
        self.assertIn('§04/§05 Metales MDP',[b['sec'] for b in doc['bullets']])
        self.assertIn('COT · fuera de la página',[b['sec'] for b in doc['bullets']])


class Cadence(unittest.TestCase):
    def test_real_oct12_midnight_delay_keeps_the_active_slot(self):
        self.assertEqual(nominal_day('2026-10-12T23:38:36Z','17 17 * * *'),date(2026,10,12))
        out=subprocess.check_output([sys.executable,str(ROOT/'scripts/tools/watch_cadence.py'),
            '--event-at','2026-10-12T23:38:36Z','--cron','17 17 * * *'],text=True)
        self.assertIn('run=true',out)
        self.assertIn('nominal_date=2026-10-12',out)

    def test_early_queue_creation_and_invalid_metadata(self):
        self.assertEqual(nominal_day('2026-11-01T01:00:00Z','13 17 * * *'),date(2026,10,31))
        for timestamp,cron in [('bad','17 17 * * *'),('2026-10-12T23:38:36','17 17 * * *'),
                               ('2026-10-12T23:38:36Z','17 17 * * 1')]:
            with self.assertRaises(ValueError): nominal_day(timestamp,cron)

    def test_workflows_use_stable_event_creation_not_runner_clock(self):
        for name,job in [('maintenance_agent.yml','preflight'),('ingest_watch.yml','cadence')]:
            wf=yaml.safe_load((ROOT/'.github/workflows'/name).read_text())
            cfg=wf['jobs'][job]
            self.assertEqual(cfg['permissions']['actions'],'read')
            step=next(s for s in cfg['steps'] if s.get('id')=='cadence')
            self.assertIn('--json createdAt',step['run'])
            self.assertIn('--event-at "$event_at" --cron "$EVENT_SCHEDULE"',step['run'])


class Recovery(unittest.TestCase):
    def test_occupancy_errors_block_dispatch_without_a_second_gh_call(self):
        for stdout,rc in [('',0),('bad',0),('{}',0),('[{}]',0),('[]',1),('[{"status":"new_unknown"}]',0)]:
            calls=[]
            def run(cmd,**kwargs):
                calls.append(cmd); return SimpleNamespace(stdout=stdout,returncode=rc)
            result=R.dispatch([{'workflow':'daily_update.yml','inputs':{},'reason':'fixture'}],run=run)
            self.assertEqual(result[0]['result'],'SKIPPED_BUSY')
            self.assertEqual(len(calls),1)
        def unavailable(*args,**kwargs): raise OSError('gh missing')
        self.assertTrue(R.busy('daily_update.yml',unavailable))


class PolicyRenewal(unittest.TestCase):
    def test_five_rates_and_official_calendars_match_without_hiding_ingest_errors(self):
        import csv
        policies=json.loads((ROOT/'sources/policy_evidence.json').read_text())['policies']
        hosts={'GB_POLICY.csv':'bankofengland.co.uk','JP_POLICY.csv':'boj.or.jp',
               'CH_POLICY.csv':'snb.ch','AU_POLICY.csv':'rba.gov.au','NZD_OCR.csv':'rbnz.govt.nz'}
        with (ROOT/'data/manual/cb_meetings.csv').open() as f:
            meetings=list(csv.DictReader(l for l in f if l.strip() and not l.startswith('#')))
        self.assertEqual(set(policies),set(hosts))
        for name,ev in policies.items():
            verified=datetime.fromisoformat(ev['verified_at'].replace('Z','+00:00'))
            expires=datetime.fromisoformat(ev['expires_at'].replace('Z','+00:00'))
            self.assertIsNotNone(verified.tzinfo)
            self.assertTrue(verified < expires <= verified + timedelta(days=7))
            for key in ('source','calendar_source'):
                url=urlparse(ev[key]); self.assertEqual(url.scheme,'https')
                self.assertTrue(url.hostname==hosts[name] or url.hostname.endswith('.'+hosts[name]))
            rates=E.rate_rows(ROOT/'data'/name,verified.date())
            self.assertAlmostEqual(rates[-1][1],ev['rate'])
            next_day=min(r['decision_date'] for r in meetings if r['country']==E.POLICY_CB[name]
                         and r['decision_date']>verified.date().isoformat())
            self.assertEqual(ev['next_review_at'][:10],next_day)
            item={'file':name,'status':'UNKNOWN'}
            E.refine(ROOT,verified+timedelta(minutes=1),item,policies)
            self.assertEqual(item['state'],'VERIFIED_POLICY')
            failed={'file':name,'status':'ERROR','flags':['LAST_FETCH_FAILED']}
            E.refine(ROOT,verified+timedelta(minutes=1),failed,policies)
            self.assertEqual(failed['status'],'ERROR')


class OwnerBundle(unittest.TestCase):
    def git(self,repo,*args):
        return subprocess.check_output(['git','-C',str(repo),*args],text=True).strip()

    def test_uncommitted_policy_edit_is_bundled_without_unrelated_staged_code(self):
        wf=workflow()
        script=next(s['run'] for s in wf['jobs']['agent']['steps'] if s.get('name')=='Package work')
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp)
            self.git(repo,'init','-q'); self.git(repo,'config','user.name','test');self.git(repo,'config','user.email','test@example.invalid')
            (repo/'sources').mkdir();(repo/'sources/policy_evidence.json').write_text('{"old":true}')
            (repo/'README').write_text('base')
            self.git(repo,'add','.');self.git(repo,'commit','-qm','base')
            base=self.git(repo,'rev-parse','HEAD')
            (repo/'sources/policy_evidence.json').write_text('{"proposed":true}')
            (repo/'README').write_text('unrelated staged')
            self.git(repo,'add','README')
            subprocess.run(['bash','-e','-c',script],cwd=repo,env=dict(os.environ,BASE=base),check=True,capture_output=True)
            changed=self.git(repo,'diff','--name-only',base,'HEAD').splitlines()
            self.assertEqual(changed,['sources/policy_evidence.json'])
            self.assertEqual(self.git(repo,'diff','--cached','--name-only'),'README')
            self.assertTrue((repo/'.agent/work.bundle').exists())
            result=G.classify(changed)
            self.assertEqual(result['decision'],'OWNER')
            with patch('os.getcwd',return_value=str(repo)):
                # CLI exercises git against the candidate tree; no candidate imports.
                out=subprocess.run([sys.executable,str(ROOT/'scripts/tools/agent_gate.py'),'--base',base],
                                   cwd=repo,capture_output=True,text=True)
            self.assertEqual(out.returncode,3)
            self.assertIn('OWNER review',out.stdout)

    def test_claude_step_failure_cannot_be_no_changes_or_auto_merged(self):
        script=next(s['run'] for s in workflow()['jobs']['publish']['steps'] if s.get('id')=='publish')
        fragment=script[script.index('result=NO_CHANGES'):script.index('if [ -f .agent/work.bundle ]')]
        for outcome,expected in [('success','NO_CHANGES'),('failure','FAILED'),('cancelled','FAILED'),('','FAILED')]:
            env=dict(os.environ,VERIFY_RESULT='success',AGENT_RESULT='success',CLAUDE_OUTCOME=outcome)
            got=subprocess.check_output(['bash','-c',fragment+'\necho "$result"'],env=env,text=True).strip()
            self.assertEqual(got,expected)
        guard=next(l.strip() for l in script.splitlines() if l.strip().startswith('if [ "$DECISION" = AUTO ]'))
        for outcome,head,expected in [('success','sha','YES'),('failure','sha','NO'),('success','','NO')]:
            env=dict(os.environ,DECISION='AUTO',gate_rc='0',SMOKE='success',VERIFIED_HEAD=head,
                     CLAUDE_OUTCOME=outcome,AGENT_RESULT='success')
            got=subprocess.check_output(['bash','-c',guard+' echo YES; else echo NO; fi'],env=env,text=True).strip()
            self.assertEqual(got,expected)

    def test_record_keeps_agent_and_gate_outcomes_separate_from_no_changes(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(MD,'ROOT',Path(tmp)), \
             patch.object(sys,'argv',['maintenance_due','--record','NO_CHANGES','--agent-outcome','success',
                                     '--verification','success','--gate-decision','NADA']):
            MD.main()
            record=json.loads((Path(tmp)/'data/_ingest/maintenance_state.json').read_text())['attempts'][-1]
        self.assertEqual(record['result'],'NO_CHANGES')
        self.assertEqual(record['agent_outcome'],'success')
        self.assertEqual(record['verification'],'success')
        self.assertEqual(record['gate_decision'],'NADA')

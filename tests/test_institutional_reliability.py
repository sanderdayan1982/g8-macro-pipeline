"""4-Oct operational regression: midnight, calendar isolation, stale final and pure EST rendering."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from tools import finalize_session as FINAL, maintenance_due as MD
import health_monitor as HM
import freshness_report as FR


class SessionClock(unittest.TestCase):
    def test_friday_delayed_until_saturday(self):
        self.assertEqual(FINAL.session_due(datetime(2026, 10, 3, 0, 43, tzinfo=timezone.utc)), date(2026, 10, 2))

    def test_weekday_after_midnight_is_previous_session(self):
        self.assertEqual(FINAL.session_due(datetime(2026, 10, 2, 1, 4, tzinfo=timezone.utc)), date(2026, 10, 1))

    def test_before_and_after_scheduled_close(self):
        self.assertEqual(FINAL.session_due(datetime(2026, 10, 5, 21, 29, tzinfo=timezone.utc)), date(2026, 10, 2))
        self.assertEqual(FINAL.session_due(datetime(2026, 10, 5, 21, 30, tzinfo=timezone.utc)), date(2026, 10, 5))

    def test_target_holiday(self):
        self.assertEqual(FINAL.session_due(datetime(2026, 12, 25, 22, tzinfo=timezone.utc)), date(2026, 12, 24))

    def test_exit_zero_without_log_is_failure(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(FINAL, 'ROOT', Path(tmp)), patch.object(FINAL.subprocess, 'call', return_value=0):
            self.assertEqual(FINAL.main(), 1)


class PublicationHealth(unittest.TestCase):
    def test_japan_holidays_do_not_hide_uk_delay(self):
        cals = HM.SC.load_calendars(str(ROOT))
        self.assertEqual(HM.business_age(date(2026, 9, 18), date(2026, 9, 24), 'GB', cals), 4)
        self.assertEqual(HM.business_age(date(2026, 9, 18), date(2026, 9, 24), 'JP', cals), 1)

    def test_recent_provisional_does_not_mask_old_state(self):
        def read(path):
            if str(path).endswith('state.json'):
                return {'t': '2026-09-23'}
            return {'as_of': '2026-10-02', 'generated_utc': '2026-10-03T20:00:00Z'}
        with patch.object(HM, 'read_json', side_effect=read):
            out = HM.build(ROOT, datetime(2026, 10, 4, 14, tzinfo=timezone.utc), {'outputs': []})
        final = next(x for x in out['feeds'] if x['file'] == 's01b/state.json')
        self.assertEqual((final['status'], final['expected_obs']), ('LATE', '2026-10-02'))

    def test_sonia_free_channel_is_two_business_days(self):
        rules, params, outputs, cals = HM.FR.load_config(str(ROOT))
        o = next(x for x in outputs if x['file'] == 'SONIA.csv')
        r = HM.FR.evaluate(o, rules['SONIA'], params['SONIA'], cals,
            datetime(2026, 10, 2, 12, tzinfo=timezone.utc), {date(2026, 9, 30)})
        self.assertEqual(r['expected_obs'], '2026-09-30')
        self.assertEqual(r['state'], 'CURRENT')

    def test_unknown_is_not_current(self):
        r = {'file': 'unknown.csv', 'state': 'UNKNOWN_SCHEDULE', 'facts': {}}
        out = HM.build(ROOT, datetime(2026, 10, 4, 14, tzinfo=timezone.utc), {'outputs': [r]})
        self.assertEqual(out['feeds'][0]['status'], 'UNKNOWN')


class MaintenanceBudget(unittest.TestCase):
    def test_limits_never_clear_incident(self):
        now = datetime(2026, 10, 4, 15, tzinfo=timezone.utc)
        health = {'needs_repair': True}
        state = {'attempts': [{'at': '2026-10-04T01:00:00Z'}, {'at': '2026-10-04T05:00:00Z'}]}
        self.assertFalse(MD.decide(health, state, now)[0])
        self.assertTrue(health['needs_repair'])
        self.assertTrue(MD.decide(health, state, now, manual=True)[0])

    def test_daily_preventive_review(self):
        self.assertTrue(MD.decide({'needs_repair': False}, {}, datetime(2026, 10, 4, tzinfo=timezone.utc))[0])


@unittest.skipUnless(shutil.which('node'), 'node required')
class DashboardRegression(unittest.TestCase):
    def test_estimate_survives_second_render_without_mutating_official_rows(self):
        code = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const h=fs.readFileSync('docs/index.html','utf8');
const start=h.indexOf('function assemble(raw) {')+'function assemble(raw) {'.length;
const end=h.indexOf('    /* 6 repo currencies */',start);
const ctx={raw:{AUD:{ry:{rows:[{DATE:20260930,NOM10:5.344}]}},audEst:[{DATE:'20261001',NOM10:'5.3882',REAL10:'2.9754',BE10:'2.4128',ERR10_BP:'3.8',ERRBE_BP:'1.6',MODEL:'EST_AUD_V2',H_BD:'1'}]},isoNorm:String};
vm.createContext(ctx);
const fn='(function(raw){'+h.slice(start,end)+';return {badge:audEstInfo,rows:raw.AUD.ry.rows.length,official:audOfficial};})(raw)';
const first=vm.runInContext(fn,ctx), second=vm.runInContext(fn,ctx);
assert(first.badge && second.badge);assert.equal(ctx.raw.AUD.ry.rows.length,1);
assert.equal(first.official.length,1);assert.equal(first.rows,2);assert.equal(second.rows,2);
'''
        subprocess.run(['node', '-e', code], cwd=ROOT, check=True, capture_output=True)


class WorkflowSeparation(unittest.TestCase):
    def test_candidate_never_gets_write_token_or_provider_secrets(self):
        import yaml
        w = yaml.safe_load((ROOT / '.github/workflows/maintenance_agent.yml').read_text())
        verify = w['jobs']['verify']
        self.assertEqual(verify['permissions'], {'contents': 'read'})
        self.assertNotIn('secrets.', json.dumps(verify))
        pub = w['jobs']['publish']
        self.assertNotIn('validate_smoke.sh', json.dumps(pub))
        self.assertNotIn('pip install', json.dumps(pub))
        self.assertIn('trusted_gate.py', json.dumps(pub))
        names = [s.get('name', s.get('id')) for s in verify['steps']]
        self.assertLess(names.index('Trusted gate and dependencies (before candidate checkout)'), names.index('load'))

    def test_new_collection_never_runs_paid_collectors(self):
        from tools.refresh_feeds import GROUPS
        scripts = [job[1] for group in GROUPS.values() for job in group]
        self.assertFalse(any('options' in x or 'futures' in x or 'acm_g8' in x for x in scripts))


if __name__ == '__main__':
    unittest.main()

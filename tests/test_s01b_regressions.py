"""Operational regressions: stale FX, fixed windows, restart, replay and delivery."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import s01b as m
import dashboard_alerts as alerts


def fixture():
    cal, d = [], date(2023, 1, 2)
    while len(cal) < 320:
        if m.is_target_day(d): cal.append(d)
        d += timedelta(days=1)
    acm = [(d, 0.5 + (0.5 if i > 300 else 0), 2., 2.5 + (0.5 if i > 300 else 0), 'ACM_K3_400m') for i,d in enumerate(cal)]
    return {'calendar': cal, 'f': [-.001]*len(cal), 'r': {c:[-.001]*len(cal) for c in m.CCY8 if c != 'USD'},
            'acm': {c:acm for c in m.CCY8}, 'nom': {c:[(d,3.) for d in cal] for c in m.CCY8},
            'y2': {c:[] for c in m.CCY8}, 'be': {c:[] for c in m.CCY8}, 'sha': {}}


class OperationalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.paths = patch.multiple(m, DATA=str(self.root), S01B=str(self.root/'s01b'),
            LOG_DIR=str(self.root/'s01b/log'), SNAP_DIR=str(self.root/'s01b/snap'), STAGE=str(self.root/'s01b/.staging'),
            STATE_PATH=str(self.root/'s01b/state.json'), EVENTS_PATH=str(self.root/'s01b/events.jsonl'), OUT_JSON=str(self.root/'S01B.json'))
        self.paths.start()
        self.addCleanup(self.paths.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_stale_fx_freezes_state(self):
        inp=fixture(); t=inp['calendar'][-1]; eff=inp['calendar'][-6]
        st={c:{'signal':'ON','persist':7,'fx_fail_streak':1,'initialized':True} for c in m.EMITTERS}
        rows, events, err=m.run_session(t,inp,st,eff,True,str(self.root))
        self.assertIsNone(err); self.assertEqual(events,[])
        for r in rows:
            if r['emitter']:
                self.assertEqual((r['avail'],r['signal'],r['persist'],r['fx_fail_streak']),('NO_DATA','ON',7,1))

    def test_desfase_keeps_start_date(self):
        inp=fixture(); cal=inp['calendar']; t=cal[-1]
        # Large move on t0 must remain excluded even with a missing final publication.
        for c in inp['r']:
            inp['r'][c]=[0.]*len(cal); inp['r'][c][-23]=-.2
        rows,events,_=m.run_session(t,inp,{},cal[-2],True,str(self.root))
        eur=next(r for r in rows if r['ccy']=='EUR')
        self.assertEqual(eur['d_fx'],0.); self.assertEqual(eur['signal'],'OFF')

    def test_missing_interior_fx_does_not_shorten_calendar(self):
        inp=fixture(); t=inp['calendar'][-1]
        del inp['calendar'][-10]; del inp['f'][-10]
        for v in inp['r'].values(): del v[-10]
        rows,events,_=m.run_session(t,inp,{},t,True,str(self.root))
        self.assertTrue(all(r['avail']=='NO_DATA' for r in rows if r['emitter']))
        self.assertEqual(events,[])

    def test_first_valid_observation_is_baseline(self):
        inp=fixture(); t=inp['calendar'][-1]
        st={c:{'signal':'OFF','avail':'NO_CAL','initialized':False} for c in m.EMITTERS}
        rows,events,_=m.run_session(t,inp,st,t,True,str(self.root))
        self.assertEqual(len(events),7)
        self.assertTrue(all(e['baseline'] and e['type']=='BASELINE' for e in events))

    def test_publish_replay_and_recovery_restore_state_and_outbox(self):
        inp=fixture(); t=inp['calendar'][-1]
        rows,events,_=m.run_session(t,inp,{},t,True,str(self.root))
        lg,out,st,_=m.build_outputs(t,rows,events,inp,t,True,'test-run')
        m.publish(t,lg,out,st,inp,str(self.root),False)
        self.assertEqual(m.replay(t.isoformat()),0)
        Path(m.STATE_PATH).unlink(); Path(m.OUT_JSON).unlink(); Path(m.EVENTS_PATH).unlink()
        m.republish_from_log(t)
        self.assertEqual(json.loads(Path(m.STATE_PATH).read_text())['currencies'],st)
        self.assertEqual(json.loads(Path(m.OUT_JSON).read_text()),out)
        self.assertEqual(len(Path(m.EVENTS_PATH).read_text().splitlines()),7)
        m.republish_from_log(t)
        self.assertEqual(len(Path(m.EVENTS_PATH).read_text().splitlines()),7)

    def test_replay_detects_snapshot_tampering(self):
        import gzip
        inp=fixture(); t=inp['calendar'][-1]
        rows,events,_=m.run_session(t,inp,{},t,True,str(self.root))
        lg,out,st,_=m.build_outputs(t,rows,events,inp,t,True,'test-run')
        m.publish(t,lg,out,st,inp,str(self.root),False)
        with gzip.open(Path(m.SNAP_DIR)/t.isoformat()/'inputs.json.gz','wb') as fh: fh.write(b'{}')
        self.assertEqual(m.replay(t.isoformat()),1)

    def test_unknown_quality_rejected(self):
        self.assertFalse(m.quality_ok('ACM_K9_400m')[0])
        self.assertFalse(m.quality_ok('ACM_K3_SHORT_SAMPLE_garbage')[0])

    def test_unsent_old_event_is_not_lost_after_new_session(self):
        ev={'ccy':'USD','t':'2026-09-17','type':'ON','d_tp':30,'theta':20,'d_fx':-1}
        ledger=self.root/'s01b'; ledger.mkdir()
        (ledger/'events.jsonl').write_text(json.dumps(ev)+'\n')
        st={}; lines=[]
        with patch.object(alerts,'DATA',str(self.root)), patch.object(alerts,'read_json',return_value={'as_of':'2026-09-18','events':[]}):
            alerts.check_s01b(st,lines)
            self.assertEqual(len(lines),1)
            alerts.check_s01b(st,lines)
            self.assertEqual(len(lines),1)

    def test_failed_delivery_does_not_ack_event(self):
        old={'s01b':{'sent':[]},'existing':True}
        def checker(st,lines):
            st['s01b']['sent']=['USD:2026-09-17:ON'];lines.append('§1b test')
        names=['check_floors','check_policy','check_tp','check_vs_usd','check_real_vs_usd','check_metals','check_walls','check_cot','check_factor','check_dqm']
        with contextlib.ExitStack() as stack:
            for name in names: stack.enter_context(patch.object(alerts,name,lambda st,lines:None))
            stack.enter_context(patch.object(alerts,'check_s01b',checker))
            stack.enter_context(patch.object(alerts,'load_state',return_value=old))
            stack.enter_context(patch.object(alerts,'tg_send',return_value=False))
            stack.enter_context(patch.object(alerts,'build_brief',return_value=None))
            save=stack.enter_context(patch.object(alerts,'save_state'))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            self.assertEqual(alerts.main([]),1)
            self.assertEqual(save.call_args[0][0]['s01b']['sent'],[])

if __name__=='__main__': unittest.main()

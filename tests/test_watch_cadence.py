import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
import yaml
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from tools.watch_cadence import active

class AlternateDayWatch(unittest.TestCase):
    def test_anchor_and_next_day(self):
        self.assertTrue(active(date(2026, 10, 4)))
        self.assertFalse(active(date(2026, 10, 5)))
        self.assertTrue(active(date(2026, 10, 6)))

    def test_never_resets_at_month_or_year_boundary(self):
        day = date(2026, 10, 4)
        for n in range(800):
            self.assertEqual(active(day + timedelta(days=n)), n % 2 == 0)

    def test_both_workflows_gate_expensive_work(self):
        watch = yaml.safe_load((ROOT / '.github/workflows/ingest_watch.yml').read_text())
        self.assertEqual(watch['jobs']['watch']['needs'], 'cadence')
        self.assertIn('cadence.outputs.run', watch['jobs']['watch']['if'])
        agent = yaml.safe_load((ROOT / '.github/workflows/maintenance_agent.yml').read_text())
        steps = agent['jobs']['preflight']['steps']
        health = next(s for s in steps if 'health_monitor.py' in s.get('run', ''))
        due = next(s for s in steps if s.get('id') == 'due')
        for step in (health, due):
            self.assertEqual(step['if'], "steps.cadence.outputs.run == 'true'")

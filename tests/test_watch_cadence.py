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
        self.assertFalse(active(date(2026, 10, 4)))  # Domingo: fin de semana apagado
        self.assertTrue(active(date(2026, 10, 5)))   # Lunes: activo
        self.assertFalse(active(date(2026, 10, 6)))  # Martes: descanso
        self.assertTrue(active(date(2026, 10, 7)))   # Miércoles: activo
        self.assertFalse(active(date(2026, 10, 8)))  # Jueves: descanso
        self.assertTrue(active(date(2026, 10, 9)))   # Viernes: activo
        self.assertFalse(active(date(2026, 10, 10))) # Sábado: fin de semana apagado
        self.assertFalse(active(date(2026, 10, 11))) # Domingo: fin de semana apagado
        self.assertTrue(active(date(2026, 10, 12)))  # Lunes: retoma activo

    def test_never_resets_at_month_or_year_boundary(self):
        day = date(2026, 10, 5)
        for n in range(800):
            d = day + timedelta(days=n)
            self.assertEqual(active(d), d.weekday() in (0, 2, 4))

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

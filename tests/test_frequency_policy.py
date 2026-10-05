"""Owner policy: never reactivate quarterly manual feeds through restored files."""
import csv
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import dashboard_alerts as A

class FrequencyPolicy(unittest.TestCase):
    def test_restored_quarterly_values_cannot_enter_brief(self):
        for disabled in (True, False):
            legacy = {c + '_BE_MANUAL': {'value': 2.7, 'date': A.TODAY.isoformat(), 'disabled': disabled}
                      for c in ('CHF', 'NZD')}
            with patch.object(A, 'read_json', side_effect=lambda p: legacy if p == 'manual/manual_inputs.json' else {}), patch.object(A, 'read_series', return_value=[]):
                rows = A.build_book({})
            for row in rows:
                if row['ccy'] in ('CHF', 'NZD'):
                    self.assertIsNone(row['be'])
                    self.assertIsNone(row['real'])
                    self.assertIn('BE/REAL no disponible: respaldo trimestral excluido', row['flags'])

    def test_registered_frequencies_respect_monthly_ceiling(self):
        # Event-driven policy decisions and derived/disabled artifacts are not
        # slow statistical publications. Contract maturities are not cadences.
        allowed = {'daily', 'weekly', 'monthly', 'disabled', 'event-driven', 'event',
                   'event+daily', 'derived', 'ended', 'excluded', 'none', 'on_demand'}
        for name in ('registry.csv', 'freshness_rules.csv'):
            with (ROOT / 'sources' / name).open() as f:
                for row in csv.DictReader(f):
                    self.assertIn(row['frequency'].split(':')[0], allowed, (name, row))

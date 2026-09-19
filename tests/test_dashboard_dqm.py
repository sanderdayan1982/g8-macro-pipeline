"""DQM must use the observation date, never the USD factor build timestamp."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import date
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dashboard_alerts as alerts

class FactorDateTests(unittest.TestCase):
    def test_observation_date_controls_freshness(self):
        for observed, expected in [("2026-09-18", "LIVE"), ("2026-08-01", "DEAD"), (None, None)]:
            with self.subTest(observed=observed), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "data").mkdir()
                (root / "data/USD_FACTOR.json").write_text(json.dumps({"as_of": observed, "generated_utc": "2026-09-19T12:00:00Z", "generated": "2026-09-19"}))
                registry = root / "registry.csv"
                registry.write_text("feed_id,primary_access,max_staleness_bd\nUSD_FACTOR,data/USD_FACTOR.json,5\n")
                state, lines = {}, []
                with patch.multiple(alerts, ROOT=str(root), DATA=str(root / "data"), REGISTRY=str(registry), TODAY=date(2026, 9, 19)), patch.object(alerts, "note") as note:
                    alerts.check_dqm(state, lines)
                    self.assertEqual(state["dqm"].get("USD_FACTOR"), expected)
                    self.assertEqual(note.called, observed is None)

if __name__ == "__main__":
    unittest.main()

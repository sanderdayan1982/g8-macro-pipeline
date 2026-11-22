"""t322u: a feed already excluded from surveillance does not count as an incidencia.

Mirrors test_H4_disabled_manual_is_excluded (tests/test_p11_concurrent_push_health.py):
an intentionally parked feed must not appear in health issues, even if a later
consistency check would have labelled it ERROR.
"""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import health_monitor as HM  # noqa: E402


class ExcludedFeedNotIssue(unittest.TestCase):
    NOW = datetime(2026, 11, 21, 14, 59, tzinfo=timezone.utc)

    def test_excluded_options_surface_incomplete_canonical_is_not_an_issue(self):
        row = {
            "file": "OPTIONS_SURFACE.json",
            "state": "NOT_MONITORED",
            "have_max": "2026-11-19",
            "expected_obs": None,
            "facts": {},
        }
        out = HM.build(ROOT, self.NOW, {"outputs": [row]})
        item = next(x for x in out["feeds"] if x["file"] == "OPTIONS_SURFACE.json")
        issues = {x["file"] for x in out["issues"]}
        self.assertEqual(item["status"], "EXCLUDED")
        self.assertEqual(item["state"], "NOT_MONITORED")
        self.assertEqual(item.get("publication_frequency"), "excluded")
        self.assertNotIn("OPTIONS_SURFACE.json", issues)
        self.assertFalse(HM.is_counted_issue(item))
        # Diagnostic only: the canonical-session check is retained as a note, not an incidencia.
        self.assertTrue(item.get("note"))

    def test_prohibited_retired_file_still_counts(self):
        item = {
            "file": "OFFICIAL_GOLD_DEMAND.csv",
            "status": "ERROR",
            "state": "PROHIBITED",
            "publication_frequency": "excluded",
        }
        self.assertTrue(HM.is_counted_issue(item))


if __name__ == "__main__":
    unittest.main()

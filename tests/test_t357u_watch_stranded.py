"""t357u: watch_vps_jobs lee INCIDENCIA STRANDED de ALERTAS.log y health_monitor la cuenta como incidencia."""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vps"))
sys.path.insert(0, str(ROOT / "scripts"))
import vps_job_common as C  # noqa: E402
import watch_vps_jobs as W  # noqa: E402
import health_monitor as H  # noqa: E402

NOW_UTC = "2026-11-24T10:00:00Z"
NOW = datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)


class Stranded(unittest.TestCase):
    def test_line_written_by_job_is_parsed(self):
        line = C.append_stranded_alert("usd-factor", "1", log_path="/nonexistent/dir/ALERTAS.log",
                                       now_utc="2026-11-24T09:00:00Z")
        lines = W.parse_fallo_lines(line, NOW_UTC, keep_sec=48 * 3600)
        self.assertEqual([x["unit"] for x in lines], ["stranded:usd-factor"])
        self.assertIn("STRANDED", lines[0]["text"])

    def test_old_line_dropped(self):
        line = C.append_stranded_alert("metals", "2", log_path="/nonexistent/dir/ALERTAS.log",
                                       now_utc="2026-11-21T09:00:00Z")
        self.assertEqual(W.parse_fallo_lines(line, NOW_UTC, keep_sec=48 * 3600), [])

    def test_counts_as_incidencia_since_cut(self):
        line = C.append_stranded_alert("metals", "1", log_path="/nonexistent/dir/ALERTAS.log",
                                       now_utc="2026-11-24T09:00:00Z")
        status = W.build_status([], W.parse_fallo_lines(line, NOW_UTC), NOW_UTC)
        items = H.vps_unit_items(status, NOW, last_cut_utc="2026-11-24T08:00:00Z", require=True)
        self.assertEqual([x["file"] for x in items], ["vps/stranded:metals"])
        self.assertTrue(H.is_counted_issue(items[0]))

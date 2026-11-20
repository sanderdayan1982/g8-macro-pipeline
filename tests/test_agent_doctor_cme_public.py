"""t283u: agent_doctor mira la proyección data/public/, no el OPTIONS_SURFACE.json público retirado."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "tools"))
import agent_doctor as AD  # noqa: E402

SHADOW_STUBS = [name for name, *_ in AD.SHADOW_ARTIFACTS]


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


class CmePublicProjection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        for name in SHADOW_STUBS:
            _write(self.data / name, {"ok": True})
        _write(self.data / "_ingest" / "health.json", {
            "issues": [{
                "file": "OPTIONS_SURFACE.json",
                "status": "ERROR",
                "flags": ["NOT_MONITORED"],
                "reason": "Inconsistencia en opciones: 'latest_session'",
            }],
            "feeds": [],
        })
        (self.data / "OPTIONS_SURFACE.json").write_bytes(b"")
        self.now = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def _project(self, name, **fields):
        body = {"status": "OK", "as_of": "2026-11-19"}
        body.update(fields)
        _write(self.data / "public" / name, body)

    def test_ignores_retired_options_surface_health_issue(self):
        for name, *_ in AD.PUBLIC_CME_PROJECTIONS:
            self._project(name)
        diag = AD.collect_diagnosis(data_dir=str(self.data))
        ids = [inc["id"] for inc in diag["incidents"]]
        self.assertNotIn("OPTIONS_SURFACE.json", ids)
        self.assertNotIn("CME_OPTIONS_C1_shadow.json", ids)

    def test_missing_projection_is_error(self):
        incs = AD.cme_public_projection_incidents(str(self.data), now=self.now)
        kinds = {inc["id"]: inc for inc in incs}
        self.assertEqual(set(kinds), {name for name, *_ in AD.PUBLIC_CME_PROJECTIONS})
        for inc in incs:
            self.assertEqual(inc["kind"], "CME_PUBLIC_PROJECTION")
            self.assertEqual(inc["state"], "ERROR")
            self.assertEqual(inc["causes"], ["PUBLIC_PROJECTION_MISSING"])
            self.assertNotIn("DATABENTO", inc["remediation"])

    def test_recent_as_of_is_current(self):
        for name, *_ in AD.PUBLIC_CME_PROJECTIONS:
            self._project(name, status="OK", as_of="2026-11-19")
        self.assertEqual(AD.cme_public_projection_incidents(str(self.data), now=self.now), [])

    def test_stale_as_of_is_late(self):
        self._project("CME_OPTIONS_SURFACE_public.json", status="OK", as_of="2026-11-16")
        self._project("CME_C1_public.json", status="OK", as_of="2026-11-19")
        self._project("CME_FXVOL_public.json", status="OK", generated_utc="2026-11-19T12:00:00Z")
        incs = AD.cme_public_projection_incidents(str(self.data), now=self.now)
        self.assertEqual([inc["id"] for inc in incs], ["CME_OPTIONS_SURFACE_public.json"])
        self.assertEqual(incs[0]["state"], "LATE")
        self.assertEqual(incs[0]["causes"], ["STALE"])
        self.assertEqual(incs[0]["last_obs"], "2026-11-16")
        self.assertNotIn("DATABENTO", incs[0]["remediation"])

    def test_unavailable_status_is_warning(self):
        self._project("CME_OPTIONS_SURFACE_public.json", status="UNAVAILABLE", as_of="2026-11-19")
        self._project("CME_C1_public.json", status="OK", as_of="2026-11-19")
        self._project("CME_FXVOL_public.json", status="OK", as_of="2026-11-19")
        incs = AD.cme_public_projection_incidents(str(self.data), now=self.now)
        self.assertEqual([inc["id"] for inc in incs], ["CME_OPTIONS_SURFACE_public.json"])
        self.assertEqual(incs[0]["state"], "WARNING")
        self.assertEqual(incs[0]["causes"], ["PUBLIC_PROJECTION_UNAVAILABLE"])

    def test_does_not_read_retired_public_options_file(self):
        for name, *_ in AD.PUBLIC_CME_PROJECTIONS:
            self._project(name)
        diag = AD.collect_diagnosis(data_dir=str(self.data))
        self.assertTrue((self.data / "OPTIONS_SURFACE.json").exists())
        self.assertEqual(os.path.getsize(self.data / "OPTIONS_SURFACE.json"), 0)
        self.assertFalse(any(inc["id"] == "OPTIONS_SURFACE.json" for inc in diag["incidents"]))


if __name__ == "__main__":
    unittest.main()

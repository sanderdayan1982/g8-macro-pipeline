"""Pruebas de scripts/ingest_watch.py — latido ausente/tardío, familias fallidas, credenciales, cambio de hora,
deduplicación, recordatorio, resolución y entrega fallida. Nunca envía (notify.send sustituido)."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import ingest_watch as W  # noqa: E402
from g8common import notify  # noqa: E402

EXE_HDR = "executor_id,job,timezone,run_times_local,days,alert_after_min,active_from,recovery,provisional,justification\n"


def ts(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "sources"))
        self.set_exec("mac-primary,nzchf-tona,Africa/Malabo,08:00,mon-sun,45,2026-09-20,x,Y,x\n")
        self.set_creds("MAC_DATA_PAT,github_pat,loc,u,NZ,UNKNOWN,,n\n")
        self.sent = []
        self.fail_send = False
        self._orig = notify.send

        def fake_send(text, dry=None, **kw):
            if self.fail_send:
                return "FAIL_TRANSIENT"
            self.sent.append(text)
            return "SENT"
        notify.send = fake_send

    def tearDown(self):
        notify.send = self._orig
        shutil.rmtree(self.root)

    def set_exec(self, line):
        open(os.path.join(self.root, "sources", "executors.csv"), "w").write(EXE_HDR + line)

    def set_creds(self, line):
        open(os.path.join(self.root, "sources", "credentials.csv"), "w").write(
            "credential_id,kind,location,used_by,affects,expires_utc,expiry_source,notes\n" + line)

    def heartbeat(self, started, families=None, cred=None, fetch=None):
        p = os.path.join(self.root, "data", "_ingest", "latest", "mac-primary__nzchf-tona.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        json.dump({"run_id": "r", "started_utc": started, "families": families or {"NZ-B2": {"status": "NOOP"}},
                   "credentials": cred or {"days_left": 90}, "fetch_status": fetch or {"nzd": "0"}}, open(p, "w"))

    def run_w(self, when):
        out = os.path.join(self.root, "res.json")
        W.main(["--result", out], now=lambda: ts(when), root=self.root)
        return json.load(open(out))

    def test_missing_heartbeat_after_alert_window(self):
        self.heartbeat("2026-09-23T07:00:05Z")                  # ayer
        r = self.run_w("2026-09-24T07:46:00Z")                  # 08:00 Bata = 07:00Z; +45 min vencido
        self.assertIn("exec:mac-primary:nzchf-tona:missing", r["alerts"])
        self.assertIn("sin latido de la ejecución de las 08:00", self.sent[0])

    def test_within_alert_window_no_alert(self):
        self.heartbeat("2026-09-23T07:00:05Z")
        r = self.run_w("2026-09-24T07:40:00Z")                  # aún dentro de los 45 min: el slot vigente es el de ayer
        self.assertNotIn("exec:mac-primary:nzchf-tona:missing", r["alerts"])

    def test_fresh_heartbeat_ok_and_family_failures(self):
        self.heartbeat("2026-09-24T07:00:05Z", families={"NZ-B2": {"status": "PUBLISH_FAIL", "cls": "FAIL_AUTH"},
                                                         "JP-TONA": {"status": "HELD", "held": [{"file": "TONA.csv", "date": "20260918", "value": 1.5}]}})
        r = self.run_w("2026-09-24T08:00:00Z")
        self.assertIn("exec:mac-primary:nzchf-tona:fam:NZ-B2", r["alerts"])
        self.assertIn("exec:mac-primary:nzchf-tona:held:JP-TONA", r["alerts"])
        self.assertNotIn("exec:mac-primary:nzchf-tona:missing", r["alerts"])

    def test_not_active_before_installation(self):
        self.set_exec("mac-primary,nzchf-tona,Africa/Malabo,08:00,mon-sun,45,,x,Y,x\n")
        r = self.run_w("2026-09-24T09:00:00Z")
        self.assertFalse([a for a in r["alerts"] if a.startswith("exec:")])

    def test_dedupe_new_slot_resolve(self):
        self.heartbeat("2026-09-23T07:00:05Z")
        self.run_w("2026-09-24T07:46:00Z")
        self.run_w("2026-09-24T10:00:00Z")
        self.assertEqual(sum("sin latido" in s for s in self.sent), 1)        # no se repite el mismo día
        self.run_w("2026-09-25T07:47:00Z")                                     # nueva ejecución omitida → nuevo aviso
        self.assertIn("25-Sep", self.sent[-1])
        self.heartbeat("2026-09-25T07:48:00Z")
        self.run_w("2026-09-25T08:00:00Z")
        self.assertIn("resuelto", self.sent[-1])

    def test_persistent_alert_reminded_every_24h(self):
        fam = {"NZ-B2": {"status": "PUBLISH_FAIL", "cls": "FAIL_PERMISSION"}}
        self.heartbeat("2026-09-24T07:00:05Z", families=fam)
        self.run_w("2026-09-24T08:00:00Z")
        self.run_w("2026-09-24T20:00:00Z")
        self.assertEqual(sum("PUBLISH_FAIL" in s for s in self.sent), 1)
        self.heartbeat("2026-09-25T07:00:05Z", families=fam)
        self.run_w("2026-09-25T08:01:00Z")
        self.assertTrue(any("🔁" in s and "PUBLISH_FAIL" in s for s in self.sent))

    def test_credentials_buckets_and_unknown_once(self):
        self.heartbeat("2026-09-24T07:00:05Z")
        self.set_creds("MAC_DATA_PAT,github_pat,loc,u,NZ,2026-09-29T00:00:00Z,,n\n")
        r = self.run_w("2026-09-24T08:00:00Z")
        self.assertIn("cred:MAC_DATA_PAT", r["alerts"])
        self.assertIn("≤7 días", self.sent[-1])
        self.run_w("2026-09-27T08:00:00Z")
        self.assertIn("≤3 días", self.sent[-1])                  # cambia el tramo → se avisa de nuevo
        self.set_creds("MAC_DATA_PAT,github_pat,loc,u,NZ,UNKNOWN,,n\n")
        self.run_w("2026-09-27T09:00:00Z")
        self.run_w("2026-09-29T09:00:00Z")
        self.assertEqual(sum("sin fecha de caducidad" in s for s in self.sent), 1)   # informativo: sin recordatorio

    def test_mac_reports_token_expiry(self):
        self.heartbeat("2026-09-24T07:00:05Z", cred={"days_left": 4.5, "expires_utc": "2026-09-28T19:00:00Z"})
        r = self.run_w("2026-09-24T08:00:00Z")
        self.assertIn("exec:mac-primary:nzchf-tona:token", r["alerts"])

    def test_delivery_failure_reported_and_retried(self):
        self.heartbeat("2026-09-23T07:00:05Z")
        self.fail_send = True
        r = self.run_w("2026-09-24T07:46:00Z")
        self.assertEqual(r["delivery"], "FAIL_TRANSIENT")
        self.fail_send = False
        self.run_w("2026-09-24T08:30:00Z")
        self.assertTrue(any("sin latido" in s for s in self.sent))   # se reintenta en la siguiente ejecución

    def test_dst_zurich_slot(self):
        row = {"timezone": "Europe/Zurich", "run_times_local": "08:00", "days": "mon-fri", "alert_after_min": "30"}
        summer = W.last_due_slot(row, datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc))   # viernes, CEST
        winter = W.last_due_slot(row, datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc))   # lunes, CET
        self.assertEqual(summer.strftime("%H:%M"), "06:00")
        self.assertEqual(winter.strftime("%H:%M"), "07:00")

    def test_weekend_rule(self):
        row = {"timezone": "Africa/Malabo", "run_times_local": "08:00", "days": "mon-fri", "alert_after_min": "45"}
        s = W.last_due_slot(row, datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc))        # domingo
        self.assertEqual(s.date().isoformat(), "2026-09-25")


if __name__ == "__main__":
    unittest.main()

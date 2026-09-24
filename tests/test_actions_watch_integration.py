"""Hallazgo #4 — integración descargador de Actions → vigilancia (sin red, sin Telegram real).

Recorre el camino completo: fetch_tona (código real, respuesta grabada del BoJ con una corrección histórica)
→ Ingest.publish deja el candidato HELD en cuarentena y termina con 0 → ingest_watch lo lee y avisa (una vez,
con recordatorio diario), reintenta si la entrega falla, y anuncia la resolución cuando una segunda descarga
correcta lo confirma. También: regresión bloqueada y pasos cortados por el plazo del job.
"""
import json
import os
import shutil
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.dirname(__file__))

import fetcher_harness as H  # noqa: E402
import ingest_watch as W  # noqa: E402
from g8common import notify  # noqa: E402

KEY = "actions:held:TONA.csv"


def ts(dt):
    return dt.replace(tzinfo=timezone.utc).timestamp()


class ActionsToWatch(unittest.TestCase):
    def setUp(self):
        self.rows = H.repo_rows("TONA.csv")
        self.orig = open(os.path.join(ROOT, "data", "TONA.csv"), "rb").read()
        self.root = H.make_root({"TONA.csv": self.orig})
        self.sent, self.fail = [], False
        self._send = notify.send

        def fake_send(text, dry=None, **kw):
            if self.fail:
                return "FAIL_TRANSIENT"
            self.sent.append(text)
            return "SENT"
        notify.send = fake_send
        # corrección histórica de TONA (fecha antigua, sin ventana revisable): el patrón de la revisión
        lo = (H.NOW - timedelta(days=365 * 5)).strftime("%Y%m") + "01"
        rows = [r for r in self.rows if r[0] >= lo]
        i = len(rows) - 30
        self.rev_date = rows[i][0]
        rows[i] = (rows[i][0], "%.4f" % (float(rows[i][1]) + 0.02))
        self.body = H.boj_json(rows)

    def tearDown(self):
        notify.send = self._send
        shutil.rmtree(self.root, ignore_errors=True)

    def fetch(self, clock=None):
        return H.run_new("fetch_tona", lambda url: (200, self.body), self.root, clock=clock)

    def watch(self, when):
        out = os.path.join(self.root, "res.json")
        W.main(["--result", out], now=lambda: ts(when), root=self.root)
        return json.load(open(out))

    def test_held_revision_alerted_deduplicated_retried_and_resolved(self):
        clock = H.clock_at_now()
        rc, calls, _ = self.fetch(clock)
        self.assertEqual(rc, 0)                                             # HELD no derriba el job…
        self.assertEqual(H.read(self.root, "TONA.csv"), self.orig)         # …se conserva el último válido
        latest = json.load(open(os.path.join(self.root, "data", "_ingest", "latest", "actions__fetch_tona.json")))
        self.assertEqual(latest["files"]["TONA.csv"]["status"], "HELD")

        t = H.NOW + timedelta(minutes=10)
        self.fail = True                                                    # 1) Telegram falla
        r1 = self.watch(t)
        self.assertIn(KEY, r1["alerts"])
        self.assertEqual(r1["delivery"], "FAIL_TRANSIENT")                  # el workflow pondrá el job en rojo
        self.fail = False
        r2 = self.watch(t + timedelta(hours=1))                             # 2) se reintenta
        self.assertEqual(r2["delivery"], "SENT")
        msg = [m for m in self.sent if "TONA.csv" in m]
        self.assertEqual(len(msg), 1)
        self.assertIn(self.rev_date, msg[0])
        self.assertIn("data/_ingest/decisions/TONA.csv/", msg[0])
        n = len(self.sent)
        r3 = self.watch(t + timedelta(hours=2))                             # 3) sin cambios: no se repite
        self.assertIn(KEY, r3["alerts"])
        self.assertEqual(len(self.sent), n)
        self.watch(t + timedelta(hours=26))                                 # 4) recordatorio diario
        self.assertTrue(any("🔁" in m and "TONA.csv" in m for m in self.sent[n:]))

        clock.t += 9 * 3600                                                 # 5) segunda descarga correcta
        rc2, _, _ = self.fetch(clock)
        self.assertEqual(rc2, 0)
        self.assertNotEqual(H.read(self.root, "TONA.csv"), self.orig)      # aceptada con trazabilidad
        m = len(self.sent)
        r5 = self.watch(t + timedelta(hours=27))
        self.assertNotIn(KEY, r5["alerts"])
        self.assertTrue(any("resuelto" in x and "TONA.csv" in x for x in self.sent[m:]))

    def test_regression_blocked_is_alerted(self):
        short = H.boj_json([r for r in self.rows if r[0] >= "20210901"][:-3])
        rc, _, _ = H.run_new("fetch_tona", lambda url: (200, short), self.root)
        self.assertEqual(rc, 1)
        r = self.watch(H.NOW + timedelta(minutes=5))
        self.assertIn("actions:fetch_tona:TONA.csv:regression_blocked", r["alerts"])

    def test_old_pointer_ignored(self):
        self.fetch()
        r = self.watch(H.NOW + timedelta(days=9))
        self.assertIn(KEY, r["alerts"])                                     # la cuarentena sigue pendiente
        self.assertFalse([a for a in r["alerts"] if a.startswith("actions:fetch_tona:")])

    def test_job_time_limits_alerted(self):
        p = os.path.join(self.root, "data", "_ingest", "latest", "actions__job_daily.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        json.dump({"job": "daily", "written_utc": "2026-09-24T21:50:00Z", "steps": [],
                   "time_limited": [{"name": "acm_g8", "status": "TIMEOUT"},
                                    {"name": "jpy_real", "status": "SKIPPED_NO_TIME"}]}, open(p, "w"))
        r = self.watch(datetime(2026, 9, 24, 21, 55))
        self.assertIn("actions:job:daily:time", r["alerts"])
        self.assertTrue(any("acm_g8 TIMEOUT" in x for x in self.sent))

    def test_reviewer_repro_now_alerts(self):
        # Reproducción de la revisión: revisión histórica sin cambiar la fecha máxima → HELD, exit 0.
        from g8common import ingest
        old = b"DATE,OPEN,HIGH,LOW,CLOSE,VOLUME\n20260917,0.9,0.9,0.9,0.9,0\n20260918,0.91,0.91,0.91,0.91,0\n"
        new = old.replace(b"0.9,0.9,0.9,0.9", b"0.92,0.92,0.92,0.92")
        with open(os.path.join(self.root, "data", "TONA.csv"), "wb") as fh:
            fh.write(old)
        ctx = ingest.Ingest("fetch_tona", root=self.root, env={})
        rep = ctx.publish("TONA.csv", new)
        self.assertEqual(rep["status"], "HELD")
        self.assertEqual(ctx.finish(0), 0)
        r = self.watch(datetime.now(timezone.utc).replace(tzinfo=None))
        self.assertIn(KEY, r["alerts"])
        self.assertEqual(r["delivery"], "SENT")


if __name__ == "__main__":
    unittest.main()

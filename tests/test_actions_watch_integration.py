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
import frozen_data  # noqa: E402  (datos congelados de ed9ed64)
import ingest_watch as W  # noqa: E402
from g8common import notify  # noqa: E402

KEY = "actions:held:TONA.csv"


def ts(dt):
    return dt.replace(tzinfo=timezone.utc).timestamp()


class ActionsToWatch(unittest.TestCase):
    def setUp(self):
        self.rows = H.repo_rows("TONA.csv")
        self.orig = frozen_data.read_bytes("TONA.csv")
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

    # ── R2-4: el envejecimiento no es una recuperación ──────────────────────────────────────────────
    def test_r2_4_aging_never_resolves(self):
        short = H.boj_json([r for r in self.rows if r[0] >= "20210901"][:-3])
        H.run_new("fetch_tona", lambda url: (200, short), self.root)
        k = "actions:fetch_tona:TONA.csv:regression_blocked"
        r1 = self.watch(H.NOW + timedelta(minutes=5))
        self.assertIn(k, r1["alerts"])
        n = len(self.sent)
        r2 = self.watch(H.NOW + timedelta(days=9))                          # sin ninguna ejecución posterior
        self.assertIn(k, r2["alerts"])                                      # sigue activo
        self.assertIn("actions:fetch_tona:silence", r2["alerts"])           # y se avisa de la ausencia
        self.assertFalse([m for m in self.sent[n:] if "resuelto" in m])
        # recuperación REAL: una ejecución posterior correcta sí lo resuelve
        H.run_new("fetch_tona", lambda url: (200, H.boj_json([r for r in self.rows if r[0] >= "20210901"])), self.root,
                  now=H.NOW + timedelta(days=9), clock=H.Clock(H.NOW_EPOCH + 9 * 86400))
        r3 = self.watch(H.NOW + timedelta(days=9, hours=1))
        self.assertNotIn(k, r3["alerts"])
        self.assertNotIn("actions:fetch_tona:silence", r3["alerts"])
        self.assertTrue([m for m in self.sent if "resuelto" in m and "REGRESSION_BLOCKED" in m])

    def test_r2_4_reviewer_repro_book_plans_no_resolved(self):
        from g8common.notify import AlertBook
        rows = [r for r in self.rows if r[0] >= "20210901"][:-3]
        H.run_new("fetch_tona", lambda url: (200, H.boj_json(rows)), self.root)
        t1 = (H.NOW + timedelta(minutes=5)).replace(tzinfo=timezone.utc)
        t2 = t1 + timedelta(days=9)
        book = AlertBook(os.path.join(self.root, "book.json"))
        a1 = {}
        W.check_actions(self.root, t1, a1, [], prev_active=book.state["active"])
        book.commit(a1, list(a1), t1.timestamp(), [])
        a2 = {}
        W.check_actions(self.root, t2, a2, [], prev_active=book.state["active"])
        self.assertFalse([x for x in book.plan(a2, t2.timestamp()) if x[0] == "RESOLVED"])

    def test_r2_4_vanished_record_is_not_a_recovery_retirement_is_explicit(self):
        short = H.boj_json([r for r in self.rows if r[0] >= "20210901"][:-3])
        H.run_new("fetch_tona", lambda url: (200, short), self.root)
        k = "actions:fetch_tona:TONA.csv:regression_blocked"
        self.watch(H.NOW + timedelta(minutes=5))
        os.remove(os.path.join(self.root, "data", "_ingest", "latest", "actions__fetch_tona.json"))
        n = len(self.sent)
        r = self.watch(H.NOW + timedelta(hours=2))
        self.assertIn(k, r["alerts"])
        self.assertTrue(any("sin evidencia de recuperación" in m for m in self.sent[n:]))
        with open(os.path.join(self.root, "sources", "actions_jobs.csv"), "a") as fh:
            fh.write("fetch_tona,RETIRED,96,retirado en prueba\n")
        n = len(self.sent)
        r = self.watch(H.NOW + timedelta(hours=3))
        self.assertNotIn(k, r["alerts"])
        self.assertTrue(any("retirado por configuración" in m for m in self.sent[n:]))
        self.assertFalse([m for m in self.sent[n:] if "resuelto" in m])

    # ── R2-3: el fallo FINAL de una descarga avisa; el recuperado no ────────────────────────────────
    def _fail_case(self, serve):
        rc, calls, _ = H.run_new("fetch_tona", serve, self.root)
        a = {}
        W.check_actions(self.root, (H.NOW + timedelta(hours=1)).replace(tzinfo=timezone.utc), a, [])
        return rc, calls, a

    def test_r2_3_http_503_exhausted_alerts_then_success_resolves(self):
        rc, calls, a = self._fail_case(lambda url: (503, b"Unavailable"))
        self.assertEqual((rc, len(calls)), (1, 4))
        self.assertIn("actions:fetch_tona:fail", a)
        self.assertIn("FAIL_TRANSIENT", a["actions:fetch_tona:fail"])
        r1 = self.watch(H.NOW + timedelta(hours=1))
        self.assertIn("actions:fetch_tona:fail", r1["alerts"])
        self.fetch(H.Clock(H.NOW_EPOCH + 7200))                              # descarga posterior correcta
        r2 = self.watch(H.NOW + timedelta(hours=3))
        self.assertNotIn("actions:fetch_tona:fail", r2["alerts"])
        self.assertTrue(any("resuelto" in m and "fetch_tona" in m for m in self.sent))

    def test_r2_3_auth_failure_alerts(self):
        rc, _, a = self._fail_case(lambda url: (401, b"denied"))
        self.assertEqual(rc, 1)
        self.assertIn("FAIL_AUTH", a["actions:fetch_tona:fail"])

    def test_r2_3_parse_error_before_publish_alerts(self):
        empty = json.dumps({"STATUS": 200, "RESULTSET": []}).encode()
        rc, _, a = self._fail_case(lambda url: (200, empty))
        self.assertEqual(rc, 1)
        self.assertIn("tras obtener respuesta", a["actions:fetch_tona:fail"])

    def test_r2_3_intermediate_failure_recovered_no_alert(self):
        seq = [(503, b""), (200, self.body)]
        rc, calls, a = self._fail_case(lambda url: seq.pop(0) if len(seq) > 1 else seq[0])
        self.assertEqual((rc, len(calls)), (0, 2))
        self.assertFalse([k for k in a if k.endswith(":fail")])

    def test_r2_3_retry_after_deferral_is_not_a_failure(self):
        rc, calls, a = self._fail_case(lambda url: (503, (b"", {"retry-after": "7200"})))
        self.assertEqual(rc, 1)
        self.assertFalse([k for k in a if k.endswith(":fail")])

    def test_r2_3_repeated_deferrals_eventually_alert(self):
        self.fetch()                                                         # correcta: last_ok = NOW
        for d in (1, 2, 3, 4, 5):                                            # 5 días seguidos aplazada
            H.run_new("fetch_tona", lambda url: (503, (b"", {"retry-after": "7200"})), self.root,
                      now=H.NOW + timedelta(days=d), clock=H.Clock(H.NOW_EPOCH + d * 86400))
        a = {}
        W.check_actions(self.root, (H.NOW + timedelta(days=5, hours=1)).replace(tzinfo=timezone.utc), a, [])
        self.assertIn("actions:fetch_tona:no_success", a)
        self.assertFalse([k for k in a if k.endswith(":fail")])

    def test_r2_3_failed_step_without_pointer_alerts_covered_step_does_not(self):
        p = os.path.join(self.root, "data", "_ingest", "latest", "actions__job_daily.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        json.dump({"job": "daily", "run_id": "77", "written_utc": "2026-09-24T21:50:00Z", "steps": [],
                   "time_limited": [], "failed": [{"name": "us_bills", "rc": 1}, {"name": "tona", "rc": 1}]}, open(p, "w"))
        q = os.path.join(self.root, "data", "_ingest", "latest", "actions__fetch_tona.json")
        json.dump({"job": "fetch_tona", "rc": 1, "finished_utc": "2026-09-24T21:40:00Z", "files": {},
                   "requests": [{"cls": "FAIL_TRANSIENT", "detail": "HTTP 503"}], "step": "tona", "gh_run_id": "77"}, open(q, "w"))
        a = {}
        W.check_actions(self.root, datetime(2026, 9, 24, 22, 0, tzinfo=timezone.utc), a, [])
        self.assertIn("actions:job:daily:step:us_bills", a)                 # descargador no adoptado (3B)
        self.assertNotIn("actions:job:daily:step:tona", a)                  # ya lo cubre su registro detallado
        self.assertIn("actions:fetch_tona:fail", a)

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


    # ── R3-2: un aplazamiento no es una recuperación ────────────────────────────────────────────────
    DEFER = staticmethod(lambda url: (503, (b"Unavailable", {"retry-after": "7200"})))

    def _run(self, serve, hours):
        return H.run_new("fetch_tona", serve, self.root, now=H.NOW + timedelta(hours=hours),
                         clock=H.Clock(H.NOW_EPOCH + hours * 3600))

    def test_r3_2_failure_then_deferral_stays_open_then_success_resolves(self):
        self._run(lambda url: (503, b"Unavailable"), 0)
        r1 = self.watch(H.NOW + timedelta(minutes=5))
        self.assertIn("actions:fetch_tona:fail", r1["alerts"])
        self._run(self.DEFER, 1)
        n = len(self.sent)
        r2 = self.watch(H.NOW + timedelta(hours=1, minutes=5))
        self.assertIn("actions:fetch_tona:fail", r2["alerts"])                          # sigue abierta
        self.assertFalse([m for m in self.sent[n:] if "resuelto" in m])
        self.assertTrue(any("aplazada por el proveedor" in m for m in self.sent[n:]))
        self._run(lambda url: (200, self.body), 4)                                      # descarga correcta
        r3 = self.watch(H.NOW + timedelta(hours=4, minutes=5))
        self.assertNotIn("actions:fetch_tona:fail", r3["alerts"])
        self.assertTrue(any("resuelto" in m and "fetch_tona" in m for m in self.sent))

    def test_r3_2_first_short_deferral_without_incident_is_silent(self):
        self._run(self.DEFER, 0)
        r = self.watch(H.NOW + timedelta(minutes=5))
        self.assertFalse([a for a in r["alerts"] if a.startswith("actions:fetch_tona")])

    def test_r3_2_regression_then_deferral_not_resolved(self):
        short = H.boj_json([r for r in self.rows if r[0] >= "20210901"][:-3])
        self._run(lambda url: (200, short), 0)
        k = "actions:fetch_tona:TONA.csv:regression_blocked"
        self.assertIn(k, self.watch(H.NOW + timedelta(minutes=5))["alerts"])
        self._run(self.DEFER, 1)
        self.assertIn(k, self.watch(H.NOW + timedelta(hours=1, minutes=5))["alerts"])

    def test_r3_2_failed_step_not_resolved_by_time_limit(self):
        p = os.path.join(self.root, "data", "_ingest", "latest", "actions__job_daily.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)

        def ledger(run, steps, failed, lim):
            json.dump({"job": "daily", "run_id": run, "written_utc": "2026-09-24T21:50:00Z", "steps": steps,
                       "failed": failed, "time_limited": lim}, open(p, "w"))
        k = "actions:job:daily:step:us_bills"
        ledger("1", [{"name": "us_bills", "status": "FAILED"}], [{"name": "us_bills", "rc": 1}], [])
        self.assertIn(k, self.watch(datetime(2026, 9, 24, 22, 0))["alerts"])
        ledger("2", [{"name": "us_bills", "status": "SKIPPED_NO_TIME"}], [], [{"name": "us_bills", "status": "SKIPPED_NO_TIME"}])
        self.assertIn(k, self.watch(datetime(2026, 9, 24, 23, 0))["alerts"])
        ledger("3", [{"name": "us_bills", "status": "OK"}], [], [])
        self.assertNotIn(k, self.watch(datetime(2026, 9, 25, 22, 0))["alerts"])

    def test_reviewer_repro_r3_2(self):
        from g8common.notify import AlertBook
        H.run_new("fetch_tona", lambda u: (503, b"Unavailable"), self.root)
        t1 = (H.NOW + timedelta(minutes=5)).replace(tzinfo=timezone.utc)
        a1 = {}
        W.check_actions(self.root, t1, a1, [])
        b = AlertBook(os.path.join(self.root, "book.json"))
        b.commit(a1, list(a1), t1.timestamp(), [])
        self._run(self.DEFER, 1)
        t2 = t1 + timedelta(hours=1)
        a2 = {}
        W.check_actions(self.root, t2, a2, [], prev_active=b.state["active"])
        self.assertFalse([x for x in b.plan(a2, t2.timestamp()) if x[0] == "RESOLVED"])

    # ── R3-3: aplazamientos indefinidos sin ningún éxito previo ─────────────────────────────────────
    def test_r3_3_never_success_repeated_deferrals_alert(self):
        for day in range(6):
            self.assertEqual(self._run(self.DEFER, 24 * day)[0], 1)
        rec = json.load(open(os.path.join(self.root, "data", "_ingest", "latest", "actions__fetch_tona.json")))
        self.assertIsNone(rec["last_ok_utc"])
        self.assertEqual(rec["failing_since_utc"], "2026-09-24T21:35:00Z")               # estable: el primer intento
        a = {}
        W.check_actions(self.root, (H.NOW + timedelta(days=5, hours=1)).replace(tzinfo=timezone.utc), a, [])
        self.assertIn("actions:fetch_tona:no_success", a)
        self.assertIn("no consta ninguna descarga correcta", a["actions:fetch_tona:no_success"])
        self._run(lambda url: (200, self.body), 24 * 5 + 2)                              # éxito posterior
        a2 = {}
        W.check_actions(self.root, (H.NOW + timedelta(days=5, hours=3)).replace(tzinfo=timezone.utc), a2, [])
        self.assertNotIn("actions:fetch_tona:no_success", a2)
        rec = json.load(open(os.path.join(self.root, "data", "_ingest", "latest", "actions__fetch_tona.json")))
        self.assertIsNone(rec["failing_since_utc"])

    def test_r3_3_initial_wait_within_limit_no_alert(self):
        for h in (0, 24, 48):
            self._run(self.DEFER, h)
        a = {}
        W.check_actions(self.root, (H.NOW + timedelta(hours=49)).replace(tzinfo=timezone.utc), a, [])
        self.assertNotIn("actions:fetch_tona:no_success", a)

    def test_r3_3_transition_from_previous_version_record(self):
        p = os.path.join(self.root, "data", "_ingest", "latest", "actions__fetch_tona.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        json.dump({"job": "fetch_tona", "rc": 1, "started_utc": "2026-09-20T21:35:00Z",
                   "finished_utc": "2026-09-20T21:36:00Z", "requests": [{"cls": "DEFERRED"}], "files": {}}, open(p, "w"))
        self._run(self.DEFER, 0)
        rec = json.load(open(p))
        self.assertEqual(rec["failing_since_utc"], "2026-09-20T21:35:00Z")               # hereda el inicio anterior
        a = {}
        W.check_actions(self.root, (H.NOW + timedelta(hours=1)).replace(tzinfo=timezone.utc), a, [])
        self.assertIn("actions:fetch_tona:no_success", a)                                # > 96 h desde el 20-sep


if __name__ == "__main__":
    unittest.main()

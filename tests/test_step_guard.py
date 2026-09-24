"""R2-1 — un paso cortado o fallido nunca deja en data/ un fichero vacío, a medias o retrocedido para el commit.

Usa el ESCRITOR REAL no atómico de fetch_us_bills (write_csv, modo «w») interrumpido a mitad de escritura por
g8step, y comprueba con git real (repositorio temporal, sin remotos ni commit) lo que prepararía `git add data/`.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts", "tools"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import g8step  # noqa: E402

OLD = b"DATE,OPEN,HIGH,LOW,CLOSE,VOLUME\n20260922,4,4,4,4,0\n20260923,4,4,4,4,0\n"
WRITER = r'''import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from fetch_us_bills import write_csv
p = Path(sys.argv[2])
def rows():
    yield ("20260922", 4.0)
    p.with_suffix(".ready").write_text("escritor abierto")
    time.sleep(60)
    yield ("20260925", 4.2)
write_csv(rows(), p)
'''
OTHER = r'''import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from fetch_us_bills import write_csv
write_csv([("20260922", 3.0), ("20260923", 3.1), ("20260924", 3.2)], Path(sys.argv[2]))
'''


class StepGuard(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.data = os.path.join(self.t, "data")
        os.makedirs(os.path.join(self.data, "options"))
        self.p = os.path.join(self.data, "US_BILL_1M.csv")
        self.q = os.path.join(self.data, "US_BILL_3M.csv")
        for f in (self.p, self.q):
            open(f, "wb").write(OLD)
        open(os.path.join(self.data, "options", "big.json"), "w").write("{}")
        subprocess.run(["git", "init", "-q", self.t], check=True)
        subprocess.run(["git", "-C", self.t, "-c", "user.email=x@x", "-c", "user.name=x", "add", "data/"], check=True)
        subprocess.run(["git", "-C", self.t, "-c", "user.email=x@x", "-c", "user.name=x", "commit", "-qm", "base"], check=True)
        self.env = dict(os.environ, G8_STEP_LOG=os.path.join(self.t, "steps.jsonl"))
        for k in ("G8_JOB_DEADLINE_EPOCH", "G8_JOB_RESERVE_S"):
            self.env.pop(k, None)
        self._g = g8step.GRACE_KILL_S
        g8step.GRACE_KILL_S = 1

    def tearDown(self):
        g8step.GRACE_KILL_S = self._g
        shutil.rmtree(self.t)

    def script(self, name, body):
        f = os.path.join(self.t, name)
        open(f, "w").write(body)
        return f

    def step(self, name, code_file, *args, cap=5):
        return g8step.run_step(name, [sys.executable, code_file, os.path.join(ROOT, "scripts")] + list(args), cap, 0.1,
                               env=self.env, guard=self.data)

    def staged(self, rel):
        subprocess.run(["git", "-C", self.t, "add", "data/"], check=True)
        return subprocess.run(["git", "-C", self.t, "show", ":data/" + rel], check=True, capture_output=True).stdout

    def records(self):
        return [json.loads(x) for x in open(os.path.join(self.t, "steps.jsonl"))]

    def test_interrupted_real_writer_keeps_last_valid_and_other_feed_update(self):
        rc1 = self.step("us_bills_3m", self.script("other.py", OTHER), self.q)            # otro feed: correcto
        self.assertEqual(rc1, 0)
        rc = self.step("us_bills", self.script("w.py", WRITER), self.p, cap=2)
        self.assertEqual(rc, 124)
        self.assertEqual(open(self.p, "rb").read(), OLD)                                   # bytes intactos
        self.assertEqual(self.staged("US_BILL_1M.csv"), OLD)                               # el commit no ve el vacío
        self.assertIn(b"20260924,3.2000", self.staged("US_BILL_3M.csv"))                  # y sí la actualización válida
        rec = self.records()[-1]
        self.assertEqual(rec["status"], "TIMEOUT")
        # la marca .ready (creada dentro de data/ cuando el escritor ya había abierto el CSV) prueba que la
        # escritura empezó; como salida nueva de un paso cortado, también se retira
        self.assertEqual({r["file"] for r in rec["restored"]}, {"US_BILL_1M.csv", "US_BILL_1M.ready"})
        self.assertFalse(os.path.exists(self.p[:-4] + ".ready"))

    def test_failed_step_keeps_only_valid_outputs(self):
        body = r'''import os, sys
d = os.path.dirname(sys.argv[2])
open(os.path.join(d, "US_BILL_3M.csv"), "w").write("DATE,OPEN,HIGH,LOW,CLOSE,VOLUME\n20260922,4,4,4,4,0\n20260923,4,4,4,4,0\n20260924,4.1,4.1,4.1,4.1,0\n")
open(os.path.join(d, "US_BILL_1M.csv"), "w").write("")                                   # vacío
open(os.path.join(d, "EUR_BILL_1Y.csv"), "w").write("DATE,CLOSE\n20260920,2\n")          # retrocede
open(os.path.join(d, "NEW_EMPTY.csv"), "w").write("")
os.remove(os.path.join(d, "GONE.csv"))
open(os.path.join(d, "STATE.json"), "w").write("{roto")
sys.exit(1)
'''
        open(os.path.join(self.data, "EUR_BILL_1Y.csv"), "w").write("DATE,CLOSE\n20260920,2\n20260923,2.1\n")
        open(os.path.join(self.data, "GONE.csv"), "w").write("DATE,CLOSE\n20260923,1\n")
        open(os.path.join(self.data, "STATE.json"), "w").write('{"ok": 1}')
        before = {n: open(os.path.join(self.data, n), "rb").read() for n in ("EUR_BILL_1Y.csv", "GONE.csv", "STATE.json")}
        rc = self.step("mixed", self.script("m.py", body), self.p)
        self.assertEqual(rc, 1)
        self.assertIn(b"20260924", open(self.q, "rb").read())                             # válido: se conserva
        self.assertEqual(open(self.p, "rb").read(), OLD)
        for n, b in before.items():
            self.assertEqual(open(os.path.join(self.data, n), "rb").read(), b, n)
        self.assertFalse(os.path.exists(os.path.join(self.data, "NEW_EMPTY.csv")))
        rec = self.records()[-1]
        self.assertEqual({r["file"] for r in rec["restored"]},
                         {"US_BILL_1M.csv", "EUR_BILL_1Y.csv", "NEW_EMPTY.csv", "GONE.csv", "STATE.json"})
        self.assertEqual(rec["kept_valid_outputs"], ["US_BILL_3M.csv"])

    def test_ok_step_regression_is_also_blocked(self):
        body = 'import sys; open(sys.argv[2], "w").write("DATE,OPEN,HIGH,LOW,CLOSE,VOLUME\\n20260922,4,4,4,4,0\\n")\n'
        self.assertEqual(self.step("short", self.script("s.py", body), self.p), 0)
        self.assertEqual(open(self.p, "rb").read(), OLD)
        self.assertIn("retrocede", self.records()[-1]["restored"][0]["reason"])

    def test_skipped_dir_change_is_reported_not_silently_kept(self):
        body = 'import os,sys,time; open(os.path.join(os.path.dirname(sys.argv[2]),"options","big.json"),"w").write("{"); time.sleep(60)\n'
        self.assertEqual(self.step("opt", self.script("o.py", body), self.p, cap=1), 124)
        rec = self.records()[-1]
        self.assertEqual([u["file"] for u in rec["unrestorable"]], [os.path.join("options", "big.json")])

    def test_ingest_records_of_cut_step_are_validated_not_dropped(self):
        body = r'''import os, sys, time, json
d = os.path.join(os.path.dirname(sys.argv[2]), "_ingest", "not_before")
os.makedirs(d, exist_ok=True)
json.dump({"not_before": {"boj": {"until": 1}}}, open(os.path.join(d, "daily.json"), "w"))
time.sleep(60)
'''
        self.assertEqual(self.step("cut", self.script("c.py", body), self.p, cap=1), 124)
        self.assertTrue(os.path.exists(os.path.join(self.data, "_ingest", "not_before", "daily.json")))

    def test_ledger_and_watch_surface_restorations(self):
        self.step("us_bills", self.script("w.py", WRITER), self.p, cap=1)
        g8step.ledger("daily", env=self.env, root=self.t)
        led = json.load(open(os.path.join(self.data, "_ingest", "latest", "actions__job_daily.json")))
        self.assertEqual(led["restored"][0]["step"], "us_bills")
        import ingest_watch as W
        from datetime import datetime, timezone
        alerts = {}
        W.check_actions(self.t, datetime.now(timezone.utc), alerts, [])
        self.assertIn("actions:job:daily:restored", alerts)
        self.assertIn("US_BILL_1M.csv", alerts["actions:job:daily:restored"])


if __name__ == "__main__":
    unittest.main()

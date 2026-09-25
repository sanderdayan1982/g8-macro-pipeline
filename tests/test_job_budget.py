"""Hallazgo #3 — plazo global del job y reserva para publicar (Daily).

Estático: el workflow fija G8_JOB_DEADLINE_EPOCH al principio, todo paso de descarga pasa por g8step (fase
fetch) con límite de paso de GitHub ≥ su tope, y los pasos posteriores caben en G8_JOB_RESERVE_S.
Dinámico (tiempo real, escala reducida): varias fuentes lentas o colgadas a la vez; el job descarga lo que
cabe, corta o salta el resto, y el registro, la publicación simulada y el aviso terminan antes del plazo.
"""
import json
import os
import re
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

try:
    import yaml
except ImportError:                                   # validate.yml instala pyyaml
    yaml = None

WF = os.path.join(ROOT, ".github", "workflows", "daily_update.yml")


def _cap(run):
    m = re.search(r"--cap (\d+)", run)
    return int(m.group(1)) if m else None


@unittest.skipIf(yaml is None, "pyyaml no disponible")
class DailyWorkflowStatic(unittest.TestCase):
    def setUp(self):
        with open(WF, encoding="utf-8") as fh:
            self.wf = yaml.safe_load(fh)
        self.job = self.wf["jobs"]["fetch-data"]
        self.steps = self.job["steps"]

    def test_deadline_first_and_consistent_with_job_timeout(self):
        first = self.steps[0]["run"]
        self.assertIn("G8_JOB_DEADLINE_EPOCH", first)
        m = re.search(r"\+ (\d+)\*60 - (\d+)", first)
        self.assertEqual(int(m.group(1)), self.job["timeout-minutes"])          # mismo límite que el job
        self.assertGreaterEqual(int(m.group(2)), 60)                              # margen de arranque
        self.assertIn("G8_STEP_LOG", first)
        self.assertEqual(self.job["env"]["G8_JOB"], "daily")

    def _index(self, prefix):
        return next(i for i, s in enumerate(self.steps) if s.get("name", "").startswith(prefix))

    def test_every_fetch_step_is_bounded(self):
        a, b = self._index("Install dependencies"), self._index("Registro de plazos")
        n = 0
        for s in self.steps[a + 1:b]:
            run = s.get("run", "")
            if not run.lstrip().startswith("python scripts/"):
                continue
            n += 1
            self.assertTrue(run.startswith("python scripts/tools/g8step.py --name "), s["name"])
            self.assertNotIn("--phase post", run, s["name"])
            cap = _cap(run)
            self.assertIsNotNone(cap, s["name"])
            # el límite de GitHub es solo respaldo: tope + gracia de cierre (10 s) + arranque
            self.assertGreaterEqual(s["timeout-minutes"] * 60, cap + g8step.GRACE_KILL_S + 30, s["name"])
        self.assertGreaterEqual(n, 25)                                            # 17 descargadores + ACM/RY/reales

    def test_output_guard_enabled(self):
        self.assertEqual(self.job["env"]["G8_STEP_GUARD"], "data")
        commit = self.steps[self._index("Commit updated data files")]["run"]
        self.assertIn("git add data/", commit)
        # todo paso que escribe en data/ antes del commit pasa por g8step (guarda de salidas)
        c = self._index("Commit updated data files")
        for s in self.steps[self._index("Install dependencies") + 1:c]:
            run = s.get("run", "")
            if run.lstrip().startswith("python ") and "--ledger" not in run and "RUNNER_TEMP" not in run.split("--")[0]:
                self.assertIn("g8step.py --name", run, s["name"])

    def test_setup_steps_have_timeouts(self):
        for name in ("Checkout repository", "Set up Python", "Install dependencies"):
            self.assertIn("timeout-minutes", self.steps[self._index(name)], name)

    def test_post_phase_fits_in_reserve(self):
        reserve = int(self.job["env"]["G8_JOB_RESERVE_S"])
        b = self._index("Registro de plazos")
        total = 15                                                                # registro
        for s in self.steps[b + 1:]:
            run = s.get("run", "")
            if "g8step.py --name" in run:
                self.assertIn("--phase post", run, s["name"])
                total += _cap(run)
            else:
                self.assertIn("timeout-minutes", s, s["name"])
        commit = self.steps[self._index("Commit updated data files")]
        total += 180                                                              # commit con 5 reintentos
        total += 15 + 60                                                          # rojo + aviso de fallo
        self.assertLessEqual(total, reserve)
        self.assertLessEqual(commit["timeout-minutes"] * 60, 180)

    def test_fetch_window_covers_observed_maximum(self):
        # Máximo observado del Daily (13–24 sep 2026): 10,7 min de job completo. La fase de descarga debe
        # disponer al menos de ese tiempo; si no, la reserva estaría quitando frescura.
        window = self.job["timeout-minutes"] * 60 - 90 - int(self.job["env"]["G8_JOB_RESERVE_S"]) - 120
        self.assertGreaterEqual(window, 11 * 60)


SLOW = "import time,sys; time.sleep(float(sys.argv[1]))"
IGNORE_TERM = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
WRITE = ("import os,sys; from g8common import series as S; "
         "S.write_atomic(os.path.join(sys.argv[1], sys.argv[2]), b'DATE,CLOSE\\n20260924,1\\n')")


class DailySimulation(unittest.TestCase):
    """Escala: job de 16 s, reserva 5 s. Mismo código (g8step) que en el workflow."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.data = os.path.join(self.tmp, "data")
        os.makedirs(self.data)
        self.branch = os.path.join(self.tmp, "branch")
        self.t0 = time.time()
        self.deadline = self.t0 + 16
        self.env = dict(os.environ, G8_JOB_DEADLINE_EPOCH="%.3f" % self.deadline, G8_JOB_RESERVE_S="5",
                        G8_STEP_LOG=os.path.join(self.tmp, "steps.jsonl"), G8_JOB="daily",
                        PYTHONPATH=os.path.join(ROOT, "scripts"))
        self._grace = g8step.GRACE_KILL_S
        g8step.GRACE_KILL_S = 1

    def tearDown(self):
        g8step.GRACE_KILL_S = self._grace
        shutil.rmtree(self.tmp)

    def step(self, name, code, *args, cap=6, phase="fetch", min_s=1):
        return g8step.run_step(name, [sys.executable, "-c", code] + list(args), cap, min_s, phase, env=self.env)

    def test_several_slow_sources_still_publish_and_log_before_deadline(self):
        self.assertEqual(self.step("sofr", WRITE, self.data, "SOFR.csv"), 0)          # rápida: publica
        self.assertEqual(self.step("eur_bills", SLOW, "60"), 124)                     # colgada: cortada al tope
        self.assertEqual(self.step("acm_g8", IGNORE_TERM, cap=3), 124)                # ignora SIGTERM → SIGKILL
        self.step("gbp_bills", SLOW, "60")                                            # consume el resto de la fase
        rc = self.step("jpy_bills", WRITE, self.data, "JPY.csv")                      # ya no cabe
        self.assertEqual(rc, 3)
        fetch_end = time.time()
        self.assertLessEqual(fetch_end, self.deadline - 5 + 1.5)                      # la reserva queda intacta
        g8step.ledger("daily", env=self.env, root=self.tmp)
        # fase posterior: S01B, publicación y aviso dentro del plazo del job
        self.assertEqual(self.step("s01b_final", SLOW, "0.2", phase="post", cap=2), 0)
        shutil.copytree(self.data, self.branch)                                       # «commit»
        commit_t = time.time()
        self.assertLess(commit_t, self.deadline)
        self.assertTrue(os.path.exists(os.path.join(self.branch, "SOFR.csv")))
        self.assertFalse(os.path.exists(os.path.join(self.branch, "JPY.csv")))
        led = json.load(open(os.path.join(self.tmp, "data", "_ingest", "latest", "actions__job_daily.json")))
        st = {s["name"]: s["status"] for s in led["steps"]}
        self.assertEqual(st["sofr"], "OK")
        self.assertEqual(st["eur_bills"], "TIMEOUT")
        self.assertEqual(st["acm_g8"], "TIMEOUT")
        self.assertEqual(st["jpy_bills"], "SKIPPED_NO_TIME")
        self.assertEqual({x["name"] for x in led["time_limited"]}, {"eur_bills", "acm_g8", "gbp_bills", "jpy_bills"})

    def test_child_receives_its_own_deadline(self):
        code = ("import os,sys; sys.path.insert(0, %r); from g8common import g8http; "
                "b = g8http.Budget(3600); print(round(b.remaining())); "
                "assert b.remaining() <= 4.5, b.remaining()") % os.path.join(ROOT, "scripts")
        self.assertEqual(self.step("probe", code, cap=4), 0)                          # 3600 s locales → ≤ 4 s

    def test_retry_after_beyond_step_deadline_defers_without_waiting(self):
        code = ("import sys; sys.path.insert(0, %r); from g8common import g8http; "
                "t = lambda *a: (503, {'retry-after': '120'}, b''); "
                "r = g8http.fetch('https://x.invalid/', transport=t); print(r.cls); "
                "sys.exit(0 if r.cls == g8http.DEFERRED else 1)") % os.path.join(ROOT, "scripts")
        t = time.time()
        self.assertEqual(self.step("defer", code, cap=5), 0)
        self.assertLess(time.time() - t, 3)

    def test_no_deadline_env_only_cap(self):
        env = {k: v for k, v in self.env.items() if k not in ("G8_JOB_DEADLINE_EPOCH",)}
        rc = g8step.run_step("x", [sys.executable, "-c", SLOW, "5"], 1, 0.5, "fetch", env=env)
        self.assertEqual(rc, 124)

    def test_cli_passthrough_exit_code(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "tools", "g8step.py"), "--name", "f",
                            "--cap", "5", "--min", "1", "--", sys.executable, "-c", "import sys; sys.exit(7)"], env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 7)


if __name__ == "__main__":
    unittest.main()

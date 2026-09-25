"""Hallazgo #2 — instalador del Mac: preparar y validar aparte, abortar sin tocar lo activo, sustituir el conjunto
con launchd descargado y el cerrojo tomado, restauración automática y reversión exacta. Sin launchctl real:
se sustituye por un doble que registra las llamadas. La compilación sí es real (Python de las pruebas)."""
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "mac"))
sys.path.insert(0, os.path.join(ROOT, "scripts", "tools"))

import build_mac_package  # noqa: E402
import instalar_lote1 as I  # noqa: E402

OLD = {"push_nzd_to_github.py": b"# v1.3 publicador\nprint('v1.3')\n",
       "nzd_local_run.sh": b"#!/bin/zsh\n# v1.3\n"}


def tree_hash(root, names):
    out = {}
    for n in names:
        p = os.path.join(root, n)
        if os.path.isdir(p):
            for dp, _, fs in os.walk(p):
                for f in fs:
                    if "__pycache__" in dp:
                        continue
                    q = os.path.join(dp, f)
                    out[os.path.relpath(q, root)] = hashlib.sha256(open(q, "rb").read()).hexdigest()
        elif os.path.exists(p):
            out[n] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    return out


class Installer(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        for n, b in OLD.items():
            open(os.path.join(self.root, n), "wb").write(b)
        shutil.copy2(os.path.join(ROOT, "mac", "com.g8.nzd-b2.plist"), os.path.join(self.root, "com.g8.nzd-b2.plist"))
        os.makedirs(os.path.join(self.root, "data"))
        self.agents = os.path.join(self.root, "_LaunchAgents")
        os.makedirs(self.agents)
        shutil.copy2(os.path.join(ROOT, "mac", "com.g8.nzd-b2.plist"), os.path.join(self.agents, "com.g8.nzd-b2.plist"))
        self.pkg = build_mac_package.build(os.path.join(self.root, "lote1"))
        self.calls = []
        self.loaded = True
        self.loads = 0
        self.fail_loads = set()
        self.fail_unload = False
        self.busy_seq = []
        self.dry = {"rc": 1, "alerts": [], "families": {"NZ-B2": {"status": "DRY:NOOP"}, "JP-TONA": {"status": "DRY:HELD", "held": [{}]}}}
        self.dry_root_fail = False
        self.token_rc = 0
        self.t = datetime(2026, 9, 25, 12, 0).timestamp()
        self.before = self.snapshot()

    def tearDown(self):
        shutil.rmtree(self.root)

    def snapshot(self):
        s = tree_hash(self.root, I.FILES + I.DIRS)
        s["agent"] = tree_hash(self.agents, ["com.g8.nzd-b2.plist"])
        return s

    # dobles ──────────────────────────────────────────────────────────────────
    def launchctl(self, *a):
        self.calls.append(a[0])
        if a[0] == "list":
            return 0 if self.loaded else 113
        if a[0] == "unload":
            if self.fail_unload:
                return 5
            self.loaded = False
        if a[0] == "load":
            self.loads += 1
            if self.loads in self.fail_loads:                 # nº de carga (1ª, 2ª…) que falla
                return 5
            self.loaded = True
        return 0

    @property
    def actions(self):
        return [c for c in self.calls if c != "list"]

    def busy(self):
        return self.busy_seq.pop(0) if self.busy_seq else []

    def runner(self, cmd, cwd, timeout=600):
        if "py_compile" in " ".join(cmd):
            r = subprocess.run([sys.executable] + cmd[1:], capture_output=True, text=True)
            return r.returncode, r.stderr
        if "push_nzd_to_github.py" in cmd:
            if self.dry_root_fail and os.path.realpath(cwd) == os.path.realpath(self.root):
                return 2, "Traceback: fallo simulado"
            os.makedirs(os.path.join(cwd, "state"), exist_ok=True)
            json.dump({"alerts": self.dry["alerts"], "families": self.dry["families"]},
                      open(os.path.join(cwd, "state", "last_dry_run.json"), "w"))
            self.lock_seen = os.path.isdir(os.path.join(self.root, "state", "run.lock"))
            return self.dry["rc"], ""
        if "check_credentials.py" in cmd:
            return self.token_rc, ""
        raise AssertionError(cmd)

    def env(self, answer="s"):
        return I.Env(self.root, python=sys.executable, agents=self.agents, launchctl=self.launchctl,
                     now=lambda: self.t, sleep=lambda s: setattr(self, "t", self.t + s), busy=self.busy,
                     runner=self.runner, ask=lambda q: answer, out=lambda *a: None)

    def assert_untouched(self):
        self.assertEqual(self.snapshot(), self.before)
        self.assertFalse([d for d in os.listdir(self.root) if d.startswith(".g8_staging_")])
        self.assertFalse(os.path.isdir(os.path.join(self.root, "state", "run.lock")))

    # casos ───────────────────────────────────────────────────────────────────
    def test_success_replaces_whole_set(self):
        self.assertEqual(I.install(self.env(), self.pkg), "INSTALLED")
        for f in ("push_nzd_to_github.py", "check_credentials.py", "nzd_local_run.sh"):
            self.assertEqual(open(os.path.join(self.root, f), "rb").read(), open(os.path.join(self.pkg, f), "rb").read())
        self.assertTrue(os.path.isfile(os.path.join(self.root, "g8common", "series.py")))
        pl = plistlib.load(open(os.path.join(self.agents, "com.g8.nzd-b2.plist"), "rb"))
        self.assertEqual(pl["ProgramArguments"][1], os.path.join(self.root, "nzd_local_run.sh"))   # ruta real
        self.assertEqual(self.actions, ["unload", "load"])
        self.assertTrue(self.loaded)
        self.assertTrue(self.lock_seen)                                    # verificación en su sitio con cerrojo
        self.assertFalse(os.path.isdir(os.path.join(self.root, "state", "run.lock")))
        backup = [d for d in os.listdir(self.root) if d.startswith("backup_lote1_")]
        self.assertEqual(len(backup), 1)
        st = json.load(open(os.path.join(self.root, backup[0], "SET.json")))["existed"]
        self.assertEqual((st["check_credentials.py"], st["g8common/"], st["push_nzd_to_github.py"]), (False, False, True))

    def test_blocking_dry_run_aborts_without_changes(self):
        for alerts, fams in ((["token:invalid"], self.dry["families"]),
                             ([], {"NZ-B2": {"status": "DRY:INVALID", "detail": "faltan"}}),
                             ([], {})):
            self.dry.update(alerts=alerts, families=fams)
            with self.assertRaises(I.Abort):
                I.install(self.env(), self.pkg)
            self.assert_untouched()
        self.assertEqual(self.calls, [])                                   # launchd ni se consulta

    def test_unexpected_exit_code_and_token_failure_abort(self):
        self.dry["rc"] = 2
        with self.assertRaises(I.Abort):
            I.install(self.env(), self.pkg)
        self.dry["rc"] = 0
        self.token_rc = 1
        with self.assertRaises(I.Abort):
            I.install(self.env(), self.pkg)
        self.assert_untouched()

    def test_compile_error_aborts(self):
        p = os.path.join(self.pkg, "g8common", "series.py")
        open(p, "a").write("\ndef roto(:\n")
        lines = [ln for ln in open(os.path.join(self.pkg, "MANIFEST.sha256")) if not ln.endswith("g8common/series.py\n")]
        lines.append("%s  g8common/series.py\n" % hashlib.sha256(open(p, "rb").read()).hexdigest())
        open(os.path.join(self.pkg, "MANIFEST.sha256"), "w").writelines(lines)
        with self.assertRaises(I.Abort) as cm:
            I.install(self.env(), self.pkg)
        self.assertIn("no compila", str(cm.exception))
        self.assert_untouched()

    def test_tampered_package_aborts(self):
        open(os.path.join(self.pkg, "push_nzd_to_github.py"), "a").write("# alterado\n")
        with self.assertRaises(I.Abort) as cm:
            I.install(self.env(), self.pkg)
        self.assertIn("alterado", str(cm.exception))
        self.assert_untouched()

    def test_answer_no_changes_nothing(self):
        self.assertEqual(I.install(self.env("n"), self.pkg), "CANCELLED")
        self.assert_untouched()
        self.assertEqual(self.calls, [])

    def test_schedule_window_refused(self):
        self.t = datetime(2026, 9, 25, 7, 55).timestamp()
        with self.assertRaises(I.Abort) as cm:
            I.install(self.env(), self.pkg)
        self.assertIn("08:00", str(cm.exception))
        self.assert_untouched()
        self.assertEqual(self.calls, [])

    def test_waits_for_running_job_then_installs(self):
        self.busy_seq = [["nzd_local_run.sh"], ["push_nzd_to_github.py"], []]
        self.assertEqual(I.install(self.env(), self.pkg), "INSTALLED")

    def test_busy_too_long_aborts_before_unload(self):
        self.busy_seq = [["nzd_local_run.sh"]] * 100
        with self.assertRaises(I.Abort):
            I.install(self.env(), self.pkg)
        self.assert_untouched()
        self.assertNotIn("unload", self.calls)

    def test_race_after_unload_reloads_old_schedule(self):
        self.busy_seq = [[]] + [["fetch_tona_mac.py"]] * 100
        with self.assertRaises(I.Abort):
            I.install(self.env(), self.pkg)
        self.assert_untouched()
        self.assertEqual(self.actions, ["unload", "load"])
        self.assertTrue(self.loaded)

    def test_failure_after_swap_restores_previous_set(self):
        self.dry_root_fail = True
        with self.assertRaises(I.Abort) as cm:
            I.install(self.env(), self.pkg)
        self.assertIn("se restauró y verificó", str(cm.exception))
        self.assertEqual(self.snapshot(), self.before)                     # incluido: sin check_credentials ni g8common
        self.assertEqual(self.actions, ["unload", "load"])                 # vuelve la programación anterior
        self.assertTrue(self.loaded)
        self.assertFalse(os.path.isdir(os.path.join(self.root, "state", "run.lock")))

    def test_revert_restores_exact_previous_set(self):
        I.install(self.env(), self.pkg)
        self.assertNotEqual(self.snapshot(), self.before)
        self.calls = []
        self.assertEqual(I.revert(self.env()), "REVERTED")
        self.assertEqual(self.snapshot(), self.before)
        self.assertEqual(self.actions, ["unload", "load"])
        self.assertTrue(self.loaded)

    # ── R2-2: la activación final de launchd forma parte de la transacción ─────────────────────────
    def test_r2_2_final_load_fails_restores_previous_set_and_schedule(self):
        self.fail_loads = {1}                                              # falla la carga del plist NUEVO
        with self.assertRaises(I.Abort) as cm:
            I.install(self.env(), self.pkg)
        self.assertNotIsInstance(cm.exception, I.RecoveryFailed)
        self.assertIn("activación de launchd fallida", str(cm.exception))
        self.assertEqual(self.snapshot(), self.before)                     # hashes iniciales
        self.assertTrue(self.loaded)                                       # programación anterior cargada
        self.assertEqual(self.actions, ["unload", "load", "load"])
        self.assertFalse(os.path.isdir(os.path.join(self.root, "state", "run.lock")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "state", "install_lote1.json")))

    def test_r2_2_recovery_failure_is_reported_never_success(self):
        self.fail_loads = {1, 2}                                           # también falla recargar el anterior
        with self.assertRaises(I.RecoveryFailed) as cm:
            I.install(self.env(), self.pkg)
        self.assertIn("RECUPERACIÓN FALLIDA", str(cm.exception))
        self.assertIn("backup_lote1_", str(cm.exception))
        self.assertEqual(self.snapshot(), self.before)                     # los ficheros sí se restauraron
        self.assertFalse(self.loaded)

    def test_r2_2_first_load_ok_is_verified_with_list(self):
        orig = self.launchctl

        def lying(*a):                                                     # load devuelve 0 pero no queda cargado
            if a[0] == "load" and self.loads == 0:
                self.calls.append("load")
                self.loads += 1
                return 0
            return orig(*a)
        self.launchctl = lying
        with self.assertRaises(I.Abort):
            I.install(self.env(), self.pkg)
        self.assertEqual(self.snapshot(), self.before)
        self.assertTrue(self.loaded)

    def test_r2_2_unload_failure_changes_nothing(self):
        self.fail_unload = True
        with self.assertRaises(I.Abort) as cm:
            I.install(self.env(), self.pkg)
        self.assertIn("no se pudo descargar", str(cm.exception))
        self.assert_untouched()
        self.assertTrue(self.loaded)

    def test_r2_2_backup_failure_changes_nothing(self):
        orig = I.snapshot

        def broken(env, dest):
            os.makedirs(dest)
            raise OSError(28, "No space left on device")
        I.snapshot = broken
        try:
            with self.assertRaises(I.Abort) as cm:
                I.install(self.env(), self.pkg)
        finally:
            I.snapshot = orig
        self.assertIn("copia de seguridad", str(cm.exception))
        self.assert_untouched()
        self.assertFalse([d for d in os.listdir(self.root) if d.startswith("backup_lote1_")])
        self.assertTrue(self.loaded)
        self.assertEqual(self.actions, ["unload", "load"])

    def test_r2_2_revert_with_failed_load_goes_back_to_installed_set(self):
        I.install(self.env(), self.pkg)
        installed = self.snapshot()
        self.loads, self.fail_loads, self.calls = 0, {1}, []
        with self.assertRaises(I.Abort):
            I.revert(self.env())
        self.assertEqual(self.snapshot(), installed)
        self.assertTrue(self.loaded)

    def test_reviewer_repro_load_returns_5(self):
        def failing_load(*args):                                           # doble exacto de repro_adicional.py
            if args[0] == "load":
                self.calls.append("load")
                return 5
            return Installer.launchctl(self, *args)
        self.launchctl = failing_load
        with self.assertRaises(I.Abort) as cm:
            I.install(self.env(), self.pkg)
        # con un launchctl que NUNCA carga, la recuperación no puede recargar la programación: se declara
        self.assertIsInstance(cm.exception, I.RecoveryFailed)
        self.assertEqual(self.snapshot(), self.before)                     # el código anterior sí está restaurado

    def test_run_script_respects_install_lock(self):
        shutil.copy2(os.path.join(ROOT, "mac", "nzd_local_run.sh"), os.path.join(self.root, "run.sh"))
        os.makedirs(os.path.join(self.root, "state", "run.lock"))
        r = subprocess.run(["bash", os.path.join(self.root, "run.sh")], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0)
        self.assertIn("omitida", open(os.path.join(self.root, "logs", "ALERTAS.log")).read())
        self.assertFalse([f for f in os.listdir(os.path.join(self.root, "logs")) if f.startswith("nzd_")])
        self.assertTrue(os.path.isdir(os.path.join(self.root, "state", "run.lock")))     # no retira un cerrojo ajeno


if __name__ == "__main__":
    unittest.main()

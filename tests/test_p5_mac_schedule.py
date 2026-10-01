"""Acta P-5 · job del Mac: una ejecución diaria a las 19:30 (Bata) + RunAtLoad + guarda «ya hecho».

S1 plist: 19:30 + reintento 21:00, RunAtLoad; el instalador lo acepta (render_plist)
S2 instalador: no activa a ±10 min de las 19:30, 21:00 ni de las antiguas 08:00/17:00
G1 turno vigente: antes de las 19:30 → el de ayer; desde las 19:30 → el de hoy
G2 turno ya hecho → no ejecuta nada y lo deja en el log
G3 todo a 0 → marca el turno; G4 una descarga falla → no lo marca (la siguiente ocasión reintenta)
G5 G8_FORCE=1 ignora la guarda
G6 reintento de las 21:00: no hace nada si las 19:30 salieron bien; repite si fallaron
"""
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "mac"))
import instalar_lote1 as I  # noqa: E402

BATA = ZoneInfo("Africa/Malabo")
PY = "/usr/bin/python3"


def epoch(y, m, d, h, mi):
    return int(datetime(y, m, d, h, mi, tzinfo=BATA).timestamp())


class Plist(unittest.TestCase):
    def test_S1_single_1930_and_run_at_load(self):
        with open(os.path.join(ROOT, "mac", "com.g8.nzd-b2.plist"), "rb") as fh:
            pl = plistlib.load(fh)
        self.assertEqual([(d["Hour"], d["Minute"]) for d in pl["StartCalendarInterval"]], [(19, 30), (21, 0)])
        self.assertTrue(pl["RunAtLoad"])
        self.assertEqual(I.SCHEDULE, [(19, 30), (21, 0)])
        out = plistlib.loads(I.render_plist(os.path.join(ROOT, "mac", "com.g8.nzd-b2.plist"), "/tmp/x"))
        self.assertEqual(out["ProgramArguments"], ["/bin/zsh", "/tmp/x/nzd_local_run.sh"])

    def test_S2_window_covers_new_and_legacy_times(self):
        class E:
            def __init__(self, t):
                self.now = lambda: t
        for (h, m), want in (((19, 25), "19:30"), ((20, 55), "21:00"), ((7, 55), "08:00"), ((17, 5), "17:00"), ((12, 0), None)):
            self.assertEqual(I.in_window(E(datetime(2026, 10, 1, h, m).timestamp())), want, (h, m))


@unittest.skipUnless(os.path.exists(PY) and shutil.which("bash"), "sin /usr/bin/python3 o bash")
class Guard(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        shutil.copy2(os.path.join(ROOT, "mac", "nzd_local_run.sh"), os.path.join(self.root, "run.sh"))
        self.codes(0, 0, 0, 0)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def codes(self, nzd, chf, tona, push):
        for name, rc in (("fetch_nzd_b2.py", nzd), ("fetch_chf_snb.py", chf), ("fetch_tona_mac.py", tona),
                         ("push_nzd_to_github.py", push)):
            with open(os.path.join(self.root, name), "w") as fh:
                fh.write("import os, sys\nopen(%r, 'a').write(%r + '\\n')\nsys.exit(%d)\n"
                         % (os.path.join(self.root, "ran.txt"), name, rc))

    def run_at(self, now, force=False):
        env = dict(os.environ, TZ="Africa/Malabo", G8_NOW=str(now))
        env.pop("G8_FORCE", None)
        if force:
            env["G8_FORCE"] = "1"
        r = subprocess.run(["bash", os.path.join(self.root, "run.sh")], capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)

    def ran(self):
        p = os.path.join(self.root, "ran.txt")
        return open(p).read().split() if os.path.exists(p) else []

    def mark(self):
        p = os.path.join(self.root, "state", "last_ok_slot")
        return open(p).read().strip() if os.path.exists(p) else None

    def logs(self):
        d = os.path.join(self.root, "logs")
        return "".join(open(os.path.join(d, f)).read() for f in os.listdir(d) if f.startswith("nzd_"))

    def test_G1_G3_slot_and_success_mark(self):
        self.run_at(epoch(2026, 10, 1, 10, 0))                       # RunAtLoad por la mañana → turno de ayer
        self.assertEqual(self.mark(), str(epoch(2026, 9, 30, 19, 30)))
        self.assertEqual(len(self.ran()), 4)
        self.run_at(epoch(2026, 10, 1, 19, 30))                      # 19:30 → turno de hoy: se ejecuta
        self.assertEqual(self.mark(), str(epoch(2026, 10, 1, 19, 30)))
        self.assertEqual(len(self.ran()), 8)

    def test_G2_already_done_skips(self):
        self.run_at(epoch(2026, 10, 1, 19, 30))
        self.run_at(epoch(2026, 10, 1, 21, 15))                      # p. ej. reinicio: mismo turno, ya hecho
        self.run_at(epoch(2026, 10, 2, 9, 0))
        self.assertEqual(len(self.ran()), 4)
        self.assertEqual(self.logs().count("ya hecho"), 2)

    def test_G4_failure_not_marked_then_retried(self):
        self.codes(0, 1, 0, 0)
        self.run_at(epoch(2026, 10, 1, 19, 30))
        self.assertIsNone(self.mark())
        self.codes(0, 0, 0, 0)
        self.run_at(epoch(2026, 10, 1, 20, 40))                      # al despertar / reiniciar: reintenta
        self.assertEqual(self.mark(), str(epoch(2026, 10, 1, 19, 30)))
        self.assertEqual(len(self.ran()), 8)

    def test_G5_force(self):
        self.run_at(epoch(2026, 10, 1, 19, 30))
        self.run_at(epoch(2026, 10, 1, 19, 45), force=True)
        self.assertEqual(len(self.ran()), 8)

    def test_G6_retry_at_2100(self):
        self.run_at(epoch(2026, 10, 1, 19, 30))                      # bien a las 19:30
        self.run_at(epoch(2026, 10, 1, 21, 0))                       # reintento: no hace nada
        self.assertEqual(len(self.ran()), 4)
        self.codes(0, 0, 1, 0)
        self.run_at(epoch(2026, 10, 2, 19, 30))                      # falla TONA
        self.assertEqual(self.mark(), str(epoch(2026, 10, 1, 19, 30)))
        self.codes(0, 0, 0, 0)
        self.run_at(epoch(2026, 10, 2, 21, 0))                       # el reintento lo completa
        self.assertEqual(self.mark(), str(epoch(2026, 10, 2, 19, 30)))
        self.assertEqual(len(self.ran()), 12)


if __name__ == "__main__":
    unittest.main()

"""mac/dispatch_drift_check.py — aviso de deriva entre el horario del Mac y mac/ del repo (solo lectura).

DR1 horario idéntico → 0 · DR2 turno solo en el repo / solo en el Mac / cron distinto → 1 con el id
DR3 excepción declarada (metals_shadow) no cuenta · DR4 campo de cabecera distinto: se nombra el campo, nunca el valor
DR5 repo ilegible (sin red) → 3, sin tocar ALERTAS.log · DR6 --alert escribe ALERTAS.log solo con deriva
DR7 la lista de excepciones del repo es válida · DR8 Python 3.9 · el script nunca escribe un horario
Sin red: --repo-dir o transporte falso.
"""
import ast
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "mac"))
import dispatch_drift_check as C  # noqa: E402

SCHEDULES = ("dispatch_schedule.json", "dispatch_schedule_mesa.json")
SECRET_PATH = "/ruta/que/no/debe/salir"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.live = os.path.join(self.tmp, "live")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.live)
        os.makedirs(os.path.join(self.repo, "mac"))
        for n in SCHEDULES + (C.EXCEPTIONS,):
            shutil.copy(os.path.join(ROOT, "mac", n), os.path.join(self.repo, "mac", n))
        for n in SCHEDULES:
            shutil.copy(os.path.join(ROOT, "mac", n), os.path.join(self.live, n))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def edit(self, name, fn):
        p = os.path.join(self.live, name)
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        fn(d)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(d, fh)

    def run_check(self, *extra, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = C.main(["--root", self.live] + list(extra), **kw)
        return rc, buf.getvalue()

    def offline(self, *extra):
        return self.run_check("--repo-dir", self.repo, *extra)

    def alerts(self):
        p = os.path.join(self.live, "logs", "ALERTAS.log")
        return open(p, encoding="utf-8").read() if os.path.exists(p) else ""


class Drift(Base):
    def test_DR1_identical(self):
        rc, out = self.offline()
        self.assertEqual(rc, 0, out)
        self.assertIn("sin deriva", out)

    def test_DR2_only_repo_only_mac_and_cron(self):
        self.edit("dispatch_schedule.json", lambda d: d.__setitem__(
            "slots", [s for s in d["slots"] if s["id"] != "votes_first"]))
        rc, out = self.offline()
        self.assertEqual(rc, 1)
        self.assertIn("solo_repo · votes_first", out)

        def extra_and_cron(d):
            d["slots"].append({"id": "solo_local", "workflow": "x.yml", "cron": "0 1 * * 1"})
            for s in d["slots"]:
                if s["id"] == "daily":
                    s["cron"] = "31 21 * * 1-5"
        self.edit("dispatch_schedule.json", extra_and_cron)
        rc, out = self.offline()
        self.assertEqual(rc, 1)
        self.assertIn("solo_mac · solo_local", out)
        self.assertIn("difiere · daily · cron", out)
        self.assertNotIn("31 21", out)

    def test_DR3_declared_exception(self):
        self.edit("dispatch_schedule.json", lambda d: d.__setitem__(
            "slots", [s for s in d["slots"] if s["id"] != "metals_shadow"]))
        rc, out = self.offline()
        self.assertEqual(rc, 0, out)
        self.assertIn("excepción declarada: dispatch_schedule.json solo_repo metals_shadow", out)

    def test_DR4_header_field_named_never_valued(self):
        self.edit("dispatch_schedule_mesa.json", lambda d: d.__setitem__("token_path", SECRET_PATH))
        rc, out = self.offline("--alert")
        self.assertEqual(rc, 1)
        self.assertIn("campo:token_path", out)
        self.assertNotIn(SECRET_PATH, out)
        self.assertNotIn(SECRET_PATH, self.alerts())

    def test_DR5_repo_unreadable_is_not_drift(self):
        calls = []

        def down(method, url, headers, body, ct, rt, tt):
            calls.append(url)
            return 503, {}, b""
        rc, out = self.run_check("--alert", transport=down, sleep=lambda _s: None)
        self.assertEqual(rc, 3)
        self.assertIn("no se compara", out)
        self.assertEqual(self.alerts(), "")
        self.assertTrue(calls and all(u.startswith("https://api.github.com/repos/") for u in calls))
        self.assertTrue(all("Authorization" not in u for u in calls))

    def test_DR5b_network_path_reads_main(self):
        seen = []

        def ok(method, url, headers, body, ct, rt, tt):
            seen.append((method, url, headers.get("Authorization")))
            name = url.split("/mac/", 1)[1].split("?", 1)[0]
            with open(os.path.join(self.repo, "mac", name), "rb") as fh:
                return 200, {}, fh.read()
        rc, out = self.run_check(transport=ok)
        self.assertEqual(rc, 0, out)
        self.assertTrue(all(m == "GET" and "ref=main" in u and a is None for m, u, a in seen))

    def test_DR6_alert_only_on_drift(self):
        self.offline("--alert")
        self.assertEqual(self.alerts(), "")
        self.edit("dispatch_schedule.json", lambda d: d.__setitem__(
            "slots", [s for s in d["slots"] if s["id"] != "votes_final"]))
        rc, out = self.offline("--alert")
        self.assertEqual(rc, 1)
        self.assertIn("votes_final", self.alerts())

    def test_DR8_never_writes_a_schedule(self):
        before = {n: open(os.path.join(self.live, n), "rb").read() for n in SCHEDULES}
        self.edit("dispatch_schedule.json", lambda d: d["slots"].pop())
        before["dispatch_schedule.json"] = open(os.path.join(self.live, "dispatch_schedule.json"), "rb").read()
        self.offline("--alert")
        for n in SCHEDULES:
            self.assertEqual(open(os.path.join(self.live, n), "rb").read(), before[n], n)


class Static(unittest.TestCase):
    def test_DR7_exceptions_file_is_valid(self):
        with open(os.path.join(ROOT, "mac", C.EXCEPTIONS), encoding="utf-8") as fh:
            doc = json.load(fh)
        for e in doc["exceptions"]:
            self.assertIn(e["kind"], C.KINDS)
            self.assertTrue(e.get("motivo"))
            self.assertTrue(os.path.exists(os.path.join(ROOT, "mac", e["file"])), e["file"])
            if e["kind"] == "solo_repo":
                with open(os.path.join(ROOT, "mac", e["file"]), encoding="utf-8") as fh:
                    ids = {s["id"] for s in json.load(fh)["slots"]}
                self.assertIn(e["id"], ids)

    def test_DR8_python39(self):
        with open(os.path.join(ROOT, "mac", "dispatch_drift_check.py"), encoding="utf-8") as fh:
            ast.parse(fh.read(), feature_version=(3, 9))


if __name__ == "__main__":
    unittest.main()

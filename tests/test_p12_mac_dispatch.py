"""Acta P-12 · mac/dispatch_workflows.py — el Mac lanza los workflows de datos a su hora nominal (GitHub ejecuta los cron
con 4–8 h de retraso). Sin red: transporte falso.

D1 turno vencido dentro de la ventana → se lanza; fuera de la ventana → no (manda el cron de GitHub)
D2 día de la semana del cron (1-5 laborables, 6 sábado) en UTC
D3 petición: POST …/actions/workflows/<fichero>/dispatches con ref main y los inputs del turno; una sola vez por turno
D4 respuesta fallida → no se marca hecho (se reintenta); 401/403 → aviso en logs/ALERTAS.log
D5 sin token → no llama a GitHub y avisa; nunca se escribe el token en logs
D6 horario = crons de los workflows (sin desvíos); grupo intradía = el que el workflow asigna a ese cron;
   todo workflow con cron está en el horario o excluido con motivo
D7 plantilla launchd y compatibilidad con el Python 3.9 del Mac
M1–M6 horario adicional de mesa-macro-fx (acta NETLIFY_CREDITS): su repo, su token, sin bloquear a g8, crons y lanes
"""
import ast
import json
import os
import plistlib
import re
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "mac"))
import dispatch_workflows as D  # noqa: E402

TOKEN = "github_pat_SECRET_DISPATCH_do_not_print"
EXCLUDED = {"ingest_watch.yml", "maintenance_agent.yml", "cme_options.yml"}


def utc(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)


class Fake(object):
    def __init__(self, status=204, headers=None):
        self.status, self.headers, self.calls = status, headers or {}, []

    def __call__(self, method, url, headers, body, ct, rt, tt):
        self.calls.append((method, url, json.loads(body.decode()) if body else None, headers.get("Authorization")))
        return self.status, self.headers, b""


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        shutil.copy(os.path.join(ROOT, "mac", "dispatch_schedule.json"), self.root)
        self.tok = os.path.join(self.root, "token")
        with open(self.tok, "w") as fh:
            fh.write(TOKEN + "\n")
        os.environ.pop("G8_DISPATCH_TOKEN", None)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def run_at(self, when, fake=None, argv=(), token_path=None):
        fake = fake if fake is not None else Fake()
        rc = D.main(list(argv), now=utc(when), transport=fake, root=self.root, token_path=token_path or self.tok)
        return rc, fake

    def logs(self):
        out = ""
        d = os.path.join(self.root, "logs")
        for n in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            with open(os.path.join(d, n), encoding="utf-8") as fh:
                out += fh.read()
        return out

    def state(self):
        return D.read_json(os.path.join(self.root, "state", "dispatch_state.json"), {})


class Schedule(Base):
    def test_D1_window(self):
        rc, f = self.run_at("2026-10-06T13:20")                       # martes, 3 min tras las 13:17
        self.assertEqual(rc, 0)
        self.assertEqual([c[1].rsplit("/", 2)[-2] for c in f.calls], ["intraday_fetch.yml"])
        rc, f = self.run_at("2026-10-06T16:30", Fake())               # 13:17 + 3 h 13 min → fuera; 15:30 y 15:47 sí
        ids = sorted(c[1].rsplit("/", 2)[-2] for c in f.calls)
        self.assertEqual(ids, ["intraday_fetch.yml", "usd_factor.yml"])
        self.assertEqual(f.calls[0][2].get("inputs"), {"group": "US"})

    def test_D2_weekday(self):
        rc, f = self.run_at("2026-10-10T13:20")                       # sábado: el intradía no corre
        self.assertEqual(f.calls, [])
        rc, f = self.run_at("2026-10-10T07:05", Fake())               # sábado 07:00 → metales
        self.assertEqual([c[1].rsplit("/", 2)[-2] for c in f.calls], ["metals_update.yml"])
        rc, f = self.run_at("2026-10-05T00:30", Fake())               # lunes 00:23 UTC → Asia
        self.assertEqual(f.calls[0][2], {"ref": "main", "inputs": {"group": "ASIA"}})


class Dispatch(Base):
    def test_D3_request_and_once_per_slot(self):
        rc, f = self.run_at("2026-10-06T21:31")
        self.assertEqual(rc, 0)
        method, url, body, auth = f.calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/repos/sanderdayan1982/g8-macro-pipeline/actions/workflows/daily_update.yml/dispatches"), url)
        self.assertEqual(body, {"ref": "main"})
        self.assertEqual(auth, "Bearer " + TOKEN)
        self.assertEqual(self.state()["done"]["daily"], "2026-10-06T21:30Z")
        rc, f2 = self.run_at("2026-10-06T21:36", Fake())               # siguiente pasada: ya hecho
        self.assertEqual(f2.calls, [])
        rc, f3 = self.run_at("2026-10-07T21:31", Fake())               # día siguiente: turno nuevo
        self.assertEqual(len(f3.calls), 1)

    def test_D4_failure_is_retried_and_auth_alerts(self):
        rc, f = self.run_at("2026-10-06T21:31", Fake(status=503))
        self.assertEqual(rc, 1)
        self.assertNotIn("daily", self.state().get("done", {}))
        rc, f = self.run_at("2026-10-06T21:36", Fake(status=401))
        self.assertEqual(rc, 1)
        self.assertEqual(len(f.calls), 1)                              # reintentado
        self.assertIn("revisa el token", self.logs())
        with open(os.path.join(self.root, "logs", "ALERTAS.log"), encoding="utf-8") as fh:
            self.assertIn("Mac lanzador", fh.read())
        rc, f = self.run_at("2026-10-06T21:41", Fake())
        self.assertEqual((rc, self.state()["done"]["daily"]), (0, "2026-10-06T21:30Z"))

    def test_D5_no_token(self):
        rc, f = self.run_at("2026-10-06T21:31", token_path=os.path.join(self.root, "missing"))
        self.assertEqual(rc, 1)
        self.assertEqual(f.calls, [])
        self.assertIn("no hay token", self.logs())
        self.run_at("2026-10-06T21:31", Fake(status=401))
        self.assertNotIn(TOKEN, self.logs())

    def test_check_and_expiry(self):
        hdr = {"github-authentication-token-expiration": "2026-10-10 12:00:00 UTC"}
        rc, f = self.run_at("2026-10-06T12:00", Fake(status=200, headers=hdr), argv=["--check"])
        self.assertEqual(rc, 2)                                        # caduca en 4 días
        self.assertEqual(f.calls[0][0], "GET")
        rc, f = self.run_at("2026-10-06T21:31", Fake(headers=hdr))
        self.assertIn("caduca", self.logs())

    def test_dry_run_never_calls(self):
        rc, f = self.run_at("2026-10-06T21:31", argv=["--dry-run"])
        self.assertEqual((rc, f.calls), (0, []))
        self.assertEqual(self.state(), {})


class Consistency(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "mac", "dispatch_schedule.json"), encoding="utf-8") as fh:
            self.sched = json.load(fh)
        self.wdir = os.path.join(ROOT, ".github", "workflows")

    def crons(self, name):
        with open(os.path.join(self.wdir, name), encoding="utf-8") as fh:
            return set(re.findall(r"^\s*- cron:\s*'([^']+)'", fh.read(), re.M))

    def test_D6_schedule_matches_workflow_crons(self):
        by_wf = {}
        for s in self.sched["slots"]:
            by_wf.setdefault(s["workflow"], set()).add(s["cron"])
            D.parse_cron(s["cron"])
        for wf, crons in by_wf.items():
            self.assertEqual(crons, self.crons(wf), wf)
        scheduled = {n for n in os.listdir(self.wdir) if n.endswith(".yml") and self.crons(n)}
        self.assertEqual(scheduled - set(by_wf) - EXCLUDED, set(), "workflow con cron sin turno ni exclusión")
        self.assertEqual(set(by_wf) & EXCLUDED, set())
        for n in EXCLUDED:
            self.assertIn(n.replace(".yml", ""), self.sched["_doc"])

    def test_D6_intraday_group_matches_workflow(self):
        with open(os.path.join(self.wdir, "intraday_fetch.yml"), encoding="utf-8") as fh:
            y = fh.read()
        mapping = {}
        for left, group in re.findall(r"^\s*(.+)\) group=(\w+);;", y, re.M):     # case "$SCHEDULE" in … esac
            for cron in re.findall(r"'([^']+)'", left):
                mapping[cron] = group
        for s in self.sched["slots"]:
            if s["workflow"] == "intraday_fetch.yml":
                self.assertEqual(s["inputs"]["group"], mapping[s["cron"]], s["id"])

    def test_D7_plist_and_python39(self):
        with open(os.path.join(ROOT, "mac", "com.g8.dispatch.plist"), "rb") as fh:
            pl = plistlib.load(fh)
        self.assertEqual(pl["Label"], "com.g8.dispatch")
        self.assertEqual(pl["StartInterval"], 300)
        self.assertEqual(pl["ProgramArguments"][0], "/usr/bin/python3")
        self.assertTrue(pl["ProgramArguments"][1].endswith("/dispatch_workflows.py"))
        with open(os.path.join(ROOT, "mac", "dispatch_workflows.py"), encoding="utf-8") as fh:
            ast.parse(fh.read(), feature_version=(3, 9))


MESA_TOKEN = "github_pat_SECRET_MESA_do_not_print"


class MesaSchedule(Base):
    """Acta NETLIFY_CREDITS · horario adicional dispatch_schedule_mesa.json (otro repo, su propio token)."""
    def setUp(self):
        Base.setUp(self)
        with open(os.path.join(ROOT, "mac", "dispatch_schedule_mesa.json"), encoding="utf-8") as fh:
            sched = json.load(fh)
        self.mesa_tok = os.path.join(self.root, "token_mesa")
        sched["token_path"] = self.mesa_tok
        with open(os.path.join(self.root, "dispatch_schedule_mesa.json"), "w", encoding="utf-8") as fh:
            json.dump(sched, fh)
        with open(self.mesa_tok, "w") as fh:
            fh.write(MESA_TOKEN + "\n")

    def test_M1_mesa_slot_uses_its_repo_token_and_lane(self):
        rc, f = self.run_at("2026-10-06T20:22")                       # martes 20:20 → USD daily de mesa
        self.assertEqual(rc, 0)
        mesa = [c for c in f.calls if "/mesa-macro-fx/" in c[1]]
        self.assertEqual(len(mesa), 1)
        method, url, body, auth = mesa[0]
        self.assertTrue(url.endswith("/repos/sanderdayan1982/mesa-macro-fx/actions/workflows/refresh-usd.yml/dispatches"), url)
        self.assertEqual(body, {"ref": "main", "inputs": {"lane": "daily", "backfill": "false"}})
        self.assertEqual(auth, "Bearer " + MESA_TOKEN)
        self.assertEqual(self.state()["done"]["mesa_usd_daily_1"], "2026-10-06T20:20Z")
        rc, f2 = self.run_at("2026-10-06T20:27", Fake())
        self.assertEqual([c for c in f2.calls if "/mesa-macro-fx/" in c[1]], [])

    def test_M2_missing_mesa_token_does_not_block_g8(self):
        os.remove(self.mesa_tok)
        rc, f = self.run_at("2026-10-06T21:31")                       # g8 daily 21:30 + mesa usd 20:20/21:20 vencidos
        self.assertEqual(rc, 1)
        self.assertTrue(all("/g8-macro-pipeline/" in c[1] for c in f.calls) and f.calls)
        self.assertEqual(self.state()["done"]["daily"], "2026-10-06T21:30Z")
        self.assertIn("no hay token (%s)" % self.mesa_tok, self.logs())

    def test_M3_env_token_never_used_for_mesa(self):
        os.remove(self.mesa_tok)
        os.environ["G8_DISPATCH_TOKEN"] = TOKEN
        try:
            rc, f = self.run_at("2026-10-06T20:22")
        finally:
            os.environ.pop("G8_DISPATCH_TOKEN", None)
        self.assertEqual([c for c in f.calls if "/mesa-macro-fx/" in c[1]], [])

    def test_M4_extra_schedule_without_token_path_is_ignored(self):
        p = os.path.join(self.root, "dispatch_schedule_mesa.json")
        sched = D.read_json(p, {})
        sched.pop("token_path")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(sched, fh)
        rc, f = self.run_at("2026-10-06T20:22")
        self.assertEqual([c for c in f.calls if "/mesa-macro-fx/" in c[1]], [])


class MesaConsistency(unittest.TestCase):
    """Turnos de mesa = crons de sus workflows y el lane que el workflow asigna a ese cron.
    Necesita una copia de mesa-macro-fx (MESA_REPO_DIR o ../_clones/mesa-macro-fx); si no está, se salta."""
    def setUp(self):
        with open(os.path.join(ROOT, "mac", "dispatch_schedule_mesa.json"), encoding="utf-8") as fh:
            self.sched = json.load(fh)

    def test_M5_ids_unique_and_parseable(self):
        with open(os.path.join(ROOT, "mac", "dispatch_schedule.json"), encoding="utf-8") as fh:
            g8 = json.load(fh)
        ids = [s["id"] for s in g8["slots"] + self.sched["slots"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(self.sched["token_path"] != D.TOKEN_PATH)
        for s in self.sched["slots"]:
            D.parse_cron(s["cron"])
            self.assertEqual(s["inputs"]["backfill"], "false", s["id"])

    def test_M6_crons_and_lanes_match_mesa_workflows(self):
        repo = os.environ.get("MESA_REPO_DIR") or os.path.join(ROOT, "..", "_clones", "mesa-macro-fx")
        wdir = os.path.join(repo, ".github", "workflows")
        if not os.path.isdir(wdir):
            self.skipTest("sin copia local de mesa-macro-fx")
        for s in self.sched["slots"]:
            with open(os.path.join(wdir, s["workflow"]), encoding="utf-8") as fh:
                y = fh.read()
            self.assertIn('- cron: "%s"' % s["cron"], y, s["id"])
            lanes = {}
            for left, lane in re.findall(r'^\s*(".+?")\)\s+echo "lane=(\w+)"', y, re.M):
                for cron in re.findall(r'"([^"]+)"', left):
                    lanes[cron] = lane
            self.assertEqual(s["inputs"]["lane"], lanes.get(s["cron"]), s["id"])


if __name__ == "__main__":
    unittest.main()

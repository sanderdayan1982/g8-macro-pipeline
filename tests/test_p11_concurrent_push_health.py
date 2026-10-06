"""Acta P-11 · 6-oct-2026: el Daily Data Update descargó bien y perdió todo al subir (CONFLICT en
data/_ingest/evidence/*.jsonl, runs/**/*.jsonl, latest/*.json y alerts/*.json): se encoló detrás del intradía,
hizo checkout del SHA congelado al encolarse y su bucle `git pull --rebase` no resolvía conflictos.
Además la cabecera contaba como incidencia lo que solo espera su pasada y dos entradas desactivadas a propósito.

U1 dos ejecuciones añaden líneas al mismo .jsonl de solo alta → main conserva las de ambas
U2 el mismo JSON de estado (latest/alerts) → gana el de esta ejecución (helper P-7) y el push no se pierde
W1 los workflows que suben datos usan el helper P-7 (sin bucles pull/push propios)
W2 los del grupo g8-shared-data-alerts hacen checkout de la punta de main
H1 OVERDUE sin pasada programada → PENDING (no incidencia)
H2 con 2 observaciones faltando (STALE) o fuera del presupuesto del registro → sigue siendo LATE
H3 otra causa (p. ej. programada sin ejecución) → sigue siendo LATE
H4 entrada manual desactivada por el propietario → EXCLUDED, no MISSING
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import health_monitor as HM

SCRIPT = ROOT / "scripts" / "tools" / "git_push_retry.sh"
WDIR = ROOT / ".github" / "workflows"
EVID = "data/_ingest/evidence/ESTR.csv.jsonl"
RUNS = "data/_ingest/runs/actions/fetch_estr/2026-10.jsonl"
LATEST = "data/_ingest/latest/actions__fetch_estr.json"


@unittest.skipUnless(shutil.which("git") and shutil.which("bash"), "sin git/bash")
class ConcurrentPush(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.remote = os.path.join(self.t, "remote.git")
        self.git(self.t, "init", "-q", "--bare", "-b", "main", self.remote)
        seed = os.path.join(self.t, "seed")
        self.git(self.t, "clone", "-q", self.remote, seed)
        self.cfg(seed)
        shutil.copy(ROOT / ".gitattributes", os.path.join(seed, ".gitattributes"))   # el fichero real del repo
        self.write(seed, EVID, '{"query_utc":"2026-10-05T07:00:00Z"}\n')
        self.write(seed, RUNS, '{"run_id":"r0"}\n')
        self.write(seed, LATEST, '{"run_id":"r0"}\n')
        self.git(seed, "add", ".")
        self.git(seed, "commit", "-qm", "seed")
        self.git(seed, "push", "-q", "origin", "HEAD:main")

    def tearDown(self):
        shutil.rmtree(self.t, ignore_errors=True)

    def git(self, cwd, *a):
        return subprocess.run(["git"] + list(a), cwd=cwd, capture_output=True, text=True, check=True).stdout

    def cfg(self, d):
        self.git(d, "config", "user.email", "t@t")
        self.git(d, "config", "user.name", "t")

    def write(self, d, rel, text, append=False):
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a" if append else "w") as fh:
            fh.write(text)

    def clone(self, name):
        d = os.path.join(self.t, name)
        self.git(self.t, "clone", "-q", self.remote, d)
        self.cfg(d)
        return d

    def run_from(self, d, run_id):
        self.write(d, EVID, '{"query_utc":"%s"}\n' % run_id, append=True)
        self.write(d, RUNS, '{"run_id":"%s"}\n' % run_id, append=True)
        self.write(d, LATEST, '{"run_id":"%s"}\n' % run_id)
        self.git(d, "commit", "-qam", run_id)

    def test_U1_U2_daily_after_intraday(self):
        daily = self.clone("daily")                 # checkout tomado ANTES de que suba el intradía
        intraday = self.clone("intraday")
        self.run_from(intraday, "intraday")
        self.git(intraday, "push", "-q", "origin", "HEAD:main")
        self.run_from(daily, "daily")
        r = subprocess.run(["bash", str(SCRIPT), "3"], cwd=daily, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        show = lambda rel: self.git(self.t, "--git-dir", self.remote, "show", "main:" + rel)
        for rel in (EVID, RUNS):
            body = show(rel)
            self.assertIn("intraday", body, rel)
            self.assertIn("daily", body, rel)
            self.assertNotIn("<<<<<<<", body, rel)
        self.assertEqual(show(LATEST).strip(), '{"run_id":"daily"}')


class Workflows(unittest.TestCase):
    PUSHERS = ("daily_update.yml", "intraday_fetch.yml", "usd_factor.yml", "metals_update.yml", "ingest_watch.yml",
               "freshness_snapshot.yml", "feed_recovery.yml", "g8_port_run.yml")

    def read(self, name):
        return (WDIR / name).read_text(encoding="utf-8")

    def test_W1_pushers_use_helper(self):
        for name in self.PUSHERS:
            code = "\n".join(l.split(" #", 1)[0] for l in self.read(name).splitlines() if not l.lstrip().startswith("#"))
            self.assertIn("bash scripts/tools/git_push_retry.sh", code, name)
            self.assertFalse(re.search(r"pull --rebase|^\s*git push", code, re.M), name)

    def test_W2_shared_group_checks_out_main_tip(self):
        for name in sorted(os.listdir(WDIR)):
            y = self.read(name)
            if "group: g8-shared-data-alerts" not in y:
                continue
            blocks = re.findall(r"uses: actions/checkout@v4\n((?:\s{8,}.*\n)*)", y)
            writers = [b for b in blocks if "persist-credentials: false" not in b]   # read-only jobs never push
            self.assertTrue(writers, name)
            for b in writers:
                self.assertIn("ref: main", b, name)


class Health(unittest.TestCase):
    NOW = datetime(2026, 10, 6, 10, 17, tzinfo=timezone.utc)

    def feed(self, row):
        out = HM.build(ROOT, self.NOW, {"outputs": [row]})
        item = next(x for x in out["feeds"] if x["file"] == row["file"])
        return item, any(x["file"] == row["file"] for x in out["issues"])

    def row(self, state, cause, file="zz_p11.csv", have="2026-10-02"):
        return {"file": file, "state": state, "cause": cause, "have_max": have, "expected_obs": "2026-10-05", "facts": {}}

    def test_H1_waiting_for_scheduled_pass_is_pending(self):
        item, issue = self.feed(self.row("OVERDUE", "SIN_PASADA_PROGRAMADA"))
        self.assertEqual(item["status"], "PENDING")
        self.assertFalse(issue)

    def test_H2_two_missing_or_over_budget_is_late(self):
        item, issue = self.feed(self.row("STALE", "SIN_PASADA_PROGRAMADA"))
        self.assertEqual(item["status"], "LATE")
        self.assertTrue(issue)
        item, issue = self.feed(self.row("OVERDUE", "SIN_PASADA_PROGRAMADA", file="NZD_BOND_10Y.csv", have="2026-09-01"))
        self.assertEqual(item["status"], "LATE")
        self.assertTrue(issue)

    def test_H3_other_cause_is_late(self):
        item, issue = self.feed(self.row("OVERDUE", "PROGRAMADA_SIN_EJECUCION_REGISTRADA"))
        self.assertEqual(item["status"], "LATE")
        self.assertTrue(issue)

    def test_H4_disabled_manual_is_excluded(self):
        manual = {"CHF_BE_MANUAL": {"value": None, "date": None, "disabled": True},
                  "NZD_BE_MANUAL": {"value": 2.34, "date": "2026-08-13"}}
        real = HM.read_json
        fake = lambda p: manual if str(p).endswith("manual_inputs.json") else real(p)
        with patch.object(HM, "read_json", side_effect=fake):
            out = HM.build(ROOT, self.NOW, {"outputs": []})
        feeds = {x["file"]: x for x in out["feeds"]}
        issues = {x["file"] for x in out["issues"]}
        self.assertEqual(feeds["manual/CHF_BE_MANUAL"]["status"], "EXCLUDED")
        self.assertNotIn("manual/CHF_BE_MANUAL", issues)
        self.assertNotEqual(feeds["manual/NZD_BE_MANUAL"]["status"], "EXCLUDED")   # activo: sigue su caducidad


if __name__ == "__main__":
    unittest.main()

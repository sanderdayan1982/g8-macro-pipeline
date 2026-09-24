"""Pruebas del publicador del Mac v2.0 contra FakeGitHub sembrado con los CSV REALES del repositorio.

Cubre: sin novedad, dato nuevo (familia en un commit), descarga truncada (familia inválida sin bloquear a
las demás), token revocado, permiso perdido, conflicto con un commit de Actions, salto implausible
(T06: retener → confirmar en ejecución posterior), revisiones con y sin ventana, caducidad próxima,
y que el token no aparece en ninguna salida. Telegram nunca se envía (G8_NO_SEND=1)."""
import glob
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
sys.path.insert(0, os.path.dirname(__file__))

from g8common import g8http, ghpublish as G  # noqa: E402
import push_nzd_to_github as P  # noqa: E402
from g8fakes import Clock, FakeGitHub  # noqa: E402

TOKEN = "ghp_SECRET_MAC_TOKEN_do_not_print"
PATTERNS = ["NZD_BILL_*.csv", "NZD_BOND_*.csv", "NZD_CASH_ON.csv", "NZD_IIB_*.csv", "NZD_OCR.csv", "CHF_SPOT_*.csv", "CHF_NOM_10Y.csv", "CHF_SARON.csv", "TONA.csv"]


def repo_files():
    out = {}
    for pat in PATTERNS:
        for p in glob.glob(os.path.join(ROOT, "data", pat)):
            with open(p, "rb") as fh:
                out["data/" + os.path.basename(p)] = fh.read()
    with open(os.path.join(ROOT, "sources", "registry.csv"), "rb") as fh:
        out["sources/registry.csv"] = fh.read()
    return out


class MacPushTests(unittest.TestCase):
    def setUp(self):
        os.environ["G8_NO_SEND"] = "1"
        os.environ["GITHUB_TOKEN"] = TOKEN
        os.environ["G8_EXECUTOR_ID"] = "mac-primary"
        self.tmp = tempfile.mkdtemp()
        self.files = repo_files()
        os.makedirs(os.path.join(self.tmp, "data"))
        for path, data in self.files.items():
            if path.startswith("data/") and path != "data/NZD_OCR.csv":   # como en el Mac real: sin NZD_OCR
                with open(os.path.join(self.tmp, path), "wb") as fh:
                    fh.write(data)
        P.LOCAL_DATA = os.path.join(self.tmp, "data")
        P.STATE_DIR = os.path.join(self.tmp, "state")
        P.LOG_DIR = os.path.join(self.tmp, "logs")
        self.clock = Clock()
        self.gh = FakeGitHub(self.files, owner=P.OWNER, repo=P.REPO, clock=self.clock)
        self.gh.scopes = "public_repo"
        self.gh.token_expiration = "2027-01-31 00:00:00 UTC"

    def tearDown(self):
        self.gh.close()
        shutil.rmtree(self.tmp)
        for k in ("GITHUB_TOKEN", "G8_EXECUTOR_ID"):
            os.environ.pop(k, None)

    def run_push(self, args=None):
        fac = lambda tok, now: G.Repo(P.OWNER, P.REPO, "main", token=tok, api=self.gh.url,  # noqa: E731
                                      budget=g8http.Budget(10 ** 6, now=self.clock, env={}), sleep=self.clock.sleep, now=self.clock)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = P.main(args or ["--fetch-status", "nzd=0,chf=0,tona=0"], repo_factory=fac, now=self.clock,
                        cfg_dir=os.path.join(self.tmp, "nocfg"))
        with open(os.path.join(self.tmp, "state", "last_run.json")) as fh:
            rec = json.load(fh)
        self.assertNotIn(TOKEN, buf.getvalue())
        for dirpath, _, fnames in os.walk(self.tmp):
            for f in fnames:
                with open(os.path.join(dirpath, f), "rb") as fh:
                    self.assertNotIn(TOKEN.encode(), fh.read(), f)
        for path, data in self.gh.files().items():
            self.assertNotIn(TOKEN.encode(), data)
        return rc, rec, buf.getvalue()

    def append_local(self, fname, line):
        p = os.path.join(self.tmp, "data", fname)
        with open(p, "rb") as fh:
            crlf = b"\r\n" in fh.read(200)
        with open(p, "ab") as fh:
            fh.write(line.replace("\n", "\r\n" if crlf else "\n").encode())

    # ── escenarios ────────────────────────────────────────────────────────────
    def test_no_news_heartbeat_only(self):
        rc, rec, _ = self.run_push()
        self.assertEqual(rc, 0)
        self.assertEqual({f["status"] for f in rec["families"].values()}, {"NOOP"})
        self.assertEqual(rec["heartbeat"]["status"], "PUBLISHED")
        lat = json.loads(self.gh.files()["data/_ingest/latest/mac-primary__nzchf-tona.json"])
        self.assertEqual(lat["run_id"], rec["run_id"])

    def test_new_day_published_as_single_family_commit(self):
        for f in glob.glob(os.path.join(self.tmp, "data", "NZD_B*.csv")):
            last = open(f).read().strip().splitlines()[-1].split(",")[1]
            self.append_local(os.path.basename(f), "2026-09-24,%s\n" % last)
        before = len(self.gh.ref_log)
        rc, rec, _ = self.run_push()
        self.assertEqual(rec["families"]["NZ-B2"]["status"], "PUBLISHED")
        self.assertEqual(len(self.gh.ref_log) - before, 2)         # 1 commit de familia + 1 latido
        remote = self.gh.files()
        for f in glob.glob(os.path.join(self.tmp, "data", "NZD_B*.csv")):
            self.assertEqual(remote["data/" + os.path.basename(f)], open(f, "rb").read())   # byte a byte

    def test_truncated_family_invalid_others_unaffected(self):
        p = os.path.join(self.tmp, "data", "NZD_BOND_10Y.csv")
        lines = open(p).read().splitlines()
        open(p, "w").write("\n".join(lines[:1] + lines[1:50] + lines[-5:]) + "\n")   # hueco enorme
        self.append_local("CHF_NOM_10Y.csv", "2026-09-24,0.58,rss\n")
        rc, rec, _ = self.run_push()
        self.assertEqual(rc, 1)
        self.assertEqual(rec["families"]["NZ-B2"]["status"], "INVALID")
        self.assertIn("respuesta incompleta", rec["families"]["NZ-B2"]["detail"])
        self.assertEqual(rec["families"]["CH-DIARIO"]["status"], "PUBLISHED")
        self.assertEqual(self.gh.files()["data/NZD_BOND_10Y.csv"], self.files["data/NZD_BOND_10Y.csv"])

    def test_revoked_token_nothing_published_alert(self):
        self.gh.fail_auth = True
        rc, rec, out = self.run_push()
        self.assertEqual(rc, 1)
        self.assertIn("token:invalid", rec["alerts"])
        self.assertEqual(rec["heartbeat"]["status"], "NOT_ATTEMPTED")
        self.assertIn("[notify DRY]", out)                          # el aviso se habría enviado (DRY)
        log = open(os.path.join(self.tmp, "logs", "ALERTAS.log")).read()
        self.assertIn("no es válido", log)

    def test_download_ok_but_publication_forbidden(self):
        self.append_local("TONA.csv", "20260918,0.9770,0.9770,0.9770,0.9770,0\n")
        self.gh.readonly_token = True
        rc, rec, _ = self.run_push()
        self.assertEqual(rec["families"]["JP-TONA"]["status"], "PUBLISH_FAIL")
        self.assertIn("fam:JP-TONA", rec["alerts"])
        self.assertEqual(rec["families"]["JP-TONA"]["cls"], g8http.FAIL_PERMISSION)

    def test_conflict_with_actions_commit_preserved(self):
        self.append_local("TONA.csv", "20260918,0.9770,0.9770,0.9770,0.9770,0\n")
        self.gh.before_patch = lambda gh: gh.direct_commit({"data/SOFR.csv": b"DATE,CLOSE\n20260923,3.9\n"}, "actions")
        rc, rec, _ = self.run_push()
        self.assertEqual(rec["families"]["JP-TONA"]["outcome"]["attempts"], 2)
        f = self.gh.files()
        self.assertEqual(f["data/SOFR.csv"], b"DATE,CLOSE\n20260923,3.9\n")
        self.assertTrue(f["data/TONA.csv"].endswith(b"20260918,0.9770,0.9770,0.9770,0.9770,0\r\n"))

    def test_t06_family_hold_then_confirmed(self):
        # NZD_BOND_10Y salta +1.00 (> 0.5 del registro) el 24-sep; el resto de la familia trae un 24-sep normal
        for f in ("NZD_BOND_10Y.csv", "NZD_BOND_5Y.csv", "NZD_BILL_90D.csv"):
            last = float(open(os.path.join(self.tmp, "data", f)).read().strip().splitlines()[-1].split(",")[1])
            self.append_local(f, "2026-09-24,%s\n" % (round(last + 1.0, 2) if f == "NZD_BOND_10Y.csv" else last))
        rc, rec, _ = self.run_push()
        fam = rec["families"]["NZ-B2"]
        self.assertEqual(fam["status"], "HELD")
        self.assertEqual(fam["hold_new_from"], "20260924")
        remote = self.gh.files()
        for f in ("NZD_BOND_10Y.csv", "NZD_BOND_5Y.csv", "NZD_BILL_90D.csv"):
            self.assertNotIn(b"2026-09-24", remote["data/" + f])      # coherencia: ningún miembro avanza
        q = json.loads(remote["data/_ingest/quarantine/NZ-B2.json"])
        (cid, cand), = q["candidates"].items()
        self.assertEqual((cand["file"], cand["status"]), ("NZD_BOND_10Y.csv", "PENDING"))
        self.assertIn("held:NZ-B2", rec["alerts"])
        # segunda ejecución independiente 9 h después: la fuente mantiene el valor → aceptado con trazabilidad
        self.clock.t += 9 * 3600
        rc2, rec2, _ = self.run_push()
        self.assertEqual(rec2["families"]["NZ-B2"]["status"], "PUBLISHED")
        remote = self.gh.files()
        self.assertIn(b"2026-09-24", remote["data/NZD_BOND_10Y.csv"])
        q2 = json.loads(remote["data/_ingest/quarantine/NZ-B2.json"])
        self.assertEqual(q2["candidates"][cid]["status"], "ACCEPTED_CONFIRMED")
        self.assertEqual(q2["candidates"][cid]["confirmed_by_run"], rec2["run_id"])

    def test_manual_reject_keeps_holding(self):
        last = float(open(os.path.join(self.tmp, "data", "NZD_CASH_ON.csv")).read().strip().splitlines()[-1].split(",")[1])
        self.append_local("NZD_CASH_ON.csv", "2026-09-24,%s\n" % round(last + 2.0, 2))
        rc, rec, _ = self.run_push()
        q = json.loads(self.gh.files()["data/_ingest/quarantine/NZ-B2.json"])
        cid = next(iter(q["candidates"]))
        self.gh.direct_commit({"data/_ingest/decisions/NZ-B2/%s.json" % cid:
                               json.dumps({"id": cid, "decision": "reject", "by": "Sander", "reason": "error de la fuente", "utc": "2026-09-24T12:00:00Z"}).encode()})
        self.clock.t += 9 * 3600
        rc2, rec2, _ = self.run_push()
        self.assertEqual(rec2["families"]["NZ-B2"]["status"], "HELD")
        q2 = json.loads(self.gh.files()["data/_ingest/quarantine/NZ-B2.json"])
        self.assertEqual(q2["candidates"][cid]["status"], "REJECTED_MANUAL")
        self.assertEqual(q2["candidates"][cid]["decision"]["by"], "Sander")

    def test_revision_detected_without_new_date(self):
        p = os.path.join(self.tmp, "data", "NZD_BILL_90D.csv")
        lines = open(p).read().splitlines()
        d, v = lines[-3].split(",")
        lines[-3] = "%s,%s" % (d, round(float(v) + 0.01, 2))
        open(p, "w").write("\n".join(lines) + "\n")
        rc, rec, _ = self.run_push()
        fam = rec["families"]["NZ-B2"]
        self.assertEqual(fam["status"], "HELD")                      # sin ventana revisable documentada: confirmación
        self.assertEqual(fam["files"]["NZD_BILL_90D.csv"]["src_max"], fam["files"]["NZD_BILL_90D.csv"]["repo_max"])

    def test_chf_rss_to_curve_revision_inside_documented_window(self):
        p = os.path.join(self.tmp, "data", "CHF_NOM_10Y.csv")
        lines = open(p).read().splitlines()
        d, v, src = lines[-2].split(",")
        lines[-2] = "%s,%s,curve" % (d, round(float(v) + 0.004, 4))
        open(p, "w").write("\n".join(lines) + "\n")
        rc, rec, _ = self.run_push()
        self.assertEqual(rec["families"]["CH-DIARIO"]["status"], "PUBLISHED")
        self.assertEqual(len(rec["families"]["CH-DIARIO"]["files"]["CHF_NOM_10Y.csv"]["revised"]), 1)

    def test_expiry_warning(self):
        from datetime import datetime, timedelta, timezone
        self.gh.token_expiration = (datetime.fromtimestamp(self.clock(), tz=timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S UTC")
        rc, rec, _ = self.run_push()
        self.assertIn("token:expiry", rec["alerts"])
        self.assertAlmostEqual(rec["credentials"]["days_left"], 5.0, places=1)

    def test_fetch_failure_reported(self):
        rc, rec, _ = self.run_push(["--fetch-status", "nzd=1,chf=0,tona=0"])
        self.assertIn("fetch:nzd", rec["alerts"])
        self.assertEqual(rec["families"]["NZ-B2"]["fetch_rc"], "1")

    def test_alerts_not_repeated_but_resolution_reported(self):
        self.gh.fail_auth = True
        _, _, out1 = self.run_push()
        _, _, out2 = self.run_push()
        self.assertIn("no es válido", out1)
        self.assertNotIn("no es válido", out2)                       # mismo aviso activo: no se repite (<24 h)
        self.gh.fail_auth = False
        _, _, out3 = self.run_push()
        self.assertIn("resuelto", out3)

    def test_dry_run_writes_nothing(self):
        self.append_local("TONA.csv", "20260918,0.9770,0.9770,0.9770,0.9770,0\n")
        before = self.gh.ref
        rc, rec, _ = self.run_push(["--dry-run"])
        self.assertEqual(self.gh.ref, before)
        self.assertEqual(rec["families"]["JP-TONA"]["status"], "DRY:PUBLISH")


if __name__ == "__main__":
    unittest.main()

"""Verificación inversa de la revisión del 24-sep: los MISMOS escenarios de repro_revision.py (datos ficticios,
sin red, solo directorios temporales), pero comprobando el comportamiento CORREGIDO. Cada caso es independiente;
salida JSON con ok/fallo por hallazgo; código 0 solo si los siete pasan.

    python tests/review/verificar_hallazgos.py [raíz_del_repo]
"""
import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import traceback

sys.dont_write_bytecode = True
ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parents[2])
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "mac"), str(ROOT / "tests"), str(ROOT / "scripts" / "tools")]
os.environ["G8_NO_SEND"] = "1"
TMP = tempfile.gettempdir()
results = {}


def case(name):
    def deco(fn):
        try:
            results[name] = {"ok": True, "detail": fn()}
        except Exception as e:                                  # noqa: BLE001
            results[name] = {"ok": False, "error": "%s: %s" % (type(e).__name__, e),
                             "trace": traceback.format_exc()[-800:]}
        return fn
    return deco


@case("1_mac_descarga_fallida_no_confirma")
def _():
    from g8common import ghpublish as G
    import push_nzd_to_github as P
    from g8fakes import Clock

    class MemoryRepo:
        def __init__(self, files):
            self.files, self.n = files, 0

        def repo_info(self):
            return {}, {"date": "Thu, 24 Sep 2026 07:00:00 GMT", "x-oauth-scopes": "public_repo"}

        def publish(self, build, message, **kw):
            cur = {p: self.files.get(p) for p in build.paths}
            for p, v in self.files.items():
                if any(p.startswith(x) for x in build.prefixes):
                    cur[p] = v
            files, info = build(cur, "fake-head")
            self.files.update(files)
            self.n += bool(files)
            return G.Outcome(G.PUBLISHED if files else G.NOOP, info=info, commit="fake-%d" % self.n)

    with tempfile.TemporaryDirectory(dir=TMP) as td:
        d = pathlib.Path(td)
        (d / "data").mkdir()
        original = b"DATE,OPEN,HIGH,LOW,CLOSE,VOLUME\n20260917,0.9,0.9,0.9,0.9,0\n"
        candidate = original + b"20260918,5,5,5,5,0\n"
        (d / "data/TONA.csv").write_bytes(candidate)
        repo = MemoryRepo({"data/TONA.csv": original, "sources/registry.csv": (ROOT / "sources/registry.csv").read_bytes()})
        clock = Clock()
        P.LOCAL_DATA, P.STATE_DIR, P.LOG_DIR = str(d / "data"), str(d / "state"), str(d / "logs")
        fams = P.FAMILIES
        P.FAMILIES = [f for f in fams if f[0] == "JP-TONA"]
        P.read_token, P.executor_id, P.env_check = (lambda: "ghp_FAKE_REVIEW"), (lambda: "mac-primary"), (lambda: {})

        def run(args):
            with contextlib.redirect_stdout(io.StringIO()):
                P.main(args, repo_factory=lambda *a: repo, now=clock, cfg_dir=str(d / "none"))
            return json.loads((d / "state/last_run.json").read_text())["families"]["JP-TONA"]
        try:
            first = run(["--fetch-status", "tona=0", "--fetch-started", str(clock.t - 5)])
            before = repo.files["data/TONA.csv"]
            clock.sleep(3600)
            second = run(["--fetch-status", "tona=1", "--fetch-started", str(clock.t - 5)])    # misma copia, fetch fallido
            assert second["status"] == "HELD", second["status"]
            assert repo.files["data/TONA.csv"] == before, "el candidato se publicó tras un fetch fallido"
            clock.sleep(3600)
            third = run(["--fetch-status", "tona=0"])                                          # relectura sin marca de tiempo
            assert third["status"] == "HELD" and repo.files["data/TONA.csv"] == before
            clock.sleep(3600)
            os.utime(d / "data/TONA.csv", None)
            fourth = run(["--fetch-status", "tona=0", "--fetch-started", str(clock.t - 5)])    # descarga correcta nueva
            q = json.loads(repo.files["data/_ingest/quarantine/JP-TONA.json"])
            (c,) = q["candidates"].values()
            assert fourth["status"] == "PUBLISHED" and c["status"] == "ACCEPTED_CONFIRMED"
            assert c["first_download"] != c["confirmed_by_download"]
        finally:
            P.FAMILIES = fams
        return {"1ª": [first["status"], first["download_kind"]], "fetch fallido": [second["status"], second["download_kind"]],
                "relectura": [third["status"], third["download_kind"]], "nueva descarga": [fourth["status"], fourth["download_kind"]]}


@case("2_instalador_prepara_valida_y_revierte")
def _():
    import unittest
    import test_mac_installer
    r = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(
        unittest.defaultTestLoader.loadTestsFromModule(test_mac_installer))
    assert r.wasSuccessful(), [str(x[0]) for x in r.failures + r.errors]
    return {"pruebas": r.testsRun}


@case("3_plazo_global_y_reserva_daily")
def _():
    import unittest
    import test_job_budget
    r = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(
        unittest.defaultTestLoader.loadTestsFromModule(test_job_budget))
    assert r.wasSuccessful(), [str(x[0]) for x in r.failures + r.errors]
    return {"pruebas": r.testsRun, "omitidas": len(r.skipped)}


@case("4_held_de_actions_avisado")
def _():
    from g8common import ingest
    import ingest_watch as W
    with tempfile.TemporaryDirectory(dir=TMP) as td:
        d = pathlib.Path(td)
        (d / "sources").mkdir()
        (d / "data").mkdir()
        (d / "sources/registry.csv").write_bytes((ROOT / "sources/registry.csv").read_bytes())
        (d / "sources/executors.csv").write_text("executor_id,job,active_from\n")
        (d / "sources/credentials.csv").write_text("credential_id,expires_utc\n")
        old = b"DATE,OPEN,HIGH,LOW,CLOSE,VOLUME\n20260917,0.9,0.9,0.9,0.9,0\n20260918,0.91,0.91,0.91,0.91,0\n"
        (d / "data/TONA.csv").write_bytes(old)
        ctx = ingest.Ingest("fetch_tona", root=td, env={})
        rep = ctx.publish("TONA.csv", old.replace(b"0.9,0.9,0.9,0.9", b"0.92,0.92,0.92,0.92"))
        with contextlib.redirect_stdout(io.StringIO()):
            rc = ctx.finish(0)
            W.main(["--dry-run", "--result", str(d / "result.json")], root=td)
        watch = json.loads((d / "result.json").read_text())
        assert rep["status"] == "HELD" and rc == 0
        assert "actions:held:TONA.csv" in watch["alerts"] and watch["delivery"] == "DRY", watch
        return {"status": rep["status"], "exit_code": rc, "watch_alerts": watch["alerts"], "delivery": watch["delivery"]}


@case("5_retry_after_conservado_en_legacy")
def _():
    from g8common import g8http, legacy
    from g8fakes import Clock
    with tempfile.TemporaryDirectory(dir=TMP) as td:
        clock, calls = Clock(), []

        def serve(method, url, headers, body, *t):
            calls.append(url)
            return 503, {"retry-after": "3600"}, b""
        legacy.TEST_TRANSPORT, legacy.TEST_CLOCK, legacy.STATE_PATH = serve, clock, os.path.join(td, "nb")
        try:
            errs = []
            for url in ("https://source.invalid/series", "https://source.invalid/series", "https://alt.source.invalid/x"):
                try:
                    legacy.http_get_text(url, budget_obj=g8http.Budget(360, now=clock, env={}), log=None)
                except legacy.LegacyHTTPError as e:
                    errs.append(e.cls)
            legacy._STORE.clear()                                            # «ejecución siguiente»
            try:
                legacy.http_get_text("https://source.invalid/series", budget_obj=g8http.Budget(360, now=clock, env={}), log=None)
            except legacy.LegacyHTTPError as e:
                errs.append(e.cls)
        finally:
            legacy.TEST_TRANSPORT = legacy.TEST_CLOCK = legacy.STATE_PATH = None
            legacy._STORE.clear()
        assert len(calls) == 1, calls
        assert errs == [g8http.DEFERRED] * 4, errs
        return {"consultas_al_proveedor": len(calls), "resultados": errs}


@case("6_redirecciones")
def _():
    from g8common import g8http
    seen = []

    def t(method, url, headers, body, *a):
        seen.append((url, headers.get("Authorization")))
        if url.endswith("/old"):
            return 302, {"location": "https://source.invalid/new"}, b""
        if url.endswith("/xorigin"):
            return 302, {"location": "https://other.invalid/new"}, b""
        if url.endswith("/down"):
            return 302, {"location": "http://source.invalid/new"}, b""
        if url.endswith("/loop"):
            return 302, {"location": "/loop2"}, b""
        if url.endswith("/loop2"):
            return 302, {"location": "/loop"}, b""
        return 200, {}, b"ok"
    r = g8http.fetch("https://source.invalid/old", transport=t)
    assert (r.status, r.cls, len(r.attempts)) == (200, g8http.OK, 1), (r.status, r.cls)
    r2 = g8http.fetch("https://source.invalid/xorigin", headers={"Authorization": "token X"}, transport=t)
    cross = seen[-1]
    assert r2.ok and cross == ("https://other.invalid/new", None)
    r3 = g8http.fetch("https://source.invalid/down", transport=t)
    r4 = g8http.fetch("https://source.invalid/loop", transport=t)
    assert r3.cls == r4.cls == g8http.FAIL_INVALID
    return {"302_valida": r.cls, "otro_origen": {"url": cross[0], "authorization_enviada": cross[1]},
            "https_a_http": r3.detail, "bucle": r4.detail}


@case("7_fechas_inexistentes")
def _():
    from g8common import series as S
    out = {}
    for raw in (b"Date,Value\n2026-99-99,1.0\n", b"Date,Value\n2026-02-30,1.0\n"):
        try:
            S.parse(raw)
            raise AssertionError("aceptada: %r" % raw)
        except S.SeriesError as e:
            out[raw.decode().split("\n")[1]] = str(e)
    return out


print(json.dumps(results, ensure_ascii=False, indent=1))
sys.exit(0 if all(v["ok"] for v in results.values()) else 1)

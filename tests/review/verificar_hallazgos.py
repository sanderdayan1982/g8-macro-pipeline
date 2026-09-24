"""Verificación inversa de la revisión del 24-sep: los MISMOS escenarios de repro_revision.py (datos ficticios,
sin red, solo directorios temporales), pero comprobando el comportamiento CORREGIDO. Cada caso es independiente;
salida JSON con ok/fallo por hallazgo; código 0 solo si pasan los siete de la primera revisión los cuatro de la segunda (R2-1…R2-4) los tres de la tercera (R3-1…R3-3) y R4-1.

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
        # la salida de los casos (y de sus subprocesos) va a stderr: stdout queda solo para el JSON final
        sys.stdout.flush()
        saved = os.dup(1)
        os.dup2(2, 1)
        try:
            with contextlib.redirect_stdout(sys.stderr):
                results[name] = {"ok": True, "detail": fn()}
        except Exception as e:                                  # noqa: BLE001
            results[name] = {"ok": False, "error": "%s: %s" % (type(e).__name__, e),
                             "trace": traceback.format_exc()[-800:]}
        finally:
            sys.stdout.flush()
            os.dup2(saved, 1)
            os.close(saved)
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


# ── segunda revisión (R2): los escenarios de repro_adicional.py con la conducta CORRECTA exigida ─────────
@case("R2-1_corte_no_publica_csv_vacio")
def _():
    import subprocess
    import g8step
    with tempfile.TemporaryDirectory(dir=TMP) as t:
        t = pathlib.Path(t)
        p, q = t / "data" / "US_BILL_1M.csv", t / "data" / "US_BILL_3M.csv"
        p.parent.mkdir()
        old = b"DATE,OPEN,HIGH,LOW,CLOSE,VOLUME\n20260923,4,4,4,4,0\n"
        p.write_bytes(old)
        q.write_bytes(old)
        child = t / "writer.py"
        child.write_text("""import sys,time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from fetch_us_bills import write_csv
p=Path(sys.argv[2])
def rows():
    yield ('20260924', 4.1)
    (p.parent.parent / 'ready').write_text('escritor abierto')
    time.sleep(60)
    yield ('20260925', 4.2)
write_csv(rows(), p)
""")
        other = t / "other.py"
        other.write_text("import sys\nfrom pathlib import Path\nsys.path.insert(0, sys.argv[1])\n"
                         "from fetch_us_bills import write_csv\nwrite_csv([('20260923', 4.0), ('20260924', 4.3)], Path(sys.argv[2]))\n")
        env = {k: v for k, v in os.environ.items() if k not in ("G8_JOB_DEADLINE_EPOCH", "G8_JOB_RESERVE_S", "G8_STEP_LOG")}
        g8step.GRACE_KILL_S = 1
        with contextlib.redirect_stdout(io.StringIO()):
            # como en el workflow: G8_STEP_GUARD apunta al data/ que escriben los pasos
            rc0 = g8step.run_step("us_bills_3m", [sys.executable, str(other), str(ROOT / "scripts"), str(q)], 5, .1,
                                  env=env, guard=str(t / "data"))
            rc = g8step.run_step("us_bills", [sys.executable, str(child), str(ROOT / "scripts"), str(p)], 3, .1,
                                 env=env, guard=str(t / "data"))
        subprocess.run(["git", "init", "-q", str(t)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(t), "add", "data/"], check=True, capture_output=True)
        staged = subprocess.run(["git", "-C", str(t), "show", ":data/US_BILL_1M.csv"], check=True, capture_output=True).stdout
        staged_q = subprocess.run(["git", "-C", str(t), "show", ":data/US_BILL_3M.csv"], check=True, capture_output=True).stdout
        assert (t / "ready").exists(), "la escritura no llegó a empezar"
        assert rc == 124 and rc0 == 0
        assert p.read_bytes() == old and staged == old, "el último CSV válido no quedó intacto"
        assert b"20260924,4.3000" in staged_q, "se perdió la actualización correcta del otro feed"
        return {"rc": rc, "bytes_validos_antes": len(old), "bytes_despues": p.stat().st_size,
                "bytes_preparados_por_git_add": len(staged), "otro_feed_actualizado": True}


@case("R2-2_activacion_launchd_en_la_transaccion")
def _():
    import test_mac_installer as MI
    out = {}
    for name, fail in (("primera_carga_falla", {1}), ("tambien_falla_la_recuperacion", {1, 2})):
        f = MI.Installer()
        f.setUp()
        try:
            f.fail_loads = fail
            err = None
            try:
                MI.I.install(f.env(), f.pkg)
            except MI.I.Abort as e:
                err = e
            assert err is not None, "declaró éxito"
            assert f.snapshot() == f.before, "no restauró el conjunto anterior"
            if fail == {1}:
                assert not isinstance(err, MI.I.RecoveryFailed) and f.loaded
            else:
                assert isinstance(err, MI.I.RecoveryFailed) and "RECUPERACIÓN FALLIDA" in str(err) and not f.loaded
            out[name] = {"excepcion": type(err).__name__, "codigo_restaurado": True, "programacion_cargada": f.loaded}
        finally:
            f.tearDown()
    return out


@case("R2-3_fallo_http_avisado")
def _():
    import shutil
    from datetime import timedelta, timezone
    import fetcher_harness as H
    import ingest_watch as W
    out = {}
    for name, serve in (("503_agotado", lambda url: (503, b"Unavailable")), ("401", lambda url: (401, b"no")),
                        ("parseo", lambda url: (200, json.dumps({"STATUS": 200, "RESULTSET": []}).encode()))):
        r = H.make_root({"TONA.csv": (ROOT / "data/TONA.csv").read_bytes()})
        try:
            rc, calls, _ = H.run_new("fetch_tona", serve, r)
            a = {}
            W.check_actions(r, (H.NOW + timedelta(hours=1)).replace(tzinfo=timezone.utc), a, [])
            assert rc == 1 and "actions:fetch_tona:fail" in a, (name, a)
            out[name] = a["actions:fetch_tona:fail"][:140]
        finally:
            shutil.rmtree(r)
    return out


@case("R2-4_sin_resolucion_por_antiguedad")
def _():
    import shutil
    from datetime import timedelta, timezone
    import fetcher_harness as H
    import ingest_watch as W
    from g8common.notify import AlertBook
    r = H.make_root({"TONA.csv": (ROOT / "data/TONA.csv").read_bytes()})
    try:
        rows = [row for row in H.repo_rows("TONA.csv") if row[0] >= "20210901"][:-3]
        H.run_new("fetch_tona", lambda url: (200, H.boj_json(rows)), r)
        t1 = (H.NOW + timedelta(minutes=5)).replace(tzinfo=timezone.utc)
        t2 = t1 + timedelta(days=9)
        book = AlertBook(str(pathlib.Path(r) / "book.json"))
        a1, a2 = {}, {}
        W.check_actions(r, t1, a1, [], prev_active=book.state["active"])
        book.commit(a1, list(a1), t1.timestamp(), [])
        W.check_actions(r, t2, a2, [], prev_active=book.state["active"])
        planned = book.plan(a2, t2.timestamp())
        assert a1 and not [x for x in planned if x[0] == "RESOLVED"], planned
        assert "actions:fetch_tona:silence" in a2
        return {"alertas_9_dias_despues": sorted(a2), "resueltos_planificados": 0}
    finally:
        shutil.rmtree(r)


# ── tercera revisión (R3): pruebas de aceptación, incluidos los escenarios exactos de repro_revision3.py ──
def _run_tests(module, prefixes):
    import unittest
    mod = __import__(module)
    suite = unittest.TestSuite()
    for t in unittest.defaultTestLoader.loadTestsFromModule(mod):
        for c in t:
            if any(c._testMethodName.startswith(pf) for pf in prefixes):
                suite.addTest(c)
    r = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
    assert r.testsRun and r.wasSuccessful(), [str(x[0]) + x[1][-300:] for x in r.failures + r.errors]
    return {"pruebas": r.testsRun}


@case("R3-1_senal_restaura_y_validacion_por_tipo")
def _():
    return _run_tests("test_step_guard", ("test_r3_1", "test_reviewer_repro_r3_1"))


@case("R3-2_aplazamiento_no_resuelve")
def _():
    return _run_tests("test_actions_watch_integration", ("test_r3_2", "test_reviewer_repro_r3_2"))


@case("R3-3_sin_exito_nunca_inicializado")
def _():
    return _run_tests("test_actions_watch_integration", ("test_r3_3",))


@case("R4-1_comilla_sin_cerrar")
def _():
    return _run_tests("test_step_guard", ("test_r4_1", "test_r3_1_every_current_data_file"))


print(json.dumps(results, ensure_ascii=False, indent=1))
sys.exit(0 if all(v["ok"] for v in results.values()) else 1)

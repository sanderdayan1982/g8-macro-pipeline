"""ingest — contexto de ingestión de un descargador de Actions (lote 3, adopción progresiva).

Un descargador que lo adopta:
  ctx = ingest.Ingest("fetch_estr")                      # registro, presupuesto, not_before, cuarentena
  requests = ctx.requests(provider="ecb")                # sustituto de `requests.get` (g8http debajo)
  … el código de parseo del script NO cambia …
  ctx.publish("ESTR.csv", render_csv(rows), retain_from=date_from)   # fusión monótona + atómica
  return ctx.finish(rc)                                  # registro JSONL del mes + puntero; código de salida

Registros (un único escritor por fichero: ese descargador; los workflows están serializados por el grupo de
concurrencia g8-shared-data-alerts):
  data/_ingest/runs/actions/<descargador>/<AAAA-MM>.jsonl    (solo alta: una línea por ejecución)
  data/_ingest/latest/actions__<descargador>.json            (puntero: última ejecución y not_before vigentes)
  data/_ingest/quarantine/<FICHERO>.json                      (candidatos en confirmación, decisión T06)
  data/_ingest/decisions/<FICHERO>/*.json                     (decisiones manuales del operador, las crea él)
"""
import csv
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

from . import defer, g8http, runlog, series as S

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXECUTOR = "actions"
TEST_TRANSPORT = None          # las pruebas inyectan aquí un transporte local (respuestas grabadas)
TEST_CLOCK = None              # las pruebas inyectan un reloj simulado (sin esperas reales)


class RequestException(Exception):
    """Compatible con el `except requests.RequestException` de los scripts."""

    def __init__(self, msg, g8cls=None, not_before=None):
        Exception.__init__(self, msg)
        self.g8cls = g8cls
        self.not_before = not_before


class HTTPError(RequestException):
    pass


class Response(object):
    def __init__(self, res):
        self.status_code = res.status
        self.headers = res.headers
        self.content = res.body or b""
        ct = res.headers.get("content-type", "")
        enc = "utf-8"
        if "charset=" in ct:
            enc = ct.split("charset=")[-1].split(";")[0].strip() or "utf-8"
        try:
            self.text = self.content.decode(enc, "replace")
        except LookupError:
            self.text = self.content.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        return None                      # g8http ya ha clasificado; solo llegan respuestas OK


class _Requests(object):
    """Espacio de nombres con la misma forma que el módulo `requests` que usan los scripts."""
    RequestException = RequestException
    HTTPError = HTTPError
    Timeout = RequestException          # los scripts que capturaban Timeout/ConnectionError siguen funcionando;
    ConnectionError = RequestException  # g8http ya reintentó debajo (sin anidar reintentos)
    exceptions = None

    def __init__(self, ctx, provider, not_found_is_no_publication=False, validate=None):
        self._ctx, self._provider = ctx, provider
        self._nf, self._validate = not_found_is_no_publication, validate
        self.exceptions = self

    def get(self, url, params=None, headers=None, timeout=None, **_ignored):
        if params:
            url = url + ("&" if "?" in url else "?") + urlencode(params)
        read_t = float(timeout) if isinstance(timeout, (int, float)) else 60.0
        return self._ctx._get(url, self._provider, headers or {}, read_t, self._nf, self._validate)


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


EVIDENCE_DIR = os.path.join("data", "_ingest", "evidence")


def _iso(d):
    d = str(d or "")
    return "%s-%s-%s" % (d[:4], d[4:6], d[6:8]) if len(d) == 8 and d.isdigit() else (d or None)


class Ingest(object):
    def __init__(self, job, root=None, budget_s=360, now=None, env=None):
        now = now or TEST_CLOCK or time.time
        self.job = job
        self.root = root or ROOT
        self.now = now
        self.started = datetime.fromtimestamp(now(), tz=timezone.utc)
        self.run_id = runlog.new_run_id(EXECUTOR, job.replace("_", "-"), self.started)
        self.budget = g8http.Budget(budget_s, now=now, env=env)
        self.latest_path = os.path.join(self.root, runlog.LATEST_DIR, "%s__%s.json" % (EXECUTOR, job))
        self.latest = _load_json(self.latest_path, {})
        self.not_before = dict(self.latest.get("not_before", {}))
        self.requests_log = []
        self.files = {}
        self.errors = []
        self._registry = None

    def rename(self, job):
        """Para descargadores con varias series por argumento (p. ej. fetch_bis_policy GB): un registro por serie."""
        self.job = job
        self.run_id = runlog.new_run_id(EXECUTOR, job.replace("_", "-").lower(), self.started)
        self.latest_path = os.path.join(self.root, runlog.LATEST_DIR, "%s__%s.json" % (EXECUTOR, job))
        self.latest = _load_json(self.latest_path, {})
        self.not_before = dict(self.latest.get("not_before", {}))

    # ── HTTP ─────────────────────────────────────────────────────────────────
    def requests(self, provider="generic", not_found_is_no_publication=False, validate=None):
        return _Requests(self, provider, not_found_is_no_publication, validate)

    def _get(self, url, provider, headers, read_t, nf, validate):
        key = provider
        if not hasattr(self, "_defer"):
            self._defer = defer.Store(os.path.join(self.root, defer.REL_DIR), now=self.now)
        scope = defer.scope_for(url)
        nbs = [x for x in (self.not_before.get(key), self._defer.get(scope)) if x]
        nb = max(nbs) if nbs else None                  # el límite compartido del ámbito también cuenta (hallazgo #5)
        res = g8http.fetch(url, provider=provider, headers=headers, budget=self.budget, read_timeout=read_t,
                           connect_timeout=min(10.0, read_t), not_found_is_no_publication=nf, validate=validate,
                           not_before=nb, transport=TEST_TRANSPORT, now=self.now, sleep=_sleep_for(self.now))
        rec = res.record()
        self.requests_log.append(rec)
        if res.not_before:
            self.not_before[key] = res.not_before
            if res.attempts:                            # solo plazos recibidos del servidor, no los heredados
                self._defer.set(scope, res.not_before, source=res.url)
        elif res.ok:
            self.not_before.pop(key, None)
        if not res.ok:
            raise HTTPError("%s: %s (%s)" % (res.cls, res.detail, res.url), g8cls=res.cls, not_before=res.not_before)
        return Response(res)

    def degraded(self, msg):
        """Una fuente COMPLEMENTARIA falló aunque el descargador pudo seguir con otra (p. ej. histórico sí, mes en
        curso no): no cambia el código de salida del script, pero queda en el registro y ingest_watch avisa
        (clave actions:<job>:degraded) hasta que una ejecución posterior termine sin degradación (lote 3B)."""
        self.errors.append({"kind": "DEGRADED", "detail": str(msg)[:300]})
        print("[ingest] DEGRADADO: %s" % msg)

    def fail(self, msg):
        """Fallo detectado por el propio descargador tras una descarga correcta (p. ej. fuente sin actualizar:
        compuerta de frescura). Queda en el registro con su motivo; el aviso lo emite ingest_watch."""
        self.errors.append({"kind": "FAIL", "detail": str(msg)[:300]})

    # ── publicación ──────────────────────────────────────────────────────────
    def registry(self):
        if self._registry is None:
            self._registry = {}
            try:
                with open(os.path.join(self.root, "sources", "registry.csv"), encoding="utf-8") as fh:
                    for r in csv.DictReader(fh):
                        acc = (r.get("primary_access") or "").strip()
                        if acc.startswith("data/"):
                            self._registry[acc[5:]] = r
            except OSError:
                pass
            try:
                with open(os.path.join(self.root, "sources", "plausibility_inherit.csv"), encoding="utf-8") as fh:
                    self._inherit = {r["file"]: r["inherit_from_file"] for r in csv.DictReader(fh)}
            except OSError:
                self._inherit = {}
        return self._registry

    def plaus(self, fname):
        reg = self.registry()
        row = reg.get(fname) or reg.get(self._inherit.get(fname, ""))
        if not row:
            return None

        def f(x):
            try:
                return float(x)
            except (TypeError, ValueError):
                return None
        return {"min": f(row.get("plaus_min")), "max": f(row.get("plaus_max")), "max_jump": f(row.get("plaus_max_jump"))}

    def horizon(self, fname):
        if not hasattr(self, "_horizon"):
            try:
                with open(os.path.join(self.root, "sources", "date_horizon.csv"), "rb") as fh:
                    self._horizon = S.load_horizons(fh.read())
            except OSError:
                self._horizon = S.load_horizons(None)
        return self._horizon(fname)

    def publish(self, fname, data, retain_from=None, revision_window=None, expect_header=None, measures=None):
        """Fusiona `data` (bytes con el formato exacto del escritor original) sobre data/<fname>.
        No escribe nada si la descarga es inválida, más antigua o incompleta. → informe (dict)."""
        path = os.path.join(self.root, "data", fname)
        qpath = os.path.join(self.root, "data", "_ingest", "quarantine", fname + ".json")
        ddir = os.path.join(self.root, "data", "_ingest", "decisions", fname)
        rep = {"file": fname}
        try:
            src = S.parse(data, expect_header=expect_header)
        except S.SeriesError as e:
            rep.update(status=S.INVALID, detail="descarga inválida: %s" % e)
            self.files[fname] = rep
            return rep
        repo = None
        if os.path.exists(path):
            try:
                with open(path, "rb") as fh:
                    repo = S.parse(fh.read())
            except S.SeriesError as e:
                rep["warning"] = "fichero publicado ilegible (%s): se reconstruye con la descarga" % e
        qdoc = _load_json(qpath, {"file": fname, "candidates": {}})
        decisions = []
        if os.path.isdir(ddir):
            for n in sorted(os.listdir(ddir)):
                if n.endswith(".json"):
                    d = _load_json(os.path.join(ddir, n), None)
                    if d:
                        decisions.append(d)
        q = S.apply_manual_decisions(qdoc.get("candidates", {}), decisions)
        # Identidad de la descarga (hallazgo #1): solo si ESTA ejecución obtuvo al menos una respuesta correcta.
        ok_req = [x for x in self.requests_log if x.get("cls") == g8http.OK]
        download_id = ("%s:%s" % (self.run_id, hashlib.sha256(data).hexdigest()[:12])) if ok_req else None
        rep["download_id"] = download_id
        r = S.merge(fname, repo, src, self.plaus(fname), revision_window, q, self.run_id, self.started,
                    retain_from=retain_from, download_id=download_id, max_future_days=self.horizon(fname),
                    measures=measures)
        rep.update(r.report())
        if r.series is not None:
            S.write_atomic(path, r.series.to_bytes())
            rep["written"] = True
        newq = dict(q)
        for c in r.candidates:
            newq[c["id"]] = c
        if newq != qdoc.get("candidates", {}):
            S.write_atomic(qpath, runlog.dumps({"file": fname, "candidates": newq, "updated_by_run": self.run_id}))
        if download_id:
            try:
                self._evidence(fname, src, repo, rep, download_id, measures)
            except (OSError, ValueError) as e:              # la evidencia nunca impide publicar
                rep["evidence_error"] = str(e)[:200]
        self.files[fname] = rep
        return rep

    def _evidence(self, fname, src, repo, rep, download_id, measures):
        """F3 · historial de evidencia (solo de alta): data/_ingest/evidence/<fichero>.jsonl, una línea por descarga
        VERIFICADA (clave única download_id: releer o repetir la misma descarga no duplica). Registra la fecha
        máxima traída, las observaciones nuevas o con versión distinta (revisiones, aceptadas o no) y los metadatos
        de las respuestas del run (Date, Last-Modified: del RECURSO, nunca hora de publicación por observación)."""
        path = os.path.join(self.root, EVIDENCE_DIR, fname + ".jsonl")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                if any('"download_id": "%s"' % download_id in line for line in fh):
                    return
        obs = []

        def version(series, d):
            v = S.row_measures(series, d, measures) if measures else series.rows[d][1]
            return hashlib.sha1(repr(v).encode()).hexdigest()[:12]
        dates = sorted(src.rows) if repo is not None else sorted(src.rows)[-1:]
        for d in dates:
            if repo is None or d not in repo.rows:
                obs.append({"date": _iso(d), "version": version(src, d), "kind": "new"})
            elif version(src, d) != version(repo, d):
                obs.append({"date": _iso(d), "version": version(src, d), "previous_version": version(repo, d),
                            "kind": "revision"})
        written = {str(h.get("date")) for h in rep.get("held", [])}
        for o in obs:
            o["accepted"] = bool(rep.get("written")) and o["date"].replace("-", "") not in written
        line = {"download_id": download_id, "run_id": self.run_id, "job": self.job, "file": fname,
                "query_utc": datetime.fromtimestamp(self.now(), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "query_utc_meaning": "instante de la fusión, inmediatamente después de la descarga",
                "ok": True, "src_max": _iso(src.max_date), "repo_max_before": _iso(repo.max_date) if repo else None,
                "status": rep.get("status"), "wrote": bool(rep.get("written")), "obs": obs[-50:],
                "obs_truncated": len(obs) > 50,
                "responses": [{"url": x.get("url"), "status": x.get("status"), "date": x.get("date_header"),
                               "last_modified": x.get("last_modified"),
                               "meaning": "metadatos HTTP del recurso; no son la hora de publicación de cada fila"}
                              for x in self.requests_log if x.get("cls") == g8http.OK]}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")

    # ── cierre ───────────────────────────────────────────────────────────────
    def finish(self, rc):
        """Registra la ejecución y devuelve el código de salida: 1 si hubo descarga fallida o inválida."""
        bad = [f for f, v in self.files.items() if v.get("status") in (S.INVALID, S.REGRESSION_BLOCKED)]
        rec = {"run_id": self.run_id, "job": self.job, "executor": EXECUTOR,
               "started_utc": self.started.strftime("%Y-%m-%dT%H:%M:%SZ"),
               "finished_utc": datetime.fromtimestamp(self.now(), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "rc": 1 if (rc or bad) else 0, "requests": self.requests_log, "files": self.files,
               "not_before": self.not_before, "errors": self.errors,
               "step": os.environ.get("G8_STEP_NAME"), "gh_run_id": os.environ.get("GITHUB_RUN_ID")}
        # última descarga correcta (se arrastra entre ejecuciones): la vigilancia detecta fallos o aplazamientos
        # que se repiten día tras día aunque cada ejecución, por sí sola, parezca una espera prevista
        rec["last_ok_utc"] = rec["finished_utc"] if rec["rc"] == 0 else self.latest.get("last_ok_utc")
        # inicio ESTABLE del periodo sin éxito (R3-3): no se reinicia en cada intento. Transición desde registros
        # anteriores sin el campo: si la ejecución previa ya había fallado, cuenta desde su inicio.
        prev = self.latest or {}
        if rec["rc"] == 0:
            rec["failing_since_utc"] = None
        elif prev.get("rc"):
            rec["failing_since_utc"] = prev.get("failing_since_utc") or prev.get("started_utc") or rec["started_utc"]
        else:
            rec["failing_since_utc"] = rec["started_utc"]
        runs = os.path.join(self.root, runlog.RUNS_DIR, EXECUTOR, self.job, self.started.strftime("%Y-%m") + ".jsonl")
        os.makedirs(os.path.dirname(runs), exist_ok=True)
        with open(runs, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n")
        S.write_atomic(self.latest_path, runlog.dumps(rec))
        for f, v in sorted(self.files.items()):
            print("[ingest] %s: %s %s" % (f, v.get("status"), v.get("detail", "")))
            if v.get("status") == S.HELD:
                # visible en la interfaz de Actions; el aviso por Telegram lo emite ingest_watch (hallazgo #4)
                print("::warning title=%s en confirmación::%s" % (f, v.get("detail", "")))
        return rec["rc"]


def boj_status(body, headers):
    """validate= de la API del BoJ: el error va en el JSON con HTTP 200 (manual oficial: 503 = error de base de datos)."""
    try:
        st = json.loads(body.decode("utf-8")).get("STATUS")
    except (ValueError, AttributeError):
        return g8http.FAIL_INVALID, "respuesta del BoJ no es JSON"
    if st == 200:
        return None
    if st in (500, 503):
        return g8http.FAIL_TRANSIENT, "BoJ STATUS %s" % st
    return g8http.FAIL_INVALID, "BoJ STATUS %s" % st


def _sleep_for(now):
    """En producción, time.sleep. Con un reloj simulado (pruebas), avanza ese reloj."""
    if now is time.time:
        return time.sleep
    return getattr(now, "sleep", time.sleep)

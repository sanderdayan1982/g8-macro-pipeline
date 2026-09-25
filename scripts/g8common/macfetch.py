"""macfetch — contexto de descarga de los descargadores del Mac (lote 3B). Solo stdlib + g8common; curl_cffi opcional.

Los descargadores del Mac (fetch_nzd_b2, fetch_chf_snb, fetch_tona_mac) siguen escribiendo sus CSV locales en
./data (el publicador push_nzd_to_github.py los fusiona contra la rama con validación y cuarentena). Este módulo
les da, sin cambiar fuentes ni frecuencias:
  · HTTP con la política común (g8http): intento + 3 reintentos 10/40/90 s solo en fallos transitorios,
    Retry-After respetado y persistido por ámbito (data/_ingest/not_before/mac.json, local), presupuesto de
    tiempo por ejecución, 4xx sin reintento (un 403 del WAF no se reintenta, como antes);
  · el MISMO transporte que usaban: curl_cffi imitando Safari/Chrome y, si no, requests (TLS verificado);
  · escritura ATÓMICA y validada por fichero: una serie vacía, ilegible o con fechas imposibles NO se
    escribe (se conserva la anterior) y se registra; las fechas de la copia local que la descarga no trae se
    conservan (unión monótona); las demás series del mismo descargador se escriben igualmente;
  · registro de la ejecución en state/fetch_<clave>.json (código, peticiones, errores, ficheros) que el
    publicador adjunta al latido: el aviso de Actions (ingest_watch) incluye el motivo.
"""
import json
import os
import sys
import time
import urllib.parse

from . import defer, g8http, runlog, series as S

IMPERSONATIONS = ("safari17_0", "chrome124", "chrome")
DEFAULT_BUDGET_S = 900
TEST_TRANSPORT = None      # pruebas: transporte local (respuestas grabadas)
TEST_CLOCK = None          # pruebas: reloj simulado (sin esperas reales)


class FetchError(RuntimeError):
    def __init__(self, res):
        RuntimeError.__init__(self, "%s: %s (%s)" % (res.cls, res.detail, res.url))
        self.cls = res.cls
        self.result = res


MIN_ATTEMPT_S = 1.0        # no se inicia una alternativa con menos de este tiempo restante


def _hdrs(r):
    return {k.lower(): v for k, v in r.headers.items()}


def _next_profile_helps(status, headers):
    """¿Tiene sentido probar OTRO perfil de navegador tras esta respuesta? Solo ante un bloqueo de acceso sin
    plazo (WAF: 401/403/406/451 sin Retry-After): para eso existen los perfiles. Un límite de uso (429, o
    cualquier respuesta con Retry-After/x-ratelimit), un error de servidor o una redirección vuelven a g8http, que
    la clasifica, respeta y persiste el plazo, reintenta con su programa o sigue la redirección con el mismo
    presupuesto (B3-2)."""
    if headers.get("retry-after") or headers.get("x-ratelimit-reset"):
        return False
    return status in (401, 403, 406, 451)


def _guarded_session(requests, guard):
    """Session de requests cuyas conexiones quedan registradas en el guardián de plazo (B3R1-1).
    `timeout=t` de requests limita cada espera de conexión o lectura, no la duración total de la descarga; al
    registrar el socket de cada conexión, g8http.SocketDeadline puede cortarla en el plazo compartido.
    El socket se vigila desde que existe la conexión TCP (HTTPConnection._new_conn), de modo que el plazo cubre
    también el túnel CONNECT de un proxy HTTP y el saludo TLS (B3R2-1). Usa atributos presentes y probados en
    urllib3 1.26 y 2.x (ConnectionCls, pool_classes_by_scheme, _new_conn)."""
    from urllib3 import connection, connectionpool

    def registering(base):
        class Conn(base):
            def _new_conn(self):                        # socket TCP recién abierto: antes de CONNECT y TLS (B3R2-1)
                return guard.watch_new_socket(base._new_conn(self))
        return Conn

    class Pool(connectionpool.HTTPConnectionPool):
        ConnectionCls = registering(connection.HTTPConnection)

    class PoolS(connectionpool.HTTPSConnectionPool):
        ConnectionCls = registering(connection.HTTPSConnection)

    pools = {"http": Pool, "https": PoolS}

    class Adapter(requests.adapters.HTTPAdapter):
        def init_poolmanager(self, *a, **kw):
            requests.adapters.HTTPAdapter.init_poolmanager(self, *a, **kw)
            self.poolmanager.pool_classes_by_scheme = pools

        def proxy_manager_for(self, proxy, **kw):
            m = requests.adapters.HTTPAdapter.proxy_manager_for(self, proxy, **kw)
            if not proxy.lower().startswith("socks"):
                m.pool_classes_by_scheme = pools
            return m

    sess = requests.Session()
    adapter = Adapter(max_retries=0)                    # sin reintentos propios: los hace g8http
    sess.mount("http://", adapter)
    sess.mount("https://", adapter)
    return sess


def _requests_with_deadline(requests, rem, method, url, headers, body, t):
    """Una petición requests que no puede durar más de `rem` s de reloj real (B3R1-1): al vencer se cortan sus
    sockets, se liberan (Session cerrada) y el resultado es NetError timeout, nunca una respuesta «OK»."""
    guard = g8http.SocketDeadline(rem)
    sess = None
    try:
        sess = _guarded_session(requests, guard)
        r = sess.request(method, url, headers=headers, data=body, timeout=t, allow_redirects=False)
        guard.cancel()
        if guard.expired:
            raise g8http.NetError("timeout", "plazo total agotado: descarga interrumpida")
        return r
    except g8http.NetError:
        raise
    except Exception as e:                              # noqa: BLE001
        if guard.expired:
            raise g8http.NetError("timeout", "plazo total agotado: descarga interrumpida")
        raise e
    finally:
        guard.cancel()
        if sess is not None:
            sess.close()


def curl_transport(log=print, impersonations=IMPERSONATIONS, now=time.time):
    """Transporte g8http: curl_cffi imitando Safari → Chrome124 → Chrome y, por último, requests (el orden del
    transporte original de los descargadores del Mac).

      · La primera respuesta 200 gana. Se pasa al perfil siguiente SOLO si la respuesta es un bloqueo de acceso
        sin plazo o un error de red; cualquier otra respuesta (429/503 con Retry-After, 5xx, 3xx, 404…) se
        devuelve de inmediato a g8http, que la trata con la política común. Así un Retry-After nunca queda oculto
        tras la respuesta de otro perfil (B3-2).
      · UN solo plazo para toda la cadena: deadline = inicio + total_timeout (lo que g8http concede del
        presupuesto). Cada alternativa recibe solo el tiempo restante y no se inicia ninguna con menos de
        MIN_ATTEMPT_S (B3-3). Las bibliotecas no siguen redirecciones (allow_redirects=False): las sigue g8http
        dentro del mismo presupuesto.
      · El plazo es EFECTIVO en cada alternativa (B3R1-1): curl_cffi con timeout escalar fija el TIMEOUT_MS de
        libcurl (duración total de la transferencia); requests y la biblioteca estándar quedan bajo
        g8http.SocketDeadline, que corta la conexión al vencer. Una respuesta cortada nunca se da por buena.
    Sin curl_cffi, directamente requests; sin requests, el transporte estándar de g8http."""
    try:
        from curl_cffi import requests as crequests
    except Exception:                                   # noqa: BLE001
        crequests = None

    def transport(method, url, headers, body, connect_timeout, read_timeout, total_timeout):
        deadline = now() + float(total_timeout)
        last, last_err = None, None

        def remaining():
            return deadline - now()

        def attempt(label, fn):
            nonlocal last, last_err
            rem = remaining()
            if rem < MIN_ATTEMPT_S:
                log("    %s: sin tiempo restante (%.1f s), no se inicia" % (label, rem))
                return None
            try:
                r = fn(max(MIN_ATTEMPT_S, min(float(read_timeout), rem)))
            except Exception as e:                      # noqa: BLE001  (error de red: se prueba la alternativa)
                last_err = e
                log("    %s error: %s" % (label, str(e)[:160]))
                return None
            last = (r.status_code, _hdrs(r), r.content or b"")
            log("    %s → HTTP %s (%s B)" % (label, r.status_code, len(last[2])))
            if r.status_code == 200 or not _next_profile_helps(r.status_code, last[1]):
                return last
            return None

        if crequests is not None:
            for imp in impersonations:
                res = attempt("curl_cffi %s" % imp, lambda t, imp=imp: crequests.request(
                    method, url, headers=headers, data=body, timeout=t, impersonate=imp, allow_redirects=False))
                if res is not None:
                    return res
        try:
            import requests
        except ImportError:
            requests = None
        if requests is not None:
            res = attempt("requests", lambda t: _requests_with_deadline(requests, remaining(), method, url, headers,
                                                                        body, t))
            if res is not None:
                return res
        elif remaining() >= MIN_ATTEMPT_S and last is None:
            rem = remaining()
            return g8http.default_transport(method, url, headers, body, min(connect_timeout, rem),
                                            min(read_timeout, rem), rem)
        if last is not None:
            return last                                 # p. ej. 403 en todos los perfiles → FAIL_ACCESS
        e = last_err
        if isinstance(e, g8http.NetError):
            raise e
        kind = "timeout" if e is None or "timeout" in type(e).__name__.lower() or "timed out" in str(e).lower() else "other"
        raise g8http.NetError(kind, str(e)[:200] if e else "plazo agotado antes de completar la cadena de transportes")
    return transport


class Fetch(object):
    def __init__(self, key, here, budget_s=DEFAULT_BUDGET_S, transport=None, now=None, sleep=None, log=print):
        now = now or TEST_CLOCK or time.time
        sleep = sleep or getattr(now, "sleep", None) or time.sleep
        transport = transport or TEST_TRANSPORT
        self.key = key
        self.here = here
        self.now, self.sleep, self.log = now, sleep, log
        self.started = now()
        self.budget = g8http.Budget(budget_s, now=now, env={})
        self.transport = transport or curl_transport(log=log, now=now)
        self.store = defer.Store(os.path.join(here, defer.REL_DIR), now=now, job="mac")
        self.requests_log, self.errors, self.files = [], [], {}

    # ── HTTP ────────────────────────────────────────────────────────────────
    def get(self, url, headers=None, params=None, timeout=90, validate=None, provider="generic"):
        if params:
            url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        scope = defer.scope_for(url)
        res = g8http.fetch(url, provider=provider, headers=headers or {}, budget=self.budget, read_timeout=timeout,
                           connect_timeout=min(10, timeout), validate=validate, not_before=self.store.get(scope),
                           transport=self.transport, now=self.now, sleep=self.sleep,
                           log=lambda m: self.log("    " + m))
        self.requests_log.append(res.record())
        if res.not_before and res.attempts:
            self.store.set(scope, res.not_before, source=res.url)
        if not res.ok:
            raise FetchError(res)
        return res.body

    # ── escritura validada y atómica ────────────────────────────────────────
    def write(self, path, data, expect_header=None, value_col=None):
        """Escribe `data` (bytes) en `path` de forma ATÓMICA si es una serie válida (P6 local):
          · inválida (vacía, ilegible, fechas imposibles, cabecera inesperada) → no se escribe, queda registrada;
          · fechas de la copia actual que la descarga ya no trae (historia empalmada que falló, respuesta
            corta) → se CONSERVAN: unión monótona, la descarga manda en las fechas que sí trae.
        → True si se escribió; False si se rechazó (el fichero anterior sigue intacto)."""
        name = os.path.basename(path)
        try:
            new = S.parse(data, value_col=value_col, expect_header=expect_header)
        except S.SeriesError as e:
            return self._reject(name, "serie inválida: %s" % e)
        out, kept = data, 0
        if os.path.exists(path):
            try:
                with open(path, "rb") as fh:
                    old = S.parse(fh.read(), value_col=value_col)
                missing = {d: old.rows[d] for d in old.rows if d not in new.rows}
                if missing:
                    rows = dict(missing)
                    rows.update(new.rows)
                    out = S.Series(new.comments or old.comments, new.header, rows, new.eol, new.value_col,
                                   new.trailing_eol).to_bytes()
                    kept = len(missing)
            except (OSError, S.SeriesError):
                pass                                    # copia actual ilegible: se sustituye por una válida
        S.write_atomic(path, out)
        # B3-4: fecha máxima de la colección completa de fechas escritas (no max() de una cadena)
        self.files[name] = {"status": "WRITTEN", "rows": len(new.rows) + kept,
                            "max_date": max([new.max_date] + ([max(missing)] if kept else [])),
                            "kept_from_previous": kept}
        if kept:
            self.log("[%s] %s: se conservan %d fechas de la copia anterior que la descarga no trae" % (self.key, name, kept))
        return True

    def _reject(self, name, why):
        self.files[name] = {"status": "REJECTED", "detail": why}
        self.error("%s: %s — se conserva la copia anterior" % (name, why))
        return False

    def error(self, msg):
        self.errors.append(str(msg)[:300])
        self.log("[%s] ERROR %s" % (self.key, msg))

    # ── cierre ──────────────────────────────────────────────────────────────
    def finish(self, rc):
        rc = 1 if (rc == 0 and self.errors) else rc
        rec = {"key": self.key, "rc": rc, "started_utc": _utc(self.started), "finished_utc": _utc(self.now()),
               "requests": self.requests_log, "errors": self.errors, "files": self.files}
        try:
            S.write_atomic(os.path.join(self.here, "state", "fetch_%s.json" % self.key), runlog.dumps(rec))
        except OSError as e:                            # el registro nunca tumba la descarga
            sys.stderr.write("[%s] no se pudo guardar el registro: %s\n" % (self.key, e))
        return rc


def _utc(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))

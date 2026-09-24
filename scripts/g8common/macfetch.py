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


def curl_transport(log=print, impersonations=IMPERSONATIONS):
    """Transporte g8http: curl_cffi con cada imitación de navegador; la primera respuesta 200 gana; si ninguna
    da 200 (o curl_cffi no está), requests. Devuelve la ÚLTIMA respuesta obtenida para que g8http la clasifique
    (403 → FAIL_ACCESS sin reintento; 503 → transitorio, etc.)."""
    try:
        from curl_cffi import requests as crequests
    except Exception:                                   # noqa: BLE001
        crequests = None

    def transport(method, url, headers, body, connect_timeout, read_timeout, total_timeout):
        timeout = max(1.0, min(read_timeout, total_timeout))
        last = None
        if crequests is not None:
            for imp in impersonations:
                try:
                    r = crequests.request(method, url, headers=headers, data=body, timeout=timeout, impersonate=imp)
                    log("    curl_cffi %s → HTTP %s (%s B)" % (imp, r.status_code, len(r.content or b"")))
                    last = (r.status_code, {k.lower(): v for k, v in r.headers.items()}, r.content or b"")
                    if r.status_code == 200:
                        return last
                except Exception as e:                  # noqa: BLE001
                    log("    curl_cffi %s error: %s" % (imp, str(e)[:160]))
        try:
            import requests
            r = requests.request(method, url, headers=headers, data=body, timeout=timeout)
            log("    requests → HTTP %s" % r.status_code)
            return r.status_code, {k.lower(): v for k, v in r.headers.items()}, r.content or b""
        except ImportError:
            if last is not None:
                return last
            return g8http.default_transport(method, url, headers, body, connect_timeout, read_timeout, total_timeout)
        except Exception as e:                          # noqa: BLE001
            if last is not None:
                return last
            kind = "timeout" if "timeout" in type(e).__name__.lower() or "timed out" in str(e).lower() else "other"
            raise g8http.NetError(kind, str(e)[:200])
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
        self.transport = transport or curl_transport(log=log)
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
        self.files[name] = {"status": "WRITTEN", "rows": len(new.rows) + kept, "max_date": max(new.max_date, *(
            [max(missing)] if kept else [])), "kept_from_previous": kept}
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

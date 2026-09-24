"""legacy — puente mínimo para los scripts que tenían su propio `_http_get` (F7, 24-sep-2026).

Sustituye el cuerpo de esos `_http_get` por g8http (misma política para todo el pipeline):
  · no reintenta errores 4xx (antes se reintentaban tres veces, con espera incluso tras el último intento);
  · respeta Retry-After y el presupuesto del script / plazo global del job;
  · TLS siempre verificado (el antiguo «reintento sin verificación» solo con G8_ALLOW_INSECURE_TLS=1, manual);
  · clasifica el rechazo de la clave de FRED (FAIL_AUTH) para que el llamador use el endpoint sin clave.
Devuelve el texto (mismo contrato que el `_http_get` original) o lanza LegacyHTTPError (subclase de
RuntimeError, que es lo que los llamadores ya capturaban).
"""
import time

from . import g8http

TEST_TRANSPORT = None      # pruebas: transporte local
TEST_CLOCK = None          # pruebas: reloj simulado (sin esperas reales)


class LegacyHTTPError(RuntimeError):
    def __init__(self, res):
        RuntimeError.__init__(self, "HTTP %s tras %d intento(s): %s :: %s" % (res.cls, len(res.attempts), res.url, res.detail))
        self.cls = res.cls
        self.result = res


_BUDGETS = {}


def budget(name, seconds):
    if name not in _BUDGETS or TEST_CLOCK is not None:
        _BUDGETS[name] = g8http.Budget(seconds, now=TEST_CLOCK or time.time)
    return _BUDGETS[name]


def _clock():
    if TEST_CLOCK is not None:
        return TEST_CLOCK, getattr(TEST_CLOCK, "sleep", time.sleep)
    return time.time, time.sleep


def http_get_text(url, timeout=90, headers=None, budget_obj=None, log=print):
    provider = "fred_api" if "api.stlouisfed.org" in url else "generic"
    now, sleep = _clock()
    res = g8http.fetch(url, provider=provider, headers=headers or {}, budget=budget_obj, read_timeout=timeout,
                       connect_timeout=min(10, timeout), transport=TEST_TRANSPORT, now=now, sleep=sleep,
                       log=(lambda m: log("    " + m)) if log else None)
    if not res.ok:
        raise LegacyHTTPError(res)
    return res.body.decode("utf-8", errors="replace")


def http_get_bytes(url, timeout=90, headers=None, budget_obj=None, log=print):
    now, sleep = _clock()
    res = g8http.fetch(url, provider="generic", headers=headers or {}, budget=budget_obj, read_timeout=timeout,
                       connect_timeout=min(10, timeout), transport=TEST_TRANSPORT, now=now, sleep=sleep,
                       log=(lambda m: log("    " + m)) if log else None)
    if not res.ok:
        raise LegacyHTTPError(res)
    return res.body

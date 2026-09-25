"""g8http — cliente HTTP común del pipeline G8 (stdlib, Python ≥3.9).

Reglas (plan v1.1 §4, autorizado 24-sep-2026):
  · Intento inicial + hasta 3 reintentos SOLO para fallos transitorios; esperas de referencia 10/40/90 s.
  · Retry-After (segundos o fecha HTTP; x-ratelimit-reset de GitHub; retry_after de Telegram) se
    respeta: la espera efectiva es max(Retry-After, programa). NUNCA se reintenta antes. Si la espera
    no cabe en el presupuesto → DEFERRED con not_before (la siguiente pasada no llama antes).
  · Tiempo máximo de conexión y de lectura separados + plazo total por petición (lectura por trozos).
  · Presupuesto = min(presupuesto local, plazo global del job G8_JOB_DEADLINE_EPOCH − G8_JOB_RESERVE_S).
  · Clasificación por respuesta Y proveedor: un 403 de un WAF no es un error de credenciales.
  · Solo esta capa reintenta (sin anidamiento). Las escrituras (POST/PATCH) no se reintentan salvo que el
    llamador lo pida explícitamente con retry_writes=True para operaciones idempotentes.
  · Las URL se redactan (api_key, token, bot<token>) antes de cualquier registro.
"""
import email.utils
import http.client
import json
import os
import re
import socket
import ssl
import threading
import time
import urllib.parse

OK = "OK"
NO_PUBLICATION = "NO_PUBLICATION"      # 404 en una URL por fecha: la fuente aún no publicó ese día
DEFERRED = "DEFERRED"                  # no se intentó (not_before vigente, sin presupuesto o Retry-After > presupuesto)
FAIL_TRANSIENT = "FAIL_TRANSIENT"
FAIL_AUTH = "FAIL_AUTH"
FAIL_PERMISSION = "FAIL_PERMISSION"
FAIL_ACCESS = "FAIL_ACCESS"            # WAF / bloqueo del proveedor — NO es credencial
FAIL_TLS = "FAIL_TLS"                  # verificación de certificado fallida — nunca se desactiva
FAIL_INVALID = "FAIL_INVALID"
CONFLICT = "CONFLICT"                  # GitHub 409/422 (p. ej. actualización de rama no fast-forward)
_RATE = "_RATE"                        # interno: límite de uso con o sin Retry-After

RETRY_DELAYS = (10, 40, 90)
MAX_REDIRECTS = 5                      # saltos por intento (hallazgo #6)
REDIRECT_CODES = {301, 302, 303, 307, 308}
# Cabeceras que NUNCA se reenvían a otro origen (esquema, host o puerto distintos) al seguir una redirección
SENSITIVE_HEADERS = {"authorization", "proxy-authorization", "cookie", "x-api-key", "api-key", "x-auth-token"}
MAX_ATTEMPTS = 4
TRANSIENT_HTTP = {408, 425, 500, 502, 503, 504}
GITHUB_SECONDARY_DEFAULT_WAIT = 60     # sin cabeceras, GitHub indica esperar al menos un minuto (verificar en su doc)
_SECRET_QS = {"api_key", "key", "token", "access_token", "apikey"}


class NetError(Exception):
    def __init__(self, kind, msg=""):
        Exception.__init__(self, "%s: %s" % (kind, msg))
        self.kind = kind


class Budget(object):
    """Presupuesto de tiempo de un script. remaining() nunca supera el plazo global del job."""

    def __init__(self, seconds, now=time.time, env=None):
        env = os.environ if env is None else env
        self._now = now
        self.local_deadline = now() + float(seconds)
        dl = env.get("G8_JOB_DEADLINE_EPOCH")
        rs = float(env.get("G8_JOB_RESERVE_S", "0") or 0)
        self.job_deadline = (float(dl) - rs) if dl else None

    def remaining(self):
        r = self.local_deadline - self._now()
        if self.job_deadline is not None:
            r = min(r, self.job_deadline - self._now())
        return max(0.0, r)


class Result(object):
    def __init__(self, cls, url, status=None, headers=None, body=None, attempts=None, not_before=None, detail=""):
        self.cls = cls
        self.url = url                      # ya redactada
        self.status = status
        self.headers = headers or {}
        self.body = body
        self.attempts = attempts or []
        self.not_before = not_before
        self.detail = detail
        self.redirects = []                 # cadena de redirecciones seguidas (URL redactadas)

    @property
    def ok(self):
        return self.cls == OK

    def record(self):
        """Resumen serializable SIN cuerpo ni cabeceras sensibles."""
        return {"cls": self.cls, "url": self.url, "status": self.status, "attempts": self.attempts,
                "not_before_epoch": self.not_before, "detail": self.detail[:300], "redirects": self.redirects,
                "date_header": self.headers.get("date"), "last_modified": self.headers.get("last-modified")}


def redact(url):
    try:
        u = urllib.parse.urlsplit(url)
        q = urllib.parse.parse_qsl(u.query, keep_blank_values=True)
        q = [(k, "***" if k.lower() in _SECRET_QS else v) for k, v in q]
        path = re.sub(r"/bot[^/]+", "/bot***", u.path)
        return urllib.parse.urlunsplit((u.scheme, u.netloc, path, urllib.parse.urlencode(q, safe="*:,"), ""))
    except Exception:                                   # noqa: BLE001
        return "<url no redactable>"


def parse_retry_after(headers, now=time.time):
    """Segundos a esperar según Retry-After (entero o fecha HTTP) o x-ratelimit-reset (GitHub). None si no hay."""
    ra = headers.get("retry-after")
    if ra:
        ra = ra.strip()
        if ra.isdigit():
            return float(ra)
        try:
            t = email.utils.parsedate_to_datetime(ra).timestamp()
            base = now()
            d = headers.get("date")
            if d:
                try:
                    base = email.utils.parsedate_to_datetime(d).timestamp()
                except Exception:                   # noqa: BLE001
                    pass
            return max(0.0, t - base)
        except Exception:                           # noqa: BLE001
            return None
    if headers.get("x-ratelimit-remaining") == "0" and headers.get("x-ratelimit-reset", "").isdigit():
        return max(0.0, float(headers["x-ratelimit-reset"]) - now())
    return None


def classify(provider, status, headers, body, not_found_is_no_publication=False, now=time.time):
    """→ (clase, retry_after_s | None, detalle). Tabla del plan v1.1 §4.1."""
    body_l = (body or b"")[:4000].lower()
    ra = parse_retry_after(headers, now)
    if 200 <= status < 300:
        return OK, None, ""
    if provider == "github":
        if status == 401:
            return FAIL_AUTH, None, "401 GitHub: token inválido, caducado o revocado"
        if status in (403, 429):
            if headers.get("x-ratelimit-remaining") == "0" or ra is not None:
                return _RATE, ra, "límite de uso de GitHub"
            if b"secondary rate limit" in body_l:
                return _RATE, float(GITHUB_SECONDARY_DEFAULT_WAIT), "límite secundario de GitHub"
            return FAIL_PERMISSION, None, "403 GitHub: el token no tiene el permiso necesario"
        if status in (409, 422):
            return CONFLICT, None, "GitHub %s: %s" % (status, body_l[:160].decode("utf-8", "replace"))
        if status == 404:
            return FAIL_INVALID, None, "404 GitHub: recurso inexistente o sin acceso"
    if provider == "fred_api" and status in (400, 401, 403) and b"api_key" in body_l:
        return FAIL_AUTH, None, "FRED rechaza la clave de API"
    if provider == "telegram":
        if status == 429:
            try:
                ra = float(json.loads(body.decode("utf-8"))["parameters"]["retry_after"])
            except Exception:                       # noqa: BLE001
                pass
            return _RATE, ra, "límite de Telegram"
        if status == 401:
            return FAIL_AUTH, None, "401 Telegram: bot token inválido"
    if status == 401:
        return FAIL_AUTH, None, "401"
    if status == 403:
        if ra is not None:
            return _RATE, ra, "403 con Retry-After"
        return FAIL_ACCESS, None, "403 del proveedor (bloqueo de acceso, no credencial)"
    if status == 404:
        if not_found_is_no_publication:
            return NO_PUBLICATION, None, "404: sin publicación para esa fecha"
        return FAIL_INVALID, None, "404 en URL fija: endpoint cambiado"
    if status == 429:
        return _RATE, ra, "429"
    if status in TRANSIENT_HTTP:
        if ra is not None:
            return _RATE, ra, "%s con Retry-After" % status
        return FAIL_TRANSIENT, None, "HTTP %s" % status
    if 300 <= status < 400:
        return FAIL_INVALID, None, "redirección %s no seguida (sin Location válido, escritura o código no seguible)" % status
    return FAIL_INVALID, None, "HTTP %s" % status


def _shutdown(sock):
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except (OSError, ValueError):
        pass


class SocketDeadline(object):
    """Plazo EFECTIVO de una petición (B3R1-1, B3R2-1). Un timeout de socket limita cada espera de conexión o
    lectura, no la duración total: un servidor (o un proxy) que envía poco a poco puede alargar sin límite la
    negociación CONNECT, el saludo TLS, las cabeceras o el cuerpo. Al vencer el plazo, este guardián hace shutdown()
    de los sockets registrados, lo que despierta en el acto una lectura bloqueada (Linux y macOS) y corta la
    conexión; el llamador comprueba `expired` y nunca da por buena esa respuesta.

    Se registra un DUPLICADO del socket TCP en cuanto existe (watch_new_socket): cubre todo lo que ocurre después,
    incluido el túnel CONNECT y el saludo TLS, que se hacen dentro de connect() y convierten el socket original en
    otro objeto (wrap_socket lo «desprende»). shutdown() sobre el duplicado actúa sobre la misma conexión; el
    duplicado es propiedad del guardián y se cierra en cancel(), así que su descriptor no puede reutilizarse
    mientras el temporizador podría actuar. Reloj real (threading.Timer): se protege tiempo de pared."""

    def __init__(self, seconds):
        self.expired = False
        self._done = False
        self._socks = []                                # [(socket, propio)]
        self._lock = threading.Lock()
        self._timer = threading.Timer(max(0.0, float(seconds)), self._fire)
        self._timer.daemon = True
        self._timer.start()

    def _fire(self):
        with self._lock:
            if self._done:
                return
            self.expired = True
            for s, _ in self._socks:
                _shutdown(s)

    def register(self, sock, owned=False):
        with self._lock:
            if self._done:
                if owned:
                    sock.close()
                return
            self._socks.append((sock, owned))
            if self.expired:
                _shutdown(sock)

    def watch_new_socket(self, sock):
        """Para el socket TCP recién conectado, antes de CONNECT/TLS. Devuelve el mismo socket."""
        self.register(sock.dup(), owned=True)
        return sock

    def cancel(self):
        self._timer.cancel()
        with self._lock:
            self._done = True
            owned, self._socks = [s for s, o in self._socks if o], []
        for s in owned:
            try:
                s.close()
            except OSError:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.cancel()
        return False


def default_transport(method, url, headers, body, connect_timeout, read_timeout, total_timeout):
    """Una petición, sin reintentos. Conexión y lectura con tiempos separados y plazo total."""
    u = urllib.parse.urlsplit(url)
    path = (u.path or "/") + ("?" + u.query if u.query else "")
    if u.scheme == "https":
        ctx = ssl.create_default_context()
        if os.environ.get("G8_ALLOW_INSECURE_TLS") == "1":
            # SOLO para ejecuciones manuales en una red con proxy que re-firma TLS (p. ej. la red local en
            # Bata). Nunca se define en los workflows (lo comprueba tests/test_f7.py). Queda registrado.
            ctx = ssl._create_unverified_context()
            print("[g8http] AVISO: verificación TLS DESACTIVADA por G8_ALLOW_INSECURE_TLS=1 → %s" % redact(url))
        conn = http.client.HTTPSConnection(u.hostname, u.port, timeout=connect_timeout, context=ctx)
    elif u.scheme == "http":
        conn = http.client.HTTPConnection(u.hostname, u.port, timeout=connect_timeout)
    else:
        raise NetError("other", "esquema no soportado")
    deadline = time.time() + total_timeout if total_timeout else None
    guard = SocketDeadline(total_timeout) if total_timeout else None
    if guard is not None:                               # B3R2-1: vigilado desde el TCP, antes del saludo TLS
        create = conn._create_connection
        conn._create_connection = lambda *a, **k: guard.watch_new_socket(create(*a, **k))
    try:
        conn.connect()
        conn.sock.settimeout(read_timeout)
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        chunks = []
        while True:
            if deadline is not None and time.time() > deadline:
                raise NetError("timeout", "plazo total de la petición agotado")
            c = resp.read(65536)
            if not c:
                break
            chunks.append(c)
        if guard is not None:
            guard.cancel()
            if guard.expired:                                   # cortada por el plazo: nunca se da por buena
                raise NetError("timeout", "plazo total de la petición agotado (descarga interrumpida)")
        data = b"".join(chunks)
        hdrs = {k.lower(): v for k, v in resp.getheaders()}
        cl = hdrs.get("content-length")
        if cl and cl.isdigit() and method != "HEAD" and len(data) != int(cl):
            raise NetError("incomplete", "%d de %s bytes" % (len(data), cl))     # transferencia cortada
        return resp.status, hdrs, data
    except NetError:
        raise
    except Exception as e:                                      # noqa: BLE001
        if guard is not None and guard.expired:                 # cualquier error provocado por el corte = plazo
            raise NetError("timeout", "plazo total de la petición agotado (descarga interrumpida)")
        _classify_raise(e)
    finally:
        if guard is not None:
            guard.cancel()
        conn.close()


def _classify_raise(e):
    """Mismo orden y mismas clases que antes de B3R1-1; lo no previsto se propaga tal cual."""
    if isinstance(e, socket.timeout):
        raise NetError("timeout", str(e))
    if isinstance(e, ssl.SSLCertVerificationError):
        raise NetError("tls_verify", str(e)[:200])
    if isinstance(e, ssl.SSLError):
        raise NetError("tls", str(e)[:200])
    if isinstance(e, socket.gaierror):
        raise NetError("dns", str(e))
    if isinstance(e, http.client.IncompleteRead):
        raise NetError("incomplete", "%d bytes leídos" % len(e.partial))
    if isinstance(e, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
        raise NetError("reset", str(e))
    if isinstance(e, http.client.HTTPException):
        raise NetError("other", type(e).__name__)
    if isinstance(e, OSError):
        raise NetError("other", str(e)[:200])
    raise e


class _RedirectRefused(Exception):
    pass


def _origin(u):
    p = urllib.parse.urlsplit(u)
    port = p.port or {"https": 443, "http": 80}.get(p.scheme)
    return (p.scheme, (p.hostname or "").lower(), port)


def _request_following(transport, method, url, headers, body, connect_timeout, read_timeout, budget, chain):
    """Una petición + las redirecciones que devuelva (hallazgo #6). Reglas:
      · solo GET/HEAD siguen redirecciones (una escritura redirigida queda como FAIL_INVALID);
      · como máximo MAX_REDIRECTS saltos; un bucle (URL repetida) se corta;
      · nunca de https a http ni a otro esquema;
      · al cambiar de origen se retiran Authorization, cookies y claves de API de las cabeceras;
      · cada salto consume el MISMO presupuesto (plazo total del intento = lo que quede del presupuesto)."""
    seen = {url}
    hdrs_out = dict(headers or {})
    cur = url
    while True:
        rem = budget.remaining()
        if rem < 1:
            raise NetError("timeout", "presupuesto agotado durante las redirecciones")
        status, hdrs, data = transport(method, cur, hdrs_out, body, min(connect_timeout, rem), min(read_timeout, rem), rem)
        if status not in REDIRECT_CODES or method not in ("GET", "HEAD"):
            return status, hdrs, data, cur
        loc = (hdrs.get("location") or "").strip()
        if not loc:
            return status, hdrs, data, cur                          # 3xx sin Location → FAIL_INVALID al clasificar
        nxt = urllib.parse.urljoin(cur, loc)
        nscheme = urllib.parse.urlsplit(nxt).scheme
        if nscheme not in ("http", "https"):
            raise _RedirectRefused("redirección %s a esquema no permitido (%s)" % (status, nscheme))
        if urllib.parse.urlsplit(cur).scheme == "https" and nscheme != "https":
            raise _RedirectRefused("redirección %s de https a http rechazada: %s" % (status, redact(nxt)))
        if nxt in seen:
            raise _RedirectRefused("bucle de redirecciones en %s" % redact(nxt))
        if len(chain) >= MAX_REDIRECTS:
            raise _RedirectRefused("más de %d redirecciones" % MAX_REDIRECTS)
        if _origin(nxt) != _origin(cur):
            hdrs_out = {k: v for k, v in hdrs_out.items() if k.lower() not in SENSITIVE_HEADERS}
        chain.append({"status": status, "to": redact(nxt)})
        seen.add(nxt)
        cur = nxt


def fetch(url, provider="generic", method="GET", headers=None, body=None, budget=None,
          connect_timeout=10, read_timeout=60, not_found_is_no_publication=False, validate=None,
          not_before=None, transport=None, sleep=time.sleep, now=time.time, delays=RETRY_DELAYS,
          max_attempts=MAX_ATTEMPTS, retry_writes=False, log=None):
    """GET/POST con la política de reintentos del plan. validate(body, headers) → None | (clase, detalle)
    permite al descargador declarar INVALID/TRANSIENT (p. ej. STATUS 503 dentro del JSON del BoJ)."""
    transport = transport or default_transport
    budget = budget or Budget(300, now=now)
    safe = redact(url)
    attempts = []
    if not_before is not None and now() < not_before:
        return Result(DEFERRED, safe, not_before=not_before, detail="not_before vigente (Retry-After anterior)")
    if method not in ("GET", "HEAD") and not retry_writes:
        max_attempts = 1
    n = 0
    while True:
        n += 1
        rem = budget.remaining()
        if rem < 1:
            cls = DEFERRED if n == 1 else FAIL_TRANSIENT
            return Result(cls, safe, attempts=attempts, detail="presupuesto agotado")
        t0 = now()
        status, hdrs, data, ra = None, {}, None, None
        chain = []
        try:
            status, hdrs, data, final = _request_following(transport, method, url, headers or {}, body,
                                                           connect_timeout, read_timeout, budget, chain)
            cls, ra, detail = classify(provider, status, hdrs, data, not_found_is_no_publication, now)
            if cls == OK and validate is not None:
                v = validate(data, hdrs)
                if v:
                    cls, detail = v[0], v[1]
        except _RedirectRefused as e:
            cls, detail = FAIL_INVALID, str(e)[:200]
        except NetError as e:
            cls = FAIL_TLS if e.kind == "tls_verify" else FAIL_TRANSIENT
            detail = str(e)[:200]
        att = {"n": n, "status": status, "cls": cls, "secs": round(now() - t0, 2), "detail": detail[:160]}
        if chain:
            att["redirects"] = chain
        attempts.append(att)
        if log:
            log("[g8http] %s intento %d → %s %s%s" % (safe, n, status, cls, " (tras %d redirección/es)" % len(chain) if chain else ""))
        if cls not in (FAIL_TRANSIENT, _RATE):
            res = Result(cls, safe, status, hdrs, data, attempts, detail=detail)
            res.redirects = chain
            return res
        if n >= max_attempts:
            if cls == _RATE and ra is not None:
                return Result(DEFERRED, safe, status, hdrs, None, attempts, not_before=now() + ra,
                              detail="límite de uso persistente; no antes de Retry-After")
            return Result(FAIL_TRANSIENT, safe, status, hdrs, None, attempts, detail=detail)
        wait = float(delays[min(n - 1, len(delays) - 1)])
        if ra is not None:
            wait = max(wait, ra)
        if wait + 1 > budget.remaining():
            if ra is not None:
                return Result(DEFERRED, safe, status, hdrs, None, attempts, not_before=now() + ra,
                              detail="Retry-After %.0fs supera el presupuesto: aplazado" % ra)
            return Result(FAIL_TRANSIENT, safe, status, hdrs, None, attempts,
                          detail="presupuesto insuficiente para el siguiente reintento (%s)" % detail)
        sleep(wait)

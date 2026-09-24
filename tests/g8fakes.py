"""Dobles de prueba LOCALES para g8common (sin red externa).

FakeGitHub reproduce SOLO la semántica documentada que usamos de la API de datos Git:
blobs, árboles (base_tree + entradas), commits con padres, lectura de la rama y actualización de la
rama con force=false aceptada únicamente si es fast-forward (422 "Update is not a fast forward").
NO reproduce: réplicas con consistencia eventual, límites secundarios reales, tamaños máximos,
latencias, ni ninguna garantía que la API real no documente. Sirve para probar NUESTRA lógica;
la verificación contra la API real queda PENDIENTE-INSTALAR.
"""
import base64
import email.utils
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs


class Clock(object):
    def __init__(self, t0=1790000000.0):
        self.t = float(t0)

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept = getattr(self, "slept", []) + [s]
        self.t += s


class _Server(object):
    def __init__(self, handler_cls):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        self.httpd.owner = self
        self.port = self.httpd.server_address[1]
        self.th = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.th.start()

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.port

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class ScriptedHandler(BaseHTTPRequestHandler):
    """Responde según una lista de respuestas por ruta: (status, headers, body) o callables."""

    def log_message(self, *a):
        pass

    def _serve(self):
        owner = self.server.owner
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        owner.requests.append((self.command, self.path, dict(self.headers), body))
        seq = owner.script.get(urlsplit(self.path).path) or owner.script.get("*")
        if not seq:
            status, headers, data = 404, {}, b"no script"
        else:
            item = seq.pop(0) if len(seq) > 1 else seq[0]
            if callable(item):
                item = item(self)
            if item == "HANG":
                import time as _t
                _t.sleep(5)
                return
            if item == "TRUNCATE":
                self.send_response(200)
                self.send_header("Content-Length", "1000")
                self.end_headers()
                self.wfile.write(b"DATE,CLOSE\n2026")
                self.wfile.flush()
                self.connection.close()
                return
            status, headers, data = item
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PATCH = _serve


class ScriptedServer(_Server):
    def __init__(self, script):
        self.script = script
        self.requests = []
        _Server.__init__(self, ScriptedHandler)


# ── FakeGitHub ────────────────────────────────────────────────────────────────
def _sha(kind, payload):
    return hashlib.sha1((kind + ":" + payload).encode("utf-8") if isinstance(payload, str)
                        else kind.encode() + b":" + payload).hexdigest()


class GHHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, status, obj, extra=None):
        gh = self.server.owner
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Date", email.utils.formatdate(gh.clock(), usegmt=True))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle(self):
        gh = self.server.owner
        n = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(n) or b"{}") if n else {}
        u = urlsplit(self.path)
        p = u.path
        gh.calls.append((self.command, p))
        auth = self.headers.get("Authorization", "")
        if gh.fail_auth:
            return self._send(401, {"message": "Bad credentials"})
        if gh.rate_limit_once:
            gh.rate_limit_once -= 1
            return self._send(403, {"message": "API rate limit exceeded"},
                              {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(gh.clock()) + 30)})
        if self.command in ("POST", "PATCH") and gh.readonly_token and auth:
            return self._send(403, {"message": "Resource not accessible by personal access token"})
        if self.command in ("POST", "PATCH") and not auth:
            return self._send(401, {"message": "Requires authentication"})
        base = "/repos/%s/%s" % (gh.owner, gh.repo)
        if p == base:
            extra = {}
            if gh.token_expiration:
                extra["GitHub-Authentication-Token-Expiration"] = gh.token_expiration
            if gh.scopes is not None:
                extra["X-OAuth-Scopes"] = gh.scopes
            return self._send(200, {"full_name": "%s/%s" % (gh.owner, gh.repo)}, extra)
        if p == base + "/git/ref/heads/main":
            return self._send(200, {"object": {"sha": gh.ref}})
        if p.startswith(base + "/git/commits/") and self.command == "GET":
            c = gh.commits[p.rsplit("/", 1)[1]]
            return self._send(200, {"sha": p.rsplit("/", 1)[1], "tree": {"sha": c["tree"]}, "parents": [{"sha": x} for x in c["parents"]]})
        if p.startswith(base + "/git/trees/") and self.command == "GET":
            t = gh.trees[p.rsplit("/", 1)[1]]
            return self._send(200, {"tree": [{"path": k, "type": "blob", "sha": v} for k, v in sorted(t.items())], "truncated": False})
        if p.startswith(base + "/git/blobs/") and self.command == "GET":
            b = gh.blobs[p.rsplit("/", 1)[1]]
            return self._send(200, {"content": base64.b64encode(b).decode(), "encoding": "base64"})
        if p == base + "/git/blobs" and self.command == "POST":
            data = base64.b64decode(payload["content"])
            s = _sha("blob", data)
            gh.blobs[s] = data
            return self._send(201, {"sha": s})
        if p == base + "/git/trees" and self.command == "POST":
            t = dict(gh.trees[payload["base_tree"]])
            for e in payload["tree"]:
                t[e["path"]] = e["sha"]
            s = _sha("tree", json.dumps(t, sort_keys=True))
            gh.trees[s] = t
            return self._send(201, {"sha": s})
        if p == base + "/git/commits" and self.command == "POST":
            gh.seq += 1
            c = {"tree": payload["tree"], "parents": payload["parents"], "message": payload["message"], "seq": gh.seq}
            s = _sha("commit", json.dumps(c, sort_keys=True))
            gh.commits[s] = c
            return self._send(201, {"sha": s})
        if p == base + "/git/refs/heads/main" and self.command == "PATCH":
            if gh.before_patch:
                hook, gh.before_patch = gh.before_patch, None
                hook(gh)
            new = payload["sha"]
            if payload.get("force") or gh.is_ancestor(gh.ref, new):
                gh.ref = new
                gh.ref_log.append(new)
                return self._send(200, {"object": {"sha": new}})
            return self._send(422, {"message": "Update is not a fast forward"})
        return self._send(404, {"message": "Not Found"})

    do_GET = do_POST = do_PATCH = _handle


class FakeGitHub(_Server):
    def __init__(self, files=None, owner="o", repo="r", clock=None):
        self.owner, self.repo = owner, repo
        self.clock = clock or Clock()
        self.blobs, self.trees, self.commits = {}, {}, {}
        self.calls, self.ref_log = [], []
        self.fail_auth = False
        self.rate_limit_once = 0
        self.readonly_token = False
        self.token_expiration = None
        self.scopes = None
        self.before_patch = None
        self.seq = 0
        tree = {}
        for path, data in (files or {}).items():
            s = _sha("blob", data)
            self.blobs[s] = data
            tree[path] = s
        ts = _sha("tree", json.dumps(tree, sort_keys=True))
        self.trees[ts] = tree
        c = {"tree": ts, "parents": [], "message": "root", "seq": 0}
        cs = _sha("commit", json.dumps(c, sort_keys=True))
        self.commits[cs] = c
        self.ref = cs
        _Server.__init__(self, GHHandler)

    def is_ancestor(self, anc, desc):
        stack, seen = [desc], set()
        while stack:
            x = stack.pop()
            if x == anc:
                return True
            if x in seen or x not in self.commits:
                continue
            seen.add(x)
            stack.extend(self.commits[x]["parents"])
        return False

    def files(self, sha=None):
        t = self.trees[self.commits[sha or self.ref]["tree"]]
        return {p: self.blobs[s] for p, s in t.items()}

    def direct_commit(self, changes, message="concurrent"):
        """Otro escritor (p. ej. Actions) avanza la rama con sus propios ficheros."""
        t = dict(self.trees[self.commits[self.ref]["tree"]])
        for path, data in changes.items():
            s = _sha("blob", data)
            self.blobs[s] = data
            t[path] = s
        ts = _sha("tree", json.dumps(t, sort_keys=True))
        self.trees[ts] = t
        self.seq += 1
        c = {"tree": ts, "parents": [self.ref], "message": message, "seq": self.seq}
        cs = _sha("commit", json.dumps(c, sort_keys=True))
        self.commits[cs] = c
        self.ref = cs
        return cs

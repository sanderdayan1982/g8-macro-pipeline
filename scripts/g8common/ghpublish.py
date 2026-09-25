"""ghpublish — publicación conjunta de una familia de ficheros en GitHub (API de datos Git) y reservas.

Qué garantiza y qué NO (condición 3, 24-sep-2026):
  · GitHub valida en el servidor UNA cosa: al actualizar la rama con force=false, que el nuevo commit
    sea fast-forward de la cabeza actual. Como nuestro commit tiene como único padre la cabeza que
    leímos, si otra escritura movió la rama entretanto, la actualización se rechaza y no se publica
    NINGÚN fichero de la familia (todo o nada). Ref.: GitHub Docs, "Update a reference".
  · GitHub NO valida la reserva (lease) ni su caducidad: eso lo comprueba este cliente, con la hora del
    servidor (cabecera Date) leída junto a la cabeza. Entre esa lectura y la actualización de la rama
    hay una ventana de segundos:
      – si en esa ventana otro ejecutor toma la reserva, su commit mueve la rama y nuestra
        actualización deja de ser fast-forward → rechazada (el antiguo titular NO publica);
      – si la reserva caduca en esa ventana y nadie la toma, nuestra publicación se acepta unos
        segundos después de la caducidad nominal (y la renueva en el mismo commit). No hay un tercero
        perjudicado y la fusión monótona impide cualquier regresión de datos.
  · Límite conocido: fast-forward no es un compare-and-swap estricto sobre un sha. Si alguien
    RETROCEDIERA la rama (force-push a un antecesor), una actualización basada en una cabeza posterior
    podría aceptarse. En este repo nadie hace force-push; se documenta como supuesto.
  · Tras publicar se relee la rama y se registra la hora del servidor en que se OBSERVÓ el commit en
    ella. La fecha interna del commit no se usa como prueba de disponibilidad.
"""
import base64
import email.utils
import json
import time
from datetime import datetime, timedelta, timezone

from . import g8http

API = "https://api.github.com"
LEASE_DIR = "data/_ingest/leases"

PUBLISHED = "PUBLISHED"
NOOP = "NOOP"
LOST_LEASE = "LOST_LEASE"
CONFLICT_EXHAUSTED = "CONFLICT_EXHAUSTED"
ABORTED = "ABORTED"


class GitHubError(Exception):
    def __init__(self, cls, detail, result=None):
        Exception.__init__(self, "%s: %s" % (cls, detail))
        self.cls = cls
        self.detail = detail
        self.result = result


def _utc(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def server_time(headers, fallback=None):
    d = headers.get("date")
    if d:
        try:
            return email.utils.parsedate_to_datetime(d).astimezone(timezone.utc)
        except Exception:                                   # noqa: BLE001
            pass
    return fallback or datetime.now(timezone.utc)


class Outcome(object):
    def __init__(self, status, **kw):
        self.status = status
        self.commit = kw.get("commit")
        self.attempts = kw.get("attempts", 0)
        self.accepted_server_utc = kw.get("accepted_server_utc")      # Date de la respuesta que aceptó la rama
        self.observed_on_branch_utc = kw.get("observed_on_branch_utc")
        self.detail = kw.get("detail", "")
        self.info = kw.get("info")

    def record(self):
        return {"status": self.status, "commit": self.commit, "attempts": self.attempts,
                "accepted_server_utc": self.accepted_server_utc,
                "observed_on_branch_utc": self.observed_on_branch_utc, "detail": self.detail[:300]}


class Repo(object):
    def __init__(self, owner, repo, branch="main", token=None, api=API, transport=None, budget=None,
                 sleep=time.sleep, now=time.time, log=None):
        self.owner, self.repo, self.branch = owner, repo, branch
        self._token = token
        self.api = api.rstrip("/")
        self.transport = transport
        self.budget = budget or g8http.Budget(600, now=now)
        self.sleep, self.now, self.log = sleep, now, log
        self._blob_cache = {}

    # ── transporte ────────────────────────────────────────────────────────────
    def _call(self, method, path, payload=None, retry_writes=False):
        h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
             "User-Agent": "g8-publish"}
        if self._token:
            h["Authorization"] = "Bearer " + self._token
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            h["Content-Type"] = "application/json"
        res = g8http.fetch(self.api + path, provider="github", method=method, headers=h, body=body,
                           budget=self.budget, transport=self.transport, sleep=self.sleep, now=self.now,
                           retry_writes=retry_writes, read_timeout=120, log=self.log)
        if res.cls != g8http.OK:
            raise GitHubError(res.cls, res.detail, res)
        return (json.loads(res.body.decode("utf-8")) if res.body else {}), res.headers

    def _r(self, suffix):
        return "/repos/%s/%s%s" % (self.owner, self.repo, suffix)

    # ── lectura ───────────────────────────────────────────────────────────────
    def repo_info(self):
        return self._call("GET", self._r(""))

    def head(self):
        j, h = self._call("GET", self._r("/git/ref/heads/%s" % self.branch))
        return j["object"]["sha"], server_time(h)

    def commit_tree(self, sha):
        j, _ = self._call("GET", self._r("/git/commits/%s" % sha))
        return j["tree"]["sha"]

    def tree_index(self, tree_sha):
        j, _ = self._call("GET", self._r("/git/trees/%s?recursive=1" % tree_sha))
        if j.get("truncated"):
            raise GitHubError(g8http.FAIL_INVALID, "árbol truncado por GitHub: no se puede leer la familia completa")
        return {e["path"]: e["sha"] for e in j.get("tree", []) if e.get("type") == "blob"}

    def blob(self, sha):
        if sha in self._blob_cache:
            return self._blob_cache[sha]
        j, _ = self._call("GET", self._r("/git/blobs/%s" % sha))
        data = base64.b64decode(j["content"]) if j.get("encoding") == "base64" else j["content"].encode("utf-8")
        self._blob_cache[sha] = data
        return data

    def read(self, tree_sha, paths=(), prefixes=()):
        idx = self.tree_index(tree_sha)
        want = set(paths) | {p for p in idx if any(p.startswith(x) for x in prefixes)}
        return {p: (self.blob(idx[p]) if p in idx else None) for p in want}

    # ── escritura ─────────────────────────────────────────────────────────────
    def _commit(self, parent, base_tree, files, message):
        entries = []
        for path, data in sorted(files.items()):
            j, _ = self._call("POST", self._r("/git/blobs"),
                              {"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"},
                              retry_writes=True)                    # blob = contenido direccionado: idempotente
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": j["sha"]})
        t, _ = self._call("POST", self._r("/git/trees"), {"base_tree": base_tree, "tree": entries}, retry_writes=True)
        c, _ = self._call("POST", self._r("/git/commits"),
                          {"message": message, "tree": t["sha"], "parents": [parent]}, retry_writes=True)
        return c["sha"]

    def _update_ref(self, sha):
        """True si la rama aceptó el commit (fast-forward). False si se movió entretanto. Un solo intento."""
        try:
            _, h = self._call("PATCH", self._r("/git/refs/heads/%s" % self.branch), {"sha": sha, "force": False})
            return True, server_time(h)
        except GitHubError as e:
            if e.cls == g8http.CONFLICT:
                return False, None
            raise

    def publish(self, build, message, max_attempts=3, fence=None, lease_ttl_s=1200, dry=False):
        """build(current: dict path→bytes|None, head_sha) → (files: dict path→bytes, info). files vacío = nada.
        Lee siempre sobre la cabeza actual; si la rama se movió, relee y reconstruye (máx. max_attempts).
        fence = (familia, titular, epoch): exige esa reserva vigente en la cabeza leída y la renueva."""
        paths, prefixes = build.paths, getattr(build, "prefixes", ())
        for attempt in range(1, max_attempts + 1):
            head, srv_now = self.head()
            tree = self.commit_tree(head)
            want = list(paths)
            if fence:
                want.append(lease_path(fence[0]))
            current = self.read(tree, want, prefixes)
            if fence:
                ok, why = lease_holds(current.get(lease_path(fence[0])), fence[1], fence[2], srv_now)
                if not ok:
                    return Outcome(LOST_LEASE, attempts=attempt, detail=why)
            files, info = build(current, head)
            if not files:
                return Outcome(NOOP, attempts=attempt, info=info)
            files = dict(files)
            if dry:
                return Outcome("DRY", attempts=attempt, info=info,
                               detail="simulación: %d fichero(s) cambiarían: %s" % (len(files), ", ".join(sorted(files))[:250]))
            if fence:
                files[lease_path(fence[0])] = lease_bytes(fence[0], fence[1], fence[2], srv_now, lease_ttl_s)
            commit = self._commit(head, tree, files, message)
            accepted, t_acc = self._update_ref(commit)
            if accepted:
                observed = None
                try:
                    h2, t2 = self.head()
                    if h2 == commit:
                        observed = t2.strftime("%Y-%m-%dT%H:%M:%SZ")
                except GitHubError:
                    pass
                return Outcome(PUBLISHED, commit=commit, attempts=attempt,
                               accepted_server_utc=t_acc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                               observed_on_branch_utc=observed, info=info,
                               detail="" if observed else "commit aceptado; la relectura no lo mostró aún en la rama")
            if self.log:
                self.log("[ghpublish] la rama se movió; se relee y se reconstruye (intento %d)" % attempt)
        return Outcome(CONFLICT_EXHAUSTED, attempts=max_attempts, detail="la rama cambió en cada intento")


class Build(object):
    """Adaptador: build(current, head) con los caminos declarados."""

    def __init__(self, paths, fn, prefixes=()):
        self.paths = list(paths)
        self.prefixes = tuple(prefixes)
        self.fn = fn

    def __call__(self, current, head):
        return self.fn(current, head)


# ── reservas ──────────────────────────────────────────────────────────────────
def lease_path(family):
    return "%s/%s.json" % (LEASE_DIR, family)


def lease_bytes(family, holder, epoch, srv_now, ttl_s):
    rec = {"family": family, "holder": holder, "epoch": int(epoch),
           "renewed_server_utc": srv_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "expires_server_utc": (srv_now + timedelta(seconds=ttl_s)).strftime("%Y-%m-%dT%H:%M:%SZ")}
    return (json.dumps(rec, indent=1, sort_keys=True) + "\n").encode("utf-8")


def lease_holds(data, holder, epoch, srv_now):
    if data is None:
        return False, "no hay reserva en la rama"
    try:
        rec = json.loads(data.decode("utf-8"))
    except Exception:                                       # noqa: BLE001
        return False, "reserva ilegible"
    if rec.get("holder") != holder or int(rec.get("epoch", -1)) != int(epoch):
        return False, "la reserva es de %s (epoch %s); yo tenía epoch %s" % (rec.get("holder"), rec.get("epoch"), epoch)
    if _parse_utc(rec["expires_server_utc"]) <= srv_now:
        return False, "mi reserva caducó a las %s (hora del servidor %s)" % (rec["expires_server_utc"], srv_now.strftime("%H:%M:%S"))
    return True, ""


def acquire_lease(repo, family, holder, ttl_s=1200, max_attempts=3):
    """Toma (o renueva) la reserva con un commit propio. → (estado, registro). Estados: ACQUIRED, HELD_BY_OTHER."""
    for _ in range(max_attempts):
        head, srv_now = repo.head()
        tree = repo.commit_tree(head)
        cur = repo.read(tree, [lease_path(family)]).get(lease_path(family))
        epoch = 1
        if cur is not None:
            rec = json.loads(cur.decode("utf-8"))
            alive = _parse_utc(rec["expires_server_utc"]) > srv_now
            if alive and rec.get("holder") != holder:
                return "HELD_BY_OTHER", rec
            epoch = int(rec.get("epoch", 0)) + (0 if (alive and rec.get("holder") == holder) else 1)
        data = lease_bytes(family, holder, epoch, srv_now, ttl_s)
        commit = repo._commit(head, tree, {lease_path(family): data}, "lease %s → %s (epoch %d)" % (family, holder, epoch))
        ok, _ = repo._update_ref(commit)
        if ok:
            return "ACQUIRED", json.loads(data.decode("utf-8"))
    return "CONFLICT", None


# ── credenciales ──────────────────────────────────────────────────────────────
def parse_expiration(value):
    """Cabecera GitHub-Authentication-Token-Expiration → datetime UTC (formatos tolerados)."""
    if not value:
        return None
    v = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S UTC", "%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            d = datetime.strptime(v, fmt)
            return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def token_kind(token):
    if not token:
        return "none"
    if token.startswith("github_pat_"):
        return "fine-grained"
    if token.startswith("ghp_"):
        return "classic"
    return "unknown"


def check_token(repo, token, role="data"):
    """Valida el token contra el repo SIN escribir y SIN exponerlo. role=data: solo necesita escribir contenidos.
    → dict serializable (nunca contiene el token)."""
    out = {"kind": token_kind(token), "role": role, "valid": False, "expires_utc": None, "days_left": None,
           "scopes": None, "required_ok": None, "excess": [], "detail": ""}
    try:
        _, h = repo.repo_info()
    except GitHubError as e:
        out["detail"] = e.cls + ": " + e.detail
        out["cls"] = e.cls
        return out
    out["valid"] = True
    out["cls"] = g8http.OK
    srv = server_time(h)
    exp = parse_expiration(h.get("github-authentication-token-expiration"))
    if exp:
        out["expires_utc"] = exp.strftime("%Y-%m-%dT%H:%M:%SZ")
        out["days_left"] = round((exp - srv).total_seconds() / 86400.0, 2)
    else:
        out["detail"] = "GitHub no devolvió fecha de caducidad (token sin caducidad o cabecera ausente)"
    sc = h.get("x-oauth-scopes")
    if sc is not None:
        scopes = [s.strip() for s in sc.split(",") if s.strip()]
        out["scopes"] = scopes
        out["required_ok"] = ("public_repo" in scopes) or ("repo" in scopes)
        if role == "data":
            out["excess"] = [s for s in scopes if s in ("workflow", "repo", "admin:org", "delete_repo", "admin:repo_hook", "user")]
    else:
        out["detail"] = (out["detail"] + "; " if out["detail"] else "") + \
            "sin X-OAuth-Scopes (token fine-grained): el permiso de escritura se verifica al publicar el latido"
    return out

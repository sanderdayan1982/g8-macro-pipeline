#!/usr/bin/env python3
"""vps_git_push.py — commit+push acotado para jobs systemd del VPS (t334u).

Solo añade rutas de la lista permitida. Prefijo de commit [vps-push] y [skip netlify].
El token (G8_FULL) se pasa por el entorno, nunca se imprime ni se escribe en el repo.
No usa dispatch_prod ni TokenGuard.
"""
from __future__ import annotations

import base64
import os
import subprocess
import sys

SNB_ALLOWED = tuple("data/CHF_SPOT_%dY_DAILY.csv" % y for y in range(1, 31)) + (
    "data/CHF_CURVE_MONTH_END_CONTROL.csv",
    "data/CHF_CURVE_METH_BREAK.json",
)


class PushError(RuntimeError):
    pass


def _run(cmd, cwd, env=None, timeout=180):
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    # never echo env; redact token-looking strings from output
    out = (r.stdout or "") + (r.stderr or "")
    tok = (env or {}).get("G8_FULL") or os.environ.get("G8_FULL") or ""
    if tok:
        out = out.replace(tok, "<redacted>")
    if r.returncode != 0:
        raise PushError("git %s rc=%s: %s" % (cmd[:3], r.returncode, " ".join(out.split())[:400]))
    return r


def auth_env(token, base=None):
    env = dict(base or os.environ)
    if not token:
        raise PushError("G8_FULL ausente")
    # basic x-access-token:TOKEN — no se escribe en .git/config
    raw = base64.b64encode(("x-access-token:" + token).encode()).decode()
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "http.https://github.com/.extraheader"
    env["GIT_CONFIG_VALUE_0"] = "AUTHORIZATION: basic %s" % raw
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "true"
    return env


def porcelain(cwd):
    r = subprocess.run(["git", "status", "--porcelain", "-u"], cwd=cwd, capture_output=True, text=True, check=True)
    paths = []
    for line in r.stdout.splitlines():
        if not line:
            continue
        # status XY, then path (or 'old -> new')
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip().strip('"')
        paths.append(path)
    return paths


def commit_and_push(cwd, allowed, message, token, remote="origin", branch="main"):
    """Add only allowed dirty paths, commit if needed, pull --rebase, push.

    Returns dict: {changed: bool, sha: str|None, files: list, skipped: bool}
    Aborts if a dirty path is not in the allowed list.
    """
    if not message.startswith("[vps-push]"):
        raise PushError("el mensaje debe empezar por [vps-push]")
    if "[skip netlify]" not in message:
        raise PushError("el mensaje debe incluir [skip netlify]")
    dirty = porcelain(cwd)
    unexpected = [p for p in dirty if p not in allowed]
    if unexpected:
        raise PushError("rutas no permitidas sucias: %s" % ",".join(unexpected[:8]))
    change = [p for p in dirty if p in allowed]
    env = auth_env(token)
    if not change:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=cwd, text=True).strip()
        return {"changed": False, "sha": sha, "files": [], "skipped": True}
    _run(["git", "add", "--"] + change, cwd, env)
    # identity local al comando
    env_c = dict(env)
    env_c["GIT_AUTHOR_NAME"] = "g8-vps"
    env_c["GIT_AUTHOR_EMAIL"] = "g8-vps@users.noreply.github.com"
    env_c["GIT_COMMITTER_NAME"] = env_c["GIT_AUTHOR_NAME"]
    env_c["GIT_COMMITTER_EMAIL"] = env_c["GIT_AUTHOR_EMAIL"]
    _run(["git", "commit", "-m", message], cwd, env_c)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=cwd, text=True).strip()
    # integrar main y empujar; 5 intentos al estilo git_push_retry
    last = None
    for i in range(1, 6):
        try:
            _run(["git", "pull", "--rebase", "--autostash", "-X", "theirs", remote, branch], cwd, env, timeout=180)
            _run(["git", "push", remote, "HEAD:" + branch], cwd, env, timeout=180)
            last = None
            break
        except PushError as e:
            last = e
            subprocess.run(["git", "rebase", "--abort"], cwd=cwd, capture_output=True)
    if last:
        raise last
    return {"changed": True, "sha": sha, "files": change, "skipped": False}


def fetch_ff_only(cwd, token, remote="origin", branch="main"):
    env = auth_env(token)
    _run(["git", "fetch", "-q", remote, branch], cwd, env, timeout=180)
    _run(["git", "merge", "-q", "--ff-only", "%s/%s" % (remote, branch)], cwd, env)

#!/usr/bin/env python3
"""vps_git_push.py — commit+push acotado para jobs systemd del VPS (t334u).

Solo añade rutas de la lista permitida. Prefijo de commit [vps-push] y [skip netlify].
El token (G8_FULL) se pasa por el entorno, nunca se imprime ni se escribe en el repo.
No usa dispatch_prod ni TokenGuard.
"""
from __future__ import annotations

import base64
import os
import re
import subprocess
import sys
import fnmatch

SNB_ALLOWED = tuple("data/CHF_SPOT_%dY_DAILY.csv" % y for y in range(1, 31)) + (
    "data/CHF_CURVE_MONTH_END_CONTROL.csv",
    "data/CHF_CURVE_METH_BREAK.json",
)


# Salidas del job nzd-b2 (el mismo clon corre B2 + CHF curva/diario + TONA).
# No incluye CHF_SPOT_*Y_DAILY.csv (eso es SNB1A; t334u).
NZD_B2_OWNED = (
    "data/TONA.csv",
    "data/CHF_NOM_10Y.csv",
    "data/CHF_SARON.csv",
    "data/CHF_NOWCAST_shadow.json",
    "data/argos_rates.json",
    "data/NZD_*",
    "data/CHF_SPOT_*_shadow.csv",
    "data/CHF_SPOT_1Y.csv",
    "data/CHF_SPOT_2Y.csv",
    "data/CHF_SPOT_3Y.csv",
    "data/CHF_SPOT_4Y.csv",
    "data/CHF_SPOT_5Y.csv",
    "data/CHF_SPOT_6Y.csv",
    "data/CHF_SPOT_7Y.csv",
    "data/CHF_SPOT_8Y.csv",
    "data/CHF_SPOT_9Y.csv",
    "data/CHF_SPOT_10Y.csv",
    "data/CHF_SPOT_20Y.csv",
    "data/CHF_SPOT_30Y.csv",
    "data/_ingest/latest/mac-primary__nzchf-tona.json",
    "data/_ingest/latest/vps-prod__nzchf-tona.json",
    "data/_ingest/latest/actions__fetch_tona.json",
    "data/_ingest/evidence/TONA.csv.jsonl",
    "data/_ingest/runs/vps-prod/",
    "data/_ingest/runs/actions/fetch_tona/",
    "data/_ingest/runs/mac-primary/nzchf-tona/",
)

SOV10Y_OWNED = (
    "data/DE_10Y_PAR_DAILY.csv",
    "data/ES_10Y_DAILY.csv",
    "data/ES_DE_10Y_SPREAD_DAILY.csv",
)

# workdirs distintos por job (t343u / t346u)
JOB_WORKDIR_DEFAULTS = {
    "snb": "/srv/g8/prod/work/snb",
    "nzd-b2": "/srv/g8/prod/work/nzd-b2",
    "sovereign-10y": "/srv/g8/prod/work/sovereign-10y",
}

JOB_OWNED = {
    "snb": SNB_ALLOWED,
    "nzd-b2": NZD_B2_OWNED,
    "sovereign-10y": SOV10Y_OWNED,
}


def path_owned(path, owned):
    """True if path is exact, prefix (dir/) or fnmatch pattern in *owned*."""
    path = (path or "").replace("\\", "/")
    for rule in owned or ():
        rule = (rule or "").replace("\\", "/")
        if not rule:
            continue
        if rule.endswith("/") and (path == rule[:-1] or path.startswith(rule)):
            return True
        if any(ch in rule for ch in "*?["):
            if fnmatch.fnmatch(path, rule):
                return True
        if path == rule:
            return True
    return False


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


def prepare_git_home(cwd):
    """HOME and global gitconfig inside the worktree (t346u).

    systemd units use ProtectHome=yes, so /home/<user> is unreadable and
    ``git pull --rebase --autostash`` can fail with rc=128. Git must not
    need that home. The workdir is already in ReadWritePaths. No secrets.
    """
    gitdir = os.path.join(cwd, ".git")
    if not os.path.isdir(gitdir):
        gitdir = os.path.join(cwd, ".vps-git")
        os.makedirs(gitdir, exist_ok=True)
    home = os.path.join(gitdir, "vps-home")
    os.makedirs(os.path.join(home, ".config", "git"), exist_ok=True)
    cfg = os.path.join(home, ".gitconfig")
    if not os.path.exists(cfg):
        with open(cfg, "w", encoding="utf-8") as fh:
            fh.write("# vps job-local gitconfig (t346u); no secrets\n")
    return home, cfg


def auth_env(token, base=None, cwd=None):
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
    if cwd:
        home, cfg = prepare_git_home(cwd)
        env["HOME"] = home
        env["XDG_CONFIG_HOME"] = os.path.join(home, ".config")
        env["GIT_CONFIG_GLOBAL"] = cfg
        # rebase reescribe el commit y, con ProtectHome, no hay identidad en /home
        env.setdefault("GIT_AUTHOR_NAME", "g8-vps")
        env.setdefault("GIT_AUTHOR_EMAIL", "g8-vps@users.noreply.github.com")
        env.setdefault("GIT_COMMITTER_NAME", env["GIT_AUTHOR_NAME"])
        env.setdefault("GIT_COMMITTER_EMAIL", env["GIT_AUTHOR_EMAIL"])
    return env


def _sync_then_push(cwd, env, remote, branch):
    """Fetch + rebase onto origin, then push. No ``pull --rebase --autostash``.

    Leftover tracked dirty (same-job files not in this commit) is stashed in
    the repo and restored after the push. ProtectHome-safe: uses env from
    auth_env(..., cwd=cwd).
    """
    leftover = [p for st, p in iter_porcelain(cwd) if not _untracked(st)]
    stashed = False
    if leftover:
        _run(["git", "stash", "push", "-q", "-m", "vps-push-leftover", "--"] + leftover, cwd, env)
        stashed = True
    last = None
    try:
        for _ in range(1, 6):
            try:
                _run(["git", "fetch", "-q", remote, branch], cwd, env, timeout=180)
                _run(["git", "rebase", "-X", "theirs", "%s/%s" % (remote, branch)], cwd, env, timeout=180)
                _run(["git", "push", remote, "HEAD:" + branch], cwd, env, timeout=180)
                last = None
                break
            except PushError as e:
                last = e
                subprocess.run(["git", "rebase", "--abort"], cwd=cwd, env=env, capture_output=True)
    finally:
        if stashed:
            subprocess.run(["git", "stash", "pop", "-q"], cwd=cwd, env=env, capture_output=True)
    if last:
        raise last


def iter_porcelain(cwd):
    r = subprocess.run(["git", "status", "--porcelain", "-u"], cwd=cwd, capture_output=True, text=True, check=True)
    for line in r.stdout.splitlines():
        if not line:
            continue
        # status XY, then path (or 'old -> new')
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip().strip('"')
        yield line[:2], path


def porcelain(cwd):
    return [p for _, p in iter_porcelain(cwd)]


def _untracked(status):
    return status == "??"


def commit_and_push(cwd, allowed, message, token, remote="origin", branch="main", owned=None):
    """Add only allowed dirty paths, commit if needed, rebase onto origin, push.

    Returns dict: {changed: bool, sha: str|None, files: list, skipped: bool}
    Aborts if a dirty path is not in the allowed list.
    *owned*: rutas de ESTE job que pueden quedar sucias sin entrar en este commit
    (no se publican aquí, no se descartan, no abortan). Cualquier sucio ajeno sigue siendo error.
    """
    if not message.startswith("[vps-push]"):
        raise PushError("el mensaje debe empezar por [vps-push]")
    if "[skip netlify]" not in message:
        raise PushError("el mensaje debe incluir [skip netlify]")
    dirty = list(iter_porcelain(cwd))
    # Untracked files outside the allowlist are left alone (cache, mac/, state/).
    # Tracked unexpected dirty paths still abort: no se publica nada fuera de lista.
    unexpected = [p for st, p in dirty
                  if p not in allowed and not _untracked(st) and not path_owned(p, owned)]
    if unexpected:
        raise PushError("rutas no permitidas sucias: %s" % ",".join(unexpected[:8]))
    change = [p for st, p in dirty if p in allowed]
    env = auth_env(token, cwd=cwd)
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
    _sync_then_push(cwd, env, remote, branch)
    return {"changed": True, "sha": sha, "files": change, "skipped": False}



def _safe_reset_paths(paths):
    """Repo-relative data paths or basenames. Never emit IPs or absolute VPS paths."""
    out = []
    for raw in paths:
        p = str(raw).replace("\\", "/")
        p = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<ip>", p)
        if p.startswith("/"):
            idx = p.find("/data/")
            p = p[idx + 1:] if idx >= 0 else p.rsplit("/", 1)[-1]
        out.append(p)
    return out


def append_dirty_reset_alert(job, paths, log_path=None, now_utc=None):
    """Append INCIDENCIA DIRTY_RESET to ALERTAS.log (t343u). Best-effort write; always prints."""
    from datetime import datetime, timezone
    job = (job or os.environ.get("G8_VPS_JOB") or "vps-job").strip() or "vps-job"
    job = re.sub(r"\s+", "_", job)
    now_utc = now_utc or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    safe = _safe_reset_paths(paths)
    line = "%s 🔴 INCIDENCIA DIRTY_RESET %s %s\n" % (now_utc, job, " ".join(safe))
    print("[vps_git] " + line.strip())
    log_path = log_path or os.environ.get("G8_ALERTAS_LOG") or "/srv/g8/logs/ALERTAS.log"
    try:
        d = os.path.dirname(log_path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(line)
        return line
    except OSError as e:
        print("[vps_git] no se pudo escribir ALERTAS.log (%s)" % type(e).__name__, file=sys.stderr)
        return line

def fetch_ff_only(cwd, token, remote="origin", branch="main", allowed=None):
    """Fetch + merge --ff-only, without leaving foreign leftover dirt to block the job.

    Tracked dirty files are stashed around the merge (same idea as _sync_then_push). After pop, if *allowed* is set, tracked files not in that list
    are restored from HEAD and a DIRTY_RESET line is logged. Real fetch/merge
    errors still raise PushError (nothing is silenced).
    """
    env = auth_env(token, cwd=cwd)
    _run(["git", "fetch", "-q", remote, branch], cwd, env, timeout=180)
    tracked = [p for st, p in iter_porcelain(cwd) if not _untracked(st)]
    stashed = False
    if tracked:
        _run(["git", "stash", "push", "-q", "-m", "vps-ff-autostash", "--"] + tracked, cwd, env)
        stashed = True
    try:
        _run(["git", "merge", "-q", "--ff-only", "%s/%s" % (remote, branch)], cwd, env)
    except PushError:
        if stashed:
            subprocess.run(["git", "stash", "pop", "-q"], cwd=cwd, capture_output=True)
        raise
    if stashed:
        subprocess.run(["git", "stash", "pop", "-q"], cwd=cwd, capture_output=True)
    if allowed is None:
        return
    reset = [p for st, p in iter_porcelain(cwd)
             if not _untracked(st) and p not in allowed and not path_owned(p, allowed)]
    if reset:
        _run(["git", "restore", "--source=HEAD", "--worktree", "--staged", "--"] + reset, cwd, env)
        print("[vps_git] DIRTY_RESET %s" % " ".join(reset))
        append_dirty_reset_alert(os.environ.get("G8_VPS_JOB"), reset)

#!/usr/bin/env python3
"""vps_job_common.py — piezas comunes de los jobs VPS migrados (t354u/t357u).

- candado compartido g8-shared-data-alerts (equivalente al concurrency group de Actions,
  cancel-in-progress: false → se espera turno, no se cancela);
- arranque limpio como un checkout fresco de main (sin índice ni commits locales varados);
- saneado de errores: nunca valores de secretos ni cabeceras de autorización;
- registro step_alarm con la misma deduplicación de 20 h que «publish --commit».
"""
from __future__ import annotations

import base64
import fcntl
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager

SHARED_ALERTS_LOCK = "/srv/g8/prod/state/locks/g8-shared-data-alerts.lock"
TRACE_VARS = ("GIT_TRACE", "GIT_TRACE_CURL", "GIT_TRACE_PACKET", "GIT_TRACE2", "GIT_TRACE2_EVENT",
              "GIT_TRACE2_PERF", "GIT_CURL_VERBOSE", "GIT_TRACE_SETUP", "GIT_TRACE_PERFORMANCE",
              "GIT_TRACE_REDACT")
_AUTH_RE = re.compile(r"(?i)(authorization|x-access-token|basic|bearer)[^\n]*")


class LockTimeout(RuntimeError):
    pass


def scrub_git_trace_env(environ=None):
    """Sin trazas de git (podrían volcar la cabecera Basic con el token)."""
    environ = os.environ if environ is None else environ
    for k in TRACE_VARS:
        environ.pop(k, None)
    return environ


def sanitize(text, secrets=()):
    text = str(text or "")
    for s in secrets:
        if not s:
            continue
        for v in (s, base64.b64encode(s.encode()).decode(),
                  base64.b64encode(("x-access-token:" + s).encode()).decode()):
            text = text.replace(v, "***")
    text = _AUTH_RE.sub(r"\1 ***", text)
    return text[:400]


@contextmanager
def wait_exclusive(lock_path, timeout=1800, poll=5.0, sleep=time.sleep, clock=time.monotonic):
    """flock exclusivo con espera (cola, no cancelación). LockTimeout si no llega el turno."""
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    fh = open(lock_path, "a+", encoding="utf-8")
    t0 = clock()
    try:
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if clock() - t0 >= timeout:
                    raise LockTimeout("turno no obtenido en %ss: %s" % (timeout, os.path.basename(lock_path)))
                sleep(poll)
        yield fh
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def _git(args, cwd, env=None):
    return subprocess.run(["git"] + list(args), cwd=cwd, env=env, capture_output=True, text=True)


def unstage_all(cwd, env=None):
    """Índice vacío antes de cada commit: commit_and_push solo publica lo que añade él."""
    _git(["reset", "-q"], cwd, env)


def start_clean(cwd, P, token, allowed, remote="origin", branch="main"):
    """Arranque como checkout fresco de main: índice vacío, sin commits locales varados.

    Devuelve la lista de avisos (STRANDED_RESET si se descartó un commit local no empujado)."""
    notes = []
    unstage_all(cwd)
    err = None
    try:
        P.fetch_ff_only(cwd, token, remote=remote, branch=branch, allowed=allowed)
    except Exception as e:  # noqa: BLE001 - se reintenta tras resetear
        err = e
    ahead = _git(["rev-list", "--count", "%s/%s..HEAD" % (remote, branch)], cwd)
    if ahead.returncode == 0 and ahead.stdout.strip() not in ("", "0"):
        _git(["reset", "-q", "--hard", "%s/%s" % (remote, branch)], cwd)
        notes.append("STRANDED_RESET %s commit(s) locales no empujados descartados" % ahead.stdout.strip())
        print("[vps_job] STRANDED_RESET: %s commit(s) local(es) descartado(s); se regeneran en esta corrida"
              % ahead.stdout.strip())
        if err is not None:
            P.fetch_ff_only(cwd, token, remote=remote, branch=branch, allowed=allowed)
            err = None
    if err is not None:
        raise err
    return notes


def remove_private_copies(workdir):
    """dashboard_alerts escribe copias completas (con walls) en data/alerts/private/: fuera del clon."""
    shutil.rmtree(os.path.join(workdir, "data", "alerts", "private"), ignore_errors=True)


def restore_paths(workdir, paths, env=None):
    paths = [p for p in paths if p]
    if paths:
        _git(["restore", "--source=HEAD", "--worktree", "--staged", "--"] + list(paths), workdir, env)


def publish_record(workdir, py, env, job, extra_args, refresh_h=20.0, now=time.time):
    """step_alarm publish (sin --commit) + la misma deduplicación de 20 h de «--commit».

    Si la sustancia no cambia y el registro de HEAD tiene < refresh_h, se deja el de HEAD
    (sin commit). Devuelve (rc, cambiado)."""
    rel = "data/_ingest/latest/actions__job_%s.json" % job
    path = os.path.join(workdir, rel)
    sys.path.insert(0, os.path.join(workdir, "scripts", "tools"))
    try:
        import step_alarm as SA  # noqa: WPS433
    finally:
        sys.path.pop(0)
    shown = _git(["show", "HEAD:" + rel], workdir, env)
    old = None
    if shown.returncode == 0:
        try:
            import json
            old = json.loads(shown.stdout)
        except ValueError:
            old = None
    r = subprocess.run([py, "scripts/tools/step_alarm.py", "publish", job] + list(extra_args),
                       cwd=workdir, env=env, capture_output=True, text=True)
    new = SA._read(path)
    if old is not None and new is not None and SA._substance(old) == SA._substance(new) \
            and SA._age_h(old, now()) < refresh_h:
        restore_paths(workdir, [rel], env)
        print("[vps_job] %s: sin cambios (registro de %.1f h) — sin commit" % (job, SA._age_h(old, now())))
        return r.returncode, False
    return r.returncode, True

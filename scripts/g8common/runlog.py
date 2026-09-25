"""runlog — registros de ejecución SOLO DE ALTA y puntero por escritor (plan v1.1 §3.1).

  data/_ingest/runs/<ejecutor>/<trabajo>/<AAAA-MM>/<run_id>.json   (un fichero por ejecución; nunca se edita)
  data/_ingest/latest/<ejecutor>__<trabajo>.json                    (un único escritor: ese ejecutor)

Dos ejecutores no comparten ninguna ruta, así que no pueden sobrescribirse. Los estados agregados se
calculan al leer (ingest_watch.py / dashboard_alerts.py), no en un fichero común mutable.
"""
import json
import os
import random
import re
import time
from datetime import datetime, timezone

RUNS_DIR = "data/_ingest/runs"
LATEST_DIR = "data/_ingest/latest"
_SAFE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


def _check(name):
    if not _SAFE.match(name):
        raise ValueError("identificador no válido: %r" % name)
    return name


def new_run_id(executor, job, now=None):
    now = now or datetime.now(timezone.utc)
    return "%s_%s_%s_%04x" % (now.strftime("%Y%m%dT%H%M%SZ"), _check(executor), _check(job), random.getrandbits(16))


def run_path(executor, job, run_id, started):
    return "%s/%s/%s/%s/%s.json" % (RUNS_DIR, _check(executor), _check(job), started.strftime("%Y-%m"), run_id)


def latest_path(executor, job):
    return "%s/%s__%s.json" % (LATEST_DIR, _check(executor), _check(job))


def dumps(rec):
    return (json.dumps(rec, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def write_local(root, executor, job, rec, started):
    """Para ejecutores con checkout (Actions): crea el registro en modo exclusivo y actualiza el puntero."""
    p = os.path.join(root, run_path(executor, job, rec["run_id"], started))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "xb") as fh:                              # 'x': falla si ya existe → nunca sobrescribe
        fh.write(dumps(rec))
    lp = os.path.join(root, latest_path(executor, job))
    os.makedirs(os.path.dirname(lp), exist_ok=True)
    tmp = lp + ".%d.tmp" % os.getpid()
    with open(tmp, "wb") as fh:
        fh.write(dumps(rec))
    os.replace(tmp, lp)
    return p


def utcnow_s():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

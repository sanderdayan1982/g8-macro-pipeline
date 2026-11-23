#!/usr/bin/env python3
"""job_flock.py — candado exclusivo por job VPS (t343u).

Cada job usa su propio fichero de lock. Ningún job espera al árbol de otro:
el clon/worktree es distinto; este flock solo evita dos instancias del mismo job.
"""
from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager

DEFAULT_LOCK_DIR = "/srv/g8/prod/state/locks"


class LockError(RuntimeError):
    pass


@contextmanager
def exclusive(lock_path):
    """Non-blocking exclusive flock. Raises LockError if already held."""
    if not lock_path:
        raise LockError("ruta de lock ausente")
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    fh = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as e:
        fh.close()
        raise LockError("flock ocupado: %s" % os.path.basename(lock_path)) from e
    try:
        yield fh
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()

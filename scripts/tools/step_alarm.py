#!/usr/bin/env python3
"""step_alarm.py — t297u: que un paso con continue-on-error que falla quede en un registro que lee ingest_watch.

  record NOMBRE RC [--detail TEXTO]        añade la línea del paso al registro de pasos (formato g8step)
  publish JOB --only NOMBRE … [--commit]   vuelca esos pasos a data/_ingest/latest/actions__job_<JOB>.json
  publish JOB --outcome NOMBRE=RESULTADO …  (t297u c) registra antes cada paso por su steps.<id>.outcome de
                                            Actions: success → OK; skipped → OK «no corrió (condición del paso)»;
                                            failure, cancelled o vacío → FAILED. Se suma a --only.

record: rc 0 → OK; otro rc (o vacío) → FAILED. El registro es G8_STEP_LOG o, si no está,
$RUNNER_TEMP/g8_alarm_steps.jsonl (record y publish usan el mismo).

publish usa g8step.ledger(only=…): un paso pedido sin línea entra como FAILED (no corrió o lo cortó GitHub).
Sin --commit escribe el fichero en el árbol de trabajo (para pasos que van antes del commit del job).
Con --commit (pasos que van DESPUÉS del commit, p. ej. «Private data verify») escribe y sube SOLO ese fichero
desde un worktree aparte sobre origin/main: el árbol de trabajo del job (que puede tener ficheros completos
sin commitear) no se toca. Mensaje con [skip netlify]. No sube si el contenido (pasos, estados, rc, avisos) no
cambió y el registro tiene menos de --refresh-h horas. Si el push choca, regenera sobre el main nuevo (5 intentos).
Nunca devuelve rc≠0 por sí mismo salvo error de uso: el aviso lo da ingest_watch, no el job.
Solo stdlib (Python ≥3.9).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import g8step  # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
REL = os.path.join("data", "_ingest", "latest", "actions__job_%s.json")
BOT = ("-c", "user.name=github-actions[bot]", "-c", "user.email=github-actions[bot]@users.noreply.github.com")


def log_path(env):
    p = env.get("G8_STEP_LOG")
    if p:
        return p
    return os.path.join(env.get("RUNNER_TEMP") or tempfile.gettempdir(), "g8_alarm_steps.jsonl")


def record(name, rc, detail="", env=None):
    env = os.environ if env is None else env
    try:
        code = int(str(rc).strip())
    except ValueError:
        code = None
    rec = {"name": name, "status": g8step.OK if code == 0 else g8step.FAILED, "rc": code, "secs": None,
           "allowed_s": None, "phase": "record", "finished_utc": g8step._utc(time.time())}
    if detail:
        rec["detail"] = detail[:300]
    g8step._log(rec, log_path(env), env)
    print("[step_alarm] %s: %s rc=%s" % (name, rec["status"], code))
    return 0


OUTCOME_RC = {"success": "0", "skipped": "0"}


def record_outcomes(pairs, env=None):
    """NOMBRE=RESULTADO (steps.<id>.outcome) → record(); devuelve los nombres."""
    names = []
    for pair in pairs or []:
        name, _, outcome = pair.partition("=")
        name, outcome = name.strip(), outcome.strip().lower()
        if not name:
            continue
        detail = "outcome=%s" % (outcome or "vacío")
        if outcome == "skipped":
            detail = "no corrió (condición del paso)"
        record(name, OUTCOME_RC.get(outcome, "1"), detail, env=env)
        names.append(name)
    return names


def _substance(doc):
    if not doc:
        return None
    steps = sorted((s.get("name"), s.get("status"), s.get("rc")) for s in doc.get("steps") or [])
    return (steps, sorted(json.dumps(x, sort_keys=True) for x in doc.get("failed") or []),
            sorted(json.dumps(x, sort_keys=True) for x in doc.get("time_limited") or []))


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _age_h(doc, now):
    try:
        t = datetime.strptime(doc["written_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return float("inf")
    return (now - t.timestamp()) / 3600.0


def _write(job, only, env, root):
    lenv = dict(env)
    lenv["G8_STEP_LOG"] = log_path(env)
    g8step.ledger(job, env=lenv, root=root, only=only)
    return os.path.join(root, REL % job)


def _git(args, cwd, check=True):
    return subprocess.run(["git"] + list(args), cwd=cwd, check=check, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, universal_newlines=True)


def publish(job, only, commit=False, refresh_h=20.0, env=None, root=ROOT, now=time.time, tries=5, remote="origin",
            branch="main"):
    env = os.environ if env is None else env
    if not commit:
        _write(job, only, env, root)
        return 0
    wt = tempfile.mkdtemp(prefix="g8alarm_")
    os.rmdir(wt)
    rel = REL % job
    try:
        for i in range(1, tries + 1):
            try:
                _git(["fetch", "-q", remote, branch], root)
                tip = _git(["rev-parse", "FETCH_HEAD"], root).stdout.strip()
                if os.path.isdir(wt):
                    _git(["reset", "-q", "--hard", tip], wt)
                else:
                    _git(["worktree", "add", "-q", "--detach", wt, tip], root)
                old = _read(os.path.join(wt, rel))
                out = _write(job, only, env, wt)
                new = _read(out)
                if old is not None and _substance(old) == _substance(new) and _age_h(old, now()) < refresh_h:
                    print("[step_alarm] %s: sin cambios (registro de %.1f h) — sin commit" % (job, _age_h(old, now())))
                    return 0
                _git(["add", "--", rel], wt)
                if _git(["diff", "--cached", "--quiet"], wt, check=False).returncode == 0:
                    print("[step_alarm] %s: sin cambios — sin commit" % job)
                    return 0
                _git(list(BOT) + ["commit", "-q", "-m", "data: registro de alarmas %s [skip netlify]" % job], wt)
                if _git(["push", "-q", remote, "HEAD:%s" % branch], wt, check=False).returncode == 0:
                    print("[step_alarm] %s: registro subido (intento %d)" % (job, i))
                    return 0
                print("[step_alarm] %s: push rechazado en el intento %d; se regenera sobre el main nuevo" % (job, i))
            except subprocess.CalledProcessError as e:
                print("[step_alarm] %s: git %s falló (intento %d): %s" % (job, e.cmd[1:3], i, (e.stderr or "")[-200:]))
            if i < tries and env.get("STEP_ALARM_NO_SLEEP") != "1":
                time.sleep(min(i * 3, 15))
        print("[step_alarm] %s: no se pudo subir el registro tras %d intentos" % (job, tries))
        return 0
    finally:
        if os.path.isdir(wt):
            _git(["worktree", "remove", "--force", wt], root, check=False)
            shutil.rmtree(wt, ignore_errors=True)
        _git(["worktree", "prune"], root, check=False)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="step_alarm.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("name")
    r.add_argument("rc")
    r.add_argument("--detail", default="")
    p = sub.add_parser("publish")
    p.add_argument("job")
    p.add_argument("--only", action="append", default=[])
    p.add_argument("--outcome", action="append", default=[], help="NOMBRE=steps.<id>.outcome (t297u c)")
    p.add_argument("--commit", action="store_true")
    p.add_argument("--refresh-h", type=float, default=20.0)
    a = ap.parse_args(argv)
    if a.cmd == "record":
        return record(a.name, a.rc, a.detail)
    only = list(a.only) + [n for n in record_outcomes(a.outcome) if n not in a.only]
    if not only:
        ap.error("publish necesita --only o --outcome")
    return publish(a.job, only, commit=a.commit, refresh_h=a.refresh_h)


if __name__ == "__main__":
    sys.exit(main())

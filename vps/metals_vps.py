#!/usr/bin/env python3
"""metals_vps.py — job systemd VPS: Metals Fair Value / MDP (t354u).

Orden Sander 24-nov (t354u): migrado al VPS, sin cron ni turno en GitHub.
Mismos pasos que .github/workflows/metals_update.yml (que queda solo con
workflow_dispatch como respaldo manual):

  private pull (filas 6,11,12) → metals_fairvalue_g8.py vía g8step → ledger,
  refresh_brief_snapshot, health_monitor → commit [vps-push] de las rutas del job
  → private push (filas 11,12) → private verify → registro verify_metals [vps-push].

Dos unidades usan este binario, cada una con su clon y su flock:
  g8-metals-weekly  (G8_VPS_JOB=metals)         sáb 07:00 UTC (MDP semanal)
  g8-metals-shadow  (G8_VPS_JOB=metals-shadow)  L–V 22:45 UTC (proyección SHADOW B2)
Además las dos toman el candado común del feed (metals-feed.lock): nunca dos
publicaciones del mismo feed a la vez.

Secretos (por nombre, desde /etc/g8/secrets.env; nunca se imprimen):
  G8_FULL            push al repo público (commit_and_push)
  G8_DATA_READ_CI    private pull de g8-macro-data (filas 6,11,12)
  G8_DATA_WRITE      private push de g8-macro-data (filas 11,12)
  FRED_API_KEY       opcional (sin ella, CSV de FRED sin clave)
  TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID  los usa g8-alarm@ (OnFailure), no este script.
Datos con licencia: data/OPTIONS_SURFACE.json (fila 6) se borra del clon al terminar.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime, timezone

import job_flock

HERE = os.path.dirname(os.path.abspath(__file__))

COMPUTE_FILES = (
    "data/MFV_G8_XAU.csv",
    "data/MFV_G8_XAG.csv",
    "data/MFV_G8_state.json",
    "data/MFV_G8_walkforward_XAU.csv",
    "data/MFV_G8_walkforward_XAG.csv",
    "data/MFV_G8_XAU_daily_shadow.csv",
    "data/MFV_G8_XAG_daily_shadow.csv",
    "data/MFV_G8_shadow_state.json",
)
RESTORE_ON_FAIL = COMPUTE_FILES[:5]  # igual que el workflow: el semanal no se publica mezclado
ALWAYS_FILES = (
    "data/_ingest/metals_provenance.json",
    "data/_ingest/latest/actions__job_metals.json",
    "data/_ingest/health.json",
    "data/alerts/brief.json",
    "data/_ingest/not_before/metals.json",
)
VERIFY_FILES = ("data/_ingest/latest/actions__job_verify_metals.json",)
# Rutas del job que pueden quedar sucias sin publicarse (copia privada de state.json).
PRIVATE_TRACKED = ("data/alerts/state.json",)
LICENSED_UNTRACKED = ("data/OPTIONS_SURFACE.json",)
OWNED = COMPUTE_FILES + ALWAYS_FILES + VERIFY_FILES + PRIVATE_TRACKED

JOBS = {
    "metals": {"workdir": "/srv/g8/prod/work/metals", "lock": "/srv/g8/prod/state/locks/metals.lock"},
    "metals-shadow": {"workdir": "/srv/g8/prod/work/metals-shadow",
                      "lock": "/srv/g8/prod/state/locks/metals-shadow.lock"},
}
FEED_LOCK = "/srv/g8/prod/state/locks/metals-feed.lock"
SECRET_NAMES = ("G8_FULL", "G8_DATA_READ_CI", "G8_DATA_WRITE", "FRED_API_KEY",
                "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "G8_RO_DATA", "G8_RO_MESA", "G8_RO_PIPELINE")


def _utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_state(path, rec):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def load_prev(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def child_env(workdir, P, extra=None):
    """Entorno de los pasos: sin ningún secreto salvo el que el paso necesita (extra)."""
    env = {k: v for k, v in os.environ.items() if k not in SECRET_NAMES}
    home, cfg = P.prepare_git_home(workdir)
    env.update(HOME=home, XDG_CONFIG_HOME=os.path.join(home, ".config"), GIT_CONFIG_GLOBAL=cfg,
               GIT_TERMINAL_PROMPT="0", G8_NO_SEND="1", G8_JOB="metals")
    for k, v in (extra or {}).items():
        if v:
            env[k] = v
    return env


def step(cmd, workdir, env, secrets=(), timeout=1500):
    """Ejecuta un paso; devuelve rc. La salida se imprime sin valores de secretos."""
    r = subprocess.run(cmd, cwd=workdir, env=env, capture_output=True, text=True, timeout=timeout)
    out = (r.stdout or "") + (r.stderr or "")
    for s in secrets:
        if s:
            out = out.replace(s, "***")
    name = " ".join(os.path.basename(c) if i == 1 else c for i, c in enumerate(cmd[:4]))
    print("[metals_vps] paso %s rc=%s" % (name, r.returncode))
    tail = out.strip().splitlines()[-15:]
    for line in tail:
        print("[metals_vps]   " + line)
    return r.returncode


def required_secrets():
    missing = [n for n in ("G8_FULL", "G8_DATA_READ_CI", "G8_DATA_WRITE") if not os.environ.get(n, "").strip()]
    if missing:
        raise RuntimeError("secretos ausentes: %s" % ",".join(missing))


def cleanup(workdir, P, env):
    subprocess.run(["git", "restore", "--source=HEAD", "--worktree", "--staged", "--"] + list(PRIVATE_TRACKED),
                   cwd=workdir, env=env, capture_output=True)
    for rel in LICENSED_UNTRACKED:
        try:
            os.remove(os.path.join(workdir, rel))
        except OSError:
            pass


def run(job, workdir):
    sys.path.insert(0, HERE)
    import vps_git_push as P  # noqa: WPS433

    token = os.environ["G8_FULL"].strip()
    read_tok = os.environ["G8_DATA_READ_CI"].strip()
    write_tok = os.environ["G8_DATA_WRITE"].strip()
    fred = os.environ.get("FRED_API_KEY", "").strip()
    secrets = (token, read_tok, write_tok, fred)
    py = sys.executable
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    P.fetch_ff_only(workdir, token, allowed=OWNED)
    priv = tempfile.mkdtemp(prefix="g8data-")
    base = child_env(workdir, P)
    steplog = os.path.join(priv + "-steps.jsonl")
    base["G8_STEP_LOG"] = steplog
    out = {"job": job, "compute_ok": False, "verify_rc": None, "push": None, "push_verify": None}
    try:
        env_r = dict(base, G8_DATA_TOKEN=read_tok)
        rc = step([py, "scripts/tools/private_data.py", "pull", priv, "--rows", "6,11,12"], workdir, env_r, secrets)
        if rc != 0:
            raise RuntimeError("private pull rc=%s" % rc)
        env_c = dict(base, FRED_API_KEY=fred) if fred else dict(base)
        rc = step([py, "scripts/tools/g8step.py", "--name", "metals_fairvalue", "--cap", "1080", "--",
                   py, "scripts/metals_fairvalue_g8.py"], workdir, env_c, secrets, timeout=1260)
        out["compute_ok"] = rc == 0
        if not out["compute_ok"]:
            subprocess.run(["git", "restore", "--"] + list(RESTORE_ON_FAIL), cwd=workdir, env=base, capture_output=True)
        post = 0
        for cmd in ([py, "scripts/tools/g8step.py", "--ledger", "metals"],
                    [py, "scripts/tools/refresh_brief_snapshot.py"],
                    [py, "scripts/health_monitor.py"]):
            post |= step(cmd, workdir, base, secrets)
        out["post_rc"] = post
        allowed = (COMPUTE_FILES if out["compute_ok"] else ()) + ALWAYS_FILES
        msg = "[vps-push] %s fair value %s compute=%s [skip netlify]" % (
            job, today, "ok" if out["compute_ok"] else "fallo")
        out["push"] = P.commit_and_push(workdir, allowed, msg, token, owned=OWNED)
        env_w = dict(base, G8_DATA_TOKEN=write_tok)
        rc = step([py, "scripts/tools/private_data.py", "push", priv, "--rows", "11,12",
                   "--workflow", "metals_update", "--session", today], workdir, env_w, secrets)
        if rc != 0:
            raise RuntimeError("private push rc=%s" % rc)
        vrc = step([py, "scripts/tools/private_data.py", "verify", priv, "--rows", "11,12"], workdir, base, secrets)
        out["verify_rc"] = vrc
        step([py, "scripts/tools/step_alarm.py", "record", "private_verify_metals", str(vrc),
              "--detail", "private_data verify rc=%s" % vrc], workdir, base, secrets)
        # Sin --commit: el registro se publica aquí con [vps-push], como el resto del job.
        step([py, "scripts/tools/step_alarm.py", "publish", "verify_metals", "--only", "private_verify_metals"],
             workdir, base, secrets)
        cleanup(workdir, P, base)
        out["push_verify"] = P.commit_and_push(
            workdir, VERIFY_FILES, "[vps-push] %s registro verify_metals %s [skip netlify]" % (job, today),
            token, owned=OWNED)
    finally:
        cleanup(workdir, P, base)
        shutil.rmtree(priv, ignore_errors=True)
        try:
            os.remove(steplog)
        except OSError:
            pass
    if not out["compute_ok"]:
        raise RuntimeError("metals_fairvalue falló (publicado solo el registro; semanal sin mezclar)")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="VPS Metals Fair Value (t354u)")
    ap.add_argument("--job", default=os.environ.get("G8_VPS_JOB", "metals"), choices=sorted(JOBS))
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--state", default=None)
    args = ap.parse_args(argv)
    cfg = JOBS[args.job]
    workdir = args.workdir or os.environ.get("G8_METALS_WORKDIR") or cfg["workdir"]
    state = args.state or os.environ.get("G8_VPS_JOB_STATE") or "/srv/g8/prod/state/vps_jobs/%s.json" % args.job
    lock = os.environ.get("G8_METALS_LOCK") or cfg["lock"]
    feed_lock = os.environ.get("G8_METALS_FEED_LOCK") or FEED_LOCK
    prev = load_prev(state)
    rec = {"job": args.job, "utc": _utcnow(), "ok": False, "rc": 1, "consecutive_fail": 0}
    try:
        required_secrets()
        with job_flock.exclusive(lock):
            with job_flock.exclusive(feed_lock):
                result = run(args.job, workdir)
        rec.update(ok=True, rc=0, consecutive_fail=0, verify_rc=result.get("verify_rc"),
                   sha=(result.get("push") or {}).get("sha"), changed=(result.get("push") or {}).get("changed"),
                   files=(result.get("push") or {}).get("files"))
    except Exception as e:
        rec.update(ok=False, rc=1, error_type=type(e).__name__, error=str(e)[:400],
                   consecutive_fail=int(prev.get("consecutive_fail") or 0) + 1)
        print("[metals_vps] FATAL: %s: %s" % (type(e).__name__, e), file=sys.stderr)
        traceback.print_exc()
    write_state(state, rec)
    print("[metals_vps] job=%s ok=%s sha=%s fail_streak=%s" % (
        args.job, rec.get("ok"), rec.get("sha"), rec.get("consecutive_fail")))
    return rec["rc"]


if __name__ == "__main__":
    sys.exit(main())

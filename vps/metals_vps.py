#!/usr/bin/env python3
"""metals_vps.py — job systemd VPS: Metals Fair Value / MDP (t354u).

Orden Sander 24-nov (t354u): migrado al VPS, sin cron ni turno en GitHub.
El respaldo manual sigue en .github/workflows/metals_update.yml (solo workflow_dispatch).
t399u: este binario trae la cadena (fila 1) y no publica el brief ya recortado:

  private pull (filas 1,6,11,12) → metals_fairvalue_g8.py vía g8step → ledger,
  refresh_brief_snapshot, health_monitor → proyección pública (apply_public_alerts)
  → commit [vps-push] de las rutas del job (sin BRIEF_KEYS)
  → private push desde data/alerts/private/ (filas 11,12) → private verify
  → registro verify_metals [vps-push].

Dos unidades usan este binario, cada una con su clon y su flock:
  g8-metals-weekly  (G8_VPS_JOB=metals)         sáb 07:00 UTC (MDP semanal)
  g8-metals-shadow  (G8_VPS_JOB=metals-shadow)  L–V 22:45 UTC (proyección SHADOW B2)
Además las dos toman el candado común del feed (metals-feed.lock): nunca dos
publicaciones del mismo feed a la vez.

Secretos (por nombre, desde /etc/g8/secrets.env; nunca se imprimen):
  G8_FULL            push al repo público (commit_and_push)
  G8_DATA_READ_CI    private pull de g8-macro-data (filas 1,6,11,12; la 1 es la cadena)
  G8_DATA_WRITE      private push de g8-macro-data (filas 11,12)
  FRED_API_KEY       opcional (sin ella, CSV de FRED sin clave)
  TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID  los usa g8-alarm@ (OnFailure), no este script.
Datos con licencia (filas 1 y 6, y los otros basenames de figura) se borran del workdir
antes del push público y otra vez al terminar. El verify privado sigue en 11,12.
t399u: el push privado sale de data/alerts/private/, nunca del brief público recortado.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

import job_flock
import vps_job_common as C

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
# Fila 6 + fila 1 (cadena) + los otros dos basenames del guardián. Nunca al público.
LICENSED_UNTRACKED = (
    "data/OPTIONS_SURFACE.json",
    "data/CME_OPTIONS_C1_shadow.json",
    "data/CME_FXVOL_shadow.json",
    "data/options/canonical",
)
# Mismos basenames que tests/test_cme_public_guardian.py (ese test no se modifica).
PUBLIC_FIGURE_BASENAMES = frozenset({
    "OPTIONS_SURFACE.json",
    "CME_OPTIONS_C1_shadow.json",
    "CME_FXVOL_shadow.json",
})
PRIVATE_ALERT_NAMES = ("brief.json", "state.json")
PULL_ROWS = "1,6,11,12"
VERIFY_ROWS = "11,12"
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
    # No stdout de productores ni private_data en logs: puede contener derivados CME/ICE.
    print("[metals_vps] salida suprimida: %d líneas" % len(out.splitlines()))
    return r.returncode


def required_secrets():
    missing = [n for n in ("G8_FULL", "G8_DATA_READ_CI", "G8_DATA_WRITE") if not os.environ.get(n, "").strip()]
    if missing:
        raise RuntimeError("secretos ausentes: %s" % ",".join(missing))


def _import_private_data(workdir):
    tools = os.path.join(workdir, "scripts", "tools")
    if os.path.isdir(tools) and tools not in sys.path:
        sys.path.insert(0, tools)
    import private_data as PD  # noqa: WPS433
    return PD


def scrub_licensed(workdir):
    """Quita superficie y cadena del workdir. No toca data/alerts/private/."""
    for rel in LICENSED_UNTRACKED:
        path = os.path.join(workdir, rel)
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)
            continue
        try:
            os.remove(path)
        except OSError:
            pass


def after_brief_snapshot(workdir, refresh_failed, env):
    """Tras el snapshot: proyección pública. Si refresh falló, el path público vuelve a HEAD.

    El stash a data/alerts/private/ ocurre dentro de apply_public_alerts, antes del restore.
    El restore solo nombra el path público: no pisa el brief privado.
    """
    PD = _import_private_data(workdir)
    remaining, stashed = PD.apply_public_alerts(workdir)
    if refresh_failed:
        C.restore_paths(workdir, ["data/alerts/brief.json"], env)
    return remaining, stashed


def public_push_paths(allowed, workdir):
    """Rutas del push público: sin BRIEF_KEYS, sin data/alerts/private/, sin ficheros CME."""
    PD = _import_private_data(workdir)
    out = []
    for rel in PD.exclude_forbidden_public_alerts(tuple(allowed), workdir):
        norm = rel.replace("\\", "/")
        base = norm.rsplit("/", 1)[-1]
        if base in PUBLIC_FIGURE_BASENAMES:
            continue
        if norm.startswith("data/alerts/private/") or "/private/" in norm:
            continue
        if norm == "data/options/canonical" or norm.startswith("data/options/canonical/"):
            continue
        if norm in LICENSED_UNTRACKED:
            continue
        out.append(rel)
    return tuple(out)


def prepare_public_push(workdir, compute_ok):
    """Borra la licencia y devuelve lo que el push público puede llevar."""
    scrub_licensed(workdir)
    allowed = (COMPUTE_FILES if compute_ok else ()) + ALWAYS_FILES
    return public_push_paths(allowed, workdir)


def copy_private_alerts(workdir, dest_root):
    """Copia data/alerts/private/{brief,state}.json. Nunca lee el path público."""
    copied = []
    for name in PRIVATE_ALERT_NAMES:
        src = os.path.join(workdir, "data", "alerts", "private", name)
        if not os.path.isfile(src):
            continue
        rel = "data/alerts/" + name
        dst = os.path.join(dest_root, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        copied.append(rel)
    return tuple(copied)


def cleanup(workdir, P, env):
    subprocess.run(["git", "restore", "--source=HEAD", "--worktree", "--staged", "--"] + list(PRIVATE_TRACKED),
                   cwd=workdir, env=env, capture_output=True)
    scrub_licensed(workdir)
    C.remove_private_copies(workdir)


def run(job, workdir):
    """Misma semántica que metals_update.yml: los pasos «if: always()» corren aunque falle uno anterior.

    Devuelve out; out["errors"] lista (saneada) lo que hace rojo el job."""
    sys.path.insert(0, HERE)
    import vps_git_push as P  # noqa: WPS433

    token = os.environ["G8_FULL"].strip()
    read_tok = os.environ["G8_DATA_READ_CI"].strip()
    write_tok = os.environ["G8_DATA_WRITE"].strip()
    fred = os.environ.get("FRED_API_KEY", "").strip()
    secrets = (token, read_tok, write_tok, fred)
    py = sys.executable
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = {"job": job, "compute_ok": False, "pull_ok": False, "verify_rc": None, "push": None,
           "push_verify": None, "errors": []}

    def fail(what, e):
        out["errors"].append("%s: %s" % (what, C.sanitize("%s: %s" % (type(e).__name__, e), secrets)))
        print("[metals_vps] ERROR %s" % out["errors"][-1], file=sys.stderr)

    out["notes"] = C.start_clean(workdir, P, token, allowed=OWNED)
    # Clon privado en un subdirectorio: private_data pone .g8-askpass en el padre (exclusivo de esta corrida).
    tmp = tempfile.mkdtemp(prefix="g8metals-")
    priv = os.path.join(tmp, "g8data")
    base = child_env(workdir, P)
    base["G8_STEP_LOG"] = os.path.join(tmp, "steps.jsonl")
    try:
        # 1. Private pull (sin always(): si falla, el cálculo no corre; los pasos always() sí).
        rc = step([py, "scripts/tools/private_data.py", "pull", priv, "--rows", PULL_ROWS], workdir,
                  dict(base, G8_DATA_TOKEN=read_tok), secrets)
        out["pull_ok"] = rc == 0
        if not out["pull_ok"]:
            out["errors"].append("private pull rc=%s" % rc)
        # 2. Cálculo (continue-on-error).
        if out["pull_ok"]:
            env_c = dict(base, FRED_API_KEY=fred) if fred else dict(base)
            rc = step([py, "scripts/tools/g8step.py", "--name", "metals_fairvalue", "--cap", "1080", "--",
                       py, "scripts/metals_fairvalue_g8.py"], workdir, env_c, secrets, timeout=1260)
            out["compute_ok"] = rc == 0
        # 3. always(): registro + snapshots. Un fallo aquí pone el job en rojo, como en Actions.
        if not out["compute_ok"]:
            subprocess.run(["git", "restore", "--"] + list(RESTORE_ON_FAIL), cwd=workdir, env=base,
                           capture_output=True)
        post = {}
        for name, cmd in (("ledger", [py, "scripts/tools/g8step.py", "--ledger", "metals"]),
                          ("refresh_brief", [py, "scripts/tools/refresh_brief_snapshot.py"]),
                          ("health_monitor", [py, "scripts/health_monitor.py"])):
            post[name] = step(cmd, workdir, base, secrets)
            if post[name] != 0:
                out["errors"].append("%s rc=%s" % (name, post[name]))
                break  # bash sin «|| true»: el primer fallo corta el resto del paso
        out["post_rc"] = post
        # t399u: proyección también en éxito. Si refresh falló, HEAD en el path público
        # y el brief de data/alerts/private/ no se pisa.
        refresh_failed = post.get("refresh_brief") != 0
        remaining = []
        try:
            remaining, _stashed = after_brief_snapshot(workdir, refresh_failed, base)
        except Exception as e:  # noqa: BLE001
            fail("proyección pública", e)
            remaining = ["apply_public_alerts"]
            if refresh_failed:
                C.restore_paths(workdir, ["data/alerts/brief.json"], base)
        if remaining:
            out["errors"].append("brief.json público: %s claves BRIEF_KEYS (%s); no se publica" % (
                len(remaining), ",".join(remaining)))
        # 4. always(): commit público. La licencia sale del workdir antes de añadir.
        try:
            allowed = prepare_public_push(workdir, out["compute_ok"])
        except Exception as e:  # noqa: BLE001
            fail("filtro público", e)
            allowed = ()
        msg = "[vps-push] %s fair value %s compute=%s [skip netlify]" % (
            job, today, "ok" if out["compute_ok"] else "fallo")
        try:
            C.unstage_all(workdir)
            out["push"] = P.commit_and_push(workdir, allowed, msg, token, owned=OWNED)
        except Exception as e:  # noqa: BLE001
            fail("push público", e)
        # 5. always(): private push (filas 11,12) desde data/alerts/private/, nunca del path público.
        private_paths = tuple("data/alerts/" + name for name in PRIVATE_ALERT_NAMES)
        try:
            if not out["pull_ok"]:
                raise RuntimeError("sin clon privado (pull fallido)")
            P.fetch_ff_only(priv, write_tok, allowed=private_paths)
            copied = copy_private_alerts(workdir, priv)
            if not copied:
                raise RuntimeError("sin copias en data/alerts/private; no se empuja el path público")
            C.unstage_all(priv)
            out["private_push"] = P.commit_and_push(
                priv, private_paths, "[vps-push] metals_update %s [skip netlify]" % today, write_tok,
                owned=private_paths)
        except Exception as e:  # noqa: BLE001
            fail("push privado", e)
        # 6. always(), continue-on-error: verify + registro (dedup 20 h como --commit).
        try:
            vrc = step([py, "scripts/tools/private_data.py", "verify", priv, "--rows", VERIFY_ROWS], workdir, base,
                       secrets)
            out["verify_rc"] = vrc
            step([py, "scripts/tools/step_alarm.py", "record", "private_verify_metals", str(vrc),
                  "--detail", "private_data verify rc=%s" % vrc], workdir, base, secrets)
            C.publish_record(workdir, py, base, "verify_metals", ["--only", "private_verify_metals"])
            cleanup(workdir, P, base)
            C.unstage_all(workdir)
            out["push_verify"] = P.commit_and_push(
                workdir, VERIFY_FILES, "[vps-push] %s registro verify_metals %s [skip netlify]" % (job, today),
                token, owned=OWNED)
        except Exception as e:  # noqa: BLE001
            fail("registro verify (continue-on-error)", e)
            out["errors"].pop()  # continue-on-error: no pone el job en rojo
    finally:
        cleanup(workdir, P, base)
        shutil.rmtree(tmp, ignore_errors=True)
    if not out["compute_ok"] and out["pull_ok"]:
        out["errors"].append("metals_fairvalue falló (publicado solo el registro; semanal sin mezclar)")
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
    C.scrub_git_trace_env()
    secrets = tuple(os.environ.get(n, "").strip() for n in SECRET_NAMES)
    try:
        required_secrets()
        with job_flock.exclusive(lock):
            with job_flock.exclusive(feed_lock):
                # Como el concurrency group g8-shared-data-alerts de Actions: cola, sin cancelar.
                with C.wait_exclusive(os.environ.get("G8_SHARED_ALERTS_LOCK") or C.SHARED_ALERTS_LOCK):
                    result = run(args.job, workdir)
        push = result.get("push") or {}
        rec.update(verify_rc=result.get("verify_rc"), sha=push.get("sha"), changed=push.get("changed"),
                   files=push.get("files"), notes=result.get("notes") or [])
        if result.get("errors"):
            raise RuntimeError("; ".join(result["errors"]))
        rec.update(ok=True, rc=0, consecutive_fail=0)
    except Exception as e:  # noqa: BLE001
        msg = C.sanitize(str(e), secrets)
        rec.update(ok=False, rc=1, error_type=type(e).__name__, error=msg,
                   consecutive_fail=int(prev.get("consecutive_fail") or 0) + 1)
        print("[metals_vps] FATAL: %s: %s" % (type(e).__name__, msg), file=sys.stderr)
    write_state(state, rec)
    print("[metals_vps] job=%s ok=%s sha=%s fail_streak=%s" % (
        args.job, rec.get("ok"), rec.get("sha"), rec.get("consecutive_fail")))
    return rec["rc"]


if __name__ == "__main__":
    sys.exit(main())

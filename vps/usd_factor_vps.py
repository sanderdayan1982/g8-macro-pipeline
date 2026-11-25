#!/usr/bin/env python3
"""usd_factor_vps.py — job systemd VPS: §10 Factor USD · Residuales · Libro (t354u/t357u).

Orden Sander 24-nov (t354u): migrado al VPS, sin cron ni turno en GitHub.
Mismos pasos que .github/workflows/usd_factor.yml (que queda solo con workflow_dispatch):

  private pull (filas 6,9,10,11,12) → usd_factor.py → strip_usd_factor_public.py (by_ccy CME/ICE
  fuera del público ANTES de cualquier commit) → book_risk.py → s01b.py --provisional
  · siempre: dashboard_alerts.py, ingest_watch.py
  → commit [vps-push] de las mismas rutas que el workflow → log de alertas + private push (11,12)
  → private verify → registros verify_usd_factor y usd_factor_steps con [vps-push]
  → job en rojo si el Telegram de ingest_watch no se entregó (como el workflow).

Una unidad, dos turnos: g8-usd-factor (L–V 15:30 y 17:30 UTC). Clon y flock propios.
Secretos (por nombre, /etc/g8/secrets.env): G8_FULL, G8_DATA_READ_CI, G8_DATA_WRITE,
TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID. Nunca se imprimen.
Datos con licencia (filas 6,9,10: OPTIONS_SURFACE y futuros canónicos, ignorados por git) se borran del clon al terminar.
"""
from __future__ import annotations

import argparse
import glob
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
JOB = "usd-factor"
WORKDIR = "/srv/g8/prod/work/usd-factor"
LOCK = "/srv/g8/prod/state/locks/usd-factor.lock"
# Igual que «git add» del workflow (directorios = prefijos).
PUBLISH_PREFIXES = ("data/usd_factor/", "data/alerts/", "data/s01b/", "data/book_snapshots/")
PUBLISH_FILES = ("data/USD_FACTOR.json", "data/BOOK_RISK.json", "data/S01B.json", "data/_ingest/watch_state.json")
RECORD_FILES = ("data/_ingest/latest/actions__job_verify_usd_factor.json",
                "data/_ingest/latest/actions__job_usd_factor_steps.json")
LICENSED_GLOBS = ("data/OPTIONS_SURFACE.json", "data/futures/canonical/*.csv")
SECRET_NAMES = ("G8_FULL", "G8_DATA_READ_CI", "G8_DATA_WRITE", "FRED_API_KEY", "TELEGRAM_BOT_TOKEN",
                "TELEGRAM_CHAT_ID", "G8_RO_DATA", "G8_RO_MESA", "G8_RO_PIPELINE")
BOT = ("-c", "user.name=g8-vps", "-c", "user.email=g8-vps@users.noreply.github.com")


def _utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def publishable(path):
    return path in PUBLISH_FILES or any(path.startswith(p) for p in PUBLISH_PREFIXES)


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


def required_secrets():
    missing = [n for n in ("G8_FULL", "G8_DATA_READ_CI", "G8_DATA_WRITE", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
               if not os.environ.get(n, "").strip()]
    if missing:
        raise RuntimeError("secretos ausentes: %s" % ",".join(missing))


def step(cmd, workdir, env, secrets=(), timeout=1200, log=None):
    r = subprocess.run(cmd, cwd=workdir, env=env, capture_output=True, text=True, timeout=timeout)
    out = (r.stdout or "") + (r.stderr or "")
    for s in secrets:
        if s:
            out = out.replace(s, "***")
    if log:
        # Como «> $RUNNER_TEMP/dashboard-alerts.log 2>&1»: el log completo solo va al repo privado.
        with open(log, "w", encoding="utf-8") as fh:
            fh.write(out)
    print("[usd_factor_vps] paso %s rc=%s" % (" ".join(os.path.basename(c) for c in cmd[1:3]), r.returncode))
    print("[usd_factor_vps] salida suprimida: %d líneas" % len(out.splitlines()))
    return r.returncode


def dirty(workdir):
    r = subprocess.run(["git", "status", "--porcelain", "-u"], cwd=workdir, capture_output=True, text=True, check=True)
    out = []
    for line in r.stdout.splitlines():
        p = line[3:]
        if " -> " in p:
            p = p.split(" -> ", 1)[1]
        out.append((line[:2], p.strip().strip('"')))
    return out


def discard_unpublished(workdir, env):
    """Como el runner efímero de Actions: lo que el workflow no añade no se publica ni queda sucio."""
    extra = [p for st, p in dirty(workdir) if st != "??" and not publishable(p) and p not in RECORD_FILES]
    if extra:
        subprocess.run(["git", "restore", "--source=HEAD", "--worktree", "--staged", "--"] + extra,
                       cwd=workdir, env=env, capture_output=True)
        print("[usd_factor_vps] no publicadas (como en Actions): %s" % " ".join(extra[:12]))
    return extra


def remove_licensed(workdir):
    for g in LICENSED_GLOBS:
        for p in glob.glob(os.path.join(workdir, g)):
            try:
                os.remove(p)
            except OSError:
                pass


def run(workdir):
    """Misma semántica que usd_factor.yml; los pasos «if: always()» corren aunque falle uno anterior."""
    sys.path.insert(0, HERE)
    import vps_git_push as P  # noqa: WPS433

    token = os.environ["G8_FULL"].strip()
    read_tok = os.environ["G8_DATA_READ_CI"].strip()
    write_tok = os.environ["G8_DATA_WRITE"].strip()
    tg = {k: os.environ.get(k, "").strip() for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")}
    secrets = (token, read_tok, write_tok) + tuple(tg.values())
    py = sys.executable
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    owned = PUBLISH_FILES + PUBLISH_PREFIXES + RECORD_FILES
    out = {"main_ok": False, "push": None, "push_records": None, "errors": []}

    def fail(what, e):
        out["errors"].append("%s: %s" % (what, C.sanitize("%s: %s" % (type(e).__name__, e), secrets)))
        print("[usd_factor_vps] ERROR %s" % out["errors"][-1], file=sys.stderr)

    out["notes"] = C.start_clean(workdir, P, token, allowed=owned)
    home, cfg = P.prepare_git_home(workdir)
    base = {k: v for k, v in os.environ.items() if k not in SECRET_NAMES}
    base.update(HOME=home, XDG_CONFIG_HOME=os.path.join(home, ".config"), GIT_CONFIG_GLOBAL=cfg,
                GIT_TERMINAL_PROMPT="0")
    tmp = tempfile.mkdtemp(prefix="usdf-")
    priv = os.path.join(tmp, "g8data")  # .g8-askpass queda en tmp, exclusivo de esta corrida
    alerts_log = os.path.join(tmp, "dashboard-alerts.log")
    iw_result = os.path.join(tmp, "ingest_watch.json")
    try:
        rc = step([py, "scripts/tools/private_data.py", "pull", priv, "--rows", "6,9,10,11,12"],
                  workdir, dict(base, G8_DATA_TOKEN=read_tok), secrets)
        main_ok = rc == 0
        failed_step = None if main_ok else "private_data pull"
        if main_ok:
            for cmd in ([py, "scripts/usd_factor.py"],
                        [py, "scripts/tools/strip_usd_factor_public.py"],
                        [py, "scripts/book_risk.py"],
                        [py, "scripts/s01b.py", "--provisional"]):
                if step(cmd, workdir, base, secrets) != 0:
                    main_ok = False
                    failed_step = os.path.basename(cmd[1])
                    break
        out["main_ok"] = main_ok
        if not main_ok:
            out["errors"].append("paso principal fallido: %s (sin publicación de datos)" % failed_step)
            # USD_FACTOR.json podría quedar sin el strip (by_ccy CME/ICE): fuera del clon.
            restore_paths = [p for st, p in dirty(workdir) if st != "??" and publishable(p)]
            C.restore_paths(workdir, restore_paths, base)
        # always(), continue-on-error
        dash_rc = step([py, "scripts/dashboard_alerts.py"], workdir, dict(base, **tg), secrets, log=alerts_log)
        tools_dir = os.path.join(workdir, "scripts", "tools")
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        import private_data as PD  # noqa: WPS433
        # t385u: proyección pública también en éxito; no dejar el brief privado en el path público.
        remaining, _stashed = PD.apply_public_alerts(workdir)
        if dash_rc != 0:
            # state.json/brief.json pueden seguir siendo las copias privadas (filas 11,12): nunca al público.
            C.restore_paths(workdir, ["data/alerts/state.json", "data/alerts/brief.json"], base)
        step([py, "scripts/ingest_watch.py", "--result", iw_result], workdir,
             dict(base, G8_PRIVATE_CLONE=priv, **tg), secrets)
        if main_ok:
            try:
                discard_unpublished(workdir, base)
                remaining, _stashed = PD.apply_public_alerts(workdir)
                change = tuple(p for st, p in dirty(workdir) if publishable(p))
                change = PD.exclude_forbidden_public_alerts(change, workdir)
                if remaining:
                    out["errors"].append("brief.json público: %s claves BRIEF_KEYS (%s); no se publica" % (
                        len(remaining), ",".join(remaining)))
                C.unstage_all(workdir)
                out["push"] = P.commit_and_push(workdir, change,
                                                "[vps-push] usd factor update %s [skip netlify]" % today,
                                                token, owned=owned)
            except Exception as e:  # noqa: BLE001
                fail("push público", e)
            private_paths = ("data/alerts/brief.json", "data/alerts/state.json", "data/alerts/usd_factor_alerts.log")
            try:
                if out.get("push") is None:
                    raise RuntimeError("omitido: el push público falló (en Actions el paso no corre)")
                P.fetch_ff_only(priv, write_tok, allowed=private_paths)
                for rel in private_paths[:2]:
                    src_priv = os.path.join(workdir, "data", "alerts", "private", os.path.basename(rel))
                    if os.path.isfile(src_priv):
                        shutil.copyfile(src_priv, os.path.join(priv, rel))
                if os.path.exists(alerts_log):
                    shutil.copyfile(alerts_log, os.path.join(priv, private_paths[2]))
                C.unstage_all(priv)
                out["private_push"] = P.commit_and_push(priv, private_paths,
                                                        "[vps-push] usd_factor %s [skip netlify]" % today,
                                                        write_tok, owned=private_paths)
            except Exception as e:  # noqa: BLE001
                fail("push privado", e)
        # always(), continue-on-error: verify + registros (dedup 20 h como --commit).
        try:
            vrc = step([py, "scripts/tools/private_data.py", "verify", priv, "--rows", "11,12"], workdir, base,
                       secrets)
            out["verify_rc"] = vrc
            step([py, "scripts/tools/step_alarm.py", "record", "private_verify_usd_factor", str(vrc),
                  "--detail", "private_data verify rc=%s" % vrc], workdir, base, secrets)
            C.publish_record(workdir, py, base, "verify_usd_factor", ["--only", "private_verify_usd_factor"])
        except Exception as e:  # noqa: BLE001
            print("[usd_factor_vps] verify (continue-on-error): %s" % C.sanitize(e, secrets), file=sys.stderr)
        try:
            C.publish_record(workdir, py, base, "usd_factor_steps",
                             ["--outcome", "dashboard_alerts_usd_factor=%s" % ("success" if dash_rc == 0 else "failure")])
        except Exception as e:  # noqa: BLE001
            print("[usd_factor_vps] registro pasos (continue-on-error): %s" % C.sanitize(e, secrets), file=sys.stderr)
        try:
            discard_unpublished(workdir, base)
            rec_change = tuple(p for st, p in dirty(workdir) if p in RECORD_FILES)
            C.unstage_all(workdir)
            out["push_records"] = P.commit_and_push(
                workdir, rec_change, "[vps-push] usd_factor registros verify/steps %s [skip netlify]" % today, token,
                owned=owned)
        except Exception as e:  # noqa: BLE001
            print("[usd_factor_vps] push registros (continue-on-error): %s" % C.sanitize(e, secrets), file=sys.stderr)
        # always(): Telegram de ingest_watch no entregado → rojo.
        try:
            with open(iw_result, encoding="utf-8") as fh:
                delivery = json.load(fh).get("delivery")
        except (OSError, ValueError):
            delivery = "NO_RESULT"
        out["delivery"] = delivery
        if delivery not in ("SENT", "DRY", "NOTHING"):
            out["errors"].append("ingest_watch entrega=%s" % delivery)
    finally:
        remove_licensed(workdir)
        C.remove_private_copies(workdir)
        if not out.get("push"):
            discard_unpublished(workdir, base)
            subprocess.run(["git", "restore", "--source=HEAD", "--worktree", "--staged", "--", "data/alerts"],
                           cwd=workdir, env=base, capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="VPS Factor USD (t354u)")
    ap.add_argument("--workdir", default=os.environ.get("G8_USDF_WORKDIR", WORKDIR))
    ap.add_argument("--state", default=os.environ.get("G8_VPS_JOB_STATE", "/srv/g8/prod/state/vps_jobs/%s.json" % JOB))
    args = ap.parse_args(argv)
    prev = load_prev(args.state)
    rec = {"job": JOB, "utc": _utcnow(), "ok": False, "rc": 1, "consecutive_fail": 0}
    C.scrub_git_trace_env()
    secrets = tuple(os.environ.get(n, "").strip() for n in SECRET_NAMES)
    try:
        required_secrets()
        with job_flock.exclusive(os.environ.get("G8_USDF_LOCK") or LOCK):
            # Como el concurrency group g8-shared-data-alerts de Actions: cola, sin cancelar.
            with C.wait_exclusive(os.environ.get("G8_SHARED_ALERTS_LOCK") or C.SHARED_ALERTS_LOCK):
                result = run(args.workdir)
        push = result.get("push") or {}
        rec.update(sha=push.get("sha"), changed=push.get("changed"), files=push.get("files"),
                   verify_rc=result.get("verify_rc"), delivery=result.get("delivery"), notes=result.get("notes") or [])
        if result.get("errors"):
            raise RuntimeError("; ".join(result["errors"]))
        rec.update(ok=True, rc=0, consecutive_fail=0)
    except Exception as e:  # noqa: BLE001
        msg = C.sanitize(str(e), secrets)
        rec.update(ok=False, rc=1, error_type=type(e).__name__, error=msg,
                   consecutive_fail=int(prev.get("consecutive_fail") or 0) + 1)
        print("[usd_factor_vps] FATAL: %s: %s" % (type(e).__name__, msg), file=sys.stderr)
    write_state(args.state, rec)
    print("[usd_factor_vps] ok=%s sha=%s fail_streak=%s" % (rec.get("ok"), rec.get("sha"), rec.get("consecutive_fail")))
    return rec["rc"]


if __name__ == "__main__":
    sys.exit(main())

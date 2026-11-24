#!/usr/bin/env python3
"""daily_update.yml como job systemd VPS (t354u/t357u).

Orden Sander 24-nov (t354u/t357u): pieza 1 de daily_update; el workflow conserva su cron hasta la pieza final; timer sin habilitar.

Secretos de la corrida: G8_FULL, G8_DATA_READ_CI, G8_DATA_WRITE, FRED_API_KEY
(opcional), TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID. Se excluyen del entorno
heredado también G8_DATA_TOKEN, G8_RO_DATA, G8_RO_MESA y G8_RO_PIPELINE.
Nunca se imprimen.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import job_flock
import vps_git_push as P
import vps_job_common as C
from usd_factor_vps import _utcnow, load_prev, write_state

JOB = "daily-update"
WORKDIR = "/srv/g8/prod/work/daily-update"
LOCK = "/srv/g8/prod/state/locks/daily-update.lock"
REQUIRED = ("G8_FULL", "G8_DATA_READ_CI", "G8_DATA_WRITE", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
SECRET_NAMES = REQUIRED + ("FRED_API_KEY", "G8_DATA_TOKEN", "G8_RO_DATA", "G8_RO_MESA", "G8_RO_PIPELINE")
PRIVATE_REPO = "https://github.com/sanderdayan1982/g8-macro-data.git"
PRIVATE_TOOL = "scripts/tools/private_data.py"
STEP_TOOL = "scripts/tools/g8step.py"
ALARM_TOOL = "scripts/tools/step_alarm.py"
ALERT_FILES = ("data/alerts/brief.json", "data/alerts/state.json")
PRIVATE_PATHS = (
    "data/options/shadow_history.json",
    "data/options/fxvol_history.csv",
    "data/CME_OPTIONS_C1_shadow.json",
    "data/CME_FXVOL_shadow.json",
) + ALERT_FILES
CREDIT_PATHS = ("data/credit/CRD_shadow.json", "data/credit/oas_vintage.csv")
PRIVATE_LOG = "data/alerts/daily_update_alerts.log"
VERIFY_FILE = "data/_ingest/latest/actions__job_verify_daily.json"
LICENSED_GLOBS = (
    "data/OPTIONS_SURFACE.json", "data/CME_OPTIONS_C1_shadow.json",
    "data/CME_FXVOL_shadow.json", "data/futures/canonical/*.csv",
)

# El workflow hace git add data/ con sus exclusiones de .gitignore.
# Los archivos públicos ya versionados bajo data/options también entran en ese git add.
PUBLISH_PREFIXES = ("data/",)
PUBLIC_OPTIONS = ("data/options/canonical/.gitkeep", "data/options/state.json")

# Nombre, límite de g8step, timeout de Actions (segundos), comando.
SCRAPERS = (
    ("sofr", "240", 360, ("scripts/fetch_sofr.py",)),
    ("estr", "240", 360, ("scripts/fetch_estr.py",)),
    ("sonia", "240", 360, ("scripts/fetch_sonia.py",)),
    ("aonia", "240", 360, ("scripts/fetch_aonia.py",)),
    ("tona", "240", 360, ("scripts/fetch_tona.py",)),
    ("corra", "240", 360, ("scripts/fetch_corra.py",)),
    ("bis_ocr", "240", 360, ("scripts/fetch_bis_ocr.py",)),
    ("bis_gb", "240", 360, ("scripts/fetch_bis_policy.py", "GB",)),
    ("bis_jp", "240", 360, ("scripts/fetch_bis_policy.py", "JP",)),
    ("bis_ch", "240", 360, ("scripts/fetch_bis_policy.py", "CH",)),
    ("bis_au", "240", 360, ("scripts/fetch_bis_policy.py", "AU",)),
    ("us_bills", "240", 360, ("scripts/fetch_us_bills.py",)),
    ("cad_bills", "240", 360, ("scripts/fetch_cad_bills.py",)),
    ("aud_bills", "240", 360, ("scripts/fetch_aud_bills.py",)),
    ("eur_bills", "420", 540, ("scripts/fetch_eur_bills.py",)),
    ("gbp_bills", "240", 360, ("scripts/fetch_gbp_bills.py",)),
    ("jpy_bills", "240", 360, ("scripts/fetch_jpy_bills.py",)),
    ("floors", "240", 360, ("scripts/fetch_floor_spreads.py",)),
    ("acm_tp", "240", 360, ("scripts/fetch_acm.py",)),
)

# Nombre, límite de g8step, timeout de Actions (segundos), comando.
ATTRIBUTION = (
    ("acm_g8", "660", 780, ("scripts/acm_g8.py", "USD", "EUR", "JPY", "GBP", "CAD", "AUD")),
    ("acm_nzd", "300", 420, ("scripts/acm_g8.py", "NZD")),
    ("acm_chf", "300", 420, ("scripts/acm_g8.py", "CHF")),
    ("nzd_tp_synth", "120", 240, ("scripts/nzd_tp_synth.py",)),
    ("real_yields", "300", 420, ("scripts/real_yields_g8.py",)),
    ("eur_real", "180", 300, ("scripts/fetch_eur_real.py",)),
    ("jpy_real", "180", 300, ("scripts/fetch_jpy_real.py",)),
    ("chf_cpi", "120", 180, ("scripts/fetch_chf_cpi.py",)),
    ("aud_nowcast", "60", 120, ("scripts/aud_nowcast.py",)),
)

FALLBACK = {
    "acm_nzd": "acm NZD not available yet — proxy fallback follows",
    "acm_chf": "acm CHF not available yet — frozen file stays",
}


def publishable(path):
    if path in PUBLIC_OPTIONS:
        return True
    return (path.startswith(PUBLISH_PREFIXES)
            and not path.startswith(("data/options/", "data/futures/", "data/alerts/private/"))
            and path not in LICENSED_GLOBS
            and path not in ("data/options", "data/futures", "data/alerts/private"))


def required_secrets():
    missing = [name for name in REQUIRED if not os.environ.get(name, "").strip()]
    if missing:
        raise RuntimeError("secretos ausentes: " + ",".join(missing))


@contextmanager
def step_deadline(seconds):
    """Límite de los pasos compuestos que llaman a helpers de git y de registros."""
    def expired(_signum, _frame):
        raise TimeoutError("plazo del paso agotado (%s s)" % seconds)

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def step(cmd, workdir, env, timeout, secrets=(), log=None):
    try:
        result = subprocess.run(cmd, cwd=workdir, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=timeout)
        rc, output = result.returncode, result.stdout
    except subprocess.TimeoutExpired as exc:
        rc, output = 124, exc.output or b""
    if log:
        for value in secrets:
            if value:
                output = output.replace(value.encode(), b"***")
        Path(log).write_bytes(output)
    print("[daily_update_vps] paso %s rc=%s líneas=%s" %
          (cmd[3] if cmd[0] == "bash" else os.path.basename(cmd[1]), rc, len(output.splitlines())))
    return rc


def remove_licensed(workdir):
    for pattern in LICENSED_GLOBS:
        for path in glob.glob(os.path.join(workdir, pattern)):
            os.unlink(path)
    # Borra también los ignorados que trajo rsync; conserva el estado público y .gitkeep.
    subprocess.run(["git", "clean", "-ffdx", "--", "data/options/"], cwd=workdir,
                   capture_output=True, check=True)


def public_changes(workdir, env):
    paths = subprocess.check_output(
        ["git", "ls-files", "-z", "--modified", "--others", "--exclude-standard"],
        cwd=workdir, env=env, stderr=subprocess.PIPE,
    )
    return tuple(sorted({path for path in map(os.fsdecode, paths.split(b"\0")) if publishable(path)}))


def prepare_alerts(workdir, full):
    full.mkdir()
    for rel in ALERT_FILES:
        path = Path(workdir, rel)
        private = path.parent / "private" / path.name
        source = private if private.is_file() else path
        if source.is_file():
            shutil.copy2(source, full / path.name)
    for rel in ALERT_FILES:
        path = Path(workdir, rel)
        if path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and "walls" in doc:
                del doc["walls"]
                tmp = path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
                tmp.replace(path)


def private_push(workdir, priv, full, alerts_log, env, write_token, today):
    for rel in ALERT_FILES:
        saved = full / Path(rel).name
        if saved.is_file():
            shutil.copy2(saved, Path(workdir, rel))
    paths = PRIVATE_PATHS + tuple(rel for rel in CREDIT_PATHS if Path(workdir, rel).exists())
    allowed = paths + ((PRIVATE_LOG,) if Path(alerts_log).is_file() else ())
    git_env = P.auth_env(write_token, base=env)
    if not Path(priv, ".git").is_dir():
        # Como private_data push: vuelve a clonar si el pull no dejó un checkout.
        rc = step(["git", "clone", "-q", "--branch", "main", PRIVATE_REPO, priv],
                  workdir, git_env, 300)
        if rc:
            raise RuntimeError("clone privado rc=%s" % rc)
    elif Path(priv, ".git/shallow").is_file():
        rc = step(["git", "fetch", "-q", "--unshallow", "origin"],
                  priv, git_env, 300)
        if rc:
            raise RuntimeError("unshallow privado rc=%s" % rc)
    P.fetch_ff_only(priv, write_token, allowed=allowed)
    for rel in paths:
        src = Path(workdir, rel)
        if not src.is_file():
            raise RuntimeError("archivo privado ausente: " + rel)
        dst = Path(priv, rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    if PRIVATE_LOG in allowed:
        Path(priv, PRIVATE_LOG).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(alerts_log, Path(priv, PRIVATE_LOG))
    C.unstage_all(priv, env)
    return P.commit_and_push(priv, allowed, "[vps-push] daily_update %s [skip netlify]" % today,
                             write_token, owned=allowed)


def push_verify_record(workdir, tmp, env, token, today, isolate):
    message = "[vps-push] daily_update registro verify %s [skip netlify]" % today
    if not isolate:
        C.unstage_all(workdir, env)
        return P.commit_and_push(workdir, (VERIFY_FILE,), message, token, owned=PUBLISH_PREFIXES)
    # Si falló el commit principal, publica el registro desde origin/main como step_alarm.
    # Así el reintento del registro no arrastra un commit de datos que quedó local.
    tree = os.path.join(tmp, "verify-worktree")
    try:
        subprocess.run(["git", "worktree", "add", "-q", "--detach", tree, "origin/main"],
                       cwd=workdir, env=env, capture_output=True, check=True)
        dest = Path(tree, VERIFY_FILE)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(workdir, VERIFY_FILE), dest)
        C.unstage_all(tree, env)
        return P.commit_and_push(tree, (VERIFY_FILE,), message, token, owned=(VERIFY_FILE,))
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", tree], cwd=workdir,
                       env=env, capture_output=True)
        C.restore_paths(workdir, (VERIFY_FILE,), env)


def run(workdir):
    required_secrets()
    credentials = {name: os.environ.pop(name) for name in SECRET_NAMES if name in os.environ}
    try:
        return run_steps(workdir, credentials)
    finally:
        os.environ.update(credentials)


def run_steps(workdir, credentials):
    start = datetime.now(timezone.utc)
    today = start.date().isoformat()
    token = credentials["G8_FULL"].strip()
    secrets = tuple(credentials.values()) + tuple(value.strip() for value in credentials.values())
    telegram = {name: credentials[name].strip() for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")}
    py = sys.executable
    out = {"push": None, "errors": [], "scrapers": {}}

    def report(name, error, tolerant=False):
        # PushError puede incluir texto de git, por ejemplo un conflicto en un archivo privado.
        detail = type(error).__name__ if isinstance(error, P.PushError) else str(error)
        msg = C.sanitize("%s: %s" % (name, detail), secrets)
        if not tolerant:
            out["errors"].append(msg)
        print("[daily_update_vps] %s" % msg, file=sys.stderr)

    try:
        with step_deadline(180):
            out["notes"] = C.start_clean(workdir, P, token, allowed=PUBLISH_PREFIXES)
    except Exception as exc:
        report("checkout", exc)
        return out
    home, cfg = P.prepare_git_home(workdir)
    base = {name: value for name, value in os.environ.items() if name not in SECRET_NAMES}
    jsda = base.pop("JSDA_BASE", None)
    base.update(HOME=home, XDG_CONFIG_HOME=os.path.join(home, ".config"), GIT_CONFIG_GLOBAL=cfg,
                GIT_TERMINAL_PROMPT="0", G8_JOB="daily", G8_JOB_RESERVE_S="780",
                G8_STEP_GUARD="data", G8_GUARD_SKIP="options,futures",
                G8_JOB_DEADLINE_EPOCH=str(int(start.timestamp()) + 31 * 60 - 90))
    tmp = tempfile.mkdtemp(prefix="g8daily-")
    priv = os.path.join(tmp, "g8data")
    base.update(G8_STEP_LOG=os.path.join(tmp, "g8_steps.jsonl"), RUNNER_TEMP=tmp)
    full = Path(tmp, "alerts-full")
    alerts_log = os.path.join(tmp, "dashboard-alerts.log")
    iw_result = os.path.join(tmp, "ingest_watch.json")

    def execute(cmd, timeout, extra=None, log=None):
        return step(cmd, workdir, dict(base, **(extra or {})), timeout, secrets, log)

    def guarded(name, cap, timeout, command, extra=None, post=False, log=None):
        flags = ["--phase", "post", "--cap", cap, "--min", "5"] if post else ["--cap", cap]
        cmd = [py, STEP_TOOL, "--name", name, *flags, "--", py, *command]
        if name in FALLBACK:
            cmd = ["bash", "-c", 'message=$1; shift; "$@" || echo "$message"',
                   name, FALLBACK[name], *cmd]
        return execute(cmd, timeout, extra, log)

    def checked(name, action, tolerant=False):
        try:
            rc = action()
            if rc:
                report(name, "rc=%s" % rc, tolerant)
            return rc
        except Exception as exc:
            report(name, exc, tolerant)
            return 1

    def ledger(job, *only):
        args = [arg for name in only for arg in ("--only", name)]
        checked(job, lambda: execute([py, STEP_TOOL, "--ledger", job, *args], 60), tolerant=True)

    public_commit_failed = False
    try:
        # Python 3.11 se aprovisiona en /srv/g8/venv, como en las otras unidades.
        pip = os.path.join(os.path.dirname(py), "pip")
        install_rc = checked("pip install", lambda: execute([pip, "install", "-r", "requirements.txt"], 240))
        pull_rc = None
        if install_rc == 0:
            pull_rc = checked("private pull", lambda: execute(
                [py, PRIVATE_TOOL, "pull", priv, "--rows", "1,4,5,6,7,8,11,12"], 300,
                {"G8_DATA_TOKEN": credentials["G8_DATA_READ_CI"].strip()}))
            if pull_rc == 0:
                for name, cap, timeout, command in SCRAPERS:
                    extra = {"FRED_API_KEY": credentials.get("FRED_API_KEY", "").strip()} if name == "floors" else None
                    rc = checked(name, lambda: guarded(name, cap, timeout, command, extra), tolerant=True)
                    out["scrapers"][name] = "failure" if rc else "success"
        # Report scraper results (always): el resumen de Actions se guarda en el estado JSON.
        for name, *_ in SCRAPERS:
            out["scrapers"].setdefault(name, "skipped")

        if pull_rc == 0:
            for name, cap, timeout, command in ATTRIBUTION:
                extra = {}
                if name in ("acm_g8", "real_yields"):
                    extra["FRED_API_KEY"] = credentials.get("FRED_API_KEY", "").strip()
                if name == "jpy_real" and jsda is not None:
                    extra["JSDA_BASE"] = jsda
                checked(name, lambda: guarded(name, cap, timeout, command, extra), tolerant=True)

        # Desde el ledger de descarga, todos los pasos llevan if: always().
        ledger("daily")
        checked("s01b_final", lambda: guarded("s01b_final", "120", 180,
                                              ("scripts/tools/finalize_session.py",), post=True))
        checked("dashboard_alerts", lambda: guarded("dashboard_alerts", "60", 120,
                ("scripts/dashboard_alerts.py",), telegram, post=True, log=alerts_log), tolerant=True)
        checked("ingest_watch", lambda: guarded("ingest_watch", "90", 180,
                ("scripts/ingest_watch.py", "--result", iw_result),
                dict(telegram, G8_PRIVATE_CLONE=priv), post=True), tolerant=True)
        for name, script, log in (
            ("cb_event_shadow", "scripts/cb_event_shadow.py", None),
            ("cme_options_shadow", "scripts/cme_options_shadow.py", os.path.join(tmp, "cme.log")),
            ("cme_fxvol_shadow", "scripts/cme_fxvol_shadow.py", os.path.join(tmp, "cme.log")),
        ):
            checked(name, lambda: guarded(name, "40", 120, (script,), post=True, log=log), tolerant=True)
        ledger("daily_fxvol", "cme_fxvol_shadow")
        for name, script in (
            ("chf_nowcast_shadow", "scripts/chf_nowcast_shadow.py"),
            ("s01b_shadow_a3", "scripts/s01b_shadow_a3.py"),
            ("credit_fred_shadow", "scripts/credit_fred_shadow.py"),
        ):
            checked(name, lambda: guarded(name, "40", 120, (script,), post=True), tolerant=True)
        ledger("daily_shadows", "cb_event_shadow", "cme_options_shadow", "chf_nowcast_shadow",
               "s01b_shadow_a3", "credit_fred_shadow")
        ledger("daily_dashboard", "dashboard_alerts")

        try:
            with step_deadline(180):
                rc = execute([py, "scripts/argos_rates.py"], 180, {"TZ": "UTC"})
                if rc:
                    raise RuntimeError("argos_rates rc=%s" % rc)
                prepare_alerts(workdir, full)
                Path(workdir, "data/public").mkdir(parents=True, exist_ok=True)
                rc = execute([py, "scripts/tools/cme_public_projection.py",
                              "--out", "data/public/CME_OPTIONS_SURFACE_public.json", "data/OPTIONS_SURFACE.json",
                              "--out", "data/public/CME_C1_public.json", "data/CME_OPTIONS_C1_shadow.json",
                              "--out", "data/public/CME_FXVOL_public.json", "data/CME_FXVOL_shadow.json"], 180)
                if rc:
                    raise RuntimeError("cme_public_projection rc=%s" % rc)
                C.unstage_all(workdir, base)
                public_commit_failed = True
                out["push"] = P.commit_and_push(
                    workdir, public_changes(workdir, base),
                    "[vps-push] daily data update %s [skip netlify]" % today, token, owned=PUBLISH_PREFIXES)
        except Exception as exc:
            report("push público", exc)
        else:
            public_commit_failed = False

        try:
            with step_deadline(300):
                out["private_push"] = private_push(workdir, priv, full, alerts_log, base,
                                                   credentials["G8_DATA_WRITE"].strip(), today)
        except Exception as exc:
            report("push privado", exc)

        try:
            with step_deadline(120):
                vrc = execute([py, PRIVATE_TOOL, "verify", priv, "--paths", *PRIVATE_PATHS,
                               "--credit-if-present"], 120)
                out["verify_rc"] = vrc
                execute([py, ALARM_TOOL, "record", "private_verify_daily", str(vrc),
                         "--detail", "private_data verify rc=%s" % vrc], 120)
                rc, changed = C.publish_record(
                    workdir, py, base, "verify_daily", ["--only", "private_verify_daily"])
                if rc:
                    raise RuntimeError("step_alarm publish rc=%s" % rc)
                # Las copias completas solo se necesitan hasta verify.
                C.restore_paths(workdir, ALERT_FILES, base)
                if changed:
                    out["push_records"] = push_verify_record(workdir, tmp, base, token, today,
                                                             isolate=public_commit_failed)
        except Exception as exc:
            report("registro verify (continue-on-error)", exc, tolerant=True)

        try:
            delivery = json.loads(Path(iw_result).read_text(encoding="utf-8")).get("delivery")
        except (OSError, ValueError, AttributeError):
            delivery = "NO_RESULT"
        out["delivery"] = delivery if delivery in ("SENT", "DRY", "NOTHING") else "NO_RESULT"
        if delivery not in ("SENT", "DRY", "NOTHING"):
            report("ingest_watch", "entrega=%s" % C.sanitize(delivery, secrets))
        # Alert on failure (if: failure()) lo ejecuta OnFailure=g8-alarm@.
    finally:
        try:
            C.restore_paths(workdir, ALERT_FILES, base)
            remove_licensed(workdir)
        except Exception as exc:
            report("limpieza", exc)
        finally:
            C.remove_private_copies(workdir)
            shutil.rmtree(tmp, ignore_errors=True)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="VPS Daily Update (t354u/t357u)")
    ap.add_argument("--workdir", default=os.environ.get("G8_DAILY_WORKDIR", WORKDIR))
    ap.add_argument("--state", default=os.environ.get("G8_VPS_JOB_STATE", "/srv/g8/prod/state/vps_jobs/%s.json" % JOB))
    args = ap.parse_args(argv)
    prev = load_prev(args.state)
    rec = {"job": JOB, "utc": _utcnow(), "ok": False, "rc": 1, "consecutive_fail": 0}
    C.scrub_git_trace_env()
    secrets = tuple(os.environ.get(n, "") for n in SECRET_NAMES)
    secrets += tuple(value.strip() for value in secrets)
    try:
        required_secrets()
        with job_flock.exclusive(os.environ.get("G8_DAILY_LOCK") or LOCK):
            # Como el concurrency group g8-shared-data-alerts de Actions: cola, sin cancelar.
            with C.wait_exclusive(os.environ.get("G8_SHARED_ALERTS_LOCK") or C.SHARED_ALERTS_LOCK):
                result = run(args.workdir)
        push = result.get("push") or {}
        rec.update(sha=push.get("sha"), changed=push.get("changed"), files=push.get("files"),
                   scrapers=result.get("scrapers"), verify_rc=result.get("verify_rc"),
                   delivery=result.get("delivery"), notes=result.get("notes") or [])
        if result.get("errors"):
            raise RuntimeError("; ".join(result["errors"]))
        rec.update(ok=True, rc=0, consecutive_fail=0)
    except Exception as e:  # noqa: BLE001
        msg = C.sanitize(str(e), secrets)
        rec.update(ok=False, rc=1, error_type=type(e).__name__, error=msg,
                   consecutive_fail=int(prev.get("consecutive_fail") or 0) + 1)
        print("[daily_update_vps] FATAL: %s: %s" % (type(e).__name__, msg), file=sys.stderr)
    write_state(args.state, rec)
    print("[daily_update_vps] ok=%s sha=%s fail_streak=%s" % (rec.get("ok"), rec.get("sha"), rec.get("consecutive_fail")))
    return rec["rc"]


if __name__ == "__main__":
    sys.exit(main())

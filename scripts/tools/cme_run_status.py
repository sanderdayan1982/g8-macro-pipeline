#!/usr/bin/env python3
"""cme_run_status.py — t299u (opción B): estado del run de cme_options al repo PRIVADO g8-macro-data.

cme_options.yml tiene `permissions: contents: read` en el repo público (t247u) y no puede subir nada aquí.
Su estado va a g8-macro-data/data/_status/cme_options.json con el mismo G8_DATA_WRITE de «Private data push»
(entorno G8_DATA_TOKEN; nunca argv, URL ni salida). ingest_watch lo lee del clon privado que ya baja
«Private data pull» (daily_update, usd_factor) y avisa si falla o si no llega.

Solo lleva rc, resultados de paso, hora UTC, sesión y motivo. NINGÚN número CME (ni OI, ni settle, ni
volumen, ni coste): los valores admitidos están cerrados y no se copia ningún log.

    write CLONE --session AAAA-MM-DD --collector-rc RC [--outcome paso=resultado …] [--no-push]
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import private_data as PD  # noqa: E402

REL = "data/_status/cme_options.json"
STEPS = ("summary", "dashboard_alerts", "private_push", "private_verify")
OUTCOMES = ("success", "failure", "cancelled", "skipped")
BAD = ("failure", "cancelled")


def build(session, collector_rc, outcomes, env=None, now=None):
    env = os.environ if env is None else env
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", session or ""):
        raise ValueError("sesión no válida")
    try:
        rc = int(str(collector_rc).strip())
    except ValueError:
        rc = None                                   # sin rc del colector = fallo (el paso no llegó a escribirlo)
    steps = {}
    for pair in outcomes or []:
        name, _, out = pair.partition("=")
        name, out = name.strip(), out.strip().lower()
        if name not in STEPS:
            raise ValueError("paso no admitido: %s" % name)
        steps[name] = out if out in OUTCOMES else "missing"
    reasons = []
    if rc is None:
        reasons.append("colector sin rc")
    elif rc != 0:
        reasons.append("colector rc=%d" % rc)
    for name in STEPS:
        out = steps.get(name, "missing")
        steps[name] = out
        if out in BAD or out == "missing":
            if name == "summary" and rc not in (None, 0) and out in ("skipped", "missing"):
                continue
            reasons.append("%s %s" % (name, out))
    t = now or datetime.now(timezone.utc)
    return {
        "workflow": "cme_options",
        "run_id": env.get("GITHUB_RUN_ID"),
        "run_attempt": env.get("GITHUB_RUN_ATTEMPT"),
        "event": env.get("GITHUB_EVENT_NAME"),
        "written_utc": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session": session,
        "collector_rc": rc,
        "steps": steps,
        "status": "FAILED" if reasons else "OK",
        "reason": "; ".join(reasons),
    }


def write(clone, doc, push=True, repo=PD.REPO_URL, retries=5, pause=5):
    token = PD.token_from_env()
    clone, env = PD.ensure_clone(clone, repo, token)
    PD.prepare_clone(clone, env, token)
    path = os.path.join(clone, REL)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)
    rc = PD.git(["add", "--", REL], clone, env, token)
    if rc != 0:
        return rc
    if PD.git(["diff", "--cached", "--quiet"], clone, env, token) == 0:
        print("cme_run_status: sin cambios")
        return 0
    rc = PD.git(["commit", "-q", "-m", "data: cme_options status %s [skip netlify]" % doc["session"]], clone, env, token)
    if rc != 0:
        return rc
    print("cme_run_status: status=%s" % doc["status"])
    if not push:
        return 0
    return PD.push_retry(clone, env, token, retries, pause)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="cme_run_status.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write")
    w.add_argument("clone")
    w.add_argument("--session", required=True)
    w.add_argument("--collector-rc", default="")
    w.add_argument("--outcome", action="append", default=[])
    w.add_argument("--no-push", action="store_true")
    w.add_argument("--repo", default=PD.REPO_URL)
    a = ap.parse_args(argv)
    doc = build(a.session, a.collector_rc, a.outcome)
    return write(a.clone, doc, push=not a.no_push, repo=a.repo)


if __name__ == "__main__":
    sys.exit(main())

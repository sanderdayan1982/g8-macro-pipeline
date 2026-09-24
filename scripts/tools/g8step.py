#!/usr/bin/env python3
"""g8step — ejecuta un paso del workflow dentro del PLAZO GLOBAL DEL JOB (hallazgo #3 de la revisión).

    python scripts/tools/g8step.py --name sofr --cap 240 -- python scripts/fetch_sofr.py
    python scripts/tools/g8step.py --name s01b --phase post --cap 120 -- python scripts/s01b.py --final
    python scripts/tools/g8step.py --ledger daily           # al final de la fase de descarga: registro + resumen

Plazos (variables que fija el primer paso del workflow):
  G8_JOB_DEADLINE_EPOCH  fin utilizable del job (inicio + timeout-minutes − margen de seguridad)
  G8_JOB_RESERVE_S       tiempo RESERVADO para la fase posterior (S01B, avisos, vigilancia, commit y registro)
Fase «fetch»: el paso termina, como muy tarde, en DEADLINE − RESERVE. Fase «post»: como muy tarde en DEADLINE.
Tiempo concedido = min(--cap, límite de la fase − ahora). Si es menor que --min, el paso NO se ejecuta
(SKIPPED_NO_TIME, código 3). Si se agota, SIGTERM al grupo de procesos, 10 s de gracia y SIGKILL (TIMEOUT, 124).

Al proceso hijo se le pasa su propio plazo (G8_JOB_DEADLINE_EPOCH = ahora + concedido, G8_JOB_RESERVE_S =
gracia): g8http.Budget lo respeta y el descargador termina y escribe (escritura atómica) antes del corte duro.

Cada paso deja una línea en G8_STEP_LOG (JSONL). --ledger la vuelca a data/_ingest/latest/actions__job_<job>.json
(la lee ingest_watch: los pasos omitidos por tiempo o cortados avisan) y al resumen del job.
Sin G8_JOB_DEADLINE_EPOCH (ejecución local u otro workflow) solo se aplica --cap. Solo stdlib (Python ≥3.9).
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GRACE_KILL_S = 10
OK, FAILED, TIMEOUT, SKIPPED = "OK", "FAILED", "TIMEOUT", "SKIPPED_NO_TIME"


def _utc(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def phase_limit(phase, env, now):
    dl = env.get("G8_JOB_DEADLINE_EPOCH")
    if not dl:
        return None
    dl = float(dl)
    if phase == "fetch":
        dl -= float(env.get("G8_JOB_RESERVE_S") or 0)
    return dl


def run_step(name, cmd, cap, min_s=20, phase="fetch", env=None, now=time.time, log_path=None):
    env = dict(os.environ if env is None else env)
    t0 = now()
    lim = phase_limit(phase, env, t0)
    allowed = float(cap) if lim is None else min(float(cap), lim - t0)
    rec = {"name": name, "phase": phase, "cap_s": cap, "allowed_s": round(allowed, 1), "started_utc": _utc(t0),
           "cmd": " ".join(cmd)[:200]}
    if allowed < min_s:
        rec.update(status=SKIPPED, rc=3, secs=0.0,
                   detail="sin tiempo: quedan %.0f s del plazo de la fase %s (mínimo %s s)" % (allowed, phase, min_s))
        _log(rec, log_path, env)
        print("[g8step] %s: %s — %s" % (name, SKIPPED, rec["detail"]), flush=True)
        return 3
    grace = max(1.0, min(15.0, allowed / 10.0))
    child_env = dict(env)
    child_env["G8_JOB_DEADLINE_EPOCH"] = "%.3f" % (t0 + allowed)
    child_env["G8_JOB_RESERVE_S"] = "%.1f" % grace
    p = subprocess.Popen(cmd, env=child_env, start_new_session=True)
    try:
        rc = p.wait(timeout=allowed)
        rec.update(status=OK if rc == 0 else FAILED, rc=rc)
    except subprocess.TimeoutExpired:
        _kill(p)
        rc = 124
        rec.update(status=TIMEOUT, rc=rc, detail="cortado al agotar %.0f s (fase %s)" % (allowed, phase))
    rec["secs"] = round(now() - t0, 1)
    _log(rec, log_path, env)
    print("[g8step] %s: %s rc=%s en %.1f s (concedidos %.0f s)" % (name, rec["status"], rc, rec["secs"], allowed), flush=True)
    return rc


def _kill(p):
    for sig, wait in ((signal.SIGTERM, GRACE_KILL_S), (signal.SIGKILL, 5)):
        try:
            os.killpg(p.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            p.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def _log(rec, log_path, env):
    path = log_path or env.get("G8_STEP_LOG")
    if not path:
        return
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n")


def ledger(job, env=None, root=ROOT, now=time.time):
    """Vuelca el registro de pasos a data/_ingest/latest/actions__job_<job>.json y al resumen del job."""
    env = os.environ if env is None else env
    path = env.get("G8_STEP_LOG")
    steps = []
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            steps = [json.loads(x) for x in fh if x.strip()]
    bad = [s for s in steps if s["status"] in (TIMEOUT, SKIPPED)]
    doc = {"job": job, "executor": "actions", "run_id": env.get("GITHUB_RUN_ID"), "written_utc": _utc(now()),
           "deadline_utc": _utc(float(env["G8_JOB_DEADLINE_EPOCH"])) if env.get("G8_JOB_DEADLINE_EPOCH") else None,
           "reserve_s": env.get("G8_JOB_RESERVE_S"), "steps": steps,
           "time_limited": [{"name": s["name"], "status": s["status"], "detail": s.get("detail", "")} for s in bad]}
    out = os.path.join(root, "data", "_ingest", "latest", "actions__job_%s.json" % job)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, out)
    summ = env.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a", encoding="utf-8") as fh:
            fh.write("\n### Plazo del job (g8step)\n\n| Paso | Estado | s | concedidos |\n|---|---|---|---|\n")
            for s in steps:
                fh.write("| %s | %s | %s | %s |\n" % (s["name"], s["status"], s.get("secs"), s.get("allowed_s")))
    print("[g8step] registro de %d pasos → %s (%d limitados por tiempo)" % (len(steps), out, len(bad)))
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = []
    if "--" in argv:
        i = argv.index("--")
        argv, cmd = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("--name")
    ap.add_argument("--cap", type=float, default=300)
    ap.add_argument("--min", type=float, default=20)
    ap.add_argument("--phase", choices=("fetch", "post"), default="fetch")
    ap.add_argument("--ledger")
    a = ap.parse_args(argv)
    if a.ledger:
        return ledger(a.ledger)
    if not a.name or not cmd:
        ap.error("--name y un comando tras -- son obligatorios")
    return run_step(a.name, cmd, a.cap, a.min, a.phase)


if __name__ == "__main__":
    sys.exit(main())

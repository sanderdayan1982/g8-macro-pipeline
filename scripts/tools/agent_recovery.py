#!/usr/bin/env python3
"""agent_recovery.py — acta P-9: relanzamientos de recuperación pedidos por el agente, ejecutados por la compuerta.

El agente (sin permisos de escritura) deja en .agent/recovery.json lo que quiere relanzar:
    [{"workflow": "cme_options.yml", "reason": "push rechazado 05-oct, sesión 02-oct sin guardar"},
     {"workflow": "intraday_fetch.yml", "inputs": {"group": "ASIA"}, "reason": "RBA publicó tras la pasada"}]
El job «publish» (código de main, con actions: write) llama a este script, que valida contra la lista cerrada y
dispara con `gh workflow run`. Nunca relanza algo que ya está en cola o en marcha. Máximo MAX_DISPATCH por pasada.

    python scripts/tools/agent_recovery.py .agent/recovery.json [--dry-run]   → JSON con lo lanzado y lo rechazado
"""
import argparse
import json
import subprocess
import sys

ALLOWED = {                       # workflows de DATOS idempotentes; nunca el propio agente, Validate ni despliegues
    "daily_update.yml": {},
    "intraday_fetch.yml": {"group": {"ASIA", "EU", "US", "LATE", "ALL"}},
    "cme_options.yml": {},
    "metals_update.yml": {},
    "usd_factor.yml": {},
}
MAX_DISPATCH = 3


def validate(requests):
    ok, rejected, seen = [], [], set()
    if not isinstance(requests, list):
        return [], [{"request": requests, "why": "no es una lista"}]
    for r in requests:
        wf = (r or {}).get("workflow") if isinstance(r, dict) else None
        inputs = (r or {}).get("inputs") or {} if isinstance(r, dict) else {}
        why = None
        if wf not in ALLOWED:
            why = "workflow fuera de la lista de recuperación"
        elif not isinstance(inputs, dict) or set(inputs) - set(ALLOWED[wf]):
            why = "inputs no permitidos"
        elif any(str(v) not in ALLOWED[wf][k] for k, v in inputs.items()):
            why = "valor de input no permitido"
        elif not str((r or {}).get("reason") or "").strip():
            why = "falta el motivo (evidencia)"
        elif wf in seen:
            why = "duplicado"
        elif len(ok) >= MAX_DISPATCH:
            why = "límite de %d relanzamientos por pasada" % MAX_DISPATCH
        if why:
            rejected.append({"request": r, "why": why})
        else:
            seen.add(wf)
            ok.append({"workflow": wf, "inputs": {k: str(v) for k, v in inputs.items()}, "reason": str(r["reason"])[:300]})
    return ok, rejected


def busy(workflow, run=subprocess.run):
    out = run(["gh", "run", "list", "--workflow", workflow, "-L", "5", "--json", "status"],
              capture_output=True, text=True)
    try:
        return any(x.get("status") in ("queued", "in_progress", "waiting", "pending") for x in json.loads(out.stdout or "[]"))
    except ValueError:
        return False


def dispatch(ok, dry=False, run=subprocess.run):
    done = []
    for item in ok:
        if busy(item["workflow"], run):
            done.append(dict(item, result="SKIPPED_BUSY"))
            continue
        cmd = ["gh", "workflow", "run", item["workflow"], "--ref", "main"]
        for k, v in item["inputs"].items():
            cmd += ["-f", "%s=%s" % (k, v)]
        if dry:
            done.append(dict(item, result="DRY_RUN", cmd=cmd))
            continue
        rc = run(cmd, capture_output=True, text=True).returncode
        done.append(dict(item, result="DISPATCHED" if rc == 0 else "FAILED_%d" % rc))
    return done


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    try:
        with open(a.path, encoding="utf-8") as fh:
            requests = json.load(fh)
    except FileNotFoundError:
        print(json.dumps({"dispatched": [], "rejected": [], "note": "sin peticiones"}))
        return 0
    except ValueError as e:
        print(json.dumps({"dispatched": [], "rejected": [{"why": "JSON ilegible: %s" % e}]}))
        return 0
    ok, rejected = validate(requests)
    print(json.dumps({"dispatched": dispatch(ok, a.dry_run), "rejected": rejected}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

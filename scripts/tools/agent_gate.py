#!/usr/bin/env python3
"""agent_gate.py — acta P-6: compuerta del agente de mantenimiento (la decide el workflow, no el agente).

    python scripts/tools/agent_gate.py --base <sha>      → JSON en stdout; rc 0 = AUTO, 3 = OWNER, 4 = NADA

AUTO  : todos los ficheros cambiados entre <base> y HEAD están en la lista permitida (descargadores, registro de
        fuentes, decisiones verificadas, proxy del dashboard, actas y tests del agente). El workflow los integra en
        main si además la suite y las comprobaciones de Validate pasan.
OWNER : algún fichero fuera de la lista (modelos, umbrales, motor de alertas, workflows, dashboard…) → PR para el
        propietario. Nunca se integra solo.
NADA  : el agente no hizo commits.
"""
import argparse
import fnmatch
import json
import subprocess
import sys

ALLOW = [
    "scripts/fetch_*.py",
    "scripts/g8common/cb_direct.py",
    "scripts/aud_nowcast.py",              # acta P-8: lectura de insumos y salida; el MODELO vive en sources/nowcast_aud.json
    "sources/registry.csv",
    "data/manual/policy_decisions.csv",
    "docs/_redirects",
    "docs/actas/ACTA_AGENTE_*.md",
    "tests/test_agent_*.py",
    "tests/fixtures/agent/*",
]
# Nunca AUTO aunque un patrón de ALLOW lo cubriera (metodología o huellas congeladas).
DENY = [
    "scripts/acm_g8.py", "scripts/s01b.py", "scripts/dashboard_alerts.py", "scripts/usd_factor.py",
    "scripts/book_risk.py", "scripts/metals_fairvalue_g8.py", "scripts/nzd_tp_synth.py", "scripts/real_yields_g8.py",
    "sources/freshness_*.csv", "sources/nowcast_aud.json", "tests/test_p8_aud_nowcast.py", "tests/test_exclusions.py", "tests/test_f3_freshness.py", "tests/frozen_data.py",
    ".github/*",
]


def allowed(path):
    if any(fnmatch.fnmatch(path, p) for p in DENY):
        return False
    return any(fnmatch.fnmatch(path, p) for p in ALLOW)


def classify(paths):
    paths = sorted(set(p for p in paths if p))
    if not paths:
        return {"decision": "NADA", "changed": [], "outside": []}
    outside = [p for p in paths if not allowed(p)]
    return {"decision": "OWNER" if outside else "AUTO", "changed": paths, "outside": outside}


def changed_since(base):
    out = subprocess.run(["git", "diff", "--name-only", base, "HEAD"], capture_output=True, text=True, check=True)
    return out.stdout.split("\n")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    a = ap.parse_args(argv)
    res = classify(changed_since(a.base))
    print(json.dumps(res, ensure_ascii=False))
    return {"AUTO": 0, "OWNER": 3, "NADA": 4}[res["decision"]]


if __name__ == "__main__":
    sys.exit(main())

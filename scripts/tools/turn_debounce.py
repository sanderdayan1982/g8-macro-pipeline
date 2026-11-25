#!/usr/bin/env python3
"""turn_debounce.py — una sola publicación por turno para un workflow disparado por `push` (t375u).

El job nzd-b2 del VPS sube sus familias (NZ-B2, CH-CURVA, CH-DIARIO, JP-TONA) en commits separados y
cada uno dispara «NZD CHF TONA shadow». Antes: dos o más commits de argos_rates.json en el mismo turno.

Regla: si en <ref> ya hay un commit POSTERIOR a <since> que toca alguna ruta de `on.push.paths` del
workflow, este run no publica (el run de ese commit posterior lo hará). El último run del turno publica.

  python scripts/tools/turn_debounce.py --workflow .github/workflows/X.yml --since <sha> [--ref origin/main]

Imprime `skip=true|false` (formato de $GITHUB_OUTPUT) y sale con 0. Error de git → rc 1 (ruidoso).
"""
from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys

import yaml


def push_paths(workflow_path):
    with open(workflow_path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    trigger = doc[True] if True in doc else doc["on"]
    return list(trigger["push"]["paths"])


def newer_commits_touching(since, ref, patterns, cwd=None):
    """Commits en since..ref (más nuevos que since) que tocan alguna ruta de *patterns*."""
    out = subprocess.run(
        ["git", "log", "--format=@%H", "--name-only", "%s..%s" % (since, ref)],
        cwd=cwd, capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise RuntimeError("git log rc=%s: %s" % (out.returncode, " ".join(out.stderr.split())[:300]))
    hits, sha = [], None
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("@"):
            sha = line[1:]
            continue
        if sha and any(fnmatch.fnmatch(line, p) for p in patterns):
            if not hits or hits[-1] != sha:
                hits.append(sha)
    return hits


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workflow", required=True)
    ap.add_argument("--since", required=True)
    ap.add_argument("--ref", default="origin/main")
    a = ap.parse_args(argv)
    try:
        hits = newer_commits_touching(a.since, a.ref, push_paths(a.workflow))
    except RuntimeError as e:
        print("[turn_debounce] %s" % e, file=sys.stderr)
        return 1
    if hits:
        print("[turn_debounce] commit posterior del mismo feed en %s: %s; publica su run" % (
            a.ref, ", ".join(h[:8] for h in hits)), file=sys.stderr)
    print("skip=%s" % ("true" if hits else "false"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

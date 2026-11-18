#!/usr/bin/env python3
"""t249u: aborta si el candidato de mantenimiento tiene rutas ignoradas o fuera de AUTO.

AUTO es ALLOW menos DENY en scripts/tools/agent_gate.py, cargado desde la copia
de confianza en RUNNER_TEMP/trusted_gate.py. No imprime el contenido de los ficheros.
"""
import importlib.util
import os
import subprocess
import sys


def load_gate():
    path = os.path.join(os.environ["RUNNER_TEMP"], "trusted_gate.py")
    spec = importlib.util.spec_from_file_location("trusted_gate", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv):
    base = argv[1]
    gate = load_gate()
    out = subprocess.check_output(["git", "diff", "--name-only", base, "HEAD"], text=True)
    paths = [line for line in out.splitlines() if line]
    bad = []
    for path in paths:
        ignored = subprocess.run(["git", "check-ignore", "-q", "--", path]).returncode == 0
        if ignored or not gate.allowed(path):
            bad.append(path)
    if bad:
        sys.stderr.write("candidato rechazado\n")
        for path in bad:
            sys.stderr.write(path + "\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

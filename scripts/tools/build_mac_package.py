#!/usr/bin/env python3
"""build_mac_package.py — construye la carpeta lote1/ que se copia al Mac (junto a los .command) con su MANIFEST.

    python scripts/tools/build_mac_package.py <destino>/lote1

Contenido: g8common/ (de scripts/), publicador, comprobación del token, nzd_local_run.sh, plist, instalador y
los tres descargadores del Mac (fetch_nzd_b2, fetch_chf_snb, fetch_tona_mac).
MANIFEST.sha256 = sha256 de cada fichero (el instalador lo verifica antes de nada).
"""
import hashlib
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MAC_FILES = ["push_nzd_to_github.py", "check_credentials.py", "nzd_local_run.sh", "com.g8.nzd-b2.plist",
             "instalar_lote1.py", "fetch_tona_mac.py"]
SCRIPT_FILES = ["fetch_nzd_b2.py", "fetch_chf_snb.py"]


def build(dest):
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    for f in MAC_FILES:
        shutil.copy2(os.path.join(ROOT, "mac", f), os.path.join(dest, f))
    for f in SCRIPT_FILES:                                   # lote 3B: descargadores del Mac
        shutil.copy2(os.path.join(ROOT, "scripts", f), os.path.join(dest, f))
    shutil.copytree(os.path.join(ROOT, "scripts", "g8common"), os.path.join(dest, "g8common"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    lines = []
    for dirpath, _, names in sorted(os.walk(dest)):
        for n in sorted(names):
            if n == "MANIFEST.sha256":
                continue
            p = os.path.join(dirpath, n)
            with open(p, "rb") as fh:
                lines.append("%s  %s" % (hashlib.sha256(fh.read()).hexdigest(), os.path.relpath(p, dest)))
    with open(os.path.join(dest, "MANIFEST.sha256"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return dest


if __name__ == "__main__":
    print(build(sys.argv[1]))

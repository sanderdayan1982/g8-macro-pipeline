#!/bin/zsh
# revertir_lote1.command — v2.0: restaura el conjunto completo (código + plist) guardado en backup_lote1_<fecha>
# por el instalador, con launchd descargado y el cerrojo tomado. Admite una carpeta concreta como argumento.
cd "$(dirname "$0")" || exit 1
/usr/bin/python3 lote1/instalar_lote1.py --revert "$@"

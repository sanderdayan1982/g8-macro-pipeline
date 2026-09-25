#!/bin/zsh
# instalar_lote1.command — v2.0 (revisión 24-sep): instala el lote 1 del Mac como un conjunto (código + launchd).
# Prepara y valida en una carpeta aparte; si algo falla, ABORTA sin tocar lo que está en uso. Pide confirmación.
# No guarda credenciales ni publica nada. Reversión: ./revertir_lote1.command
cd "$(dirname "$0")" || exit 1
/usr/bin/python3 lote1/instalar_lote1.py "$@"
rc=$?
echo; echo "Código de salida: $rc (0 = correcto o cancelado sin cambios; 1 = abortado, ver el motivo arriba)"
exit $rc

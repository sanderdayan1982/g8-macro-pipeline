#!/bin/zsh
# instalar_lote1.command — instala el lote 1 (publicador v2.0 + latido + avisos locales) en esta carpeta.
# NO guarda credenciales y NO activa launchd salvo que respondas "s" a la pregunta final.
# Reversión: ./revertir_lote1.command (restaura las copias *.bak_v13 creadas aquí).
cd "$(dirname "$0")" || exit 1
set -e
ts=$(date '+%Y%m%d%H%M%S')
for f in push_nzd_to_github.py nzd_local_run.sh com.g8.nzd-b2.plist; do
  [ -f "$f" ] && cp -p "$f" "$f.bak_v13_$ts"
done
[ -d g8common ] && mv g8common "g8common.bak_$ts"
cp -R lote1/g8common ./g8common
cp lote1/push_nzd_to_github.py lote1/check_credentials.py lote1/nzd_local_run.sh lote1/com.g8.nzd-b2.plist .
chmod +x nzd_local_run.sh
echo "1) Prueba sin escribir (lee la rama pública, no publica nada):"
/usr/bin/python3 push_nzd_to_github.py --dry-run --fetch-status nzd=0,chf=0,tona=0 || true
echo
echo "2) Comprobación del token (no lo muestra):"
/usr/bin/python3 check_credentials.py || true
echo
printf "¿Recargar launchd con el plist nuevo (08:00 + 17:00)? [s/N] "
read -r ans
if [ "$ans" = "s" ]; then
  cp com.g8.nzd-b2.plist ~/Library/LaunchAgents/
  launchctl unload ~/Library/LaunchAgents/com.g8.nzd-b2.plist 2>/dev/null || true
  launchctl load ~/Library/LaunchAgents/com.g8.nzd-b2.plist
  echo "launchd recargado."
fi
echo "Listo. Copias de seguridad con sufijo .bak_v13_$ts"

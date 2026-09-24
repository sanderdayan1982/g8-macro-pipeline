#!/bin/zsh
# revertir_lote1.command — vuelve al publicador v1.3 usando las copias *.bak_v13_* más recientes.
cd "$(dirname "$0")" || exit 1
for f in push_nzd_to_github.py nzd_local_run.sh com.g8.nzd-b2.plist; do
  b=$(ls -t "$f".bak_v13_* 2>/dev/null | head -1)
  if [ -n "$b" ]; then cp -p "$b" "$f"; echo "restaurado $f ← $b"; fi
done
cp com.g8.nzd-b2.plist ~/Library/LaunchAgents/ && launchctl unload ~/Library/LaunchAgents/com.g8.nzd-b2.plist 2>/dev/null; launchctl load ~/Library/LaunchAgents/com.g8.nzd-b2.plist
echo "Revertido. g8common/ puede quedarse (v1.3 no lo usa)."

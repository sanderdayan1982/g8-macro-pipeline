#!/usr/bin/env bash
# git_push_retry.sh — acta P-7: sube el commit local a main aunque otro proceso haya subido entretanto.
#   bash scripts/tools/git_push_retry.sh [intentos=5]
# Cada intento: integra main (git pull --rebase) y hace push; si otro proceso sube en medio, espera y repite.
# Conflicto en un fichero que tocan los dos: gana la versión de ESTE run (-X theirs = el commit que se reaplica),
# porque es la recién calculada. Solo afecta a los ficheros de este commit (p. ej. opciones: data/options,
# OPTIONS_SURFACE.json, data/alerts); todo lo demás llega intacto de main. Sin éxito tras N intentos → rc 1 (ruidoso).
set -u
n=${1:-5}
if [ "$(git rev-parse --is-shallow-repository 2>/dev/null)" = "true" ]; then
  git fetch -q --unshallow origin || true                       # rebase fiable: historia completa
fi
for i in $(seq 1 "$n"); do
  echo "push: intento $i de $n"
  if git pull -q --rebase -X theirs origin main; then
    if git push origin HEAD:main; then
      echo "✓ push correcto en el intento $i"
      exit 0
    fi
  else
    git rebase --abort 2>/dev/null || true
    echo "✗ no se pudo integrar main en el intento $i"
  fi
  sleep $((i * 5))
done
echo "✗ push fallido tras $n intentos"
exit 1

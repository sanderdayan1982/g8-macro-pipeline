#!/usr/bin/env bash
# git_push_retry.sh — acta P-7: sube el commit local a main aunque otro proceso haya subido entretanto.
#   bash scripts/tools/git_push_retry.sh [intentos=5]
# Cada intento: integra main (git pull --rebase --autostash) y hace push; si otro proceso sube en medio, espera y repite.
# Lo no commiteado en el árbol (t329u: private_data pull en freshness_snapshot) se guarda y se restaura; no bloquea el rebase.
# Conflicto en un fichero que tocan los dos: gana la versión de ESTE run (-X theirs = el commit que se reaplica),
# porque es la recién calculada. Solo afecta a los ficheros de este commit (p. ej. opciones: data/options,
# OPTIONS_SURFACE.json, data/alerts); todo lo demás llega intacto de main. Sin éxito tras N intentos → rc 1 (ruidoso).
# Excepción data/votes.csv (ORDEN_ENZO_REVISION-TRIPLE-01 bloque 2, 14-nov-2026 19:16 WAT):
# -X theirs no resuelve ese fichero. El driver votesAppend deja los bytes de main como prefijo
# y añade solo las filas de este commit cuya clave (fecha_cierre, script, ccy) no esté ya.
set -u
n=${1:-5}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
HELPER="$ROOT/scripts/tools/votes_csv_merge.py"
if [ "$(git rev-parse --is-shallow-repository 2>/dev/null)" = "true" ]; then
  git fetch -q --unshallow origin || true                       # rebase fiable: historia completa
fi
# Local al clon (Actions y los repos de prueba). No cambia .gitattributes del árbol.
mkdir -p .git/info
attr_line="data/votes.csv merge=votesAppend"
touch .git/info/attributes
grep -qxF "$attr_line" .git/info/attributes || printf '%s\n' "$attr_line" >> .git/info/attributes
git config merge.votesAppend.name "votes.csv append-only by key"
git config merge.votesAppend.driver "python3 \"$HELPER\" %O %A %B"
for i in $(seq 1 "$n"); do
  echo "push: intento $i de $n"
  # t329u: --autostash guarda lo no commiteado (p. ej. un private_data pull)
  # para que el rebase no falle con «You have unstaged changes».
  if git pull -q --rebase --autostash -X theirs origin main; then
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

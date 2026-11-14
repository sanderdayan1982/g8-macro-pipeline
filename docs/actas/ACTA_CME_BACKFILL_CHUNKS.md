# ACTA — backfill CME por meses (orden Sander 14-nov-2026 10:54 WAT)

**Decisión:** Sander, 14-nov-2026 10:54 WAT. Un solo PR. No se fusiona y no se dispara el workflow.

## Hallazgo
- `scripts/cme_options_backfill.py` pedía el rango entero (2024-06-01 → 2026-06-01, los 12 padres juntos) y, si se descargaba, iba sesión por sesión (`timeseries.get_range` por padre y por sesión).
- El workflow hacía un solo commit al final. Un job de 360 min que muriera antes del commit no dejaba nada y el siguiente disparo repetía el rango.
- El informe ya en `main` (`data/options/backfill_report.json`, commit `df86fdd7`, mensaje `data: cme options backfill 2026-11-14 [skip netlify]`) es un dry-run del rango entero: `get_cost` 20.481660716235 USD, `billable_size` statistics 13443086880 (13,443 GB) y definition 5028781680 (5,029 GB). No hubo `get_range`. Eso no es gasto.
- `scripts/cme_options_collector.py` sigue congelado por SHA-256 (`61276d17636d921913e383127b47176e946adc5eab59420416f4f3a244a477f9` en los tests). No se toca. Ningún fichero con huella congelada cambia, así que SALUD-1 no reescribe ninguna huella.

## Decisión
- Cada mes (2024-06 … 2026-05, 24 meses) es un chunk. Por chunk: un `timeseries.get_range` por padre (12) y por schema (`statistics`, `definition`). 24 × 12 × 2 = 576 `get_range` en una descarga completa. Antes, los mismos rangos se cotizan con `metadata.get_cost`, `get_record_count` y `get_billable_size` (576 × 3 = 1728 llamadas de metadata).
- La ventana del mes empieza el primer hábil a las 00:00 UTC y termina a las 06:00 UTC del siguiente hábil de la última sesión (`pull_window`, igual que el colector). Las filas vuelven a la sesión por la fecha de `ts_ref`, como `assemble_product`. Mayo 2026 termina en `2026-06-01T06:00:00` y no escribe ninguna sesión ≥ 2026-06-01.
- Se lee el cuerpo en streaming, un padre cada vez, y se tira el bruto. A disco solo van los CSV canónicos (`origin=backfill`) y `data/options/backfill_report.json`.
- Un mes está hecho cuando el informe lo marca `complete`. No se vuelve a pedir. Un directorio de sesión que ya tiene CSV no se reescribe.
- `spent_usd` es la suma de `get_cost` de los chunks cuya descarga terminó, y persiste entre pasadas. Si `spent_usd` + el `get_cost` del chunk siguiente pasa de 35.00 USD, no se descarga ese chunk, el informe dice por qué y el proceso sale con rc 3 (el mismo código distinto de cero del tope original). Corrección de ENZO, no una orden nueva de Sander: «clean exit» era redacción de ENZO; Sander pidió el tope acumulado de 35 USD. 35.00 cabe, 35.01 no (`over_cap`).
- HTTP 402 para y no deja sesión parcial (las escrituras van después de leer todos los rangos del mes; un `.tmp` se borra). rc 2. El gasto de ese chunk no se suma. El resto de sesiones ya cerradas de chunks anteriores no se deshace.
- Una sesión que no pasa `_sanity` (suelos o definiciones desconocidas) se salta y se anota (`session`, `root`, `reason`). El resto del chunk sigue. Cero liquidaciones en la sesión: festivo.
- `dry_run` sigue en true por defecto: tabla por chunk de coste / record_count / billable_size y el acumulado, sin `get_range`. El dry-run no aumenta `spent_usd` ni marca el mes completo.
- El workflow hace el bucle en el job. Tras cada chunk: `data: cme options backfill <YYYY-MM> [skip netlify]` y `scripts/tools/git_push_retry.sh`. Si el tope corta un chunk, ese informe se sube y el job sale con el rc del script (rojo). Los chunks ya terminados siguen en `main`. Deja de abrir chunks nuevos a los 320 min (el job corta a los 360) para que un relanzamiento siga. Un parón por tiempo usa el mensaje `data: cme options backfill time-budget [skip netlify]`.
- El informe de rango entero que ya está en `main` no se reescribe en este PR. Al correr, si no tiene `chunks`, se guarda como `legacy_whole_range_quote` y `spent_usd` arranca en 0.
- `committed_sha` se rellena cuando `git log` ya tiene el commit de ese mes (el commit que acaba de crearse todavía no puede citar su propio sha). El mes siguiente, o un relanzamiento, lo anota.

## Sin cambios
`scripts/cme_options_collector.py` y el resto de ficheros con SHA-256 congelado. Fórmulas, umbrales, s01b, factor USD. No se llama a Databento. No se dispara el workflow. No se fusiona el PR. No hay secretos en el commit.

## Pruebas
`tests/test_cme_options_backfill_chunks.py` (nuevo, HTTP falso): 576 llamadas, ventanas mensuales, resume, tope acumulado (35.00 descarga, el céntimo siguiente no), sanity sin deshacer el resto del chunk, 402 sin sesión parcial, dry-run sin gasto, presupuesto de 320 min, el workflow no tiene schedule y commitea con `[skip netlify]`.

Suite `python3 -m unittest discover -s tests -p 'test_*.py'` sobre `main` ya con `33ffdee` (PR #20): 963 pruebas, 6 skipped, 0 fallos. Verde. No se llamó a Databento.

## Siguiente
Sander fusiona y lanza el workflow a mano, primero en dry-run. La descarga solo con `dry_run=false`. Si el acumulado más el chunk siguiente pasa de 35 USD, ese chunk no se baja, el job queda rojo (rc 3) y lo ya subido permanece.

## Nota 14-nov-2026 16:55 WAT

**Orden:** Sander, 14-nov-2026 16:55 WAT. Un solo PR contra `main`. No se fusiona. No se modifica ningún test ya existente. No se toca el colector, el tope ni el troceo.

### Hallazgo
`lookup_commit_shas()` hacía `git log` con el directorio del fichero (`ROOT`, el checkout del proceso), no con el directorio de datos que recibe el script. En un temporal de prueba, que no es un repo, igual encontraba el historial real.

El 14-nov-2026 el run [43606084284](https://github.com/sanderdayan1982/g8-macro-pipeline/actions/runs/43606084284) (`workflow_dispatch`, `createdAt` 2026-11-14T14:40:27Z, `conclusion` success, `headSha` `26749feaf0e943cceac04e719c190eded67a520e`; comprobado en GitHub el 14-nov-2026) fue cost-only. Dejó 24 commits, de `bcf4d70edf4a8de1b3282a6de18ddee899099dde` (`data: cme options backfill 2024-06 [skip netlify]`, 2026-11-14 14:41:48 +0000) a `f10a81fb4092cdfdf896dc53a0c6c4162e36eb9b` (`2026-05`, 2026-11-14 15:10:09 +0000). El mensaje es el de descarga. En cada uno de esos 24 commits el informe tiene `dry_run` true y ningún chunk con `complete` true (comprobado con `git show <sha>:data/options/backfill_report.json` el 14-nov-2026). `tests/test_cme_options_backfill_chunks.py::ChunkCalls::test_one_month_is_one_range_per_parent_per_schema` exige `committed_sha is None` y fallaba porque el lookup devolvía `bcf4d70`.

El informe que está en `main` (`data/options/backfill_report.json`) ya cita 23 de esos shas en chunks con `complete` false. `2026-05` sigue en `null` porque ese commit no podía citarse a sí mismo. Este PR no reescribe ese fichero.

### Decisión
- `lookup_commit_shas()` y `apply_commit_shas()` leen git solo en el work tree del directorio de datos (`report_path` / `out_dir`). Si ese camino no tiene `.git`, devuelven cero shas y no fallan. No se usa el repo ambiente ni `GIT_DIR`.
- Con `dry_run=true` el workflow commitea `data: cme options backfill cost-only <YYYY-MM> [skip netlify]`. La descarga sigue en `data: cme options backfill <YYYY-MM> [skip netlify]`. Un parón por tiempo en dry-run usa el mismo prefijo (`cost-only time-budget`); en descarga el mensaje sigue siendo `data: cme options backfill time-budget [skip netlify]`.
- El lookup solo acepta el mensaje de descarga, y solo si en ese commit el chunk del mes tiene `complete` true. Un mensaje `cost-only` no entra aunque el chunk estuviera completo. Los 24 commits del run 43606084284 se ignoran por `complete` false: se puede hacer con esa comprobación, y se hace. No hace falta otro criterio.
- Si el scan del repo sale bien, un chunk incompleto solo conserva `committed_sha` cuando el lookup tiene un commit de descarga de ese mes. Así la siguiente pasada sobre este repo suelta los 23 shas de cost-only, y una descarga posterior no se los queda por el upsert. Si el directorio no es un repo, los shas que ya estaban escritos no se tocan.
- Ningún fichero con SHA-256 congelado cambia. SALUD-1 no reescribe ninguna huella.

### Sin cambios
`scripts/cme_options_collector.py`, el tope de 35 USD, las ventanas, el número de llamadas y los tests que ya existían. No se llama a Databento. No se dispara el workflow. No se fusiona el PR.

### Pruebas
`tests/test_cme_options_backfill_commit_scope.py` (nuevo): el mensaje cost-only del workflow, el lookup ignora cost-only y los commits llanos con `complete` false, el lookup queda en el repo de trabajo (y no sigue `GIT_DIR`), y una descarga no hereda el sha de un commit llano incompleto.

Suite `python -m pytest -q tests` (14-nov-2026): 971 passed, 6 skipped, 0 failed. Verde. `bash scripts/tools/validate_smoke.sh`: 977 tests, skipped=6, OK (YAML, registry, JS y brief en seco incluidos). No se llamó a Databento.

## Nota 14-nov-2026 16:36 WAT

**Orden:** Sander, 14-nov-2026 16:36 WAT. Un solo PR contra `main`. No se fusiona. No se añade schedule.

Entrada opcional `month` en `workflow_dispatch` de `.github/workflows/cme_options_backfill.yml`: tipo string, por defecto vacía, no obligatoria. Descripción: `YYYY-MM: process only this chunk. Empty = all 24 chunks as before.`

- Vacío: el job sigue con el mismo bucle `for month in $(python scripts/cme_options_backfill.py --list-months)`, el mismo `--month "$month" $mode`, el mismo `commit_month` y el mismo trato del rc.
- Con valor: llega por la variable de entorno `BACKFILL_MONTH`. `${{ inputs.month }}` está solo en `env:`, no en el shell. Si no cumple `^[0-9]{4}-[0-9]{2}$` o no aparece en `--list-months`, el job sale antes de cualquier llamada a Databento. Si vale, corre solo ese mes, con el mismo `--month`, `commit_month` y salida por rc.

No se modifica `scripts/cme_options_backfill.py`, `scripts/cme_options_collector.py` ni ningún test ya existente. Ninguna huella congelada en `tests/` cubre este workflow, así que SALUD-1 no reescribe ningún hash. Pruebas nuevas: `tests/test_cme_options_backfill_month_input.py`.

`python -m pytest -q tests` en este árbol: 969 passed, 1 failed, 6 skipped. El fallo es el ya existente en `origin/main` (`d8ebd825`): `tests/test_cme_options_backfill_chunks.py::ChunkCalls::test_one_month_is_one_range_per_parent_per_schema` espera `committed_sha` None y `lookup_commit_shas` ahora ve `bcf4d70edf4a8de1b3282a6de18ddee899099dde` (`data: cme options backfill 2024-06`). No se edita ese test.

Al fusionar `origin/main` (notas 16:55 y luego 16:36), el `month` de un solo chunk usa el mismo `commit_month` que el bucle: cost-only `data: cme options backfill cost-only ${month} [skip netlify]`, descarga `data: cme options backfill ${month} [skip netlify]`.

`python -m pytest -q tests` tras esa fusión: 988 passed, 6 skipped, 0 failed.

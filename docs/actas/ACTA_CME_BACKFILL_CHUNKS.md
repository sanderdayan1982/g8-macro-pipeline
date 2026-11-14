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
- `spent_usd` es la suma de `get_cost` de los chunks cuya descarga terminó, y persiste entre pasadas. Si `spent_usd` + el `get_cost` del chunk siguiente pasa de 35.00 USD, no se descarga ese chunk: salida limpia (rc 0), `stop_loop`, y el informe dice por qué. El tope sigue siendo estricto: 35.00 cabe, 35.01 no (`over_cap`).
- HTTP 402 para y no deja sesión parcial (las escrituras van después de leer todos los rangos del mes; un `.tmp` se borra). rc 2. El gasto de ese chunk no se suma. El resto de sesiones ya cerradas de chunks anteriores no se deshace.
- Una sesión que no pasa `_sanity` (suelos o definiciones desconocidas) se salta y se anota (`session`, `root`, `reason`). El resto del chunk sigue. Cero liquidaciones en la sesión: festivo.
- `dry_run` sigue en true por defecto: tabla por chunk de coste / record_count / billable_size y el acumulado, sin `get_range`. El dry-run no aumenta `spent_usd` ni marca el mes completo.
- El workflow hace el bucle en el job. Tras cada chunk: `data: cme options backfill <YYYY-MM> [skip netlify]` y `scripts/tools/git_push_retry.sh`. Deja de abrir chunks nuevos a los 320 min (el job corta a los 360) para que un relanzamiento siga. Un parón por tiempo usa el mensaje `data: cme options backfill time-budget [skip netlify]`.
- El informe de rango entero que ya está en `main` no se reescribe en este PR. Al correr, si no tiene `chunks`, se guarda como `legacy_whole_range_quote` y `spent_usd` arranca en 0.
- `committed_sha` se rellena cuando `git log` ya tiene el commit de ese mes (el commit que acaba de crearse todavía no puede citar su propio sha). El mes siguiente, o un relanzamiento, lo anota.

## Conflicto con un test ya existente (no se edita)
`tests/test_cme_options_backfill.py`, `Cap.test_main_does_not_download_when_cost_exceeds_cap`, exige que `main(["--download"])` devuelva distinto de cero cuando `get_cost` se pasa de 35 USD. La orden dice salida limpia. En este repo «clean exit» es rc 0 (el colector, cuando la sesión aún no está, sale 0). El test no se ha modificado. Esa aserción queda en rojo. El resto de ese test (no hay `timeseries`, no hay CSV) sigue siendo cierto y lo cubre `tests/test_cme_options_backfill_chunks.py`. No se improvisa un tercer código de salida.

## Sin cambios
`scripts/cme_options_collector.py` y el resto de ficheros con SHA-256 congelado. Fórmulas, umbrales, s01b, factor USD. No se llama a Databento. No se dispara el workflow. No se fusiona el PR. No hay secretos en el commit.

## Pruebas
`tests/test_cme_options_backfill_chunks.py` (nuevo, HTTP falso): 576 llamadas, ventanas mensuales, resume, tope acumulado (35.00 descarga, el céntimo siguiente no), sanity sin deshacer el resto del chunk, 402 sin sesión parcial, dry-run sin gasto, presupuesto de 320 min, el workflow no tiene schedule y commitea con `[skip netlify]`.

Suite `python3 -m unittest discover -s tests -p 'test_*.py'` en este entorno (Python 3.12; Actions usa 3.11): 958 pruebas, 6 skipped, 2 fallos. Uno es el conflicto de rc descrito arriba. El otro, `test_votes_ingest.GmailClient.test_cli_dry_run_prints_rows`, ya falla en `main` porque `data/votes.csv` está commiteado (1d19f911); no lo toca este cambio. YAML de workflows, `registry.csv`, sintaxis JS, equivalencia s01b y `dashboard_alerts.py --dry-run` en verde. No se llamó a Databento.

## Siguiente
Sander revisa el conflicto del rc 0. Si lo acepta, fusiona y lanza el workflow a mano, primero en dry-run. La descarga solo con `dry_run=false` y si la suma de los `get_cost` por chunk cabe en 35 USD.

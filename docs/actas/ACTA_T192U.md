# ACTA — volumen CME mes a mes (orden t192u)

**Decisión:** Sander, 15-nov-2026. Un solo PR contra `main`. No se fusiona. No se dispara el workflow. No se llama a Databento.

## Hallazgo
El run [43724209169](https://github.com/sanderdayan1982/g8-macro-pipeline/actions/runs/43724209169) (`workflow_dispatch`, `headSha` `64c5ec09f8374668c73dc132d4e7b109fd7fd83f`, `createdAt` 2026-11-15T14:24:44Z, `conclusion` failure; log del job `132842486668` leído el 15-nov-2026) pidió `mode=volume_only`, `dry_run=false`, `volume_start=2026-07-01`, `volume_end` vacío. El script usó el fin exclusivo `2026-11-15` (hoy UTC). La cotización de la ventana entera fue `get_cost` 3.322251435370 USD (`statistics` 1.999918147922, `definition` 1.322333287448), por debajo del tope de 6.00. Después imprimió `volume 2026-07-01 EUU.OPT statistics` y `volume download stopped: HTTP 504 on timeseries.get_range`. Salió con rc 1, no hubo commit. El paso de artifact dijo que no había ficheros en el temporal. No quedó CSV ni gzip.

## Decisión
- `volume_only` recorre la ventana por meses civiles, en orden: 2026-07, 2026-08, 2026-09, 2026-10 y 2026-11 hasta el fin exclusivo. Cada mes se cotiza solo (`metadata.get_cost`, `get_record_count`, `get_billable_size`, los 12 padres, `statistics` y `definition`) y, si cabe en el tope, se descarga solo ese mes.
- Un mes con `complete: true` en `data/options/volume_backfill_report.json` no se vuelve a pedir cuando el intervalo guardado (`start`, `end_exclusive`) cubre el tramo. El mes parcial —el fin exclusivo es anterior al día 1 del mes siguiente, o el inicio es posterior al día 1— está completo solo hasta ese intervalo. Una repetición con el mismo fin no lo pide. Una repetición con un fin posterior pide solo la cola `[fin guardado, fin nuevo)`, la cotiza aparte y, si termina, alarga `end_exclusive`. La cola no se baja si `spent_usd` más esa cotización pasa de 6.00 USD.
- Tope 6.00 USD acumulado: `spent_usd` ya guardado en el informe más la cotización del mes siguiente. 6.00 cabe; 6.01 no. Si no cabe, no se descarga ese mes ni los posteriores, se anota el motivo y el proceso sale con rc 3. Los meses ya cerrados siguen en disco y, en el workflow, ya están en `main`.
- HTTP 504: el mismo mes se reintenta una vez (cotización y descarga). Un segundo 504 en ese mes para el run, anota el mes (`HTTP 504 twice`), conserva los meses anteriores y sale con rc 1. El mensaje es `volume_only stopped: HTTP 504 twice on <YYYY-MM>. Earlier months are kept. Later months were not started.`
- `dry_run=true` cotiza cada mes que no esté ya completo, imprime el total y no escribe ficheros ni hace commit. Si la suma pasa de 6.00 USD sale con rc 3, también sin ficheros.
- El modo `full` no cambia: mismos 24 meses, mismo informe, mismos commits, mismo tope de 35 USD. Los CSV de settle/OI no se abren en escritura.
- En el workflow real la descarga es el job `volume-month`, matriz con `max-parallel: 1` y `fail-fast: true`. Cada celda hace commit `data: cme options volume-only <YYYY-MM> [skip netlify]` (solo el árbol hermano y `volume_backfill_report.json`) y sube el gzip de ese mes como artifact `cme-options-volume-raw-<run_id>-<YYYY-MM>`, retención 90 días, antes de que empiece el mes siguiente. El gzip no se añade a git. Una celda que falla cancela las que no han empezado; las ya terminadas conservan commit y artifact. `volume-plan` solo lista los meses (`--list-volume-months`) y no recibe la clave.
- El paso del job `backfill` sigue cotizando la ventana en un solo proceso cuando `dry_run=true` (el script parte los meses por dentro). En una descarga de Actions ese paso no llama a Databento: la variable `VOLUME_MONTHS_JOB` está definida y el trabajo lo hace `volume-month`. El test de shell de t189u no define esa variable, así que sigue viendo el commit del árbol hermano. El literal `git commit -m "data: cme options volume-only ${vol_start} ${vol_end} [skip netlify]"` se queda en un comentario para ese test; el commit que se ejecuta es el del mes.
- SALUD-1: no cambia ningún fichero con huella congelada. No se reescribe ninguna huella.

## Sin cambios
Fórmulas, umbrales, ventanas de z, ACM, s01b, factor USD, el modo `full`, el colector diario, el resumen, el dashboard y `docs/index.html`. Los CSV canónicos de settle/OI. No hay secretos en el commit. No se llama a Databento. No se dispara ningún workflow. No se fusiona.

## Pruebas
`tests/test_cme_options_volume_months.py` (nuevo, fixture sintético): orden de los meses, salto de un mes completo, cola del mes parcial, dry-run por mes más el total, tope a mitad de ventana, tope con `spent_usd` ya guardado, 6.00 exacto, un solo 504 que reintenta y termina, dos 504 que paran y conservan el mes anterior, y la estructura del workflow (matriz, commit y artifact por mes).

Los tests de t189u no se editan y siguen en verde.

`python3 -m unittest discover -s tests -p 'test_*.py'` (15-nov-2026, Python 3.12 con las dependencias de `requirements.txt` y `pyyaml`): 1062 pruebas, 6 skipped, 3 fallos. Los tres fallos ya estaban admitidos en `main`: `test_flujos01_pr2.Rv21Vectors.test_live_eur_matches_the_oracle_and_the_roll_session_is_na` (`NEUTRAL` != `UNAVAILABLE`), `test_fxvol_shadow.FxvolShadow.test_additive_fields_do_not_recompute_existing_ones` y `test_fxvol_shadow.FxvolShadow.test_five_sessions_within_tolerance`. `test_idempotent_and_under_60s` pasó en esta corrida. No se llamó a Databento.

## Siguiente
Sander fusiona y lanza el workflow a mano. Inputs del run real, los mismos del 43724209169:

- `mode`: `volume_only`
- `dry_run`: `false`
- `month`: vacío (el modo `full` es el único que lo usa)
- `volume_start`: `2026-07-01`
- `volume_end`: vacío (fin exclusivo = hoy UTC; el 15-nov-2026 fue `2026-11-15`)

La cotización de la ventana entera fue 3.322251435370 USD. Cada mes se vuelve a cotizar; el tope de 6.00 se aplica a la suma de esas cotizaciones más lo ya anotado en el informe. Un mes que devuelva 504 dos veces deja el job en rojo y lo ya subido permanece.

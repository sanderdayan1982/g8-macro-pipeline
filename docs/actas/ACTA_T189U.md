# ACTA — backfill solo de volumen compensado (orden t189u)

**Decisión:** Sander, 15-nov-2026 14:19 WAT. Un solo PR contra `main`. No se fusiona y no se dispara el workflow. No se llama a Databento.

## Hallazgo
- El PR #33 (fusionado en `main`, `b8cc2587`) guarda el `stat_type` 6 en `data/options/volume_shadow/YYYY-MM-DD/{ROOT}.csv` desde el colector diario. El backfill (`scripts/cme_options_backfill.py`, `.github/workflows/cme_options_backfill.yml`) seguía pidiendo `statistics` y `definition` para settle y OI, tiraba el `stat_type` 6 y no archivaba el cuerpo.
- Las claves del CSV hermano son `session,root,type,symbol,expiry,right,strike,volume`. `statistics` no trae strike, expiry ni right: el colector los saca de `definition` (`build_definition_map` + `contract_rows`). Sin `definition` el backfill no puede escribir la misma clave.
- El modo completo está cerrado por tests que comparan el bucle de 24 meses byte a byte (`tests/test_cme_options_backfill_month_input.py`) y que prohiben en este `.py` los literales `volume_shadow`, `STAT_VOLUME` y `G8_OPT_RAW_DIR` (`tests/test_cme_options_volume_shadow.py`). Esos tests no se editan.

## Decisión
- Nuevo input `mode`: `full` (defecto) o `volume_only`. `full` no cambia: mismos meses 2024-06..2026-05, mismo informe, mismo reanudado, mismos mensajes de commit.
- `volume_only` pide el esquema `statistics` y también `definition`, solo para unir `instrument_id` a la misma clave que el colector. La cotización es de la ventana entera (todos los padres), con `metadata.get_cost`, `get_record_count` y `get_billable_size`, antes de cualquier `timeseries.get_range`. Se imprime en el step summary.
- Tope 6.00 USD sobre esa suma. 6.00 cabe; 6.01 no. Por encima: rc 3, sin descarga y sin ficheros.
- `dry_run` (defecto del workflow, sigue en true) en este modo solo cotiza. No escribe CSV, ni el informe de volumen, ni el bruto, y el workflow no hace commit.
- La descarga escribe solo `data/options/volume_shadow/YYYY-MM-DD/{ROOT}.csv` (mismo encabezado que el colector; el nombre del directorio sale de `VOLUME_DIR` del colector para no deletrear el token vetado) y `data/options/volume_backfill_report.json`. No abre en escritura el CSV canónico ni `backfill_report.json`. Un fichero de sesión que ya existe se salta. Si todos existen, no hay `get_range`.
- Sin `stat_type` 6 en esa sesión el campo queda vacío. No se rellena con 0 ni se arrastra de otro día. Un 0 publicado se queda en `0`.
- Cada cuerpo de pago se comprime con gzip (`csv.gz`) en `G8_VOL_RAW_DIR`. En Actions es `${{ runner.temp }}/cme-options-volume-raw`, artifact `cme-options-volume-raw-<run_id>`, retención 90 días, `if-no-files-found: ignore`. En local cae en `data/options/raw/`, que ya está en `.gitignore`. No se commitea.
- Ventana por defecto: inicio `2026-07-01` inclusive, fin exclusivo = hoy UTC (`volume_end` vacío). El grupo de concurrencia sigue siendo `cme-options-backfill`.
- El commit de datos, solo en descarga con rc 0, termina en `[skip netlify]` y solo añade el árbol hermano y `volume_backfill_report.json`.
- SALUD-1: no cambia ningún fichero con huella congelada (`cme_options_collector.py`, `cme_options.yml`, `build_options_summary.py`, `dashboard_alerts.py`). No se reescribe ninguna huella.

## Sin cambios
Fórmulas, umbrales, ventanas de z, ACM, s01b, factor USD, el modo `full` del backfill, el colector diario, el resumen, el dashboard y `docs/index.html`. Números publicados: ninguno. No hay secretos en código ni en el log (el archivo anota el nombre y el tamaño). No se llama a Databento. No se dispara ningún workflow. No se fusiona.

## Pruebas
`tests/test_cme_options_volume_backfill.py` (nuevo, fixture sintético, sin red):
- coste por encima de 6.00 USD sale antes de `get_range` y no crea ficheros;
- `dry_run` no escribe nada;
- `volume_only` no modifica el CSV canónico ni `backfill_report.json`;
- un fichero de sesión ya presente se salta, y si la ventana está completa no hay `get_range`;
- sin `stat_type` 6 el campo queda vacío, un 0 real sigue en `0`, y no se arrastra del día anterior;
- el workflow, en dry-run de este modo, no hace commit; una fecha mala no llama a Python; la descarga commitea solo el árbol hermano con `[skip netlify]`.

## Siguiente
Sander fusiona y lanza el workflow a mano. Primero dry-run (`mode=volume_only`, `dry_run=true`, inicio `2026-07-01`, fin vacío). Si el step summary muestra `get_cost` ≤ 6.00 USD, la descarga es `dry_run=false` con las mismas fechas. Si el coste pasa de 6.00 USD el job queda rojo y no baja nada. Una segunda pasada de una ventana que ya tiene todos los `{ROOT}.csv` no vuelve a llamar a `get_range`; si falta alguno, la cotización sigue siendo la de la ventana entera.

# ACTA FAMILIAS-00 — backfill Databento (parte B)

**Decisión:** Sander, FAMILIAS-00, firmada 14-nov-2026 00:18 WAT. Este workflow se fusiona primero, con su OK. El agente no lo fusiona y no lo dispara.

## Hallazgo
El colector diario (`scripts/cme_options_collector.py`) está congelado por SHA-256. Cubre la sesión viva, con tope de 0,25 USD al día. No sirve para el hueco 2024-06-01 → 2026-05-31 sin tocarlo.

## Decisión
- Workflow manual `cme_options_backfill.yml` (`workflow_dispatch` solo). Entrada `dry_run` por defecto `true`: solo `metadata.get_cost`, `get_record_count` y `get_billable_size` del rango entero y de los dos schemas.
- Script `scripts/cme_options_backfill.py`. Reutiliza dataset `GLBX.MDP3`, `stype_in=parent`, los 12 padres y el join del colector importándolo. No modifica ese fichero.
- Tope duro 35,00 USD sobre la suma de `get_cost`. Por encima: no descarga y sale distinto de cero. HTTP 402: para, borra las sesiones creadas en esa pasada, no deja basura para el commit.
- Sesiones ≥ 2026-06-01 y cualquier sesión que ya tenga un CSV: no se reescriben.
- Marca de origen: columna `origin=backfill` al final del CSV. Los lectores (`cme_fxvol_shadow.read`, `build_options_summary.read_rows`) usan nombres de columna. Una columna de más al final no mueve las diez columnas existentes. No hace falta el sidecar.
- `actual_cost_usd` queda `null`: ni `get_cost` ni `timeseries.get_range` devuelven el cargo liquidado.
- Credencial: solo `DATABENTO_API_KEY`.
- Commit de datos con `[skip netlify]`.

## Sin cambios
`scripts/cme_options_collector.py`, `scripts/cme_fxvol_shadow.py`, `scripts/build_options_summary.py`, `docs/index.html`, reglas y umbrales FXVOL, tests ya existentes.

## Pruebas
`tests/test_cme_options_backfill.py` (nuevo): tope > 35 bloquea la descarga; no se pisa una sesión existente ni una ≥ 2026-06-01; la columna `origin` vale `backfill`; 402 no deja carpeta parcial. Sin red.

## Siguiente
Sander fusiona este PR y, ya en `main`, lanza el workflow a mano. La primera vez en modo coste (el defecto). La descarga solo si cambia la entrada a false y el coste cabe en 35 USD. Un job de GitHub se corta a las 6 h: si el proceso muere antes del commit, no queda nada en el repo y un segundo disparo volvería a pedir el rango entero.

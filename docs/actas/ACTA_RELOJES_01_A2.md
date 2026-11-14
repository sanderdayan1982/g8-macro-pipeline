# ACTA RELOJES-01 A2 — COT del lunes (14-nov-2026)

**Orden:** RELOJES-01, bloque A, punto A2. Firmada por Sander el 14-nov-2026 a las 21:47 WAT.
**Base:** `origin/main` en el momento del cambio (los 13 commits de backfill CME posteriores a `857d6e7f` no tocan el colector ni los horarios).

## Hallazgo
- La CFTC publica el COT el lunes 16-nov-2026, el 30-nov-2026 y el 28-dic-2026 a las 15:30 ET (20:30 UTC en invierno). Comprobado en el enunciado de la orden; el comentario de cabecera del workflow decía «Fri 19:30 UTC», que es la hora de verano.
- `.github/workflows/g8_port_run.yml` solo tenía `5 21 * * 5` y `5 9 * * 6`. En esas semanas el informe del lunes no entraba hasta el viernes siguiente.
- `scripts/pos_g8_cot_collector.py` escribía `data/pos_g8_cot.json` en cada pasada, también con la misma `report_date` (cambiaba `generated_utc`).
- El MDP semanal de metales (`metals_fairvalue_g8.py`, lanzado por `metals_update.yml`) se ancla a la rejilla W-TUE y el overlay COT sale de `load_cot_mm` (`POS_G8_COT.csv` / `POS_G8.csv` / `COT_G8.csv`, frecuencia `weekly`). `sources/freshness_outputs.csv` lista `pos_g8_cot.json` como entrada de `MFV_G8_XAU.csv` y `MFV_G8_XAG.csv`. La página lee el mismo JSON para la matriz MDP × COT. No había una pasada de metales atada al informe del lunes.

## Decisión
- Se añaden `5 21 * * 1` y `5 9 * * 2` en el workflow, en `mac/dispatch_schedule.json` (si no, `test_D6_schedule_matches_workflow_crons` deja de coincidir) y en `sources/schedules.csv` (`COT_MON`, `COT_TUE`). Se conservan el viernes y el sábado.
- El colector escribe el JSON solo si la fecha del informe es estrictamente posterior a la del fichero. La misma fecha u otra anterior no toca bytes, ni `generated_utc`. Fechas mezcladas o ausentes no se escriben.
- Tras esa pasada, el MDP semanal se relanza solo si el colector escribió un informe nuevo y el cron fue el del lunes o el del martes. Viernes, sábado y un `workflow_dispatch` no lo relanzan. Si no toca, no abre las salidas ni `generated` / `generated_at`.
- La cabecera del workflow dice que el COT es a las 20:30 UTC en invierno (15:30 ET; 19:30 UTC en verano), con datos del martes.

## Sin cambios
- Fórmulas, umbrales, ventanas, schema `pos_g8_cot/1.2.0` y el modelo de metales. No se edita `metals_fairvalue_g8.py`.
- El paso a dos publicaciones por semana (miércoles y viernes, anunciado por la CFTC para antes de fin de 2026) no se implementa. `sources/suite_contract.json` sigue describiendo el viernes y el reintento del sábado.
- Ningún número publicado. No hay commit de datos.

## Pruebas
- `tests/test_relojes01_a2.py`: los cuatro cron; el colector no escribe si la fecha no es nueva; el MDP solo corre tras un COT nuevo del lunes o del martes y no toca `generated_at` en caso contrario.
- `python3 -m pytest -q tests -rf` el 14-nov-2026, en paralelo sobre `origin/main` (`35119371`) y sobre esta rama: los mismos 3 fallos, con el mismo `AssertionError`. Main: 3 failed, 985 passed, 6 skipped. Rama: 3 failed, 996 passed, 6 skipped. Los 11 passed de más son `tests/test_relojes01_a2.py`. `test_D2_weekday` y `test_p12` pasan sin editarlos (Anexo 1 no hizo falta).
- No se tocan votos ni backfill. Los ficheros que la suite escribió en `data/CME_FXVOL_shadow.json` y `data/options/fxvol_history.csv` se restauraron y no entran en el commit.

## Siguiente
- El doble semanal (miércoles y viernes) queda fuera. Las piezas que cuentan un informe por semana están listadas en el informe de la orden, con fichero y línea.

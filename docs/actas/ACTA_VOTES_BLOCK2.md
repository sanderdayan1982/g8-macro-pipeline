# ACTA — ORDEN_ENZO_REVISION-TRIPLE-01 bloque 2 (registro de votos)

Orden de Sander, firmada 14-nov-2026 19:16 WAT. Plazo: fusionada antes del mar 17-nov 04:00 WAT. Este PR no se fusiona aquí. La base es `cursor/votes-declared-parts-f7fe` (PR #26); hay que retargetear a `main` cuando ese PR entre.

## Hallazgo
1. El intento final de `votes_ingest.yml` corría a las 04:00 UTC (05:00 WAT), quince minutos antes del corte de las 05:15 WAT. Un correo llegado entre las 05:00 y las 05:15 quedaba fuera del buzón en el momento de escribir `MISSING`, y la fila ya no se añade.
2. Un día laborable sin vela 1D (cierre completo CME / Nueva York) se trataba como sesión. El informe semanal podía contarlo como incompleto.
3. `workflow_dispatch` dejaba `attempt` en `final`. Una prueba manual escribía `MISSING`.
4. `scripts/tools/git_push_retry.sh` resuelve cualquier conflicto con `-X theirs`. En `data/votes.csv` eso se queda con la copia de este run y tira las filas que main había añadido.

Comprobado el 14-nov-2026: calendario NYSE Group 2026–2028 (https://ir.theice.com/press/news-details/2025/NYSE-Group-Announces-2026-2027-and-2028-Holiday-and-Early-Closings-Calendar/default.aspx) y horario de festivos CME Group (https://www.cmegroup.com/trading-hours.html). Acción de Gracias 2026-11-26, Navidad 2026-12-25, 4 de julio observado 2026-07-03 y Viernes Santo 2026-04-03 son cierres. El día después de Acción de Gracias y Nochebuena tienen vela (cierran antes). Columbus Day y Veterans Day no cierran. El 1 de enero de 2028 cae en sábado y NYSE no lo observa.

No hay en el repo un calendario por regla que sirva de reloj de la vela. `sources/calendars.csv` y `G8_HOLIDAYS` son listas de fechas. `scripts/tools/gen_calendars.py` depende de la librería `holidays`, que no está en `requirements.txt`. La regla queda en `scripts/votes_ingest.py`.

## Decisión
- Cron final: `20 4 * * 2-6` (04:20 UTC, 05:20 WAT), después del corte. El de las 03:00 UTC no cambia. El mismo cron entra en `mac/dispatch_schedule.json` porque `tests/test_p12_mac_dispatch.py` exige que coincidan y ese test no se edita.
- En el intento final, `list_message_ids` recorre todas las páginas (`nextPageToken`) antes de `messages.get` y antes de escribir `MISSING`. stderr lleva `gmail window pages=… ids=… query=…` solo en ese intento.
- `attempt_for` sigue en las 05:00 WAT. `tests/test_votes_ingest.py::CloseClock.test_attempt_flips_at_0500_wat` no está autorizado. El workflow pasa `--attempt` y no usa esa función.
- Festivo sin vela: ninguna fila, igual que el fin de semana. `votes_weekly.json` lo omite de `incomplete_sessions` y no lo cuenta como día completo.
- `workflow_dispatch` `attempt` por defecto: `first`.
- `data/votes.csv` tiene un driver local (no va en `.gitattributes`) que llama a `scripts/tools/votes_csv_merge.py`. Los bytes de main quedan de prefijo. Se añaden solo las claves `(fecha_cierre, script, ccy)` que falten. El resto de ficheros sigue con `-X theirs`.

## Sin cambios
Ningún fichero con SHA-256 congelado en `tests/` cambia: `git_push_retry.sh` no está fijado por huella, así que SALUD-1 no reescribe ningún hash. `data/` no entra en este commit. Fórmulas, umbrales, Pine y la Suite G8 no se tocan.

## 14-nov-2026 — orden expresa de Sander (19:56 WAT, t166u)

`tests/test_p12_mac_dispatch.py` `Schedule.test_D2_weekday` es el único test ya existente que cambia. A las 07:05 UTC del sábado 2026-10-10 la ventana de recuperación de 3 h incluye el turno de las 04:20 UTC, así que el Mac lanza `metals_update.yml` y `votes_ingest.yml`. La aserción acepta los dos, en ese orden. Ningún otro test se toca.

## Pruebas
Nuevas: `tests/test_votes_block2.py`, `tests/test_votes_csv_push.py` (dos repos git locales).

`python3 -m unittest discover -s tests -p 'test_*.py'` (2026-11-14, tras t166u, Python 3.12 con `requirements.txt` y `pyyaml`; en esta máquina no hay 3.11): 1021 ran, 1012 passed, 6 skipped, 3 failed.

Rojos previstos, ajenos a este bloque: `tests/test_fxvol_shadow.py` (`test_additive_fields_do_not_recompute_existing_ones`, `test_five_sessions_within_tolerance`) y `tests/test_flujos01_pr2.py` `Rv21Vectors.test_live_eur_matches_the_oracle_and_the_roll_session_is_na`.

Ese rojo de `test_D2_weekday` queda cubierto por la orden t166u de arriba. `test_D6_schedule_matches_workflow_crons` sigue en verde: el cron del workflow y el de `dispatch_schedule.json` coinciden.

## Siguiente
Retargetear este PR a `main` cuando el PR #26 esté fusionado. No fusionar este bloque antes.

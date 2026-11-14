# ACTA — REVISION-TRIPLE-01 bloque 1 (orden Sander 14-nov-2026 19:16 WAT)

**Decisión:** Sander, orden ORDEN_ENZO_REVISION-TRIPLE-01, 14-nov-2026 19:16 WAT. Un solo PR contra `main`. No se fusiona. No se dispara ningún workflow. No se toca `data/`.

## Hallazgo
- `complete` se ponía a true al terminar `download_month` aunque `skipped` trajera un fallo de `_sanity` (suelo o definiciones). El único test ya existente que esa regla tenía que voltear es `tests/test_cme_options_backfill_chunks.py:476`.
- El tope miraba `spent_usd + get_cost` y el informe se escribía después de `timeseries.get_range`. Un corte a mitad del chunk no dejaba reserva: el relanzamiento volvía a pedir el rango.
- `write_report` escribía el JSON directo sobre el fichero.
- La ventana del mes sigue cerrando a las 06:00 UTC del siguiente hábil (`month_window` / `pull_window`). No se baja a las 00:00. Ver punto 4.

## Decisión
1. `complete` es true solo si cada fila de `skipped` es `exists`, `protected`, o `closed` con el calendario de días sin settlement de CME: 1 de enero y 25 de diciembre. Comprobado el 14-nov-2026 en los avisos de CME Group: el 1-ene-2025 no hay fichero de settlement (`https://www.cmegroup.com/tools-information/holiday-calendar/files/2025-new-years-advisory.pdf`) y el 25-dic-2025 CME no calcula ni publica settlements (`https://www.cmegroup.com/tools-information/holiday-calendar/files/2025/christmas-holiday-settlement-times-2025.pdf`). La regla es esa fecha de contratación en cualquier año del tramo. En 2024-06…2026-05 caen en día de semana, así que no se aplica un traslado de sábado o domingo. Juneteenth, Viernes Santo, Thanksgiving y los lunes festivos no entran: no hay aviso de CME de que no haya settlement. `no records` no es un cierre. Un día vacío que el calendario no cierra sigue en `holidays` y no entra en `skipped`: meterlo como `no records` voltearía `complete` en `test_one_month_is_one_range_per_parent_per_schema` (línea 223) y en `test_download_does_not_inherit_a_plain_cost_only_sha` (línea 262), y esta orden no autoriza editarlos.
2. Antes de `get_range` se escribe `reserved_usd` del chunk con `reservation=pending` (el `get_cost` ya cotizado). El tope compara `spent + reserved + get_cost` de este chunk. Si el chunk ya tiene reserva `pending`, no hay llamada a Databento y el proceso sale 4 con el motivo de reconciliar. `--reconcile-reservation YYYY-MM --reconcile-as spent` suma esa reserva a `spent_usd` una sola vez; una descarga posterior es un cargo nuevo. `--reconcile-as release` suelta la reserva. Una descarga que termina y puede marcarse `complete` pasa la reserva a `spent`. Si termina pero `skipped` no permite `complete`, la reserva sigue `pending` para que el reintento no vuelva a llamar a `get_range`. El workflow no se toca: ya commitea el informe cuando el proceso vuelve, y `tests/test_cme_options_backfill_month_input.py` fija el bucle byte a byte. La reserva queda escrita en disco antes de `get_range`; el push es el commit que el workflow ya hace al salir el proceso.
3. No se aplica. `complete` solo cuando `committed_sha` está en `main` rompe tests que esta orden no deja editar: exigen `complete` true con `committed_sha` None justo después de la descarga (`test_cme_options_backfill_chunks.py:223` y `:227`, `:274`; `test_cme_options_backfill_commit_scope.py:262-263`). Exigir el sha dentro de `month_complete` también rompe `test_complete_month_skips_http`, cuyo `committed_sha` es `abc123` y no es un commit de `main`.
4. No se aplica. Cerrar a las 00:00 del siguiente hábil deja fuera el open interest de la última sesión. Databento filtra `start`/`end` por `ts_recv` cuando el schema lo trae (documentación `timeseries.get_range`, comprobada el 14-nov-2026: `https://databento.com/docs/api-historical/timeseries/timeseries-get-range`). El schema `statistics` lleva `ts_recv` (huella del colector). El colector anota que el OI de la sesión T se publica ~01:44 UTC de T+1 (el hábil siguiente si T es viernes) y no da la sesión por disponible hasta las 03:30 UTC de ese día (`AVAILABILITY_MIN_UTC_HOUR = 3.5`). Esas filas tienen `ts_ref` de la última sesión. El mes siguiente empieza a las 00:00 y solo escribe sus propias sesiones, así que el OI no lo guarda nadie. El settlement de ~19:13 UTC del propio día sí caería dentro de un corte a las 00:00; el OI no. Los tests que fijan `2024-07-01T06:00:00` y `2026-06-01T06:00:00` (`test_months_and_windows`, `test_one_month_is_one_range_per_parent_per_schema`) tampoco se pueden editar. La ventana sigue en 06:00. El solape de las primeras 6 h del mes siguiente es el tramo donde va ese OI.
5. `write_report` escribe `<nombre>.tmp` y hace `os.replace`. Si `os.replace` falla, el informe anterior queda entero.
6. `--list-skipped` imprime los meses cuyo chunk tiene `skipped` no vacío y sale. No pide clave ni llama a Databento. Sirve cuando el backfill que está corriendo en `main` haya terminado.

## Sin cambios
`data/`. El workflow. `scripts/cme_options_collector.py` y el resto de ficheros con SHA-256 congelado: SALUD-1 no reescribe ninguna huella. La ventana de 06:00. `complete` no espera un sha en `main`. Fórmulas, umbrales, s01b, factor USD. No se llama a Databento. No se dispara el workflow. No se fusiona el PR.

El único test ya existente que cambia es la aserción de `tests/test_cme_options_backfill_chunks.py:476`, con el comentario `# orden expresa de Sander 14-nov-2026 REVISION-TRIPLE-01 punto 1`.

## Pruebas
`tests/test_cme_options_backfill_revision_triple.py` (nuevo): la regla de `complete`, el cierre de calendario frente a `no records`, la reserva ya escrita cuando llega `get_range`, el bloqueo hasta reconciliar (`release` y `spent`, este último una sola vez), el tope `spent + reserved + chunk` (35.00 descarga, 35.01 no, y por encima no se reserva), `write_report` atómico, y `--list-skipped` sin llamada a Databento.

## Siguiente
Con los puntos 1, 2 y 5 el PR #30 queda completo. No se fusiona en esta nota. Para listar meses con `skipped` cuando el backfill en curso termine, sin Databento: `python scripts/cme_options_backfill.py --list-skipped`.

El punto 3 queda como mejora futura (ver la nota de las 19:53). Hace falta una orden nueva antes de tocar los cinco tests que hoy exigen `complete` true con `committed_sha` None, o un sha que no está en `main`.

## Nota 14-nov-2026 19:53 WAT

**Decisión de Sander.** Los puntos 1, 2 y 5 bastan: el PR #30 está completo. No se toca `data/` ni ningún test en esta nota.

**Punto 4, no aplicado.** La ventana del mes sigue cerrando a las 06:00 UTC del siguiente día hábil. Sander acepta el argumento del open interest: Databento filtra `end` por `ts_recv`, el OI de la última sesión llega ~01:44 UTC de ese hábil siguiente (el colector no la da por disponible hasta las 03:30 UTC), y el mes siguiente no escribe la sesión anterior. Un corte a las 00:00 perdería esas estadísticas. El solape de las primeras 6 h del mes siguiente es ese tramo.

**Punto 3, aplazado.** Un chunk pasará a `complete` solo cuando su `committed_sha` exista en `main`, para que un push fallido no cobre el rango dos veces. No se implementa ahora. Los cinco tests no se tocan:

1. `tests/test_cme_options_backfill_chunks.py` `test_one_month_is_one_range_per_parent_per_schema`, línea 223 (`complete` true).
2. El mismo test, línea 227 (`committed_sha` es None).
3. `test_existing_session_inside_an_incomplete_month_is_not_overwritten`, línea 274 (`complete` true sin sha en `main`).
4. `tests/test_cme_options_backfill_commit_scope.py` `test_download_does_not_inherit_a_plain_cost_only_sha`, líneas 262-263 (`complete` true y `committed_sha` None).
5. `test_complete_month_skips_http`: el sha guardado es `abc123` y no es un commit de `main`; exigir el sha ahí volvería a pedir el mes.

Mejora futura: cuando haya orden de editar esos cinco tests, `complete` (y el salto de un mes ya hecho) solo si el sha empujado está en `main`.

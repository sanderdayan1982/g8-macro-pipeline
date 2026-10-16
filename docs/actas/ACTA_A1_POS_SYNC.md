# ACTA A1 — El POS del dashboard clasifica igual que POS-G8 v1.2.6 (16-oct-2026)

**Origen:** plan de armonía (enmienda v2), punto A1, firmado por Claude y Argon 4. Sander dio el "procede".
**Base:** B1 (este lote va encima de B1).

## Problema [MEDIDO, repo 4a37969]
- `scripts/pos_g8_cot_collector.py` aplicaba THR fx = 1.5 / 2.0 (marcado "PROVISIONAL") a las 7 divisas y también al USD°.
- `scripts/generators/pos_g8.py` exigía esa misma tabla (EXPECTED_THR, aserción Rule 32).
- `scripts/twin_test.py` reclasificaba Pine con 1.5 / 2.0.
- POS-G8 v1.2.6 en TradingView usa en cambio `f_thr_fx` por divisa (CAL 14sep26) y, para el USD°, un par propio: 0.79 / 1.15.
- Consecuencias:
  - el dashboard marcaba CROWD más fácil que TradingView (2.0 < 2.02–2.42);
  - el USD° casi nunca entraba en extremo;
  - JPY y NZD tenían bandas de STRETCH distintas.

## Cambios
| Archivo | Cambio |
|---|---|
| `pos_g8_cot_collector.py` | `THR["fx_ccy"]` es la tabla literal de POS-G8 v1.2.6, con USD 0.79 / 1.15. `THR["fx"]` (1.5 / 2.0) queda solo como respaldo, como `z_warn`/`z_ext` en Pine. Cada fila publica `thr`. Se añade **`am_state_shadow`**, que aplica `f_thr_am` [Z2] (umbrales distintos para el lado corto y el largo) en SHADOW, también para la AM del USD° (−media de las 7 AM Z). Schema `pos_g8_cot/1.2.0` y `pine_ref`. |
| `generators/pos_g8.py` | EXPECTED_THR = la misma tabla. `thresholds_frozen` por divisa (USD con su par). `am_state_shadow` en components. `PINE_VERSION = v1_2_6`. El historial del twin lleva una columna `thr`: las filas anteriores se migran solas como `v113` en la primera ejecución. |
| `twin_test.py` | Pine se clasifica con la tabla nueva, pero cada fila de Python se compara con la tabla con la que se clasificó (`thr`). Así no se mezclan tablas. |
| `docs/index.html` (§09) | Colorea la Z con `r.thr` de cada fila y muestra la etiqueta [fx CAL] cuando el JSON trae `fx_ccy`. Con un JSON antiguo vuelve a 1.5 / 2.0 [fx PROV]. La §09 se retira de la web en A5. |
| `tests/test_a1_pos_sync.py` (nuevo) | 7 pruebas: tabla igual a Pine en colector, generador y twin; USD con su par; CROWD por divisa; AM por lado; respaldo para divisas sin par. |

**No cambia:** ventanas (156 / 104 semanas), tope de Z, metales, `divergence`, `time_in_state` (se recalcula con el par de cada divisa), el libro (§00) y las alertas, que leen `state`.
**STALE no vota:** no se aplica, porque las 7 divisas salen del mismo informe CFTC y comparten su frescura. Ya existe un control global de frescura (`freshness`).

## Verificación [MEDIDO]
- Simulación sobre el informe del 06-oct, reclasificando con la tabla nueva: **EUR STRETCH−, AUD STRETCH+, resto NEUTRAL**, igual que la captura de POS-G8 v1.2.6 en TradingView. AM en sombra: GBP STRETCH− (AM Z −2,03; en TradingView −2,01).
- Generador, twin y `dashboard_alerts --dry-run` sobre el JSON migrado: correctos.
- Twin: con historial etiquetado sigue **PASSED 6,67 %**, igual que antes de A1. Sin la etiqueta habría subido a 8,33 % por mezclar tablas: el JPY pasaba de 0 % a 33 % de discrepancia.
- `python -m unittest discover -s tests`: **773 OK**. JS en línea correcto.

## Operativa
- El colector no se ha ejecutado desde aquí: la red del entorno de pruebas bloquea la CFTC.
- El primer JSON con la tabla nueva lo genera `g8_port_run.yml` en su siguiente pasada (viernes 21:05 o sábado 09:05 UTC), y el orden es colector → generador → twin.
- Hasta entonces la web muestra el JSON antiguo con su etiqueta [fx PROV]. Es coherente con lo que hay en los datos.

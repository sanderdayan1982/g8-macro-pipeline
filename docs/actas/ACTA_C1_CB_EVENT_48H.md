# ACTA C1 — CB_EVENT_48H y DTE/ΔOI de opciones, en SHADOW (17-oct-2026)

**Origen:** Argon 4 aprobó B6 (`fetch_nzd_b2.py` v1.6 sobre `5c209de`) sin observaciones y dio luz verde a C1. Complementos firmados #5 y #1 de la enmienda v2.
**Base local:** el commit de B6 `313bc27`, cuyo padre es `5c209de`. Este lote no toca `fetch_nzd_b2.py`. El parche de C1 aplica sobre `5c209de` (main público al cerrar el lote) y también encima de B6.
**Qué cambia:** dos módulos nuevos, dos JSON de sombra, `tests/test_c1_shadow.py`, esta acta, y la lista de exclusiones de frescura en `tests/test_f1_config.py` y `tests/test_f3_freshness.py`.

## 1. CB_EVENT_48H (complemento #5)

`scripts/cb_event_shadow.py`. Calendario oficial `data/manual/cb_meetings.csv`. Días hábiles del banco en `sources/calendars.csv`: USD→US, EUR→TARGET, GBP→GB, JPY→JP, AUD→AU, NZD→NZ, CAD→CA, CHF→CH.

`days_to_next_cb_meeting` es 0 el día del anuncio. Cada día hábil posterior suma 1. El fin de semana no cuenta. Un festivo intermedio no cuenta. El día del anuncio cuenta aunque ese calendario lo marque festivo. Sin reunión futura el valor es `None` y la bandera queda en falso.

La bandera de la divisa es verdadera solo si `0 <= days_to_next_cb_meeting <= 2`. La de un par de los 28 de `sources/suite_contract.json` es verdadera si al menos una pata lo es (`or`). `both_legs_in_window` guarda el choque doble (`and`), que era la regla anterior.

Corrección del 17-oct-2026, pedida por Argon sobre `5114a1e`: con `and`, un FOMC, un SNB o un RBA en solitario dejaba en falso todos los pares de esa divisa. En mesa, el par tiene evento si cualquiera de las dos patas decide dentro de la ventana.

Medido el 17-oct-2026 (sábado). Ninguna divisa y ningún par están dentro de la ventana (`CB_EVENT_48H` y `both_legs_in_window` en falso):

| Divisa | Próxima decisión | Días hábiles |
| --- | --- | --- |
| USD | 2026-10-28 | 8 |
| CAD | 2026-10-28 | 8 |
| NZD | 2026-10-28 | 7 |
| EUR | 2026-10-29 | 9 |
| JPY | 2026-10-30 | 10 |
| AUD | 2026-11-03 | 12 |
| GBP | 2026-11-05 | 14 |
| CHF | 2026-12-10 | 39 |

NZD va un día por debajo de USD porque el Labour Day neozelandés (2026-10-26) no cuenta. El viernes 23-oct NZD ya está en 2 y USD sigue en 3. Se encienden los siete pares de NZD (`AUD/NZD`, `EUR/NZD`, `GBP/NZD`, `NZD/CAD`, `NZD/CHF`, `NZD/JPY`, `NZD/USD`) con `both_legs_in_window` en falso.

El miércoles 28-oct entran USD, CAD y NZD (día 0), el BCE (1 día hábil) y el BoJ (2). `GBP/USD` y `AUD/USD` quedan en `CB_EVENT_48H` aunque la libra y el dólar australiano no decidan. Los diez choques dobles, con `both_legs_in_window` verdadero, siguen siendo `CAD/JPY`, `EUR/CAD`, `EUR/JPY`, `EUR/NZD`, `EUR/USD`, `NZD/CAD`, `NZD/JPY`, `NZD/USD`, `USD/CAD` y `USD/JPY`. `GBP/AUD` sigue en falso: ninguna pata está en la ventana. El jueves 29 la de USD ya se apagó y el único choque doble que queda es `EUR/JPY`; `GBP/JPY` sigue encendido por el BoJ.

Salida: `data/CB_EVENT_48H_shadow.json` (`mode: SHADOW`). No entra en el libro.

## 2. Opciones CME, DTE y ΔOI (complemento #1)

`scripts/cme_options_shadow.py`. Lee `data/OPTIONS_SURFACE.json` y no lo reescribe. Los CSV canónicos no están en el repo (`data/options/canonical/` solo tiene `.gitkeep`), así que CALL WALL y PUT WALL se eligen entre los cinco strikes que el JSON ya publica, con la regla de la §08: call por encima de `ref` con más OI de calls; put por debajo de `ref` con más OI de puts; cota de OI total 100; empate, strike menor.

`dte_calendar` es la resta de fechas hasta `front`. `dte_bd` cuenta días hábiles del calendario US (CME) desde el día siguiente a la sesión hasta el vencimiento. ΔOI es el cambio del OI de ese lado, y del agregado `oi_front`, frente a la sesión anterior. Sin snapshot previo, o si ese strike no está en la sesión anterior, ΔOI es `null`. No se escribe un cero.

Sesión publicada 2026-10-15, vencimiento EUR `2026-11-06`: `dte_calendar` 22, `dte_bd` 16. CALL WALL 1.135, ΔOI de calls +25. PUT WALL 1.11, ΔOI de puts −13. Agregado 79688, ΔOI +2335. NZD queda vacío: no hay opciones CME. La cotización sigue en convención CME. La inversión de JPY, CAD y CHF de la §08 no se mueve de `dashboard_alerts.py`.

Salida: `data/CME_OPTIONS_C1_shadow.json`.

## Lo que no cambia

| Fichero | SHA-256 |
| --- | --- |
| `scripts/book_risk.py` | `78514c2daa319d08454a99dae8225f7c2208da1fcd34cab13eb2547d72ef9d50` |
| `scripts/dashboard_alerts.py` | `56e46654e7cc86b0be45e9d5d1792476169db3426d38d12aacb93d50056b5ebb` |
| `scripts/cme_options_collector.py` | `61276d17636d921913e383127b47176e946adc5eab59420416f4f3a244a477f9` |
| `scripts/build_options_summary.py` | `f0fdf61d1047d3d899b5037f3047dc194329b4a4d5de42b59bed1346e304e019` |
| `scripts/s01b.py` | `0c43d6dd54677974bf4ffde880de049396f4b0d9cdd1c6e39964d796ef132ee5` |

Los dos JSON de sombra entran en la lista de exclusiones de `tests/test_f1_config.py` y `tests/test_f3_freshness.py`. No son serie de frescura. `tests/test_exclusions.py` no se edita y sigue en verde.

## Tests

Las 10 de `tests/test_c1_shadow.py` cubren la corrección de pares. El recuento del árbol completo, con B3, está en el acta de B3.

La corrección de pares queda en el mismo día que se abre B3. El nowcast suizo está en `docs/actas/ACTA_B3_CHF_NOWCAST.md`.

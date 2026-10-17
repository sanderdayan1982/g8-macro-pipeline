# ACTA C2 — distancia, muro cercano, biografía y roll de opciones, en SHADOW (17-oct-2026)

**Origen:** Argon 4 cerró el plan de la enmienda v2 y, con el dashboard ya en `main`, ordenó el lote C2. Piezas 2 y 3 de opciones, solo en sombra.
**Base:** `25e8dff` (`chore: options surface update 2026-10-17`), padre `11b65a4`.
**Qué cambia:** `scripts/cme_options_shadow.py`, `data/CME_OPTIONS_C1_shadow.json` (esquema `cme-options-c2-shadow-1`), `data/options/shadow_history.json`, `tests/test_c2_options_shadow.py` y esta acta.

No se tocan los siete ficheros congelados en `tests/test_exclusions.py`: `cme_options_collector.py`, `build_options_summary.py`, `.github/workflows/cme_options.yml`, `dashboard_alerts.py`, `s01b.py`, `usd_factor.py`, `book_risk.py`. No se reescribe `data/OPTIONS_SURFACE.json`. El módulo no importa `dashboard_alerts.py`.

Los cortes son los ya vigentes. No hay umbral nuevo.

| Corte | Valor | Uso |
| --- | --- | --- |
| `MIN_OI` | 100 | Entra en el muro solo si `c + p >= 100` |
| Proximidad | 0,5 % | `near_pin_05pct` |
| Aviso de cambio | 15 % | `chip_15pct_strike` y `chip_15pct_chain` |
| Drenaje | −33,33 % y −66,67 % | `drain_1_3` y `drain_2_3` en el agregado |
| Roll | `dte_calendar <= 7` | `roll_window_7d` |

## Distancia

En cada muro, `dist_pct = round((k - ref) / ref * 100, 2)` en convención CME, con signo. `abs_dist_pct` es el valor absoluto. `near_pin_05pct` es verdadero si el valor absoluto, antes de redondear, es menor o igual que 0,5. Es un chip, sin voto.

Medido en la superficie del 15-oct-2026. GBP, futuro de referencia `1.3274`. El call wall absoluto sigue en `1.43` (el mayor OI de calls) y queda a `+7.73 %`. El `nearest_call_wall` es `1.33`, a `+0.20 %`, y el chip de 0,5 % se enciende. El `nearest_put_wall` es `1.315`. El `1.33` no está en la sesión previa: su `delta_oi` es `None`, no cero.

## Muro táctico

`call_wall` y `put_wall` no se quitan. `nearest_call_wall` es el strike `k > ref` con `c + p >= 100` y `c > 0` de menor `k - ref`. `nearest_put_wall` es el strike `k < ref` con `c + p >= 100` y `p > 0` de menor `ref - k`. Empate de distancia: gana el strike menor.

## Biografía

`delta_oi_1d` y `pct_oi_1d` comparan con `prev` del JSON publicado. `delta_oi_1w` y `delta_oi_1m` leen `data/options/shadow_history.json`: la misma pareja `(front_expiry, strike)`, 5 y 21 sesiones atrás. Si no hay tantas sesiones, o el vencimiento no coincide, o el strike no está, el valor es `None`.

El historial es rotativo y guarda 30 sesiones. Volver a escribir la misma fecha sustituye esa sesión, no la duplica. En esta entrega solo está la sesión `2026-10-15`, así que 1w y 1m salen vacíos. El fichero vive en `data/options/` y no entra en el glob de frescura de `data/*.json`.

En el agregado, `drain_1_3` es `pct_oi_1d <= -33.33` y `drain_2_3` es `pct_oi_1d <= -66.67`. Sin `pct_oi_1d`, el chip y los drenajes son `None`.

## Roll y liquidez

`roll_window_7d` es verdadero cuando `dte_calendar` existe y es menor o igual que 7. En ese caso la nota es `EXPIRING_≤7D_USE_NEXT_MONTH` y `map_status` es `EXPIRING_≤7D`, aunque falte un muro.

`map_status`, en este orden:

- `NO_CME_OPTIONS` para NZD.
- `EXPIRING_≤7D` si la ventana de roll está abierta.
- `THIN_ONE_SIDED` si falta `call_wall` o `put_wall` por no llegar a `MIN_OI`. CHF, el 15-oct-2026: los cinco strikes publicados están por encima del futuro y `put_wall` es `None`.
- `READABLE` cuando existen los dos muros y `dte_calendar > 7`.

La sesión publicada del 15-oct-2026 tiene `dte_calendar` 22 en el vencimiento del 6-nov. GBP, EUR y el resto con dos muros quedan `READABLE`. NZD queda `NO_CME_OPTIONS`.

Suite completa, Python 3.12, sobre `25e8dff` más este lote: 841 OK, 11 omitidas. Las 11 son los `skipUnless` que ya estaban. `s01b.py` sigue en `0c43d6dd54677974bf4ffde880de049396f4b0d9cdd1c6e39964d796ef132ee5`.

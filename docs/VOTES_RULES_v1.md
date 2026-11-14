# VOTES_RULES v1

Aprobado por Sander (FAMILIAS-00, 14-nov-2026 00:18 WAT; fallo de Gmail, 14-nov-2026 00:31 WAT).
`rule_version=1`. No se cambia sin un acta.

`rule_version=2` (orden expresa de Sander 14-nov-2026 18:23 WAT, t158u; voto, 18:44 WAT, t159u): `FRESH_PROXY` cuenta como fresco solo para la marca `stale` (`stale=false`). El `voto` de `IYDT_2Y`, `IYDT_10Y`, `FFVA_G8` y `POL_G8` sigue siendo `NA`, como antes. El `estado` conserva su etiqueta. `2` va solo en las filas nuevas.

`id_correo` (orden de Sander 14-nov-2026 18:44 WAT, t160u) es `sha256` del id de Gmail en hexadecimal, los primeros 16 caracteres. Nunca el id en claro. Varios ids van unidos por `;`, cada uno hasheado y la lista ordenada. La deduplicación y la clave de solo añadir siguen siendo `(fecha_cierre, script, ccy)` y `MESSAGE_ID` del cuerpo; no usan el id de Gmail.

## Qué se escribe

`data/votes.csv` solo crece. Una fila ya escrita no se reordena ni se reescribe. Una corrección, si llega a hacerla una persona, es una fila nueva; el ingestor no la genera solo: si `(fecha_cierre, script, ccy)` ya existe, no añade otra.

14-nov-2026: por orden de Sander (10:34 WAT) se retiran una sola vez las 104 filas de la prueba manual del 13-nov (commit 1d19f91, run 43577055363, fallo de autenticación OAuth de Gmail). La regla de solo añadir rige desde la primera pasada buena.

Columnas, en este orden: `fecha_cierre`, `script`, `version`, `ccy`, `estado`, `valor`, `voto`, `stale`, `snapshot_id`, `hora_recepcion_utc`, `id_correo`, `bar_close_ms`, `family`, `parts_expected`, `parts_received`, `rule_version`.

`voto` es `+1`, `0`, `-1` o `NA`. `0` es un script direccional FRESH en estado neutro. `NA` es ausencia de voto. `NA` no se rellena con `0` ni con el valor del día anterior.

`fecha_cierre` es la fecha de la sesión del cierre 1D, en hora de Malabo (WAT, UTC+1). El cierre esperado es las 17:00 de Nueva York: en invierno (EST) son las 23:00 WAT; en verano (EDT) son las 22:00 WAT. El dato del 12-nov-2026 cae en invierno.

## Intentos

| Instante | UTC | Qué hace |
|---|---|---|
| Primer intento | 03:00 UTC, mar–sáb | 04:00 WAT. Escribe los módulos completos y E21. No escribe `MISSING` ni `INGEST_INCOMPLETE`: el reintento de las 05:00 WAT sigue pendiente. |
| Intento final | 04:00 UTC, mar–sáb | 05:00 WAT, antes del corte de las 05:15 WAT (04:15 UTC). Lo que siga ausente queda `MISSING`. |
| Fin de semana | | Ninguna fila. |

`workflow_dispatch` acepta `session` y `attempt` (`first` o `final`, por defecto `final`).

## Correo

Consulta exacta, solo lectura, incluyendo spam y papelera. No se marca, mueve ni borra nada.

```
from:noreply@tradingview.com subject:"Alert:" -subject:"G8-COLLECT"
```

Secretos de Actions: `GMAIL_READONLY_REFRESH_TOKEN`, `GMAIL_OAUTH_CLIENT_ID`, `GMAIL_OAUTH_CLIENT_SECRET`. Un refresh token solo no puede pedir un access token: el endpoint de Google exige también el client id y el client secret. Alcance `https://www.googleapis.com/auth/gmail.readonly`.

Cuerpo: `KEY=VALUE` separado por `|`, prefijo `G8|`. Un valor no debe contener `|` (el `HOST` de FFVA puede ser JSON; `=` y `:` sí se conservan). `SNAPSHOT_ID=MODULE:INSTANCE:HOST:BAR_CLOSE_MS:SENT_MS`. `MESSAGE_ID=SNAPSHOT_ID:Pn`.

`RISK_G8` y `XCCY_OIS_SHADOW` son un solo mensaje, sin `TRANSPORT`. Su deduplicación es `MODULE:BAR_CLOSE_MS`. El resto viaja en `TRANSPORT=G8_PARTS_V1` con `TRANSPORT_REV` 1.2 o 1.3. Se juntan todas las partes de un `SNAPSHOT_ID`. Deduplicación de partes: `MESSAGE_ID` o, si falta, `(SNAPSHOT_ID, PART)`. Dos snapshots con el mismo `BAR_CLOSE_MS`: se queda el de mayor `SENT_MS`.

`PARTS` es un entero ASCII `^[0-9]{1,2}$` en el rango 1..64. `PART` es un entero ASCII dentro de `1..PARTS`. Cualquier otra cosa (0, negativo, basura, mayor que 64, dígito no ASCII, `PART` fuera de rango) deja el snapshot incompleto. Dos copias del mismo `PART` con el mismo cuerpo se cuentan una vez; si el cuerpo difiere, o si las partes no declaran el mismo `PARTS`, el snapshot queda incompleto. Esa comparación es anterior a la deduplicación por `MESSAGE_ID`. Entre varios snapshots se prefiere uno completo: el rango es (completo, `SENT_MS`, `SNAPSHOT_ID`).

Si falta cualquier parte del `PARTS` declarado, las partes no declaran el mismo `PARTS`, ninguna declara un `PARTS` válido, `TRANSPORT_REV` no es 1.2 ni 1.3, dos partes discrepan en el mismo campo, hay copias distintas del mismo `PART`, o `BATCH_STATUS=ERROR`, el módulo entero es `INGEST_INCOMPLETE` y `voto=NA`. Eso se escribe solo en el intento final. En el primer intento el módulo se omite y stderr lleva una línea `skip <módulo> incomplete declared=<N> missing=[…] extra=[…] reason=<corto>`, sin el cuerpo.

`BAR_CLOSE_MS` distinto del cierre esperado, o `STATUS` distinto de `FRESH` y de `FRESH_PROXY` cuando el módulo tiene campo de estado: `stale=true` y `voto=NA`. `FRESH_PROXY` deja `stale=false`. En `IYDT_2Y`, `IYDT_10Y`, `FFVA_G8` y `POL_G8` ese estado sigue dando `voto=NA` (orden t159u). El `estado` se conserva. `RISK_G8` y `XCCY_OIS_SHADOW` no traen `STATUS`; no se marcan stale por un estado ausente.

## Catálogo (cierre real del 12-nov-2026)

Versión y recuento de referencia. Las filas `MISSING` usan esta versión y este recuento porque no hubo correo. El número de partes de un snapshot es el que declara el correo (`PARTS`); la columna `partes` no lo fija.

| script | versión | partes | familia | divisas |
|---|---|---|---|---|
| RISK_G8 | 1.2.1 | 1 | RISK | G8 (una fila) |
| XCCY_G8 | 2.8.1 | 2 | XCCY | las 8 |
| XCCY_OIS_SHADOW | 1.0.0 | 1 | XCCY_OIS | las 8 |
| PSI_G8 | 2.4.3 | 3 | PSI | las 8 |
| CURVA_G8 | 1.4.8 | 5 | CURVA | las 8 |
| RTF10_G8 | 3.10.0 | 5 | RTF10 | las 8 |
| POL_G8 | 1.3.8 | 4 | POL | las 8 |
| IYDT_2Y | 1.7.2 | 8 | IYDT | las 8 |
| IYDT_10Y | 1.6.1 | 8 | IYDT | las 8 |
| FFVA_G8 | 1.5.0 | 5 | FFVA | las 8 |
| TOT_G8 | 1.1.3 | 5 | TOT | las 8 |
| EQF_G8 | 1.1.3 | 5 | EQF | las 8 |
| POS_G8 | 1.2.6 | 6 | POS | las 8 |
| E21 | la de `USD_FACTOR.json` | 1 | R | las que traiga `currencies[]` |

El número de partes es el que declara el correo (`PARTS`). Todas las partes del mismo snapshot tienen que declarar el mismo `PARTS` y llegar exactamente `1..N`. Si no declaran `PARTS`, o no coinciden, el módulo queda incompleto. El valor de la tabla es el recuento de referencia (las filas `MISSING` siguen usándolo). Orden de Sander 14-nov-2026 (FFVA/TOT 13-nov): ese día FFVA_G8 llegó completo con `PARTS=6` y TOT_G8 con `PARTS=4`; la tabla sigue en 5 y 5 como referencia.

Divisas de los módulos de correo: USD, EUR, GBP, JPY, AUD, NZD, CAD, CHF. Edición ARGOS v1.0.1 §2 no está en el repo (búsqueda el 13-nov-2026). El mapa de familias de arriba es el que usa el código hasta que esa edición entre con un acta. E21 es la familia R, como indicó Sander.

## Tabla de voto

| script | voto | estado | valor |
|---|---|---|---|
| IYDT_2Y, IYDT_10Y | `POS_STRONG` +1; `NEG_STRONG` −1; `POS_BELOW_STRONG` y `NEG_BELOW_STRONG` 0; cualquier otra banda, incluida una `EXTREME`, NA. No hay banda EXTREME en el código ni en el cierre del 12-nov: no se trata como STRONG. `STATUS=FRESH` aplica la banda. `STATUS=FRESH_PROXY` → voto NA y `stale=false`. Cualquier otro `STATUS` → `stale=true` y voto NA. | la banda | el score |
| FFVA_G8 | `ACCUM` y `SC` +1; `DIST` y `LIQ` −1; `FLAT_OI` 0; `ROLL=YES`, estado/status `STALE`, o `STATUS=FRESH_PROXY` → NA (`FRESH_PROXY` deja `stale=false`). | el régimen crudo | `DIRECTIONAL_INTENSITY`, o un número tipo `Z_FLOW` si la intensidad no viene |
| POL_G8 | `REPRICE_COUNTED` `FULL` o `PATH` con `NOW_DIRECTION` `HAWKISH` +1, `DOVISH` −1; `NONE` → 0. `CLASS_ELIGIBLE` distinto de `YES`, o `STATUS=FRESH_PROXY`, → NA (`FRESH_PROXY` deja `stale=false`). | `STATE`, o `NOW_DIRECTION` si `STATE` viene vacío | `HAWK_SCORE` |
| POS_G8 | NA | `LF_STATE` | `LF_Z` |
| PSI_G8 | NA | `HEALTH` | `P_HEALTHY_BP` |
| RISK_G8, XCCY_G8, XCCY_OIS_SHADOW, CURVA_G8, RTF10_G8, TOT_G8, EQF_G8 | siempre NA | régimen, driver o estado que traiga el correo | el número si existe (`SCORE`, `SPREAD_BP`, …) |

## Fallo de Gmail

Secreto ausente, secreto inválido, error de autenticación, o error de API después de 3 intentos: el intento final marca el módulo que todavía no tiene fila como `MISSING`, `voto=NA`, una fila por módulo y divisa. E21 se escribe igual, con la regla de abajo. Se envía un solo aviso de Telegram con `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID`. El texto no lleva ningún secreto ni el cuerpo de la respuesta HTTP. El job sale distinto de cero. En el intento de las 04:00 WAT no se escriben esas filas `MISSING`.

Si `https://oauth2.googleapis.com/token` responde con un código que no es 2xx, el stderr y el Telegram añaden el estado HTTP y el campo JSON `error` (el código OAuth de Google, por ejemplo `invalid_grant`, `invalid_client`, `unauthorized_client`, `invalid_request`). Ese valor se escribe solo si coincide con `^[a-z_]{1,40}$`. Si el cuerpo no se puede leer, o el campo no cumple el patrón, se escribe `unknown`. No se escribe `error_description` ni ninguna otra parte del cuerpo. Los reintentos no cambian: 400 y 401 salen en el primer intento; 429 y 5xx siguen hasta tres intentos.

## E21

Lee `data/USD_FACTOR.json`, `currencies[].E21`. USD no está en esa lista (el dólar es el numerador) y no se inventa una fila.

Fresco solo si `as_of` es exactamente `fecha_cierre` y `generated_utc`, si viene, no es de un día anterior. Si `as_of` es anterior a la sesión, está stale: es más viejo que la sesión esperada. Si `as_of` es posterior, también está stale: el fichero solo guarda una foto y esa foto ya no es la de la sesión. En stale, `voto=NA`, `stale=true`, `estado=STALE`, y `valor` conserva el E21 (no se pone a 0). En fresco, `voto` es el signo: +1, −1, o 0 si el número es exactamente 0.

Si el fichero no se puede leer, no se inventan filas E21. Se dice en stderr.

## Informe semanal

`data/votes_weekly.json` es un informe, no la tabla. Se reescribe. Un día está completo cuando los 13 módulos tienen todas sus divisas, hay al menos una fila E21, y ninguna fila de esa sesión tiene `estado` `MISSING` o `INGEST_INCOMPLETE`. No se construye el §08. Eso espera a 250 sesiones.

## Frescura del fichero

`votes.csv` y `votes_weekly.json` no son feeds. Entran en `freshness_rules.csv` como `excluded` / `not_monitored` para que, el día que el workflow los publique, `tests/test_f1_config.py` y `tests/test_f3_freshness.py` sigan en verde. Esos tests no se editan: su lista de exclusiones está congelada dentro del propio test.

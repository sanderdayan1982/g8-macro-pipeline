# ACTA FAMILIAS-00 — votes.csv (parte A)

**Decisión:** Sander, 14-nov-2026 00:18 WAT. El fallo de Gmail pasa a `MISSING` en el intento final, 14-nov-2026 00:31 WAT. `rule_version=1` queda en `docs/VOTES_RULES_v1.md`. No se cambia sin otra acta.

## Hallazgo
No había ingesta de los correos de alerta de TradingView. Edición ARGOS v1.0.1 §2 no está en el repo (búsqueda el 13-nov-2026 en docs, scripts y data). Las familias van en un mapa fijo del script; E21 es la familia R.

## Decisión
- `scripts/votes_ingest.py` separa el parser (cuerpos → filas) del cliente Gmail. El cliente solo lee. La consulta es exactamente `from:noreply@tradingview.com subject:"Alert:" -subject:"G8-COLLECT"`, con spam y papelera, sin modificar nada.
- Tres secretos: `GMAIL_READONLY_REFRESH_TOKEN`, `GMAIL_OAUTH_CLIENT_ID`, `GMAIL_OAUTH_CLIENT_SECRET`. El refresh token no pide un access token él solo.
- Cron 03:00 y 04:00 UTC, mar–sáb (04:00 y 05:00 WAT). El de las 04:00 WAT no escribe `MISSING`. El de las 05:00 WAT, antes del corte de las 05:15 WAT, escribe `MISSING` para lo que falte. Un fallo de Gmail en ese intento final hace lo mismo, conserva E21 y manda un Telegram sin secretos en el texto. El job sale distinto de cero.
- `data/votes.csv` solo se añade. `data/votes_weekly.json` es un recuento, no el §08.
- E21 sale de `data/USD_FACTOR.json`. Stale si `as_of` no es la sesión (más viejo, o más nuevo porque el fichero solo guarda una foto) o si `generated_utc` es de un día anterior.
- Para que el CSV, cuando exista, no rompa `test_f1_config` ni `test_f3_freshness` (esos tests no se tocan), la regla `VOTES` queda `excluded` / `not_monitored`.
- El cron entra en `mac/dispatch_schedule.json` porque `tests/test_p12_mac_dispatch.py` exige un turno por cada cron y ese test no se edita. El segundo disparo es idempotente.

## Sin cambios
Tests ya existentes, `docs/index.html`, Pine, `scripts/cme_fxvol_shadow.py`, umbrales FXVOL, ficheros con huella SHA-256.

## Pruebas
`tests/test_votes_ingest.py`, fichero nuevo, fixtures sintéticos. Camino de 13 módulos, MISSING solo al final, stale, parte ausente, lote ERROR, duplicados, dos snapshots, append-only, tabla de voto, signo y stale de E21, sin red, fallo de Gmail con un Telegram y la lógica de reintento.

## Siguiente
Sander crea los tres secretos (pasos en la descripción del PR, sin valores) y fusiona. El §08 no se construye hasta 250 sesiones.

## 14-nov-2026 — orden de Sander (10:34 WAT)

14-nov-2026: por orden de Sander (10:34 WAT) se retiran una sola vez las 104 filas de la prueba manual del 13-nov (commit 1d19f91, run 43577055363, fallo de autenticación OAuth de Gmail). La regla de solo añadir rige desde la primera pasada buena.

El mismo orden pide el código OAuth en el fallo del token. Cuando `https://oauth2.googleapis.com/token` no es 2xx, stderr y Telegram llevan el estado HTTP y el campo JSON `error`, solo si coincide con `^[a-z_]{1,40}$`; si no, `unknown`. No se escribe `error_description` ni el cuerpo. Filas `MISSING`, E21, códigos de salida y reintentos quedan igual. El código de solo añadir no se debilita. Ningún test ya existente se edita. Ningún fichero con SHA-256 congelado en `tests/` cambia, así que SALUD-1 no actualiza ningún hash.

`data/votes_weekly.json` se regenera desde el CSV resultante. El ingestor reescribe ese informe en cada pasada; no se borra. El informe anterior ya tenía 0 días completos, porque una sesión `MISSING` no cuenta. `generated_utc` pasa a `2026-11-14T09:45:34Z`.

`test_cli_dry_run_prints_rows` ya fallaba en `main`: afirma que `data/votes.csv` no existe, y el commit `1d19f91` creó el fichero. La orden deja la cabecera, así que el fichero sigue existiendo. Ese test no se edita.

## 14-nov-2026 — orden expresa de Sander (10:54 WAT)

14-nov-2026: por orden expresa de Sander (10:54 WAT) se cambia solo el test test_cli_dry_run_prints_rows: en vez de exigir que data/votes.csv no exista, comprueba por hash que el archivo (si existe) queda idéntico antes y después del dry-run.

## 14-nov-2026 — orden de Sander (15:47 WAT)

14-nov-2026: por orden de Sander (14-nov 15:47 WAT) se retiran, una sola vez, las 97 filas MISSING del 13-nov del commit 26749fe, escritas por una prueba manual lanzada por error con attempt=final. Desde ahora toda prueba manual se lanza con sesión explícita y attempt=first, verificado en pantalla antes de enviarla.

La misma orden pide el `reason` y el `status` de un fallo de la API de Gmail (la lectura, no el canje del token). Cada valor se escribe solo si coincide con `^[A-Za-z_]{1,40}$`; si el valor está y no cumple, `unknown`; si falta, o el cuerpo no es JSON, `none`. No se escribe `error.message`, el cuerpo, las cabeceras ni ningún secreto. El código OAuth del token no cambia. La regla de solo añadir sigue en pie, salvo esta retirada.

14-nov-2026 16:08 WAT: Sander elige la opción A. `HTTP <status> gmail_reason=<reason> gmail_status=<status>` se añade solo cuando la lectura de Gmail (no el canje del token) falla con HTTP 401 o 403. Un 5xx, y cualquier otro fallo de lectura, dejan el stderr y el Telegram como estaban antes de este cambio.

La cabecera y las 7 filas E21 `FRESH` del 13-nov quedan byte a byte como en el commit 4a076b8. `data/votes_weekly.json` se regenera con `write_weekly` de `scripts/votes_ingest.py` a partir de ese CSV. Sigue en 0 días completos. `generated_utc` pasa a `2026-11-14T14:55:40Z`.

Ningún test ya existente se edita. Ningún fichero con SHA-256 congelado en `tests/` cambia, así que SALUD-1 no actualiza ningún hash. Pine, la Suite G8, los umbrales y los workflows no se tocan.

## Pruebas
`python -m pytest -q tests`: 962 passed, 6 skipped, 0 failed. Ningún test ya existente se ha editado. Los tests nuevos de `tests/test_votes_gmail_api_error.py` cubren: 403 con reason y status, valor fuera de la lista blanca → `unknown`, campos ausentes → `none`, cuerpo no JSON → `none`, que el mensaje y el cuerpo no aparecen en el log ni en el Telegram, y que un 503 deja la línea de stderr en `Gmail failure (api) on attempt final. Session 2026-11-13.` sin `gmail_reason`.

## 14-nov-2026 — orden de Sander (16:28 WAT)

14-nov-2026: por orden de Sander (14-nov 16:28 WAT) la lectura de Gmail deja de pedir todo el historial de alertas. `GMAIL_QUERY` no cambia. Con la sesión, la consulta añade `after:` y `before:` en segundos Unix: desde el cierre 1D (`expected_bar_close_ms`, 17:00 America/New_York; 23:00 WAT en invierno, 22:00 WAT en verano) hasta el corte de las 05:15 WAT de la mañana siguiente. Ese corte ya está en la cabecera de `scripts/votes_ingest.py`, en `docs/VOTES_RULES_v1.md` y en el comentario de `votes_ingest.yml` (04:15 UTC). No había una función: `attempt_for` es el reintento de las 05:00 WAT, quince minutos antes. No es una hora nueva.

Entre `messages.get` consecutivos hay una pausa `MESSAGES_GET_PAUSE_S` (0,15 s). El mismo `sleep` inyectable cubre esa pausa y la espera de los reintentos.

Un HTTP 403 con `error.errors[0].reason` `rateLimitExceeded` o `userRateLimitExceeded`, y un HTTP 429, no son fallos de autenticación. Se reintentan con espera exponencial y jitter, como mucho 5 veces, con la espera de cada intento y la espera total acotadas. Si se agotan, el fallo es `api`, no `auth`, y no lleva el sufijo `gmail_reason` (ese sufijo sigue solo en el 401/403 de autenticación, orden 16:08 WAT). Un 403 distinto, por ejemplo `insufficientPermissions`, sigue siendo `auth` al primer intento, con el mismo sufijo y la lista blanca `^[A-Za-z_]{1,40}$`. No se escribe el cuerpo ni `error.message`.

Ningún test ya existente se edita. Ningún fichero con SHA-256 congelado en `tests/` cambia, así que SALUD-1 no actualiza ningún hash. Pine, la Suite G8, `data/`, los umbrales y los workflows no se tocan.

`python -m pytest -q tests` (2026-11-14): 970 passed, 6 skipped, 1 failed. El fallo es anterior y ajeno a este cambio: `tests/test_cme_options_backfill_chunks.py::ChunkCalls::test_one_month_is_one_range_per_parent_per_schema` espera `committed_sha` vacío para 2024-06, y `git log` en `main` ya tiene `bcf4d70` (`data: cme options backfill 2024-06`). Ese test no se edita.

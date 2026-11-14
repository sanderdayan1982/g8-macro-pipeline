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

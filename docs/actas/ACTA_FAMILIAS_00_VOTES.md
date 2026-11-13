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

# ACTA P-7 — Push con reintento: CME Options dejaba de guardar la sesión (1-oct-2026)

Autorizado por el propietario (1-oct-2026): «procede con los dos… que no pase más».

## Hallazgo
- Telegram 1-oct 19:13 UTC: «G8 pipeline FAILED · workflow=options run=191» (run 36910780024).
- Causa: el último paso de `cme_options.yml` hacía `git push` sin integrar antes `main`. El run tarda ~15 min y en ese
  tiempo otros procesos suben a `main` (latido del Mac, *Ingest Watch*, *Daily Data Update*, commits de código). GitHub
  rechazó el push («rejected (fetch first)»). El 1-oct coincidió con dos commits de Claude (18:56 y 18:59 UTC).
- **Recurrente**: el run de las 17:00 UTC falló igual el 28-sep, 29-sep, 30-sep y 1-oct. El 29 y el 30 el propietario lo
  relanzó a mano. El 28-sep lo recuperó el run de las 21:17.
- Efecto: la sesión de opciones no se guardaba en el repo (el 1-oct faltaba `data/options/canonical/2026-09-30/`), aunque
  el mensaje de Telegram sí salía (se envía antes del push).
- Mismo `git push` suelto en `backfill-eur-real.yml` y `backfill-jpy-real.yml` (manuales). El resto de workflows que suben
  datos ya integraban y reintentaban.

## Decisión
1. `scripts/tools/git_push_retry.sh`: hasta N intentos de `git pull --rebase -X theirs origin main` + `git push`, con espera
   creciente. Con clon superficial, primero `--unshallow`. Conflicto en un fichero que tocan los dos: gana la versión de
   este run, recién calculada; solo afecta a los ficheros de su propio commit, el resto llega intacto de `main`.
   Sin éxito → rc 1, y sigue saltando el aviso «FAILED» por Telegram.
2. `cme_options.yml`, `backfill-eur-real.yml`, `backfill-jpy-real.yml`: el `git push` final pasa a ser el script (5 intentos).
   Nada más cambia en el cálculo de opciones.
3. Huella nueva autorizada de `cme_options.yml` en `tests/test_exclusions.py`.
4. Recuperación: relanzado a mano el 1-oct a las 19:19 UTC (run 36913397786). Claude no subió nada mientras corría.

## Sin cambios
`cme_options_collector.py`, `build_options_summary.py`, `dashboard_alerts.py`, horarios, avisos.

## Pruebas
`tests/test_p7_push_retry.py` (4), con repositorios git reales:
- `main` avanzó con otros ficheros → los dos cambios quedan en `main`;
- `main` avanzó en el mismo fichero → gana este run y el resto se conserva;
- clon superficial (checkout por defecto);
- los tres workflows usan el script y no queda ningún `git push` suelto.
`bash scripts/tools/validate_smoke.sh`: verde.

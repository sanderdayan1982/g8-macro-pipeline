# ACTA P-11 — Subidas concurrentes sin pérdida y cabecera «N INCIDENCIAS» sin falsos avisos (6-oct-2026)

Autorizado por el propietario (6-oct-2026): «OK PROCEDE CON EL 1 Y EL 2, Y EL 3» (propuesta en el chat de esa sesión).

## Hallazgo
Cabecera del dashboard a las 10:17Z: **19 INCIDENCIAS** (`data/_ingest/health.json`, `scripts/health_monitor.py`),
con §05 en 47/47. Desglose con evidencia:

1. **5 por una subida perdida.** *Daily Data Update* run 37401053406 (cron 21:30Z del 5-oct, ejecutado 01:49–01:53Z del
   6-oct) descargó bien y falló en «Commit updated data files»: `CONFLICT` en 16 `data/_ingest/evidence/*.jsonl`,
   7 `data/_ingest/runs/actions/*/2026-10.jsonl`, 7 `data/_ingest/latest/*.json`, `data/alerts/brief.json` y
   `state.json`. Se perdieron SONIA (2-oct), RY_G8_CAD (2-oct) y el cierre definitivo §01-b de la sesión 5-oct
   (`s01b/state.json`, `S01B.json`); ESTR quedó pendiente.
   Causa: el run se encoló a las 01:48:50Z detrás de *Intraday official feeds* (mismo grupo `g8-shared-data-alerts`),
   pero `actions/checkout` sin `ref` toma el SHA fijado **al encolarse**; el intradía subió `2251f00` a las 01:49:38Z y
   el Daily integró después con un `git pull --rebase` desnudo (sin resolución ni `rebase --abort`).
   Relanzado a mano 17:47Z → run 37506313749 OK (`6f3eb02`, 148 ficheros).
2. **12 por esperar su pasada** (`cause = SIN_PASADA_PROGRAMADA`, una sola observación pendiente): 11 ficheros NZD
   (solo llegan con el job del Mac, 19:30/21:00 Malabo, porque el RBNZ bloquea las IPs de Actions) y ESTR (el BCE
   publica ~07:00Z; ninguna pasada lo recoge hasta el Daily de la noche). Se cierran solos cada tarde; mientras tanto
   la cabecera los contaba como incidencias todos los días.
3. **2 permanentes**: `manual/CHF_BE_MANUAL` y `manual/NZD_BE_MANUAL` tienen `disabled: true` (fuentes trimestrales
   excluidas por decisión del propietario) y el vigilante los contaba como `MISSING` para siempre.

## Decisión
1. **Subidas sin pérdida.**
   - `.gitattributes`: `data/_ingest/evidence/*.jsonl` y `data/_ingest/runs/**/*.jsonl` con `merge=union` (registros de
     solo alta; los lectores ordenan por `query_utc`/`finished_utc`, el orden de líneas no importa). Comprobado: sin
     esta regla, `git_push_retry.sh` (`-X theirs`) sube sin error pero **borra** las líneas de la otra ejecución.
   - Los 8 workflows que suben datos usan el helper P-7 `scripts/tools/git_push_retry.sh 5` en vez de bucles propios:
     `daily_update`, `intraday_fetch`, `usd_factor` (antes un solo pull+push sin reintento), `metals_update`,
     `ingest_watch`, `freshness_snapshot`, `feed_recovery`, `g8_port_run` (antes el bucle nunca ponía el job en rojo).
     Fotos JSON en conflicto (`latest/*.json`, `alerts/*.json`): gana la de la ejecución que sube (criterio P-7).
   - Checkout con `ref: main` en los workflows del grupo `g8-shared-data-alerts` (todos se disparan solo en `main`): el
     job parte de la punta de `main` al arrancar, no del SHA congelado mientras esperaba.
2. **Entrada manual desactivada → `EXCLUDED`** (no `MISSING`). Las activas siguen su caducidad (`manual_expiry_days`).
3. **`OVERDUE` + `SIN_PASADA_PROGRAMADA` → `PENDING`** («Publicada; espera la próxima pasada programada»). Sigue siendo
   incidencia (`LATE`) si faltan 2+ observaciones (`STALE`), si supera el presupuesto del registro o si la causa es
   otra (programada sin ejecución, ejecutada con fallo, retenida…). La fila sigue visible con su fecha en «Ver fuentes».
   Mismo criterio para `S01B.json`: tras el Factor USD de la tarde el panel es un PROVISIONAL (`provisional: true`) de
   una sesión **más nueva** que el último cierre; si ese cierre es el que toca (`s01b/state.json` y su log definitivo),
   queda `PENDING` hasta la pasada de las 21:30Z. Visto el 6-oct 18:20Z (único aviso tras los cambios anteriores).
   Sin cierre al día sigue siendo `LATE`.

## §01-b (revisado a petición del propietario)
- En vivo tras el relanzamiento: as-of **2026-10-05**, las 8 divisas con entradas del 2 o 5-oct (CHF `NO_QUAL` por
  diseño, ΔBE sin feed). Lo «viejo» que se veía era la sesión 2-oct, porque el cierre del 5-oct se perdió en (1).
- Hueco histórico en `data/s01b/log`: sin sesiones entre 23-sep y 2-oct. Causa (run 36651287383, 30-sep 00:42Z):
  con el cron retrasado pasada la medianoche, `s01b.py --final` evaluaba el día nuevo, respondía
  `EARLY_FINAL … publishing PROVISIONAL` y la sesión anterior nunca se cerraba. Ya corregido por el propietario el
  4-oct (PR #2, `scripts/tools/finalize_session.py`), que por diseño **no rellena** sesiones perdidas. Sin cambios.

## Sin cambios
s01b (detector, umbrales, estado, historial), ACM, fórmulas, umbrales de alertas, presupuestos de `registry.csv`,
reglas de `freshness_rules.csv`, `health.js`, horarios de cron, job del Mac.

## Pruebas
`tests/test_p11_concurrent_push_health.py` (9):
- U1/U2 repo temporal con el `.gitattributes` real: Daily con checkout anterior al intradía → push OK, `.jsonl` con las
  líneas de ambas ejecuciones y sin marcadores, foto `latest` de esta ejecución;
- W1 los 8 workflows suben con el helper (sin `pull --rebase` ni `git push` sueltos); W2 checkout `ref: main` en el
  grupo compartido (salvo jobs de solo lectura);
- H1 `OVERDUE`+sin pasada → `PENDING`; H2 `STALE` o fuera de presupuesto → `LATE`; H3 otra causa → `LATE`;
  H4 manual desactivada → `EXCLUDED`, activa no; H5 §01-b provisional más nuevo con cierre al día → `PENDING`;
  H6 provisional sin cierre al día → `LATE`.
- Con los datos del 6-oct 18:20Z: 19 incidencias (10:17Z) → **0**; 11 NZD y `S01B.json` en `PENDING`.
Suite completa y `scripts/tools/validate_smoke.sh` en verde antes de subir.

## Siguiente
- El Factor USD (cron 15:30Z) se ejecutaba ~6 h tarde; relanzado a mano el 6-oct (run 37509794815).
- Si el propietario quiere que ESTR deje de esperar al Daily: añadir `fetch_estr.py` al grupo EU del intradía
  (13:17Z). No incluido: no se pidió.

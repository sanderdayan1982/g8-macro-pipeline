# ACTA P-12 — El Mac lanza los workflows de datos a su hora (los cron de GitHub llegan 4–8 h tarde) (6-oct-2026)

Autorizado por el propietario (6-oct-2026): «prepara 1» (opción 1 de la propuesta en el chat). Estado: **preparado, sin
instalar**. La activación en el Mac (token + launchd) la hace el propietario; ver «Activación».

## Hallazgo
Hora real de arranque de los cron programados de *Intraday official feeds* (`gh run list`, `SCHEDULE` del log):

| cron (UTC) | 5-oct | 6-oct |
|---|---|---|
| 23 0 (ASIA) | 05:51 (+5,5 h) | 06:29 (+6,1 h) |
| 17 3 (ASIA) | 10:25 (+7,1 h) | 10:16 (+7,0 h) |
| 17 13 (EU) | 21:05 (+7,8 h) | 18:54 (+5,6 h) |
| 47 15 (US) | 22:12 (+6,4 h) | — |
| 17 23 (LATE) | 02:56 (+3,7 h) | — |

El mismo retraso explica el Daily de las 21:30Z corriendo pasada la medianoche (acta P-11), el hueco §01-b de
24-sep→1-oct (corregido en PR #2) y el Factor USD de las 15:30Z llegando a las 22:05Z. Corrección del chat: ESTR ya
estaba en el grupo EU del intradía; no hacía falta moverlo.
Los `workflow_dispatch` arrancan en segundos (relanzamientos manuales del 6-oct: Daily, Factor USD, snapshots).

## Decisión
1. `mac/dispatch_workflows.py` (launchd `com.g8.dispatch`, cada 5 min, Python 3.9 del Mac, stdlib + `g8common`):
   para cada turno de `mac/dispatch_schedule.json` calcula su última hora nominal (UTC, misma expresión cron que el
   workflow) y, si han pasado ≤ 3 h y no se lanzó ya, hace `POST /actions/workflows/<fichero>/dispatches`
   (`ref: main` + `inputs.group` en el intradía). El turno se marca hecho en `state/dispatch_state.json` solo tras el
   2xx; un fallo se reintenta en la pasada siguiente. `--dry-run` y `--check` (valida el token sin lanzar nada).
2. Turnos (11): intradía ASIA ×2 / EU / US / LATE, Factor USD ×2, Daily, G8 Port vie/sáb, metales sáb.
   **Excluidos**: `ingest_watch` y `maintenance_agent` (un lanzamiento manual se salta su calendario de días alternos y,
   en el agente, su presupuesto) y `cme_options` (Databento es de pago: el cron tardío duplicaría consultas).
3. Los cron de GitHub **no se tocan**: siguen como respaldo y, al llegar tarde, son un segundo intento inocuo (descargas
   idempotentes; el cierre §01-b es de una sola escritura: `s01b.py` «session already evaluated» y
   `finalize_session.py` lo verifica). Concurrencia: el grupo `g8-shared-data-alerts` y el helper P-7/P-11 ya
   serializan e integran las subidas.
4. Credencial **separada** `~/.g8/github_dispatch_token`: fine-grained, solo este repo, solo «Actions: Read and write».
   No usa ni amplía el token de datos (`~/.g8/github_token`, cuyo `check_token` marca como exceso cualquier permiso
   extra). Nunca se imprime. Sin token / 401-403 / caducidad ≤ 7 días → `logs/ALERTAS.log` del Mac.
5. Si el Mac está dormido o apagado, el turno cae fuera de su ventana y manda el cron de GitHub (comportamiento actual).
   Los turnos de noche (23:17, 00:23, 03:17 UTC) solo se adelantan si el Mac está despierto.
6. `sources/ingest_inventory.csv`: fila `mac/dispatch_workflows.py` (`NO_ES_DESCARGADOR`).

## Activación (propietario)
1. GitHub → Settings → Developer settings → Fine-grained tokens → nuevo token: repositorio *solo*
   `sanderdayan1982/g8-macro-pipeline`; permisos de repositorio: **Actions: Read and write** (Metadata: Read se añade
   solo); nada más; caducidad 90 días. Guardarlo en `~/.g8/github_dispatch_token` (`chmod 600`).
2. Copiar `mac/dispatch_workflows.py` y `mac/dispatch_schedule.json` a `~/Trading_Sander/g8-nzd/` (ya contiene
   `g8common/` del lote 1) y comprobar: `/usr/bin/python3 dispatch_workflows.py --check` (0 = bien).
3. Copiar `mac/com.g8.dispatch.plist` a `~/Library/LaunchAgents/` y cargarlo:
   `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.g8.dispatch.plist`.
   Retirada: `launchctl bootout gui/$(id -u)/com.g8.dispatch` y borrar el plist (los cron siguen funcionando).

## Sin cambios
Workflows y sus cron, modelos, s01b, umbrales, job `com.g8.nzd-b2` y token de datos del Mac.

## Pruebas
`tests/test_p12_mac_dispatch.py` (10): ventana de recuperación; días de semana UTC; petición exacta y una vez por turno;
fallo → reintento, 401 → aviso; sin token no llama a GitHub ni filtra el token; `--check` y aviso de caducidad;
`--dry-run` sin llamadas; horario idéntico a los cron de los workflows y grupo intradía igual al del `case` del
workflow; toda workflow con cron incluida o excluida con motivo; plist y sintaxis Python 3.9.
Suite completa y `scripts/tools/validate_smoke.sh` en verde.

## Siguiente
Tras activarlo: comprobar en 1–2 días con `gh run list` que los turnos arrancan a su hora (evento `workflow_dispatch`)
y que la cabecera del dashboard deja de acumular retrasos por la tarde.

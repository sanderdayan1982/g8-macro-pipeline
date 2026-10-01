# ACTA P-5 — Job del Mac (NZD/CHF/TONA): 19:30 de Bata, RunAtLoad, guarda «ya hecho» y despertar (1-oct-2026)

Autorizado por el propietario («pasamos al punto 4», 1-oct-2026).

## Hallazgo
- `com.g8.nzd-b2` (launchd, `~/Trading_Sander/g8-nzd`) corría a las 08:00 y a las 17:00 con RunAtLoad. Si el Mac
  dormía, launchd lanzaba la ejecución perdida al despertar, pero nada despertaba al Mac, y RunAtLoad repetía
  ejecuciones ya hechas (por ejemplo al reiniciar).
- Lo instalado coincide byte a byte con el repo, salvo `g8common/__init__.py` (cadena de versión 1.4.3 → 1.5.4) e `ingest.py`
  (parámetro opcional `max_attempts` de P-4; el Mac no lo usa).
- **SNB directo para CH_POLICY: descartado.** El cubo `snbgwdzid` (serie `LZ`, «SNB policy rate») se publica una vez por
  semana: última publicación 28-sep-2026 10:00, con datos hasta el 25-sep (verificado en data.snb.ch, 1-oct). El BIS ya
  llega al 29-sep, así que no aporta frescura. Nuevas decisiones del SNB → `data/manual/policy_decisions.csv` (P-1).

## Decisión
1. `mac/com.g8.nzd-b2.plist` v1.4: una única ejecución diaria a las **19:30** hora del Mac (Bata, UTC+1, sin cambio de
   hora), antes de la ejecución final de Actions (~21:30Z), con RunAtLoad.
2. `mac/nzd_local_run.sh` v1.5: guarda «ya hecho». Turno vigente = las 19:30 más recientes. Si `state/last_ok_slot`
   ya tiene ese turno, sale sin descargar ni publicar y lo anota en el log. El turno se marca solo si las tres
   descargas y el publicador terminan en 0; si algo falla, la siguiente ocasión (despertar, reinicio, ejecución a mano)
   reintenta. `G8_FORCE=1` ignora la guarda.
3. `mac/instalar_lote1.py` v2.1: `SCHEDULE = [(19, 30)]`. No activa a ±10 min de las 19:30 ni de las antiguas 08:00/17:00.
4. Despertar del Mac: `sudo pmset repeat wakeorpoweron MTWRFSU 19:25:00`. Es un ajuste del sistema con contraseña de
   administrador, así que **lo ejecuta el operador**, no Claude.
5. Vigilancia de Actions (`sources/executors.csv`, `run_times_local`): pasa de `08:00|17:00` a `19:30` **en un commit
   aparte, después de instalar**; cambiarla antes daría una falsa alarma de «sin latido» a las 20:15.

## Paquete e instalación
- Paquete nuevo en `~/Trading_Sander/g8-nzd/lote1` (misma lista de 19 ficheros, `MANIFEST.sha256` regenerado). El
  anterior queda en `lote1_prev_20261001`.
- Validación con el propio instalador, respondiendo «N» (1-oct, 17:28): manifiesto OK, compilación con el Python del
  Mac OK, simulación del publicador `CH-CURVA/CH-DIARIO/JP-TONA/NZ-B2 = NOOP`. «Cancelado: no se ha cambiado nada».
- Instalación: el operador abre `instalar_lote1.command` y responde «s» (fuera de 19:20–19:40). Reversión:
  `revertir_lote1.command`.

## Ampliación (1-oct-2026, OK del propietario): reintento a las 21:00
- Al pasar `sources/schedules.csv` a una sola pasada (19:30), falló la regla de cobertura (`tests/test_f1_config.py`:
  2 oportunidades de consulta en 24 h; «NZ_B2 publicado 03:04 UTC: solo 1 consulta»). No se rebaja la regla.
- plist v1.5: 19:30 + **21:00**. La guarda «ya hecho» es por turno (las 19:30 más recientes), así que si las 19:30
  terminaron bien la ejecución de las 21:00 sale sin hacer nada (sin latido); si fallaron, las repite. Las 21:00 de Bata
  son las 20:00Z, antes de la ejecución final de Actions.
- `instalar_lote1.py`: `SCHEDULE = [(19, 30), (21, 0)]`. `schedules.csv`: MAC1 19:30 + MAC2 21:00 (reintento), ACTIVE.
  `executors.csv` vigila solo el latido de las 19:30 (la de las 21:00 no publica nada si no hay nada que hacer).
- Instalación del 1-oct 19:43 (solo 19:30): primera ejecución con rc 0, latido publicado, turno marcado. Reinstalación con
  el reintento: la ejecuta Claude con el OK del propietario, por el mismo instalador (validación + reversión automática).

## Sin cambios
Descargadores (`fetch_nzd_b2.py`, `fetch_chf_snb.py`, `fetch_tona_mac.py`), publicador, familias, token, fuentes y
metodología.

## Pruebas
`tests/test_p5_mac_schedule.py` (7, incluido el reintento de las 21:00):
- plist con 19:30 + 21:00 y RunAtLoad, aceptado por `render_plist`;
- ventana del instalador (19:25 / 20:55 / 07:55 / 17:05 bloquean; 12:00 no);
- guarda ejecutada con bash y fetchers falsos: turno de ayer antes de las 19:30 y de hoy desde las 19:30; turno hecho no
  repite; un fallo no marca y luego reintenta; `G8_FORCE`; el reintento de las 21:00 no hace nada si las 19:30 salieron
  bien y completa el turno si fallaron.
`tests/test_mac_installer.py` (20) sigue en verde con el horario nuevo.

## Instalación final (1-oct-2026 19:56, la ejecuta Claude con el OK del propietario)
Instalador: validación correcta (las 4 familias NOOP), copia en `backup_lote1_20261001195648`, «Instalado. launchd cargado y
comprobado». launchd: 19:30 + 21:00 + RunAtLoad. La ejecución de RunAtLoad dio «ya hecho» (turno de las 19:30 terminado
bien a las 19:43). `pmset -g sched`: «wakepoweron at 7:25PM every day» (lo programó el operador). `executors.csv`: 19:30.

## Siguiente
Punto 5: agente de mantenimiento (acta P-6).

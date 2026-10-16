# ACTA B1 — Un solo monitor de salud, con límite de espera (16-oct-2026)

**Origen:** auditoría del dashboard por Argon 4, rondas 1 y 2; plan PLAN_ARMONIA_SUITE_DASHBOARD.md con la enmienda v2, firmado por Claude y Argon. Sander dio el "procede": "tú lo haces todo, me entregas y Argon revisa".
**Base:** commit 4a37969 (2026-10-16 20:02 UTC).

## Problema [MEDIDO]
1. **El agente nunca encontraba nada que reparar.** `data/_ingest/maintenance_state.json` registra 6 intentos entre el 4 y el 14 de octubre, los 6 con resultado NO_CHANGES. Motivo: `health_monitor.py` solo trata como incidencia los estados LATE, ERROR y MISSING.
   - Las verificaciones de tipo oficial caducadas quedaban en **UNKNOWN**. Las cinco de `sources/policy_evidence.json` (GB, JP, CH, AU, NZ) caducaron el 11-oct, y `needs_repair` seguía en `false`.
   - Una observación atrasada que esperaba pasada programada (OVERDUE con causa SIN_PASADA_PROGRAMADA, acta P-11) pasaba a **PENDING sin límite de tiempo**. Solo se convertía en incidencia cuando faltaba una segunda observación; en una serie semanal, eso es una semana más.
2. **Había dos monitores con criterios distintos.**
   - `health.json`, en Python.
   - El antiguo G8DQM del navegador: §05, sondeo propio cada 4 h, estados LIVE/DEGRADED/STALE/DEAD y su propio contador en la cabecera.

## Cambios
| Archivo | Cambio |
|---|---|
| `scripts/health_monitor.py` | Nueva función `sla_deadline()`. Un OVERDUE que espera pasada programada es PENDING solo hasta **1 día hábil del calendario del feed** desde `overdue_since`; después pasa a **LATE** ("SLA superado"). El plazo se publica en `sla_deadline_utc`. Si no se conoce `overdue_since`, sigue en PENDING. |
| `scripts/health_evidence.py` | Una verificación oficial caducada (7 días o reunión ya celebrada) pasa a **LATE** con `state = EVIDENCE_EXPIRED`. Solo la verificación con fecha futura queda en UNKNOWN. |
| `docs/js/health.js` | Es el **único dueño de la cabecera**: AL DÍA (verde), N INCIDENCIAS (rojo), VERIFICACIÓN PARCIAL o SIN VERIFICAR. |
| `docs/index.html` | Se retira la sección §05 Data Quality Monitor. G8DQM deja de pintar, de sondear y de escribir la cabecera; se conservan `report()` y `computeStaleness()`, que las secciones usan para sus marcas de as-of. El atajo "5" del menú apunta ahora a la vigilancia de publicaciones. |
| `docs/js/charts.js` | Se quita la escritura en `#quality-grid`, que ya no existe. |
| `scripts/tools/dashboard_render_check.cjs` | La comprobación de render exige `#publication-health` con su tabla en lugar de `#dqm-panel`. |
| `tests/test_b1_sla.py` (nuevo) y `tests/test_health_evidence.py` | 5 pruebas nuevas del SLA, 1 nueva de evidencia futura y 2 aserciones actualizadas: la evidencia caducada ahora es LATE. |

Equivalencias con la tabla de tolerancias firmada:
- **Diario T+1:** incidencia al 3.er día hábil.
- **SONIA T+2:** al 4.º día hábil (la regla ya incluye el retraso de origen).
- **Semanales (RBA, COT):** incidencia un día hábil después de la publicación esperada, o en el acto si una pasada programada ya corrió y no trajo el dato (causa distinta de SIN_PASADA_PROGRAMADA).
- **Tipos oficiales:** incidencia en cuanto caduca su verificación (7 días o la reunión ya celebrada).

## Resultado sobre los datos de hoy [MEDIDO, 2026-10-16 20:34 UTC]
- **Antes:** UNVERIFIED · 0 incidencias · 5 sin calendario confirmado.
- **Después:** DEGRADED · **5 incidencias** (GB/JP/CH/AU/NZ: verificación oficial caducada el 11-oct).
  - AUD 2Y/real y COT siguen en PENDING, con plazo el 19-oct a las 04:00 y a las 20:30 UTC.
- En cuanto se suba el lote, `needs_repair = true`: el agente de mantenimiento tiene trabajo real (reverificar los cinco tipos, mantenimiento.md §3) y llegará el aviso por Telegram (ingest_watch `--notify`).
- **Primera prueba en vivo:** si el agente tampoco actúa ahora, el fallo está en el propio agente.

## Pruebas
- `python -m unittest discover -s tests`: **766 OK** (11 omitidas).
- Sintaxis JS correcta: 3 archivos y 12 scripts en línea.
- `dashboard_alerts --dry-run` correcto.
- Render en Chromium: la vigilancia de publicaciones se pinta y la cabecera muestra "5 INCIDENCIAS".
- `attr-matrix` sale vacío en este entorno de pruebas **también en la base sin cambios**: es preexistente del entorno (hosts externos cortados) y no lo introduce B1.
- En el entorno de pruebas, la instalación de navegadores de `validate_smoke.sh` falla por la red; el render se ejecutó con el Chromium local.

## Lo que no cambia
Modelos, señales, umbrales, la §01-b, el ACM, los metales, el calendario de festivos y los presupuestos del registro.

## Auditoría de Argon 4 (16-oct-2026): APROBADO con 2 observaciones
- **Obs. 1, aplicada:** `sla_deadline()` trata como UTC un `overdue_since` sin zona horaria. Antes podía lanzar un TypeError al comparar fechas con y sin zona (naive/aware). Prueba nueva: `test_naive_timestamp_is_utc_and_comparable`.
- **Obs. 2, aplazada a A4/A5:** limpiar `render()`/`runAll()` de G8DQM, que ya son código muerto e inofensivo.

## Pendiente (siguientes pasos del plan)
A1 → A4 → A5. El calendario completo de los 8 bancos centrales va en A5, que es donde se muestra.

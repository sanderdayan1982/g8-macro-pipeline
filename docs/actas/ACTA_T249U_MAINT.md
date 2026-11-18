# ACTA — ramas agent/maint-* con el filtro AUTO (orden t249u)

**Decisión:** Sander, 18-nov-2026. PR contra `main`. No se fusiona. No se reactiva `maintenance_agent.yml` ni `daily_update.yml`.

## Hallazgo
El job `publish` empujaba `agent/maint-*` con todo el candidato, incluidas rutas fuera de AUTO y, si el agente usaba `git add -f`, rutas ignoradas.

## Decisión
Antes de ese push, `scripts/tools/agent_publish_filter.py` aborta si alguna ruta del candidato está ignorada o no pasa `allowed()` de `scripts/tools/agent_gate.py` (ALLOW menos DENY). El prompt del agente y `disallowedTools` prohíben `git add -f` y `git add --force`.

## Sin cambios
Permisos, cron, compuerta AUTO de integración a `main`, y los tests existentes.

## Siguiente
No fusionar. No activar el workflow.

# ACTA — El Mac lanza también los refrescos de mesa-macro-fx a su hora (2026-10-08)

**Hallazgo.** Los cron de mesa-macro-fx llegan 3–4 h tarde (GitHub Actions). Ejemplo `refresh-usd` (cron 20:20 / 21:20 / 23:00 UTC):
ejecuciones reales 06-oct 23:53, 07-oct 00:48 y 01:51; 07→08-oct 00:15, 01:03 y 02:19 (`gh run list -w refresh-usd`).
g8 no lo sufre porque el Mac ya lanza sus workflows con workflow_dispatch (acta P-12, `com.g8.dispatch`).

**Decisión.**
- `mac/dispatch_workflows.py`: además de `dispatch_schedule.json`, lee cada `dispatch_schedule_<nombre>.json` (otro repo, con
  su propio `token_path` obligatorio; el env `G8_DISPATCH_TOKEN` solo vale para g8). Sin token de un horario → aviso y solo se salta ese horario.
- `mac/dispatch_schedule_mesa.json`: 32 turnos diarios y semanales de USD, EUR, JPY, NZD, AUD, CHF, GBP y CAD, con el mismo
  cron y el mismo `lane` que asigna cada workflow (backfill=false). Mensuales/keepalive del día 1 siguen con el cron de GitHub
  (el lanzador no admite día del mes). Excluidos: mesa-digest (envía mensaje; un segundo run lo duplicaría), maintenance-agent.
- Token nuevo: `~/.g8/github_dispatch_token_mesa`, fine-grained, solo mesa-macro-fx, «Actions: Read and write».

**Sin cambios.** Metodología, workflows de mesa, fuentes. El cron de GitHub queda como segundo intento (refrescos idempotentes).

**Pruebas.** `tests/test_p12_mac_dispatch.py` M1–M6: repo y token propios, lane correcto, una vez por turno, sin token no bloquea
a g8, el env nunca se usa para mesa, crons y lanes = workflows de mesa (contra una copia local). Suite completa: `validate_smoke.sh`.

**Siguiente.** Crear el token de mesa; instalar en `~/Trading_Sander/g8-nzd/`; `--check` del token.

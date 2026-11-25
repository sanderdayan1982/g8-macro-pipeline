# Migración de Daily Update al VPS

1. **Pieza 1.** Añadir el script, el servicio y el timer de systemd con sus pruebas. El cron de Actions y el turno del dispatcher siguen activos. El timer queda sin habilitar. La unidad usa Python 3.11 y las dependencias aprovisionadas en `/srv/g8/venv`.
2. **Pieza 2.** Ejecutar un dry-run en el VPS sobre un clon de pruebas, sin push al repo público ni al privado. Comparar los archivos, registros de pasos y resultado de ingest_watch con la ejecución de Actions del mismo día.
3. **Pieza 3.** Dejar el workflow solo con `workflow_dispatch`. Quitar el turno `daily` de `mac/dispatch_schedule.json` y de la copia privada del launcher (46 → 45). Ajustar la prueba con el comentario literal «orden Sander 24-nov: migrado al VPS, sin cron ni turno en GitHub». Habilitar el timer solo después de que Sander confirme los secretos.

Secretos por nombre: `G8_FULL`, `G8_DATA_READ_CI`, `G8_DATA_WRITE`, `FRED_API_KEY` (opcional), `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID`.

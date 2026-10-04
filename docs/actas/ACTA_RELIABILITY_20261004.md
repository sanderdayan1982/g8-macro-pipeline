# Auditoría y refuerzo institucional — 4 octubre 2026

## Mandato y alcance

El propietario autoriza reforzar frescura, reparación automática y búsqueda de alternativas oficiales. Base auditada: `dd37ff81f53914057c83cf5a7d2ccfdf806e058d`. Sin cambios en fórmulas, calibraciones ni umbrales de los modelos. Esta acta distingue capacidad implementada de recuperación efectiva de los datos.

## Hallazgos con evidencia

1. **Cierre CTF detenido.** `data/s01b/state.json` conserva 2026-09-23 mientras `S01B.json` provisional llega a 2026-10-02. Actions run 37082707921/job 111086482894 llega el sábado 3-oct a 00:43:47Z y omite la sesión por fin de semana. Run 36948648656/job 110656399712 llega el 2-oct a 01:04:13Z y convierte FINAL en PROVISIONAL. Causa: usar fecha/hora de ejecución en vez de sesión programada.
2. **EST AUD mutable.** `assemble(raw)` añadía la fila estimada a los datos oficiales originales. Una segunda renderización conservaba el número estimado pero perdía su etiqueta. Diferencial visible estimado +14.82 pb frente a +5.4 pb oficial del brief. Reproducción y regresión Node incluidas.
3. **Frescura por calendario equivocado.** La unión de festivos G8 podía reducir el retraso británico por festivos japoneses. Del 18 al 24 septiembre: 4 días hábiles GB, 1 JP. Se añade vigilancia por jurisdicción sin modificar motores de señal congelados.
4. **Permisos de mantenimiento.** Se separan generación, pruebas sin secretos de proveedores ni escritura, y publicación. La compuerta se copia desde la base confiable antes de cargar el candidato y se vuelve a aplicar desde main antes de publicar. El modelo AUD deja de estar permitido para integración automática.
5. **SONIA: desfase real del canal gratuito.** BoE publica inicialmente T+1 a las 09:00 Londres; IADB gratuito llega el día hábil siguiente, T+2 a las 10:00. Se corrige el calendario del canal, conservando el margen existente. No se declara acceso T+1 que no existe.

Fuentes primarias comprobadas el 4-oct-2026:
- https://www.bankofengland.co.uk/markets/sonia-benchmark/sonia-key-features-and-policies (Access).
- https://www.bankofengland.co.uk/statistics/yield-curves (curvas normalmente antes de mediodía del día hábil siguiente).
- Alternativa BoE descubierta: https://www.bankofengland.co.uk/-/media/boe/files/statistics/yield-curves/latest-yield-curve-data.zip. La prueba en este entorno obtuvo HTTP 403; NO se adopta ni se presenta como fallback validado.

## Cambios

- `finalize_session.py` elige la última sesión TARGET cuyo cierre de 21:30Z ya transcurrió; invoca el detector congelado con fecha explícita y exige log final + estado. No reconstruye sesiones omitidas con datos revisados de hoy.
- `health_monitor.py` publica estado por fuente, fecha observada, observación esperada, fallos de descarga, presupuestos locales, caducidad manual y cierre CTF. UNKNOWN nunca significa CURRENT. El cierre dispone de cuatro horas de plazo operativo para retrasos observados del scheduler.
- La vigilancia se programa cada cuatro horas; sus avisos usan un registro separado, deduplicación y confirmación de entrega. Dashboard y cabecera muestran incidencias o verificación parcial. Informe con más de ocho horas se considera no actualizado.
- Recogidas adicionales laborables gratuitas: ASIA 00:23/03:17, EU 13:17, US 15:47, LATE 23:17 UTC. Presupuesto de ejecución, salidas previas preservadas por los descargadores existentes y fallo visible. No añaden llamadas a colectores de pago ni recalibraciones ACM.
- Agente: preflight cada cuatro horas, revisión preventiva diaria, máximo dos intentos automáticos/día, cuatro horas entre intentos. Límites no cierran incidencias. Dispatch manual disponible.
- Alternativas automáticas: fuente oficial HTTPS, definición/unidad/divisa/plazo/frecuencia/fecha, verificación reciente, al menos 20 observaciones comparables y diferencia normalizada cero, regresión y acta. Definición distinta o evidencia insuficiente requiere revisión. El manifiesto y tests son evidencia auditable, no una certificación independiente de los datos.
- Publicación automática solo para archivos permitidos y prueba verde; cambios concurrentes de código requieren nueva revisión. Tras integrar, el workflow solicita Validate y Daily Update explícitamente. No se ejecuta código candidato durante el trabajo con permisos de escritura.
- AUD: copia de entradas al ensamblar, etiqueta EST persistente y diferenciales sobre serie oficial como el brief.

## Sin cambios

Motores S01B, alertas macro, factor USD, BOOK_RISK, ACM, metales, opciones/futuros y parámetros del modelo AUD. No se cambian valores macro históricos, no se añaden fuentes de pago, no se fabrican cierres históricos. Las pruebas existentes protegidas de modelo se conservan; solo se ajusta la expectativa de permisos del modelo AUD.

## Validación

Base: 666 tests, 5 omitidos. Lote: 680 tests, 5 omitidos, más equivalencia S01B sobre dos snapshots reales/sintéticos, sintaxis Python/JS/YAML, registro e imports y brief en seco. La primera pasada detectó una expectativa de cron antigua; actualizada para incluir la ventana EU activa. Pasada final completa: verde (680 tests en 187.728 s, 5 omitidos); smoke completo con código de salida 0. Factor USD: 16/16 comprobaciones superadas. Entorno local Python 3.12; CI usa Python 3.11.

## Pendientes reales al generar el informe

- `RY_G8_GBP.csv`: 2026-09-30, esperada 2026-10-01; alternativa ZIP aún bloqueada.
- `JP_POLICY.csv`: último intento registrado fallido; conservar y comprobar el dato existente (2026-09-29) no equivale a reparar su canal.
- `s01b/state.json`: 2026-09-23, exigible 2026-10-02. El wrapper arregla la próxima ejecución, no demuestra aún recuperación en producción.
- `CHF_BE_MANUAL`: fecha 2026-06-18, plazo manual de 95 días agotado. No sustituir por una cifra inventada.
- 15 salidas con calendario no confirmado o tratamiento manual requieren documentar sus contratos de publicación. No se pintan como frescas.
- NZ/CHF siguen dependiendo parcialmente del Mac; no hay respaldo residencial independiente instalado.
- Persiste deuda de consistencia de snapshot (lecturas raw desde main) y límites de forward-fill en otros consumidores. Fuera de este lote.

## Criterio de aceptación operativa

Además del CI verde: observar una recogida y cierre final exitosos, descenso de incidencias basado en publicaciones reales, entrega de avisos y una reparación del agente con evidencia. Los cron de GitHub pueden retrasarse. Ni la automatización ni un proceso verde garantizan que el proveedor publique. No se promete que todos los feeds estén frescos mientras queden estas incidencias.

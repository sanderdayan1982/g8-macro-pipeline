# Evidencia de entradas y fallo seguro — 4 octubre 2026

## Hallazgos

La revisión posterior del código de metales encontró una ruta trimestral que
la auditoría anterior no había eliminado. El estado publicado usaba
`DebtToPenny(diaria)`; el riesgo estaba en la ruta de emergencia, no en esa
última publicación. GFDEBTN es trimestral nativo: solicitarlo semanal/mensual
no convierte sus observaciones. FDHBFIN también es trimestral, además mide
otro concepto (tenencias extranjeras) y está en miles de millones, frente a
los millones usados por las entradas de NFA. Ambas rutas quedan prohibidas.

Metales también terminaba con código cero aun con errores parciales, y el
workflow podía publicar una mezcla de salidas. `debt_asof` se tomaba después
de prolongar la serie: podía representar la fecha de otra entrada.

En opciones, el run 37230069058/job 111517698990 del 4-oct registra que la
sesión 2-oct no está completamente disponible: `available_end` 4-oct 11:55 UTC,
frente a mínimo 5-oct 03:30 UTC. El 1-oct del panel no es, por sí mismo, fallo.
El colector existente incorpora ese comportamiento del OI del viernes.

En cash NZD, la página primaria y el heartbeat local del 4-oct 18:30 UTC
coinciden en última observación 1-oct. B2 se publica diariamente, pero cash
contiene celdas sin dato. No se inventan observaciones para esas celdas.

## Cambios

- Solo deuda diaria Treasury verificada. Si falta, falla con motivo explícito;
  no hay sustituto semanal/mensual equivalente validado para activar ahora.
  El archivo anual de oro retirado tampoco puede reactivarse silenciosamente.
- Evidencia por entrada original, antes del arrastre: fuente, frecuencia de
  publicación, fecha máxima y fecha utilizada para el corte semanal. Se guardan
  huellas de los cinco archivos de salida; cualquier discordancia invalida la
  certificación. Fecha de observación de deuda corregida antes del arrastre.
- Publicación de metales completa o conservación de la anterior. Errores y
  timeouts quedan en ledger; un intento fallido posterior invalida el estado
  verde anterior. Fallo de cálculo de un metal devuelve rc=1. Una salida válida
  con calidad REVIEW sigue publicándose con su etiqueta; no se confunde calidad
  de señal con fallo de datos. Workflow restaura salidas
  antes de refrescar brief si el paso falla. Concurrencia compartida evita
  carreras con otros snapshots. No mensajes desde este workflow.
- Límite operativo de entradas en el corte semanal: 7 días para publicaciones
  diarias/semanales; 31 para mensual, marcado como último recurso. No es un
  cambio de parámetros del modelo. DTWEXBGS tiene observaciones diarias y
  difusión H.10 semanal: se declara esa difusión. Precios Stooq/export local
  sin equivalencia verificada no reciben certificación ni sustituyen una
  publicación completa verificada en esta ruta.
- Metales vence al terminar el sábado UTC, ventana de su ejecución semanal;
  no se exige un precio diario a una salida que el modelo calcula semanalmente.
- Cash exige descarga sin retención ni fallo, publicación coherente y consulta
  posterior al último horario B2 exigible (15:00 Auckland, calendario NZ),
  con fecha actual del bono diario 10Y del mismo libro para descartar un XLSX
  sin actualizar aunque el servidor responda correctamente.
  Si aparece otra publicación después de la descarga, vuelve a sin verificar.
- Opciones: validación de solo lectura de estado, 12 archivos canónicos y
  recomputación de métricas con el constructor existente. El colector y sus
  fórmulas permanecen idénticos. Plazo operativo: día laborable siguiente a la
  sesión, 21:00 UTC (último intento 17:00 + 4 h de margen). Es plazo operativo,
  no promesa del proveedor; festivos sin evidencia específica no se dan por
  satisfechos. Antes del plazo, PENDING; después, LATE si no llega. Las fechas
  trimestrales de vencimiento de contratos no son frecuencia de datos.

## Sin cambios

Kalman, calibraciones, señales, drivers algebraicos, ventanas y resampling sin
cambios. Prueba de hashes AST para nueve funciones matemáticas existentes.
Vigilancia y agente: un día sí y otro no. Colector de opciones y sus horarios
intactos; sin llamadas nuevas a Databento ni compras de datos. Metales conserva
su cron semanal; el acta dispara solo una comprobación extraordinaria con las
fuentes gratuitas ya existentes. Prioridad diaria, semanal, mensual último
recurso; trimestral y anual prohibidos.

## Evidencia primaria y operativa (verificada 4-oct)

- https://fred.stlouisfed.org/series/GFDEBTN — frecuencia nativa trimestral.
- https://fred.stlouisfed.org/series/FDHBFIN — trimestral y miles de millones;
  concepto diferente del total de deuda pública usado por la fuente diaria.
- https://fred.stlouisfed.org/series/DTWEXBGS — serie de observaciones diarias H.10.
- https://www.federalreserve.gov/releases/h10/ — difusión H.10 semanal.
- https://www.rbnz.govt.nz/statistics/series/exchange-and-interest-rates/wholesale-interest-rates
  — publicación diaria, desfase un día laborable, aproximadamente 15:00 NZ;
  cash 1-oct 2.84 y huecos visibles en fechas anteriores.
- https://github.com/sanderdayan1982/g8-macro-pipeline/actions/runs/37230069058
  — disponibilidad real de opciones, sin descarga adicional.
- https://github.com/sanderdayan1982/g8-macro-pipeline/actions/runs/37121980017
  — metales 3-oct: precios hasta 2-oct; DGS10/EFFR 1-oct;
  WRESBAL/WTREGEN 30-sep; DTWEXBGS 25-sep; salida semanal 29-sep.

## Pruebas y publicación

Regresiones de bloqueo trimestral y anual; rechazo de entradas atrasadas o sin
verificar; conservación completa ante fallo parcial; huellas de salidas;
caducidad semanal; fallo posterior; opciones pendientes frente a retrasadas;
validación cash después de nueva publicación; equivalencia de funciones del modelo.
Gate obligatorio antes de subir: `bash scripts/tools/validate_smoke.sh`.
El resultado del gate y de la prueba real en Actions se comprueba en el PR.
CHF BE/real continúa sin fuente equivalente diaria/semanal/mensual verificada.

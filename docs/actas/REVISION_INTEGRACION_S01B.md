# Integración S01B — 19 de septiembre de 2026

Estado al preparar la publicación: integrada y verificada localmente. C no ha empezado.
Autenticación GitHub renovada y verificada con permiso workflow. Pendiente completar subida,
comprobar Validate y ejecutar USD Factor. El 19-sep es sábado: la primera sesión TARGET
posible posterior es el lunes 21-sep, condicionada a despliegue y disponibilidad de datos.

## Cambios

- Motor S01B v1.1, tabla §01-b, DQM, registro y conexión a ambos workflows de datos.
- D5: etiqueta descriptiva TP/NOM y advertencia sobre nominal negativo o próximo a cero.
- FX atrasado congela señal; DESFASE conserva inicio de ventana; calendario completo detecta huecos.
- Baseline independiente por divisa al obtener datos válidos.
- Snapshots con todas las entradas analizadas y copia del motor; hashes, replay completo y recuperación de estado/eventos/salida.
- Cola de eventos pendientes y confirmación del envío Telegram antes de marcar entregados.
- Concurrencia compartida entre workflows; Validate ejecuta las pruebas de regresión.
- Acta distingue ensayo retrospectivo de inicio prospectivo de C. Anexo v1.0 conservado como antecedente.

## Comprobaciones completadas

17 pruebas pasan (8 originales + 9 regresiones). Compilación Python, sintaxis JS (12 scripts inline más
archivos docs/js), parseo YAML, 121 filas del registro sin columnas sobrantes ni identificadores duplicados,
dry-run de alertas, dry-run del motor y git diff --check.
Ensayo temporal con datos reales a 18-sep: publicación aislada y replay exacto de filas y eventos.
No se escribieron datos de producción ni se enviaron mensajes en estas pruebas.
Snapshot medido: 992.528 bytes (~1 MB/sesión, ~63 MB/63 sesiones; variable).

## Límites y siguiente paso

La CI remota y la vista publicada aún no se han comprobado. No se ha ejecutado el workflow remoto.
Telegram puede repetir un mensaje si falla la confirmación tras entregarlo; no se promete exactamente una entrega.
La retrospectividad de ACM permanece; este trabajo no valida económicamente CTF ni sustituye RTF10.
RTF10 v3.6.4 y sus 12 alertas no se han tocado.

Publicación: referencia remota comprobada sin cambios nuevos; publicar todos los ficheros juntos,
comprobar Validate y USD Factor, y confirmar el primer log prospectivo y su replay.
El ZIP adjunto contiene los archivos finales, no el parcheador original: no volver a ejecutar patch_s01b.py
sobre esta integración, porque las correcciones posteriores ya están incorporadas.

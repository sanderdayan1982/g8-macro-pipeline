# Frecuencia máxima mensual — 2026-10-05

## Hallazgo
El registro y las reglas activas no declaran publicaciones trimestrales o más lentas. Sin embargo, `dashboard_alerts.build_book` aún aceptaba los antiguos valores `CHF_BE_MANUAL` y `NZD_BE_MANUAL` si reaparecían en el JSON. Hoy están vacíos y desactivados, pero eso no protegía frente a una restauración accidental. El pie explicativo de oro aún citaba FDHBFIN aunque la ingesta utiliza Treasury DebtToPenny diario.

## Decisión e implementación
Eliminar completamente el consumo de esas dos constantes en el brief; conservar BE/REAL no disponibles cuando faltan fuentes admisibles. Corregir el texto de oro para describir la fuente que utiliza el código. Prueba con valores trimestrales restaurados, tanto disabled=true como false. Prueba de frecuencias declaradas en registro y calendarios, para bloquear regresiones en CI.

Prioridad obligatoria: diario > semanal > mensual como último recurso. Prohibidos trimestral, semestral, anual y más lentos, incluso como respaldo. Una frecuencia de descarga o un relleno diario no cambia la frecuencia nativa. No se incorpora ninguna fuente nueva.

## Sin cambios
Fórmulas, calibración, umbrales, modelo semanal de metales, contratos de futuros/opciones, periodicidad de vigilancia alterna, compras y envíos. El vencimiento trimestral de un contrato no equivale a datos trimestrales: sus precios/OI siguen teniendo observaciones diarias.

## Verificación
Suite completa `bash scripts/tools/validate_smoke.sh` exigida antes de publicar. Las pruebas reproducen el riesgo de reactivación de valores retirados. No se certifica frescura global: el health publicado 2026-10-05 10:25 UTC contiene retrasos operativos en ESTR, SONIA y B2 NZD, independientes de esta corrección de frecuencia.

La primera pasada completa ejecutó 716 pruebas: dos fallos por huellas congeladas del archivo modificado. Se contrastaron las funciones por AST contra main: únicamente cambia `build_book`; las funciones de señal permanecen idénticas. Se actualizan las dos huellas y se repite la suite completa.

## Recuperación autorizada en esta sesión
Se ejecutan una vez los colectores oficiales existentes, B2 sin histórico, NZ linkers y factor BCE, con presupuestos y publicación de salidas validadas. Sin cambiar fórmulas ni calendarios recurrentes. `G8_NO_SEND=1`; sin colectores de pago. Un rechazo del proveedor conserva los datos previos y queda visible como fallo.

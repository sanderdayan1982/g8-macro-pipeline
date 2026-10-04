# Recuperación de pendientes — 4 octubre 2026

Mandato: continuar pendientes; vigilancia y agente conservan días alternos.

Diagnóstico inicial: main ad0c799; CI verde, health aún con cuatro incidencias. El fallo JP del run 37082707921 era respuesta HTTP 200 sin cabecera CSV. GBP nominal ya consume el ZIP oficial que también contiene curvas reales. Se añade diagnóstico manual desde Actions (solo lectura, sin secretos, salidas como artefactos), porque las conexiones a estas fuentes desde el entorno de auditoría agotan su timeout. El disparo de rama es exclusivo de esta reparación, no una programación recurrente.

SNB: https://www.snb.ch/en/publications/communication/press-releases-restricted/pre_20260924 confirma 0.8% para 2028. El dato anterior 0.7% para 2028 consta en https://www.snb.ch/en/publications/communication/press-releases-restricted/pre_20260618. Esta actualización se RETIRA antes de integrar: el propietario prohíbe cualquier fuente trimestral, incluso como respaldo. No se publica el 0.8 ni se instala su descargador.

Alternativa BIS oficial: https://data.bis.org/bulkdownload enlaza https://data.bis.org/static/bulk/WS_CBPOL_csv_flat.zip (mismo WS_CBPOL). Fuente BoE: https://www.bankofengland.co.uk/statistics/yield-curves confirma curvas nominales/reales spot, capitalización continua anual y publicación al mediodía del siguiente día hábil.

Validación y resultados de recuperación se completarán después de las pruebas y las comprobaciones de fuente.

## Resultado del diagnóstico real desde Actions

Run 37224657436: BoE latest ZIP HTTP 200 (187658 bytes), BIS API HTTP 200 (62368083 bytes), BIS bulk HTTP 200 (4134512 bytes). El canal japonés estaba fallando de forma transitoria; una respuesta posterior volvió a ser CSV. La alternativa bulk reproduce exactamente las últimas 20 observaciones comunes de JP, hasta 2026-09-29; los metadatos confirman serie diaria D.JP, porcentaje anual y multiplicador cero. Se exige ese solapamiento también antes de activar el fallback en producción. El fallo primario conserva aviso DEGRADED si se usa el respaldo.

BoE latest contiene los tres libros oficiales, hoja `4. spot curve`, tenor exacto 10 años. Para 2026-10-01: nominal 5.424033836234473, real 1.992255268983954, inflación implícita 3.431778567250519 (% anual, capitalización continua). Nominal coincide, redondeado a cuatro decimales, con GBP_BILL_10Y existente; NOM−REAL coincide con inflación implícita. El archivo de mes nuevo tiene una observación: no se afirma un solapamiento de 20 para GBP. La identidad, definición publicada por BoE y comprobación del nominal fundamentan esta reparación revisada; no es una sustitución aprobada autónomamente por la compuerta del agente.

## Reparación

- `fetch_gbp_real.py`: libros oficiales diarios como vía primaria, IADB de las mismas series como respaldo; comprueba tipo de curva, tenor, fechas alineadas, números finitos e identidad. Publicación atómica mediante el módulo existente, preservando toda la historia al cambiar de mes y bloqueando regresiones. `real_yields_g8.py` delega solo GBP en ese descargador; cálculos del resto intactos.
- `fetch_bis_policy.py`: exportación bulk oficial si el API falla; dimensiones, país, frecuencia, unidades y 20 observaciones comunes obligatorias. Se mantiene BIS más decisiones verificadas; no se convierte un dato mensual en diario.
- Recuperación puntual al integrar esta acta: prueba los descargadores, evalúa la última sesión CTF vencida sin reconstruir sesiones perdidas y actualiza brief/health sin enviar mensajes ni avanzar el registro de entrega. Los cron de vigilancia y del agente NO se alteran: siguen en días alternos.

## Instrucción de frecuencia del propietario

Prioridad: diario > semanal > mensual como último recurso documentado. No confundir frecuencia de las observaciones, publicación y consulta. Los tipos oficiales siguen por evento. La compuerta rechaza incorporación automática de alternativas mensuales o más lentas; el agente debe investigar primero equivalentes diarios/semanales. Fuentes trimestrales o más lentas PROHIBIDAS incluso como respaldo. Las constantes CHF_BE_MANUAL y NZD_BE_MANUAL se desactivan en JSON y en el navegador (sin fallback de arranque ni lectura del antiguo valor). NZD conserva los IIB diarios; si faltan, BE/REAL queda NO DISPONIBLE. CHF BE/REAL queda NO DISPONIBLE, sin inventar equivalencia con CPI mensual. Proxies de otra definición no sustituyen silenciosamente fuentes lentas.

El registro antiguo etiquetaba seis salidas ACM como mensuales pese a su aplicación diaria: corregido. Las ocho salidas ACM tienen límite operativo de cuatro días hábiles, coherente con los principales feeds diarios; el calendario puede declarar atraso antes. Esto endurece frescura, sin cambiar parámetros, muestras ni fórmulas del modelo. La curva completa CHF sigue publicada mensualmente, con NOWCAST diario explícito basado en nominal 10Y; no se presenta ese NOWCAST como curva diaria observada. La demanda oficial de oro mensual permanece como último recurso. Las referencias CPI trimestrales se excluyen, no se actualizan ni se utilizan como respaldo.

## Verificación

Diagnóstico inicial: 683 pruebas (5 omitidas). La primera propuesta pasó 695 pruebas, pero se retiró antes de integrar al corregir el propietario el límite máximo mensual. La versión final elimina el descargador SNB y sus pruebas; añade controles que impiden reactivar constantes trimestrales desde JSON o el navegador. Suite final local verde: 693 pruebas (5 omitidas), equivalencia S01B, YAML, registro, JavaScript, brief e importaciones. CI se verifica nuevamente antes de integrar. La recuperación puntual no modifica la vigilancia en días alternos.

Revisión adicional: retirados los helpers de constantes BE y los valores nominales de arranque de junio del navegador. Los enlaces de entrada manual permanecen sin cifras; solo se acepta nominal manual fechado y dentro de su presupuesto existente de 5 días hábiles. Sin valor válido no se sintetiza historial. Esto evita que caché/localStorage o defaults antiguos oculten una ausencia.

## Resultado de recuperación y cierre de evidencia anterior

Run 37227461147 finalizó correctamente: GBP publicó 2026-10-01 (REAL10 1.9923), Japón descargó y publicó la serie oficial hasta 2026-09-29, y CTF confirmó 2026-10-02. El panel conservaba el error del wrapper bis_jp del 3 de octubre pese a la publicación validada posterior del descargador del 4 de octubre. Se corrige la resolución temporal: solo una publicación PUBLISH/NOOP posterior, rc=0 y para el mismo archivo puede cerrar ese fallo; una ejecución sin publicación, otro archivo, fecha desconocida o fallo posterior permanecen abiertos. Se conserva la evidencia histórica. Esta corrección dispara una segunda verificación puntual, sin cambiar horarios.

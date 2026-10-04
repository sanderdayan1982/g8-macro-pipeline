# Corrección de frecuencia — 4 octubre 2026

El propietario aclara: «un día sí y un día no», no vigilancia cada cuatro horas. Esta corrección sustituye la frecuencia descrita en ACTA_RELIABILITY_20261004.md.

Vigilancia independiente: 18:13 Africa/Malabo. Revisión del agente: 18:17 Africa/Malabo. Días alternos anclados al 4 de octubre de 2026: 4, 6, 8… sin reiniciar la secuencia al cambiar de mes o año. GitHub programa un disparo diario barato; una compuerta de fecha omite la vigilancia y el agente en los días de descanso. La ejecución manual sigue disponible. Los cron pueden retrasarse.

Las recogidas de datos y los controles que forman parte de esas recogidas conservan sus horarios. No se cambia ningún modelo ni dato macro. El informe visible se considera caducado tras 56 horas: intervalo de 48 horas más 8 horas de margen. Un informe sigue mostrando siempre cuándo se generó.

Pruebas: alternancia durante 800 días y conexión de la compuerta a ambos workflows; suite y smoke completos antes de publicar. El resto de mejoras queda pausado por instrucción del propietario.

# U2 — auditoría independiente y regresiones de la web

Base inspeccionada: `843918b332186ce340e34b8e35d78b35097fffaa`. Referencia solicitada: `6c387bea77c1e7ae8ff88e675a9122d8cb79882e`.
Los commits `011ef19a` y `843918b3` ya incorporaban los seis puntos funcionales del adjunto. Se conserva ese trabajo.

## Hallazgos y decisión

- `tests/test_u1_dashboard_integration.py` solo buscaba texto para comprobar `briefSec`. Ahora ejecuta la función real en Node y exige la numeración visible y `COT · fuera de la página`.
- La inversión de opciones se comprueba ejecutando `userWalls` y `fmtC2Dist`: CALL CME JPY `0.0065`, referencia `0.006353`, aparece en PUT del operador a `-2.26%`. La prueba también comprueba el cruce de ambos lados.
- Se ejecuta `shadowAgeOk` con reloj fijo y se prueba `policyCells`: una sombra vencida muestra su `as_of`, marca atraso y recalcula los días. Una fecha imposible que JavaScript normaliza (30-febrero) se rechaza ahora explícitamente.
- `b2Line` convertía `null` y cadena vacía a cero. Ahora muestra `—`; un cero real sigue siendo `+0.00`. Hay regresión ejecutable sobre la función real.

## Alcance preservado

Los siete archivos congelados conservan sus SHA-256. No cambian modelos, contratos semanales, Paso C, fuentes, evidencia de política ni datos canónicos. No se envían mensajes ni se relanzan workflows.

## Auditoría para Argon 4

El paquete externo `entrega_argon4` incluye el informe, fuentes completas, configuraciones, inventarios CSV, actas originales de siete pasadas de mantenimiento, logs, manifiesto SHA-256 y dos diffs: desde U1 y desde la base actual.

Hallazgos operativos principales:

- `NO_CHANGES` mide ausencia de commits y puede coexistir con incidencias, revisión incompleta o un relanzamiento ejecutado (8-octubre).
- La compuerta no permite integrar automáticamente `sources/policy_evidence.json`; sin commits no se abre PR. B1 hace visibles las evidencias caducadas, pero no elimina ese atasco.
- El estado de AUD conserva `zband=HI` cuando `zscore` devuelve `None` con 252 spreads cero. Se documenta; no se edita `dashboard_alerts.py`.
- B2 está visible, pero `build_panel_xau/xag` limitan el panel diario al último martes semanal. Se documenta sin modificar el cálculo.
- Las etiquetas y fechas de los diagnósticos SHADOW no acreditan frescura de todos sus insumos ni sustituyen sus señales canónicas.

## Pruebas

`validate_smoke.sh` completo en verde: 864 pruebas (6 omitidas), cuatro equivalencias S01B históricas/sintéticas, YAML, 125 filas del registro, sintaxis JS, brief en seco, imports y siete paneles renderizados en Chromium. Las 23 pruebas específicas U1/U2 pasan. A3 reconstruido en evidencia coincide exactamente con el JSON del checkout; los siete hashes congelados coinciden. Una comprobación adicional del DOM verifica B2 de ambos metales, A3, sesión C2 y rótulo COT.

Entorno local: Python 3.12.14, Node 24.18.0, Playwright 1.62.1 y Chromium temporal. Los workflows configuran Python 3.11/Node 20; no se ha ejecutado CI remota ni desplegado esta rama. La primera corrida en sandbox falló al abrir sockets locales; al repetir con el permiso necesario, la suite y el smoke completos terminaron con código 0. Los logs de ambas corridas quedan en la entrega.

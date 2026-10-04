# Corrección de evidencia de frescura — 4 octubre 2026

## Hallazgo y decisión

El propietario autoriza reparar fuentes y presentación, con prioridad diaria,
semanal después, mensual solo como último recurso; trimestral y más lento
prohibidos. La vigilancia permanece un día sí y otro no. Sin gastos nuevos.

1. FRED IORB contiene fecha efectiva 5-oct ya publicada el 4-oct. El CSV conserva
   el anuncio original. El gráfico, el cálculo visual de spreads y el resumen de
   tipos oficiales usan solo fechas vigentes UTC. La comprobación operativa
   distingue `have_max` efectivo y `announced_max`: un anuncio futuro no cura un
   dato atrasado. Fuente: https://fred.stlouisfed.org/series/IORB (4-oct).
2. `OFFICIAL_GOLD_DEMAND.csv` tiene 27 observaciones anuales/estimadas, incluida
   agosto-2026, y base aproximada; no es un feed mensual. Se conserva byte a byte
   en `docs/actas/evidence/OFFICIAL_GOLD_DEMAND_RETIRED.csv`, fuera del circuito
   activo. El estado publicado de XAU ya tiene `official:false`, `spec:baseline`.
   El motor no cambia: su comportamiento existente ante ausencia del opcional
   utiliza baseline. El monitor denuncia su reaparición como ERROR.
3. Cinco tipos por evento: evidencia verificable de decisión/calendario en
   `sources/policy_evidence.json`. No se crean observaciones diarias artificiales.
   Coincidencia de valor, fecha posterior a la decisión, sin error de descarga y
   evidencia no caducada son necesarios. Caducidad operativa: como máximo siete
   días desde comprobación, o próxima reunión si antes. No es un umbral de modelo.
   JP/GB: inicio conservador de día donde no se fija una hora en la evidencia.
4. S01B.json: contraste de solo lectura con estado y log definitivos de la sesión
   exigible. No basta la hora reciente de generación. Detector intacto.
5. Dos walkforwards de metales: diagnósticos de calibración sin fecha de mercado;
   excluidos de certificación de frescura, visibles como DIAGNOSTIC. Los CSV XAU,
   XAG y su estado siguen UNKNOWN: falta certificación conjunta de precios y COT.
6. Cash NZD: publicación B2 diaria con celdas sin observación; no se rellenan.
   OPTIONS: fecha de sesión visible, validación del colector pendiente. No se
   cambia su metodología, frecuencia ni contratos.
7. La tabla de vigilancia muestra el motivo y caducidad de cada comprobación.

## Fuentes primarias contrastadas el 4-oct-2026

- GB 3.75%, decisión 17-sep, próxima 5-nov:
  https://www.bankofengland.co.uk/monetary-policy-summary-and-minutes/2026/september-2026
- JP 1.25%, vigente desde 24-sep, reunión 29–30 oct:
  https://www.boj.or.jp/en/mopo/mpmdeci/mpr_2026/k260918a.pdf
  https://www.boj.or.jp/en/mopo/mpmsche_minu/index.htm
- AU 4.60%, decisión 29-sep, vigente 30-sep según acta P-1, próxima 2–3 nov:
  https://www.rba.gov.au/media-releases/2026/mr-26-27.html
  https://www.rba.gov.au/schedules-events/board-meeting-schedules.html
- NZ 2.75%, decisión 2-sep, ejecución día hábil siguiente, próxima 28-oct 14:00 NZ:
  https://www.rbnz.govt.nz/monetary-policy
  https://www.rbnz.govt.nz/news-and-events/how-we-release-information/ocr-decision-dates-and-financial-stability-report-dates-to-feb-2028
- CH 0%, decisión 24-sep, próxima 10-dic 09:30 CH:
  https://www.snb.ch/en/publications/communication/press-releases-restricted/pre_20260924
  https://www.snb.ch/en/services-events/digital-services/event-schedule
- Cash NZD, B2 diario con celdas vacías:
  https://www.rbnz.govt.nz/statistics/series/exchange-and-interest-rates/wholesale-interest-rates

## Alternativas investigadas, no activadas

- Oro WGC: cambios mensuales con desfase de dos meses y tenencias trimestrales
  son conjuntos distintos. No se ha validado una historia mensual equivalente
  de stock acumulado. No se usa la serie trimestral ni se reconstruye con una
  base anual aproximada. https://www.gold.org/goldhub/data/gold-reserves-by-country
- CHF BE/real 10Y: no se encontró un feed equivalente diario/semanal/mensual
  verificable. El trabajo SNB propone construir una estimación con instrumentos
  extranjeros y supuestos económicos; exigiría metodología nueva. No se activa:
  https://www.snb.ch/en/publications/research/working-papers/2017/working_paper_2017_09
- Los fallbacks BE CHF/NZD trimestrales siguen deshabilitados, con ausencia visible.

## Alcance y comprobación

No se cambian fórmulas, calibraciones, S01B, ACM, ventanas, señales, contratos CME,
presupuestos del registro ni cron de vigilancia/mantenimiento. Única modificación
al archivo antes congelado `dashboard_alerts.py`: selección de fecha vigente en
la presentación de tipos oficiales. Huella actualizada explícitamente para esa
corrección autorizada de fuente/presentación, con prueba de un anuncio de valor
diferente aún no vigente; los demás archivos congelados se comprueban idénticos.

Pruebas: regresiones de anuncio futuro vs dato atrasado; discrepancia de tipos;
caducidad semanal y de reunión; reaparición de fuente retirada; selección de
vigencia en navegador y brief. Gate obligatorio: `bash scripts/tools/validate_smoke.sh`.
El resultado completo y la publicación se registran en el PR. El estado operativo
no se declara completamente verde: quedan cinco validaciones parciales y dos
respaldos manuales deshabilitados. El oro retirado no se cuenta como fuente fresca.

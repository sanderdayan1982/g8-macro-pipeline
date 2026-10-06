# ACTA P-9 — Auditoría del dashboard (6-oct-2026): nada más viejo que mensual, ACM al día y agente autónomo

Autorizado por el propietario (6-oct-2026): «AUDÍTAME EL DASHBOARD… DIARIO PRIMERO, SEMANAL DESPUÉS Y MENSUAL EN ÚLTIMA
INSTANCIA, NUNCA MÁS DE MENSUAL… APLICA LAS 4 Y QUE EL AGENTE SEA TAN PODEROSO QUE PUEDA ARREGLAR LAS COSAS SIN QUE TÚ
TENGAS QUE INTERVENIR».

## Auditoría (6-oct-2026 ~00:00 Bata, https://g8-institutional.netlify.app/ + `health_monitor.py`, 106 feeds)
- Diario: 58 feeds. Semanal: AUD oficial (RBA, con estimación diaria), COT, metales. Por evento: tipos oficiales.
  Mensual: curva CHF del SNB (con NOWCAST diario). **Trimestral o más lento: ninguno en uso** (retirados el 4-oct).
- **Infracción**: respaldo CHF ACM congelado en 2025-07 (14 meses) en §01, §04 y §00.
- Respaldos mensuales evitables: `FEDFUNDS` en metales (existe el EFFR diario del NY Fed desde 2006, verificado el 6-oct);
  CHF 10Y OCDE (FRED, mensual) sin tope de antigüedad.
- Fila muerta en §05: `USD_TP_MIRROR` (espejo semanal eco3min, sin consumidor, sin fecha).
- Atrasos de ese momento (sin fallo de feed): *Daily Data Update* lanzado por GitHub ~3 h tarde (~00:35Z); el lunes 5-oct
  fue festivo en Sídney y la RBA publicó viernes + lunes a las 22:15Z (f1-data.csv, Last-Modified Mon 05 Oct 22:15:33
  GMT); el ACM solo se recalculaba en el Daily aunque sus entradas llegasen antes por la pasada intradía.

## Decisión
1. **CHF sin respaldo congelado.** §01: si no hay SNB vigente ni respaldo mensual del mes en curso o el anterior
   (`monthsBehind ≤ 1`), CHF queda NO DISPONIBLE (DQM DEAD). §04 (`data-loader.js`): el fichero sin QUALITY no se pinta;
   ninguna serie ACM con último dato de más de 35 días se pinta como vigente. §00: «TP NO DISPONIBLE (último …)» en vez de
   «TP FROZEN 2025-07»; regla general de 35 días para el TP del libro.
2. **ACM en la pasada LATE (23:17Z)**: `acm_g8.py` (USD…AUD, NZD, CHF) y `aud_nowcast.py` después de sus entradas.
   Mismo script y argumentos que el Daily; solo cambia la hora. Las pasadas ASIA/EU/US siguen sin recalcular ACM.
3. **Metales**: respaldo de EFFR = API del NY Fed (`markets.newyorkfed.org/api/rates/unsecured/effr`, diario desde
   2006-01-03, 5.215 obs. hasta 2026-10-02). `FEDFUNDS` retirado. La fórmula de la pendiente (DGS10 − EFFR) no cambia.
4. §05 sin `USD_TP_MIRROR`.
5. **Agente más autónomo**, siempre con la compuerta de confianza y sin escribir él:
   - **Relanzamientos**: pide en `.agent/recovery.json`; `scripts/tools/agent_recovery.py` (código de main, job `publish`)
     valida la lista cerrada (`daily_update`, `intraday_fetch` con su grupo, `cme_options`, `metals_update`, `usd_factor`),
     motivo obligatorio, sin duplicados, máximo 3, no relanza lo que ya está en marcha, y lo notifica por Telegram.
   - **Dashboard**: `docs/index.html` y `docs/js/*.js` (salvo `health.js`) pasan a ser reparables con integración
     automática si:
     - pasa la **prueba de renderizado en Chromium** (`dashboard_render_check.cjs`: datos de la rama, paneles pintados,
       sin excepciones JS ni «Render error»);
     - no cambia ninguna **función de cálculo protegida** (`PROTECTED_JS`: z-scores, diferenciales, estadísticas, frescura, salud);
     - hay acta `ACTA_AGENTE_*`.
     Con la prueba hecha antes de integrar, se habría cazado el error `esc` del 1-oct (comprobado: «Render error» en §01, rc 1).
   - La prueba de renderizado entra en `validate_smoke.sh`: protege también los cambios de Claude y del propietario.
     Validate pasa de 10 a 18 min; verificación del agente, de 20 a 30.
6. Huellas autorizadas: `dashboard_alerts.py`; `load_slope` de metales (solo el respaldo); regla intradía de ACM ajustada
   en `test_institutional_reliability.py` (opciones/futuros siguen prohibidos en intradía).

## Sin cambios
Fórmulas, calibraciones, umbrales, s01b, ACM (modelo), factor USD, metales (modelo), alertas, cadencia del agente
(días alternos, límite de 2 intentos al día). CHF real/BE sigue NO DISPONIBLE (Suiza no emite bonos indexados).

## Pruebas
`tests/test_p9_institutional.py` (12):
- CHF sin congelado en §01/§04;
- §00 con TP de más de un mes → NO DISPONIBLE (con datos reales y un CHF congelado simulado);
- EFFR del NY Fed y `FEDFUNDS` fuera; §05 sin la fila muerta; orden de la pasada LATE;
- compuerta del dashboard: AUTO con acta, OWNER si toca `zScore` o `health.js`; relanzamientos (lista, límites, ocupado);
- render en el smoke.
Prueba de renderizado local: 7/7 paneles OK; con el error `esc` reintroducido → FAIL.
Suite completa + `validate_smoke.sh` (con renderizado): verde.

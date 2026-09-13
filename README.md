# G8 Macro Pipeline

Pipeline de datos y dashboard de **composición macro** para el Sistema Institucional G8
(MMT/Mosler, timeframe diario, operador único). Repo `sanderdayan1982/g8-macro-pipeline` ·
front-end en `docs/` publicado por Netlify sin build en **g8-institutional.netlify.app**.

> Doctrina: el dashboard **no es un paso del embudo**. El embudo 7+1 (RISK-G8 → XCCY/PSI →
> Curva/RTF10 → POL → IYDT → POS → FFVA → cockpit) vive en TradingView. Aquí se consulta el
> contexto de composición (long-end, fontanería, metales, walls, COT) al formar tesis de
> long-end y en la revisión semanal. Ninguna sección es una señal de entrada.

## Estado (2026-09-13)

| Componente | Estado |
|---|---|
| Dashboard `docs/index.html` | **v2.5.5** — §00 brief, una sola fuente para inputs manuales, DQM con fechas efectivas |
| Alertas Telegram `scripts/dashboard_alerts.py` | **v1.2** — un mensaje/día solo en cambios; genera `data/alerts/brief.json` |
| Fase 1 (feeds macro) | Sellada |
| Fase 2 — port FFVA (Databento futuros) | **PAUSADO** (2026-09-12): sin gasto Databento en futuros |
| Opciones CME (§08 strike walls) | Activo — Databento, ~$0.05/sesión, tope $0.25/sesión en código |
| COT (§09) | Activo — CFTC SODA, gratis, semanal (vie 21:05 + sáb 09:05 UTC) |
| XCCY G8 Command Center (proyecto anterior) | **Cancelado** (mayo 2026). Su código sigue en `docs/` como `.legacy-hidden`; el XCCY operativo es el script TradingView v2.6.1 |

## Secciones del dashboard

| § | Sección | Fuente en `data/` |
|---|---|---|
| 00 | Brief · lectura de 8 segundos (as-of por capa, gates, extremos vigentes, walls) | `alerts/brief.json` |
| 01 | Long-End Attribution Matrix (NOM = REAL + BE · Y10 = RNY + TP, ACM propio) | `ACM_G8_*.csv`, `RY_G8_*.csv`, `manual/manual_inputs.json` |
| 02 | Money-market floor spreads (RFR − suelo administrado) | `SOFR/ESTR/SONIA/TONA/CORRA/AONIA.csv`, `FLOOR_*.csv`, `*_POLICY.csv` |
| 03 | Policy rates | `*_POLICY.csv`, `FLOOR_*.csv`, `NZD_OCR.csv` |
| 04 | ACM term premium (Adrian-Crump-Moench) | `ACM_G8_*.csv` |
| 05 | Data Quality Monitor | `sources/registry.csv` (presupuestos) |
| 06/07 | Oro / plata — MDP (Monetary Disorder Premium, Kalman TVP) | `MFV_G8_*.csv`, `MFV_G8_state.json` |
| 08 | CME FX strike walls (Gate 0 = permiso, nunca trigger) | `OPTIONS_SURFACE.json`, `options/canonical/<sesión>/` |
| 09 | COT positioning (TFF + Disagg MM) | `pos_g8_cot.json` |

## Workflows (GitHub Actions)

| Workflow | Cron (UTC) | Qué hace |
|---|---|---|
| `daily_update.yml` | 18:00 lun–vie | Scrapers de tasas/RFR/floors/real yields/ACM → alertas → commit |
| `cme_options.yml` | 13:30 + 17:00 | Colector Databento de opciones (T+1, tope $0.25/sesión) → resumen → alertas → commit |
| `g8_port_run.yml` | vie 21:05 + sáb 09:05 | COT (CFTC) → generador POS → twin-test POS → Telegram diff → commit. Bloques FFVA comentados (pausa) |
| `metals_update.yml` | semanal | `metals_fairvalue_g8.py` (MDP) |
| `acm_validate.yml`, `ry_validate.yml` | manual | Validaciones ACM/real yields |
| `fx_futures_backfill.yml` | **manual, no lanzar** | Backfill Databento de futuros (proyecto pausado; consume presupuesto) |

Secrets: `FRED_API_KEY`, `DATABENTO_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

## Alertas Telegram

`scripts/dashboard_alerts.py` corre al final de `daily_update` y `cme_options`. Lee solo el repo
(coste $0), compara contra `data/alerts/state.json` y envía **solo cambios de estado** con
histéresis (umbrales de nivel = los del dashboard; umbrales de movimiento = percentiles
rolling 252 d — Regla 8, nada inventado). Cubre §01/§02/§03/§04/§05/§06-07/§08/§09 para todas
las divisas con dato. En cada sesión nueva de opciones manda la tarjeta completa de walls
(cadena completa, convención del operador para USD/JPY, USD/CAD, USD/CHF).

## Inputs manuales (principio 6 — una sola fuente)

`data/manual/manual_inputs.json` es la fuente primaria (NZD 10Y: RBNZ B2 bloquea datacenter).
`index.html` la lee al arrancar; el valor tecleado en el navegador (localStorage) solo manda si
su fecha es más reciente. Expiry por feed en `sources/registry.csv`.

## Deuda registrada

- CHF: ACM congelado 2025-07 (cubo SNB), nominal FRED OECD mensual con rezago; bills CHF manuales. Candidato: SIX Confederation reference yield / portal SNB nuevo.
- NZD: 1Y/2Y/5Y manuales sin dato; `NZD_BOND_*/NZD_BILL_*` sin refrescar desde 2026-06.
- Twin-test POS: exige ≥3 fechas-reporte antes de un FAILED válido (el brief lo etiqueta como *muestra insuficiente*).
- 2027: sincronizar umbrales XCCY copiados en `f_mx_thr` del FFVA (única deuda estructural de la suite).

## Cómo trabajar en este repo

GitHub web UI únicamente (lápiz / *Upload files*) · Netlify publica `docs/` sin build ·
calibraciones y pesos congelados no se tocan sin gate nuevo pre-registrado (Regla 32) ·
todo fallo debe ser ruidoso (Ley 2).

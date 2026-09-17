# G8 Macro Pipeline

Pipeline de datos y dashboard de **composición macro** para el Sistema Institucional G8
(MMT/Mosler, timeframe diario, operador único). Repo `sanderdayan1982/g8-macro-pipeline` ·
front-end en `docs/` publicado por Netlify sin build en **g8-institutional.netlify.app**.

> Doctrina: el dashboard **no es un paso del embudo**. El embudo 7+1 (RISK-G8 → XCCY/PSI →
> Curva/RTF10 → POL → IYDT → POS → FFVA → cockpit) vive en TradingView. Aquí se consulta el
> contexto de composición (long-end, fontanería, metales, walls, COT) al formar tesis de
> long-end y en la revisión semanal. Ninguna sección es una señal de entrada.

## Estado (2026-09-13, auditoría institucional)

| Componente | Estado |
|---|---|
| Dashboard `docs/index.html` | **v2.6.1** — §00 brief (libro G8 + walls §08), §02 una divisa a la vez, las 8 divisas con floor + ACM propio; loader/charts `docs/js/` **v5.6** |
| Alertas Telegram `scripts/dashboard_alerts.py` | **v1.9** — un mensaje/día solo en cambios; genera `data/alerts/brief.json`; vigila los feeds del Mac; diferencial REAL vs USD |
| CI de humo `validate.yml` | **Activo** — en cada push: sintaxis Python/JS/YAML, registry, `dashboard_alerts.py --dry-run` |
| Feeds locales (Mac) | **Activo** — `fetch_nzd_b2.py` v1.4 + `fetch_chf_snb.py` v1.0 + `push_nzd_to_github.py` v1.2, launchd 08:00 (ver *Eslabón Mac*) |
| Fase 1 (feeds macro) | Sellada |
| Fase 2 — port FFVA (Databento futuros) | **PAUSADO** (2026-09-12): sin gasto Databento en futuros |
| Opciones CME (§08 strike walls) | Activo — Databento, ~$0.05/sesión, tope $0.25/sesión en código |
| COT (§09) | Activo — CFTC SODA, gratis, semanal (vie 21:05 + sáb 09:05 UTC) |
| XCCY G8 Command Center (proyecto anterior) | **Cancelado** (mayo 2026). Su código sigue en `docs/` como `.legacy-hidden`; el XCCY operativo es el script TradingView v2.6.1 |

## Secciones del dashboard

| § | Sección | Fuente en `data/` |
|---|---|---|
| 00 | Brief · lectura de 8 segundos (as-of por capa, gates, libro G8 con spread nominal y REAL vs USD, extremos vigentes, walls §08) | `alerts/brief.json` |
| 01 | Long-End Attribution Matrix (NOM = REAL + BE · Y10 = RNY + TP, ACM propio, 8 divisas) | `ACM_G8_*.csv`, `RY_G8_*.csv`, `NZD_BOND_10Y.csv`, `CHF_NOM_10Y.csv`, `manual/manual_inputs.json` (BE NZD/CHF) |
| 02 | Money-market floor spreads (RFR − suelo administrado), selector por divisa | `SOFR/ESTR/SONIA/TONA/CORRA/AONIA.csv`, `NZD_CASH_ON.csv`, `CHF_SARON.csv`, `FLOOR_*.csv`, `*_POLICY.csv`, `NZD_OCR.csv` |
| 03 | Policy rates | `*_POLICY.csv`, `FLOOR_*.csv`, `NZD_OCR.csv` |
| 04 | ACM term premium (Adrian-Crump-Moench) | `ACM_G8_*.csv` |
| 05 | Data Quality Monitor | `sources/registry.csv` (presupuestos) |
| 06/07 | Oro / plata — MDP (Monetary Disorder Premium, Kalman TVP) | `MFV_G8_*.csv`, `MFV_G8_state.json` |
| 08 | CME FX strike walls (Gate 0 = permiso, nunca trigger) | `OPTIONS_SURFACE.json`, `options/canonical/<sesión>/` |
| 09 | COT positioning (TFF + Disagg MM) | `pos_g8_cot.json` |

## Workflows (GitHub Actions)

| Workflow | Cron (UTC) | Qué hace |
|---|---|---|
| `daily_update.yml` | 18:00 lun–vie | Scrapers de tasas/RFR/floors → ACM (USD EUR JPY GBP CAD AUD **NZD CHF**) → synth NZD (solo fallback) → real yields (+NZD) → alertas → commit |
| `cme_options.yml` | 13:30 + 17:00 | Colector Databento de opciones (T+1, tope $0.25/sesión) → resumen → alertas → commit |
| `g8_port_run.yml` | vie 21:05 + sáb 09:05 | COT (CFTC) → generador POS → twin-test POS → Telegram diff → commit. Bloques FFVA comentados (pausa) |
| `metals_update.yml` | semanal | `metals_fairvalue_g8.py` (MDP) |
| `acm_validate.yml`, `ry_validate.yml` | manual | Validaciones ACM/real yields |
| `validate.yml` | push / PR | CI de humo: compila `scripts/`, valida YAML, registry y JS inline, corre `dashboard_alerts.py --dry-run` (sin secrets, sin datos) |
| `fx_futures_backfill.yml` | **manual, no lanzar** | Backfill Databento de futuros (proyecto pausado; consume presupuesto) |

Secrets: `FRED_API_KEY`, `DATABENTO_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

## Alertas Telegram

`scripts/dashboard_alerts.py` corre al final de `daily_update` y `cme_options`. Lee solo el repo
(coste $0), compara contra `data/alerts/state.json` y envía **solo cambios de estado** con
histéresis (umbrales de nivel = los del dashboard; umbrales de movimiento = percentiles
rolling 252 d — Regla 8, nada inventado). Cubre §01/§02/§03/§04/§05/§06-07/§08/§09 para todas
las divisas con dato. En cada sesión nueva de opciones manda la tarjeta completa de walls
(cadena completa, convención del operador para USD/JPY, USD/CAD, USD/CHF).

## Eslabón Mac (NZD y CHF) — arquitectura

La RBNZ (tabla B2) y el portal de datos del SNB rechazan las IP de datacenter y las huellas TLS
que no son de navegador: **GitHub Actions nunca llama a esos endpoints**. Un job launchd en el
Mac del operador (`Trading_Sander/g8-nzd`, 08:00 hora local, `nzd_local_run.sh`) hace:

1. `fetch_nzd_b2.py` v1.4 — B2 daily close (`curl_cffi` impersonando Safari) → `NZD_BILL_30D/60D/90D`,
   `NZD_BOND_1Y/2Y/5Y/10Y`, `NZD_CASH_ON`, `NZD_IIB_<año>` (bonos indexados, descubiertos por
   cabecera); empalma el fichero histórico 1985-2017 (cacheado) — bonos disponibles solo desde 2009.
2. `fetch_chf_snb.py` v1.0 — cubo `rendeiduebd` (curva cupón cero NSS 1J–10J/20J/30J, diaria
   desde 1988, **publicada mensualmente**) → `CHF_SPOT_<n>Y`; SARON (`zirepo` + fixing RSS diario)
   → `CHF_SARON`; 10Y spot diario (curva + RSS R10) → `CHF_NOM_10Y` (columna `Source`).
3. `push_nzd_to_github.py` v1.2 — sube `data/NZD_*.csv` y `data/CHF_*.csv` por la API REST
   (token classic `public_repo` en `~/.g8/github_token`), solo si el contenido cambió.

Actions consume esos CSV: `acm_g8.py NZD/CHF`, `real_yields_g8.py NZD`, floors §02, brief.
Cada uno de esos ficheros está en `sources/registry.csv` con presupuesto de frescura
(5 días hábiles; curva SNB 25): **si el Mac no corre, §05 pasa a STALE/DEAD y el Telegram avisa**.
`repo_delete_files.py` (Mac) borra por API ficheros retirados del repo.

## Excepciones documentadas (calidad declarada en cada fichero)

- **NZD ACM K=3 con 136 meses** (`QUALITY=ACM_K3_SHORT_SAMPLE_<n>m`): no existe curva NZ abierta
  antes de 2009. Monte Carlo con la dinámica NZ estimada (5 tenores + 8 pb de ruido, refit NS):
  correlación de forma del TP mediana 0,99, p10 0,96, peor 0,78. Decisión 2026-09-13: forma
  (z, deltas) utilizable, **nivel de baja confianza** (badge "lvl" en §01). `MIN_OBS_OVERRIDE` en
  `acm_g8.py`. Fallback: `nzd_tp_synth.py` (proxy AUD, `QUALITY=SYNTH_AUD_ANCHOR`), solo si no hay ajuste real fresco.
- **CHF NOWCAST** (`QUALITY=ACM_K3_NOWCAST_PARALLEL`): entre la última curva publicada por el SNB
  (fin de mes) y hoy, la última curva ajustada se desplaza en paralelo por el 10Y diario del RSS.
  Solo factor nivel; las filas se sustituyen por curva real el día 1. Badge "lvl" mientras dure.
- **BE sin linker**: CHF no tiene bonos indexados → BE = pronóstico condicional de CPI del SNB
  (`manual_inputs.json` `CHF_BE_MANUAL`, se refresca en cada MPA trimestral; caduca a 95 días).
  NZD usa linkers reales (IIB interpolados a 10 años, `RY_G8_NZD.csv`, `PROXY_THIN_MARKET`);
  `NZD_BE_MANUAL` (encuesta RBNZ 2 años) queda solo como fallback.
- **Convención de usuario en walls**: USD/JPY, USD/CAD, USD/CHF se muestran invertidos (1/k) con
  call↔put intercambiados respecto al contrato CME; §00 y §08 comparten esa conversión (INV).

## Inputs manuales (principio 6 — una sola fuente)

`data/manual/manual_inputs.json` es la fuente primaria de las constantes manuales (BE de NZD y CHF,
nominal NZD como fallback). `index.html` y `dashboard_alerts.py` leen las **mismas claves**
(`NZD_BE_MANUAL`, `CHF_BE_MANUAL`, `NZD_NOM_RBNZ`); el valor tecleado en el navegador (localStorage)
solo manda si su fecha es más reciente. Expiry por feed en `sources/registry.csv`.

## Deuda registrada

- Twin-test POS: exige ≥3 fechas-reporte antes de un FAILED válido (el brief lo etiqueta como *muestra insuficiente*); depende del export Pine semanal.
- Curva SNB con rezago de hasta un mes (publicación mensual): el TP CHF diario es NOWCAST hasta el día 1.
- NZD ACM: excepción de muestra corta hasta que la B2 acumule 240 meses (2029).
- 2027: sincronizar umbrales XCCY copiados en `f_mx_thr` del FFVA (única deuda estructural de la suite).

## Cómo trabajar en este repo

GitHub web UI únicamente (lápiz / *Upload files*) · Netlify publica `docs/` sin build ·
calibraciones y pesos congelados no se tocan sin gate nuevo pre-registrado (Regla 32) ·
todo fallo debe ser ruidoso (Ley 2).


## §08b Cross Walls v2.0 (acta CW-2 · RESEARCH, 17-sep-2026)
`scripts/cross_walls.py` — la lectura manual de los muros del operador escrita como regla: por divisa (nativo CME XXX/USD) los tres strikes con más OI de la cadena de análisis (≥ 100 y ≥ 5 % de la cadena; OI plano 10 sesiones = MUERTO), distancia en % del futuro TRIMESTRAL (paridad put-call como guardia), voto solo con ≥ ⅔ del OI en un lado, D = distancia media del lado mayoritario; sesgo del dólar = cuota media del lado USD entre las que votan; cruce A/B = signo(D_A − D_B), nunca niveles. Universo congelado EUR/GBP/JPY/AUD. Skew RR25, D en σ y `agree` son diagnósticos. Escribe `data/CROSS_WALLS.json` (dashboard §08b) y `data/cross_walls/canonical.csv`. `scripts/wall_behaviour.py` registra PIN/THROUGH/AWAY cerca de cada muro, ΔOI y residuo de sonrisa (`data/cross_walls/walls.csv`) para un futuro CW-3: diagnóstico, sin voto. `scripts/gate_cross_walls.py` = gate CW-2 pre-registrado (`docs/actas/ACTA_CW2.md`): H0 por pata (signo de D vs retorno XXX/USD t+1→t+6) y H1 por cruces; evaluación única al llegar a 252 fechas evaluables (`verdict_cw2.json`), nunca antes. v1.x (score por ECDF) queda documentada en `ACTA_CW1.md` como retirada tras auditoría.

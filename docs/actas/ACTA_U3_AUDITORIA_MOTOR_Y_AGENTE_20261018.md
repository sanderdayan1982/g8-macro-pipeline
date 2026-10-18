# ACTA U3 — Auditoría Integral de Dashboard, Coherencia de Motor en Metales, Refuerzo del Agente y Economía de Créditos Netlify (18-oct-2026)

## 1. Autorización del propietario
Tras completar las correcciones fuera de motor (frontend, rendimiento de carga, escapado de atributos, respaldo oficial BIS bulk ZIP en `fetch_bis_ocr.py`, y refuerzo del agente de mantenimiento con `agent_doctor.py`), el propietario autorizó explícitamente intervenir en los puntos de motor pendientes y optimizar la economía del agente y del dashboard para ahorrar créditos de Netlify:
1. Unificar en `scripts/metals_fairvalue_g8.py` (`build_metal`) los umbrales de la puerta de calidad (`QUALITY=REVIEW`) con los del panel de validación institucional de `docs/js/metals.js` (`var_ratio > 1.0`, `halflife > 156`, `ic_8w < 0.10`, `hit_8w < 0.52`).
2. Actualizar `scripts/dashboard_alerts.py` (festivos G8 2025 y coherencia de `[REVIEW]` en el bullet `§04/§05 Metales MDP` de `§00`) y sincronizar su nueva huella SHA-256 en la suite de tests.
3. Configurar la cadencia del agente y vigilancia un día sí y un día no dentro de la semana laboral (**Lunes, Miércoles y Viernes**, `1,3,5`), apagando los fines de semana (sábado y domingo) y retomando el lunes, y aplicar medidas de ahorro de créditos en Netlify y en la capa de red del dashboard.

## 2. Intocabilidad de las fórmulas macroeconómicas
Ninguna ecuación ni calibración cuantitativa ha sido alterada:
- **Metales (`scripts/metals_fairvalue_g8.py`)**: las funciones `asof_weekly`, `load_slope`, `forward_returns`, `evaluate_delta`, `calibrate_delta`, `cot_activation`, `build_panel_xau` y `build_panel_xag` conservan exactamente su huella AST SHA-256 original. Solo `build_metal` incorpora los cuatro umbrales institucionales (`vr > 1.0`, `hl > 156`, `ic8 < 0.10`, `hit8 < 0.52`) para que `MFV_G8_state.json`, `MFV_G8_XAU.csv` y `MFV_G8_XAG.csv` emitan `QUALITY=REVIEW` de origen cuando el panel de validación detecta alertas.
- **Motores intactos byte a byte**:
  - `scripts/s01b.py`: `0c43d6dd54677974bf4ffde880de049396f4b0d9cdd1c6e39964d796ef132ee5`
  - `scripts/usd_factor.py`: `d96bbaa366637a5d1238eac2af1e03096561ff63d92fc3bc246919bad804d3d3`
  - `scripts/book_risk.py`: `78514c2daa319d08454a99dae8225f7c2208da1fcd34cab13eb2547d72ef9d50`
  - `scripts/cme_options_collector.py`: `61276d17636d921913e383127b47176e946adc5eab59420416f4f3a244a477f9`
  - `scripts/build_options_summary.py`: `f0fdf61d1047d3d899b5037f3047dc194329b4a4d5de42b59bed1346e304e019`
- **Funciones JS protegidas (`PROTECTED_JS` en `scripts/tools/agent_gate.py`)**: intactas byte a byte.

## 3. Nuevas huellas SHA-256 registradas
- `scripts/dashboard_alerts.py`:
  - Anterior: `e7554e4451330ff9b8e47e1d17d23da19c11a349f311a4665b91b5d6463ca477`
  - Nueva: `1d8d24fb0ebc6284cf66ebadbf4151e3ac567b368de947a27236eec8d784befe`
- `scripts/metals_fairvalue_g8.py::build_metal` (segmento AST):
  - Anterior: `aa8955af15f50eaa8ca62d947b7cb1bd9dd69a6a7dfd726a5de32d26f1eda0cd`
  - Nueva: `79e8ded371c865421e9ac3ad7973440195bd51e7c9a460e833a3488dac5ed8c4`
- Salidas sincronizadas en `data/_ingest/metals_provenance.json`:
  - `MFV_G8_XAU.csv`: `66523ce24f0ee1d529c5ce5bccfd68beeca4b3063d39f3e48af8d642462f3b65`
  - `MFV_G8_XAG.csv`: `b4a64efe98a901ada19059572a9c0a776c5e3db2ab3c3f77bd2501d29434e9b6`
  - `MFV_G8_state.json`: `b82a99b9fc0344b7ff7ce83c1c8fd3f5200d2feca75d105c302da3ff7e77cc96`

## 4. Economía del Agente (Lunes / Miércoles / Viernes) y Ahorro de Créditos en Netlify
1. **Cadencia Lunes, Miércoles y Viernes (sin fines de semana, retomando el Lunes)**:
   - `scripts/tools/watch_cadence.py`: `ACTIVE_WEEKDAYS = (0, 2, 4)` (Lunes, Miércoles y Viernes activos; Martes, Jueves, Sábado y Domingo inactivos; retoma siempre el Lunes).
   - `.github/workflows/maintenance_agent.yml` (`cron: '17 17 * * 1,3,5'`, `--max-turns 30`) e `.github/workflows/ingest_watch.yml` (`cron: '13 17 * * 1,3,5'`): reduce de 7 a 3 los disparos programados por semana (-57% de ejecuciones) y recorta el presupuesto máximo de turnos de Claude Code gracias al pre-diagnóstico determinístico de `agent_doctor.py`.
2. **Ahorro de créditos en Netlify (`netlify.toml`, `docs/index.html`, `docs/js/health.js`, `docs/js/charts.js`)**:
   - `netlify.toml`:
     - `[build].ignore`: añade `':!docs/*.md'` junto a `':!docs/actas'` para que ningún cambio en documentación dispare un deploy de 15 créditos.
     - `Cache-Control`: amplía la caché CDN/navegador en `/css/*` y `/js/*` a `max-age=3600, stale-while-revalidate=86400`, en `/*.html` a `max-age=300, stale-while-revalidate=3600`, y añade caché en `/proxy/*` (`max-age=900, stale-while-revalidate=3600`).
   - `docs/index.html`:
     - `G8NET` amplía `CACHE_MS` a 30 min entre semana y 60 min en fin de semana (conservando el botón manual `↻ datos` para limpiar caché bajo demanda).
     - `manual_inputs.json` pasa por `G8NET.fetchChain` (con caché `localStorage`) en vez de hacer `fetch(..., { cache: 'no-store' })` en cada apertura.
   - `docs/js/charts.js`: omite la descarga de 41 CSVs de secciones `.legacy-hidden` en cada carga del dashboard.
   - `docs/js/health.js`: amplía la ventana de vigencia visual del reporte a 80h (para cubrir el puente de viernes a lunes sin falsas alertas) y pausa el sondeo periódico de 30 min cuando la pestaña está oculta (`document.hidden`) o en fin de semana.

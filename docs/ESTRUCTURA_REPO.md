# G8 PORT — Estructura del repo `g8-macro-pipeline` (Fase 1)

**Principio:** no se renombra nada de lo que ya funciona. Las capas del traspaso (§3) se
*superponen* al árbol actual (`.github/workflows/`, `data/`, `docs/`, `scripts/`).
`docs/` sigue siendo la raíz de publicación de Netlify (`netlify.toml` no cambia).

```
g8-macro-pipeline/
├── .github/workflows/
│   ├── daily_update.yml            (existente — no se toca en Fase 1)
│   ├── cme_options_surface.yml     (existente)
│   ├── metals_update.yml           (existente)
│   └── g8_port_run.yml             (Fase 2: cron 4h 00:05/04:05/…/20:05 UTC → generadores → funnel → brief)
│
├── sources/                        NUEVO — documentación por serie
│   └── registry.csv                una fila por feed: fuente primaria, fallback, lag, calendario, plausibilidad
│
├── scripts/
│   ├── fetch_*.py                  (existentes — 19 scrapers; NO se tocan sin twin-test, D10)
│   ├── generators/                 NUEVO — un módulo por script Pine
│   │   ├── _template_generator.py  plantilla obligatoria (esta entrega)
│   │   ├── pos_g8.py               (Fase 2)
│   │   ├── ffva_g8.py              (Fase 2)
│   │   ├── risk_g8.py, psi_g8.py, iydt_2y.py, iydt_10y.py, curva_g8.py   (Fase 3)
│   │   └── rtf10_g8.py, pol_g8.py, xccy_g8.py                            (Fase 4)
│   ├── funnel/funnel.py            (Fase 5) embudo 7+1, abortos, Hard Stop 9, Módulo 19
│   ├── brief/render_brief.py       (Fase 5) plantilla → §00 + mensaje Telegram (diff-based)
│   ├── dqm.py                      (Fase 1-2) lee sources/registry.csv + data/ → data/state/dqm.json
│   └── twin_test.py                (esta entrega) compara export Pine vs state JSON
│
├── data/
│   ├── *.csv, *.json               (existentes — salidas de los scrapers actuales)
│   ├── raw/YYYY-MM-DD/             NUEVO — descarga cruda por run (gitignored salvo muestra)
│   ├── normalized/                 NUEVO — esquema común (divisa, metrica, fecha_dato, valor, fuente, calidad, version)
│   ├── state/                      NUEVO — UN JSON POR PREGUNTA
│   │   ├── pos_g8.json … xccy_g8.json     (schemas/state.schema.json)
│   │   ├── dqm.json                        (schemas/dqm.schema.json)
│   │   ├── funnel.json                     (Fase 5)
│   │   └── brief.md                        (Fase 5)
│   ├── twin/                       NUEVO — exports Pine (CSV de TradingView) para el twin-test
│   └── manual/manual_inputs.json   NUEVO — NZD/CHF con fecha de caducidad (principio 6)
│
├── schemas/                        NUEVO
│   ├── state.schema.json           esquema maestro (con columnas Gate M + journal desde el día uno, D8)
│   └── dqm.schema.json
│
├── journal/                        NUEVO
│   ├── schema.json
│   └── journal.jsonl               una fila por decisión, con commit del estado que la justificó
│
├── gates/                          NUEVO
│   ├── GATE_M_prereg_v0.1.md       pre-registro sellado (no se corre en Fase 1)
│   └── actas/                      actas de recalibración (Regla 32)
│
├── docs/                           (Netlify root — existente)
│   ├── index.html                  se amplía por secciones §00–§11 (D2); lee data/state/*.json del repo
│   └── PROYECTO_G8_PORT_traspaso_v1.md
├── netlify.toml, requirements.txt, README.md, .gitignore
```

## Cómo lee el front los JSON (principio 1)
El `index.html` actual ya usa `RAW = https://raw.githubusercontent.com/sanderdayan1982/g8-macro-pipeline/main/data/`
para los feeds `repo(...)`. **Eso se mantiene**: el front lee `data/state/*.json` del propio repo.
Lo que se elimina progresivamente son los fetches del navegador a ECB / FRED / eco3min vía proxies
CORS (`allorigins`, `codetabs`, `corsproxy.io`) — hoy hay 6 feeds así en `FEED_REGISTRY`. Cada uno
pasa a un scraper de Actions cuando se porte el generador que lo consume.

## Convenciones
- Nombres de archivo de estado: `<script>_g8.json` en minúsculas; claves internas en inglés (no se traducen).
- Fechas ISO-8601 UTC. Valores numéricos nunca `null` para un feed muerto: se repite el último válido con `quality: "STALE"` y `age_bd` (principio 2).
- `.gitignore`: añadir `data/raw/` (salvo `data/raw/_sample/`).

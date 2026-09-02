# G8 PORT — Entrega Fase 1 (semana 1) · 02-sep-2026

## Qué vi en tus capturas (y qué implica)

1. **Repo público (D5 ok, pero…):** con Databento en Fase 2 el pipeline solo puede commitear **estados derivados** (Z, percentil, régimen), nunca settlements crudos. El `registry.csv` marca cada feed con `license_class` (PUBLIC / DATABENTO) para que el normalizador lo respete. El token de Telegram y la API key de Databento van en *Settings → Secrets → Actions*, nunca en el repo.
2. **Scrapers:** de las 245 ejecuciones visibles todas están en verde salvo **`CME Options Surface #123` (programada, hoy 18:12, ✗)**. La #124 manual de 9 min después pasó, así que probablemente fue transitorio — pero abre el log de la #123 y pégame la línea del error. Es exactamente el caso que el principio 9 debe atrapar por Telegram.
3. **README del repo** sigue describiendo el proyecto como *XCCY G8 — Cross-Currency Basis Command Center*, con el `markdown#` suelto arriba y el checklist de estado sin marcar. No es urgente; lo reescribimos en Fase 2 cuando el traspaso esté en `docs/`.
4. **Un manual caducado ya hoy:** `NZD_NOM_RBNZ` en el `index.html` lleva `value: 4.55, lastDate: '2026-06-11'` — 83 días. Es el principio 6 fallando *antes* de existir el pipeline. Refréscalo esta semana (RBNZ B2, columna 10 year).
5. **DQM real:** el `FEED_REGISTRY` del front tiene 41 feeds (no ~80; el 80 del traspaso contaba series por archivo). Siete de ellos aún se descargan **desde el navegador vía proxies CORS** (ECB, FRED×4, eco3min, SNB) — son los primeros que pasan a Actions cuando se porte el generador que los consume.

## Contenido de esta entrega

| Archivo | Para qué |
|---|---|
| `ESTRUCTURA_REPO.md` | Cómo se superponen las capas del traspaso al árbol actual sin renombrar nada |
| `schemas/state.schema.json` | **Esquema JSON maestro**: `meta` (twin-test, funnel_eligible), `inputs` (provenance por feed), `outputs` (per_ccy / pairs / regime, umbrales congelados copiados), `dqm`, `gate_m`, `journal_link` |
| `schemas/dqm.schema.json` | `data/state/dqm.json` — sustituye a las comprobaciones del navegador |
| `sources/registry.csv` | 118 feeds: 44 existentes (37 repo + 7 navegador) + 74 nuevos del mapa §5. Fuente primaria, fallback, calendario, staleness, plausibilidad, lag de publicación, licencia, consumidores |
| `scripts/generators/_template_generator.py` | Plantilla obligatoria. Tres bloques a rellenar por script; el resto es doctrina (last-good con edad, plausibilidad, percentil medido, hash de estado, diff para Telegram) |
| `scripts/twin_test.py` | Compara export Pine vs histórico Python; >10 % discrepancia = FAILED |
| `journal/schema.json` | Una fila por decisión, con hashes de estado, tape del cockpit y `size_override` como bandera conductual |
| `gates/GATE_M_prereg_v0.1.md` | Pre-registro sellable. **Necesita tu objeción.** |

## Dónde va cada cosa en el repo (vía GitHub web UI → Add file → Upload files)
- `schemas/`, `sources/`, `journal/`, `gates/` → carpetas nuevas en la raíz (arrastra la carpeta entera).
- `scripts/generators/_template_generator.py` y `scripts/twin_test.py` → dentro de `scripts/` existente.
- `ESTRUCTURA_REPO.md` y `README_FASE1.md` → `docs/g8_port/` (así Netlify los sirve).
- Añade a `.gitignore`: `data/raw/`.

Nada de esto cambia ningún workflow ni ningún scraper: **cero riesgo para lo que hoy corre.**

## Tu tarea de esta semana (una sola, corta)
Leer `gates/GATE_M_prereg_v0.1.md` y contestar dos cosas: (1) la fecha de última calibración real de los 8 scripts marcados con `?` en la tabla 3.1, y (2) cualquier objeción a los umbrales de §4. Si no objetas nada, escribe "sellar v0.1" y lo sello con el hash.

Secundario, si te sobra tiempo: log de `CME Options Surface #123` y refrescar NZD 10Y manual.

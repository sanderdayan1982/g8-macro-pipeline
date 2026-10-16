# ACTA A4 — FFVA fuera del dashboard (16-oct-2026)

**Origen:** decisión de Sander: "el FFVA del dashboard no lo quiero, prefiero el de TradingView; si se puede quitar se quita". Plan de armonía, enmienda v2, punto A4, firmado por Claude y Argon 4.
**Base:** A1 (va encima de B1 + A1).
**Dueño único de la pregunta FFVA:** 9-FFVA en TradingView.

## Borrado (8 archivos; estaba en pausa desde el 12-sep, sin ejecutarse)
- `scripts/ffva_g8.py`
- `scripts/generators/ffva_g8.py`
- `scripts/fx_futures_collector.py` (Databento)
- `.github/workflows/fx_futures_backfill.yml` (Databento, manual)
- `data/state/ffva_g8.json`
- `data/twin/ffva_g8_pine.csv`
- `data/twin/ffva_g8_py_history.csv`
- `data/twin/ffva_g8_twin.json`

## Editado
| Archivo | Cambio |
|---|---|
| `.github/workflows/g8_port_run.yml` | Se quitan los 4 bloques comentados "FFVA (paused)". La cabecera explica la retirada. |
| `scripts/twin_test.py` | Se quita el adaptador `_ffva_g8`; ADAPTERS solo contiene POS. |
| `docs/index.html` | Se quita la píldora FFVA del brief §00 y de las fichas de la cabecera. |
| `tests/test_exclusions.py` | Se retiran las huellas de los dos archivos borrados. |
| `sources/ingest_inventory.csv` | Se quita la fila del colector de futuros. |
| `README.md`, `docs/ESTRUCTURA_REPO.md` | Se actualizan. |

## Se conserva (deliberadamente)
- **`data/futures/canonical/*.csv`**. `usd_factor.py` lo lee como twin descriptivo ("número, no veredicto"), con `last_session` visible (2026-09-03). No se refresca.
- **`scripts/dashboard_alerts.py`, intacto.** Es un motor congelado por huella SHA-256, que vigilan `test_exclusions` y `test_f3_freshness`. Su línea `gates.ffva` sigue escribiendo una etiqueta "FFVA pausado" en `brief.json`, pero ya ningún panel la muestra: es un metadato inerte. Se quitará en el próximo cambio autorizado de ese motor.
- `schemas/state.schema.json` (el enum incluye "FFVA") y `registry.csv` (filas CME_* planificadas): no afectan a nada que se ejecute.

## Pruebas
- `python -m unittest discover -s tests`: **773 OK**.
- YAML de `g8_port_run` válido.
- Render en Chromium: brief, s01b, salud, factor, COT y opciones se pintan; `attr-matrix` sale vacío igual que en la base sin cambios (entorno de pruebas).

# ACTA — volumen compensado CME en sombra (orden t186u)

**Decisión:** Sander, 15-nov-2026. Un solo PR contra `main`. No se fusiona y no se dispara el workflow. No se llama a Databento.

## Hallazgo
- `scripts/cme_options_collector.py` (Databento GLBX.MDP3, schema `statistics`) se quedaba con `stat_type` 3 (settlement, columna `price`) y 9 (open interest, columna `quantity`). Tiraba el `stat_type` 6, que es el volumen compensado: contratos compensados del instrumento en la sesión anterior (columna `quantity`).
- El CSV canónico `data/options/canonical/YYYY-MM-DD/{ROOT}.csv` es el que abren `build_options_summary.py`, la cadena de `dashboard_alerts.py`, `cme_fxvol_shadow.py` y `health_derivatives.py`. Una columna `volume` en ese fichero cambia sus bytes aunque el `stat_type` 6 no venga (cabecera y campo vacío). El requisito de este lote es que, en un fixture sin volumen, la salida de settle y OI sea byte a byte la de antes.
- Esos dos ficheros están congelados por SHA-256 en `tests/` (`cme_options_collector.py` y `.github/workflows/cme_options.yml`).

## Decisión
- El colector diario guarda el `stat_type` 6 por contrato, al lado del settle y del OI, en sombra: `data/options/volume_shadow/YYYY-MM-DD/{ROOT}.csv` (en el Mac, al lado del directorio canónico). Mismas claves de contrato (`session,root,type,symbol,expiry,right,strike`) y una columna `volume`.
- No es una columna del CSV canónico. Así el canónico no cambia de esquema, ningún consumidor actual lo lee, y no entra en voto, score, estado, `blocked_entry`, FXVOL ni dashboard.
- Si no hay `stat_type` 6 para ese contrato en la sesión (`ts_ref`), `volume` queda vacío (UNAVAILABLE). No se escribe 0 y no se arrastra de otra sesión. Un 0 que sí publique Databento se guarda como `0`.
- t186u: cada cuerpo de `timeseries.get_range` se comprime con gzip en `G8_OPT_RAW_DIR`. Por defecto, `~/Documents/G8_options_pipeline/raw` (fuera del repo). En Actions, `${{ runner.temp }}/cme-options-raw`, subido con `actions/upload-artifact@v4`, retención 90 días, nombre `cme-options-raw-<run_id>`. `.gitignore` cubre `data/options/raw/`, `*.dbn` y `*.dbn.zst` por si el directorio cae dentro del árbol. El `git add` del workflow no incluye ese path.
- SALUD-1: las huellas de `scripts/cme_options_collector.py` y `.github/workflows/cme_options.yml` se reescriben en el mismo commit. Comentario: orden t186u (Sander, 15-nov-2026).

## Sin cambios
Fórmulas, umbrales, s01b, factor USD, `build_options_summary.py`, `dashboard_alerts.py`, `cme_fxvol_shadow.py`, `docs/index.html`. El CSV canónico no gana columna. `scripts/cme_options_backfill.py` y `.github/workflows/cme_options_backfill.yml` no cambian: el backfill sigue tirando el `stat_type` 6 y no archiva el cuerpo. Conviene el mismo paso de artifact cuando se toque el backfill; este PR no lo mete. No hay secretos en código ni en el log del archivo (solo el nombre y el tamaño). No se llama a Databento. No se dispara ningún workflow. No se fusiona.

## Pruebas
`tests/test_cme_options_volume_shadow.py` (nuevo, fixture sintético): `stat_type` 6 → `volume`; sin `stat_type` 6 el campo queda vacío y no se arrastra; el CSV de settle/OI es byte a byte el de antes, con y sin volumen en el fixture; el gzip del cuerpo no lleva secreto; el workflow sube el artifact fuera de git; FXVOL, dashboard, resumen, backfill e `index.html` no mencionan el volumen.

## Siguiente
Sander fusiona. El colector diario, al correr, escribirá la sombra y dejará el gzip solo como artifact. El backfill (`cme_options_backfill.yml` / `cme_options_backfill.py`) debería archivar igual el cuerpo comprimido en un lote aparte; hoy no lo hace.

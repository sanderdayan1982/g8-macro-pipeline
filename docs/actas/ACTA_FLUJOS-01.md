# ACTA FLUJOS-01 — fixture congelada anterior al backfill CME

**Orden:** Sander, 14-nov-2026 19:08 WAT (t161u). Un solo PR contra `main`. No se fusiona.

## Hallazgo
El backfill de opciones CME está dejando en `main` commits `data: cme options backfill <YYYY-MM> [skip netlify]`. El primero de esta pasada es `aaf244bd` (`data: cme options backfill 2024-06`). En `59be57c7` (el commit anterior) `data/options/canonical` tiene 116 sesiones, de 2026-06-01 a 2026-11-12. Con esa historia `hist_n` de EUR queda por debajo de 252 y `vol_regime` sale `UNAVAILABLE`. Al alargar la historia hasta 2024-06, la ventana de 252 sesiones existe: EUR el 2026-10-30 pasa a `hist_n` 252 y el régimen a `NEUTRAL`.

Eso rompe dos pruebas que leían el canonical vivo:

- `tests/test_fxvol_shadow.py::FxvolShadow::test_additive_fields_do_not_recompute_existing_ones` exige `hist_n` < 252 y, por tanto, percentil `NA`.
- `tests/test_flujos01_pr2.py::Rv21Vectors::test_live_eur_matches_the_oracle_and_the_roll_session_is_na` exige `vol_regime` `UNAVAILABLE` en EUR.

`test_five_sessions_within_tolerance` también falla contra el shadow publicado. No se toca: vuelve a verde cuando se regenere ese shadow, después del backfill.

Comprobado en el árbol de `59be57c7` el 14-nov-2026 (`git ls-tree` y `FX.build` sobre una copia de esas sesiones). No hay dato nuevo de CME: los CSV son los que ya estaban en ese commit.

## Decisión
`FX.build(repo, session)` ya recibe el directorio del repo y lee `repo/data/options/canonical`. No hace falta tocar `scripts/`. Las dos pruebas pasan a leer `tests/fixtures/fxvol/pre_backfill`, una copia mínima de `59be57c7`:

- Sesiones 2026-07-17..2026-08-17 (22), 2026-09-04..2026-09-15 y 2026-10-01..2026-11-06. Son las que usan el RV21 de las cinco sesiones del vector y el día de roll de EUR.
- Futuros (`6E` `6J` `6B` `6A` `6C` `6S`) copiados byte a byte desde `59be57c7`.
- Cadenas de opciones enteras, también byte a byte, solo en 2026-08-17, 2026-09-15, 2026-10-30, 2026-11-02 y 2026-11-06. El resto de sesiones lleva la cabecera del CSV y no la cadena: esas pruebas no leen la cadena de esos días, y RV21 solo usa el settlement del futuro.

Las aserciones no cambian. Siguen exigiendo `hist_n` < 252, percentil `NA`, `vol_regime` `UNAVAILABLE`, y los mismos `iv30_atm` / `rv21` / `vrp` / `vol_regime` del vector. En cada prueba modificada queda el comentario `# orden expresa de Sander 14-nov-2026 (t161u): fixture congelada pre-backfill`.

`tests/test_fxvol_pct252_long.py` (nuevo) arma 252 sesiones sintéticas, sin leer el canonical vivo. Con la ventana llena el percentil existe (`100.0` en esta serie plana) y `vol_regime` es `EXPANSION`, no `UNAVAILABLE`.

## Sin cambios
Fórmulas, umbrales, ventanas de z, `scripts/cme_fxvol_shadow.py` y el resto de scripts. Nada bajo `data/`. Ningún otro test ya existente. `test_five_sessions_within_tolerance` sigue igual.

Ninguna huella SHA-256 de `tests/` cubre los ficheros de este lote (`test_fxvol_shadow.py`, `test_flujos01_pr2.py`, el test nuevo, la fixture ni esta acta). SALUD-1 no reescribe ninguna huella.

## Pruebas
`python3 -m pytest -q tests` el 14-nov-2026, sobre este árbol (base `65135589`): 988 passed, 6 skipped, 1 failed. El fallo es `tests/test_fxvol_shadow.py::FxvolShadow::test_five_sessions_within_tolerance`. Las dos pruebas retargeteadas y `tests/test_fxvol_pct252_long.py` pasan. El test de idempotencia reescribe `data/CME_FXVOL_shadow.json` y `data/options/fxvol_history.csv` al correr; no forman parte de este lote y se dejan como estaban.

## Siguiente
Cuando el backfill termine, regenerar el shadow publicado para que `test_five_sessions_within_tolerance` vuelva a verde. Esta fixture no se actualiza con esas sesiones nuevas.

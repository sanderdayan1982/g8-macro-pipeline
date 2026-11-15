# ACTA — fecha IMM en fut_roll_window (orden t191u)

**Decisión:** Sander, 15-nov-2026. Un solo PR contra `main`. No se fusiona. No se dispara ningún workflow. No se llama a Databento. No se toca g8-radar-news.

## Hallazgo
`scripts/cme_fxvol_shadow.py`, `roll_window`, calcula el tercer miércoles del mes de contrato (`w`) y publica solo `start`, `end` y `active`. El JSON de `data/CME_FXVOL_shadow.json` (sesión `2026-11-12`, `generated_utc` `2026-11-14T01:38:17Z`) no tiene la clave.

RadarNews, `main` leído el 15-nov-2026 (`src/executionWindows.js`, copia idéntica en `public/executionWindows.js`, PR #22 fusionado en `3e307feb`). `immFor` lee `fut_roll_window.imm`. Solo acepta una fecha ISO `YYYY-MM-DD` (`isIsoDate`). Si la clave falta, el estado es `UNAVAILABLE` / `ABSENT`. No hace falta cambiar el radar para esta clave.

## Decisión
Se añade `imm` al objeto `fut_roll_window`: el mismo `w`, en ISO `YYYY-MM-DD`. `start`, `end` y `active` no cambian. La fórmula de `third_wed` no se toca.

Sesión publicada `2026-11-12` (1-dic-2026 es martes; el tercer miércoles es el 16):

Antes:

```json
"fut_roll_window": { "start": "2026-12-07", "end": "2026-12-19", "active": false }
```

Después, lo que escribe el código (no se commitea el fichero):

```json
"fut_roll_window": { "start": "2026-12-07", "end": "2026-12-19", "active": false, "imm": "2026-12-16" }
```

Ningún fichero de datos entra en el commit. Correr el escritor sobre el canónico de esta máquina también reescribe percentiles e historial (el desvío ya admitido de `five_sessions`). Esa salida se descartó.

## Sin cambios
Fórmulas, umbrales, ventanas de z, ACM, s01b, factor USD, `third_wed`, el CSV de historia y el JSON publicado. SALUD-1: ningún fichero con huella congelada cambia; no se reescribe ninguna huella. Tests ya existentes, sin editar. g8-radar-news, sin tocar.

## NZD opex (lectura, el siguiente PR es del radar)
El `null` no lo pone `netlify/functions/radar_live.mjs`. Esa función llama a `collectRadarAndIrisData` (`src/irisCollector.js`), que pide el JSON con `fetchFxvolShadow` y lo pasa a `buildExecutionWindows`. El `null` se asigna en `opexFor` (`src/executionWindows.js` y `public/executionWindows.js`, líneas 424-426): si `exp1` no es ISO, escribe `null`; si `dte1` no es un número finito, escribe `null`. NZD está en `FXVOL_CCY` (línea 295) y el productor lo publica sin `exp1` ni `dte1` (`status` `NA`, `reason` `NO_CME_OPTIONS`). El siguiente PR del radar puede poner el texto `UNAVAILABLE` en ese hueco. Este PR no lo hace.

## Pruebas
`tests/test_fxvol_imm_t191u.py` (nuevo, fixture sintético): tercer miércoles con el mes empezando en cada día de la semana, incluido miércoles (día 15) y jueves (día 21, febrero bisiesto), y `imm` presente en ISO dentro de `fut_roll_window` sin mover `start`, `end` ni `active`.

`python3 -m unittest discover -s tests -p 'test_*.py'` (15-nov-2026, Python 3.12.3, `requirements.txt` + `pyyaml`): 1071 pruebas, 6 skipped, 3 fallos. En `main`, el acta t192u dejó 1065 pruebas, 6 skipped y los mismos 3 fallos. Las 6 de más son las de este lote.

Los tres fallos ya estaban admitidos:

- `test_flujos01_pr2.Rv21Vectors.test_live_eur_matches_the_oracle_and_the_roll_session_is_na`: `NEUTRAL` != `UNAVAILABLE`. No lee `fut_roll_window`.
- `test_fxvol_shadow.FxvolShadow.test_additive_fields_do_not_recompute_existing_ones`: `hist_n` 252 no es menor que 252 (`2026-08-17` EUR). No lee `fut_roll_window`.
- `test_fxvol_shadow.FxvolShadow.test_five_sessions_within_tolerance`: las cinco sesiones ya salían del vector. Antes de este cambio los diffs eran 44, 38, 44, 44 y 44. Después, 45, 39, 45, 45 y 45. La línea nueva en cada sesión es `fut_roll_window claves extra=['imm'] faltan=[]`. El resto de los diffs no cambia. No se editó el test.

`test_idempotent_and_under_60s` pasó (`runtime_s=43.256`). Equivalencia s01b, YAML, `registry.csv` y el JS del dashboard, en verde. No se llamó a Databento. No se disparó ningún workflow.

## Siguiente
Sander fusiona. La pasada programada de `cme_fxvol_shadow.py` escribe `imm` en `data/CME_FXVOL_shadow.json`. Hasta ese fichero en `main`, RadarNews sigue en `UNAVAILABLE` / `ABSENT`. El `UNAVAILABLE` explícito del opex de NZD es un PR aparte en g8-radar-news.

# ACTA FAMILIAS-01 B — dealers del TFF en sombra

**Orden:** FAMILIAS-01, módulo B. Diseño aprobado por Sander, REVISION_TRIPLE 14-nov §3.B.
**Base:** `main`. Un PR. No se fusiona desde aquí.

## Hallazgo
- `scripts/pos_g8_cot_collector.py` pedía, en una sola fila TFF (`gpe5-46if`), solo el largo y el corto de fondos apalancados y de gestoras. El mismo informe publica dealers, el número de operadores y el cambio del interés abierto, y no se usaban.
- El reintento ante un HTTP 400 cambiaba el sufijo `_all` de **todas** las columnas. En este dataset el sufijo no es simétrico: dealers lo llevan (`dealer_positions_long_all`); `lev_money_*` y `asset_mgr_*` no.
- Comprobado el 15-nov-2026 en la fuente primaria, una sola consulta:
  `https://publicreporting.cftc.gov/resource/gpe5-46if.json`
  `$select` con las columnas nuevas, incluido `traders_dealer_spread_all`, `$where` `cftc_contract_market_code='099741'`, `$limit=1`.
  HTTP 200. Fecha del informe `2026-11-03`. `traders_dealer_spread_all` viene en la fila (EUR, valor `"7"`). Se incluye.
- Un 400 de Socrata nombra la columna: `No such column: <nombre>`. Comprobado el mismo día con un nombre inexistente, solo para leer el cuerpo del error.

## Decisión
- La misma petición TFF alarga el `$select`. No hay una segunda llamada en el camino normal.
- Columnas pedidas: `dealer_positions_long_all`, `dealer_positions_short_all`, `dealer_positions_spread_all`, `traders_dealer_long_all`, `traders_dealer_short_all`, `traders_dealer_spread_all`, `traders_tot_all`, `change_in_open_interest_all`.
- Si el API rechaza una columna, el reintento renombra o tira **solo** esa columna (el sufijo `_all` se cambia una vez; un segundo rechazo la quita). Las demás se quedan como se pidieron.
- En cada divisa del TFF se añade una clave nueva, `dealer_shadow`. No entra en z, estado, umbral, proxy USD, voto ni score. Metales y USD no la llevan.
- Dentro: `long`, `short`, `spread`, `net` (largo − corto solo si los dos lados están; si falta uno, `null` y `net_state` = `UNAVAILABLE`), `traders` (`long` / `short` / `spread`, sin sumar: la CFTC cuenta al mismo dealer en cada lado y sumarlos sería una fórmula nueva), `traders_total`, `oi_change`, `oi_change_state` (`UP` / `DOWN` / `FLAT`, o `UNAVAILABLE` si el cambio no está). Un cero presente se conserva. No se rellena con `or 0`.
- El esquema sigue siendo `pos_g8_cot/1.2.0`.

## Sin cambios
- Fórmulas, umbrales, ventanas, estados LF/AM/MM y el `or 0` que ya tenían esos netos. Ningún test ya existente. Ningún fichero con SHA-256 congelado en `tests/` (las huellas de `test_exclusions` siguen iguales). Pine, workflows, `data/` y el generador `pos_g8.py`.

## Pruebas
- `tests/test_familias01_b_dealer_shadow.py` (nuevo). Fixture sintético. La foto de las claves viejas se tomó **antes** de tocar el colector (`tests/fixtures/familias01_b/existing_keys.json`).
- Diff de claves existentes, misma entrada, antes y después: **0**. El JSON canónico de la divisa, del metal y del USD es byte a byte el de esa foto.
- `python3 -m unittest discover -s tests -p 'test_*.py'` el 15-nov-2026 (Python 3.12, `requirements.txt`): **1077** tests, **3** fallos, **6** skipped. Fallos, los de `main` y sin cambio de código en esos módulos:
  - `test_flujos01_pr2.Rv21Vectors.test_live_eur_matches_the_oracle_and_the_roll_session_is_na`
  - `test_fxvol_shadow.FxvolShadow.test_additive_fields_do_not_recompute_existing_ones`
  - `test_fxvol_shadow.FxvolShadow.test_five_sessions_within_tolerance`
- `idempotent_and_under_60s` no saltó en esta pasada (es de tiempo). Los ficheros que la suite reescribió en `data/CME_FXVOL_shadow.json` y `data/options/fxvol_history.csv` se restauraron y no entran en el commit.

## Siguiente
- Sander fusiona. El bloque no vota. Si el conteo de dealers debe ser un solo número (lado dominante, u otra regla), hace falta otra orden: aquí no se inventa esa suma.

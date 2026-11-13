# argos_rates.json — notas de lectura (DATOS-ARGOS-01)

Comprobado 2026-11-13 (WAT). El generador no inventa cifras: lee el render de §01 / §01-b.

## C3

El render de `docs/index.html` (§01, §01-b y §02, vía `scripts/argos_render.js`) no calcula un cuadrante de tabla 2×2 ni pinta la palabra «cuadrante». `c3` queda `UNAVAILABLE`. No se rellena con un número inventado.

## dominant_driver

Regla del panel §01-b (`scripts/s01b.py`, `H = 22`): `BE_LED` si `|ΔBE22| > |Δreal22|`; si no, `REAL_LED` (el empate también). `ΔBE22` es `d_be` de `data/S01B.json` (as-of, atraso máximo 3 días hábiles). `Δreal22` usa los mismos `t` y `t0` sobre la serie real que arma §01, con el mismo `asof`. Si falta uno de los dos, `UNAVAILABLE`.

PR #16 comparaba un lookback de 21 observaciones (el `1M` diario de §01, `deltaBps` con `lb = 21` en `docs/index.html`). Esa ventana no es la de §01-b. El lookback de 21 se conserva solo en `driver_field` para los vectores sintéticos de `tests/test_flujos01_pr2.py`.

CHF: §01-b deja `d_be` en blanco (`BE_FILES` no incluye CHF; no hay linker). El driver es `UNAVAILABLE`, no un `BE_LED` calculado sobre el IPC mensual.

## CHF — real ex post y el salto de 50 pb

Suiza no emite bonos ligados a la inflación. En §01 (`docs/index.html`, acta P-10):

- nominal = SNB 10Y spot (`data/CHF_NOM_10Y.csv`, RSS), en **%**
- «BE» = IPC interanual BFS (`data/CHF_CPI_YOY.csv`, columna CLOSE), en **%**, no un breakeven de mercado
- real ex post = nominal − IPC a/a, en **%**. El signo negativo es la resta: el IPC (1,50 % en octubre 2026, fecha de fila `20261031`) supera al nominal (~0,52 % el 2026-11-12)

Fuente del IPC: BFS, «LIK/IPC — Tableau d'indexation», pedido `cc-e-05.02.08`, vía `scripts/fetch_chf_cpi.py` (columna «% m-12»). Comprobado en el fichero del repo el 2026-11-13: `20260930 = 1.00`, `20261031 = 1.50`.

Los cambios van en **puntos básicos** (`× 100`), igual que `deltaBps` del panel. El paso octubre−septiembre es `0,50 pp = 50 pb`. Como la serie es mensual, §01 pinta `1M 50` y `3M 110` (no `1W/1M`). `level_change_1d.breakeven_10y = 50` es ese paso de **una observación mensual**, no un movimiento de un día ni un 50 %. `level_change_22d.breakeven_10y` es el cambio a 22 observaciones mensuales, en pb. El dato no se sustituye ni se pone a cero.

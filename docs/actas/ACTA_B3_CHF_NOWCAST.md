# ACTA B3 — Nowcast diario del CHF 2Y y 5Y, en SHADOW (17-oct-2026)

**Origen:** Argon 4 aprobó las opciones de C1 y el conteo por divisa, pidió la corrección de pares (`or` más `both_legs_in_window`) y dio luz verde a B3, último lote del plan.
**Base local:** encima de la corrección de C1. No se toca `fetch_nzd_b2.py` ni ningún fichero de `tests/test_exclusions.py`.

## 1. Qué fuente diaria hay

Consultado el 17-oct-2026 el RSS oficial `https://www.snb.ch/public/rss/en/interestRates` (publicado el 16-oct-2026 11:02Z). Los códigos de serie son `R10`, `SARH`, `SNBLZ`, `LSFF`, `SNBGIRO1`, `SNBGIRO2`, `SNBFBF` y `Discount`. El único rendimiento de bono es `R10`, el spot a 10 años (0.572 el 16-oct). El SARON es `SARH` (−0.05 el 15-oct). No hay código de 2 años ni de 5 años.

`scripts/fetch_chf_snb.py` ya lo dice: el cubo `rendeiduebd` es la curva spot diaria de la Confederación (1Y…10Y, 20Y, 30Y), con una sola metodología, y se publica **una vez al mes** (las filas diarias del mes aparecen el primer día hábil del mes siguiente). El RSS solo rellena el 10Y y el SARON. SIX publica el SARON y sus tipos a un día; la página de Swiss Reference Rates no es una curva de bonos de la Confederación y pide licencia. El calendario `CH` del repo es un proxy de festivos SIX, no una fuente de rendimientos.

No se añade un descargador. No hay un 2Y/5Y diario oficial sin bloqueo que el colector del Mac no esté usando ya.

## 2. Nowcast

Doctrina firmada: diario aproximado antes que mensual congelado. `scripts/chf_nowcast_shadow.py` no reescribe `CHF_SPOT_2Y.csv` ni `CHF_SPOT_5Y.csv`. `scripts/s01b.py` sigue leyendo el cubo mensual.

Ancla M = último cierre del cubo, **2026-09-30** (2Y 0.287, 5Y 0.453). Ese día el SARON es −0.07 y el 10Y diario es 0.586.

```
w = plazo / 10          (2Y → 0.2,  5Y → 0.5)
mezcla(t) = (1 − w) · SARON(t) + w · NOM10(t)
nowcast(t) = oficial(M) + mezcla(t) − mezcla(M)
```

SARON(t) y NOM10(t) son el último impreso en o antes de t. La fila M se etiqueta `OFFICIAL`. Los días hábiles del calendario `CH` posteriores se etiquetan `NOWCAST`. Un festivo o un fin de semana no se emite. Si un ancla no ha impreso ese día, se arrastra la anterior y la fecha del ancla queda en la fila.

Medido el 17-oct-2026, última fila 2026-10-16 (el SARON de esa fila es el del 15-oct, porque el RSS aún no trae el fixing del 16):

| | Oficial 30-sep | Nowcast 16-oct | Movimiento |
| --- | --- | --- | --- |
| 2Y | 0.287 | 0.3002 | +1.32 pb |
| 5Y | 0.453 | 0.456 | +0.30 pb |

El 2Y se mueve más con el SARON; el 5Y, a medias con el 10Y. El 1-oct el nowcast llega a 0.3216 (2Y) y 0.4945 (5Y) y luego vuelve: es la variación de las anclas, no un dato nuevo del cubo.

Salidas, las tres en sombra y fuera de frescura:

- `data/CHF_SPOT_2Y_daily_shadow.csv`
- `data/CHF_SPOT_5Y_daily_shadow.csv`
- `data/CHF_NOWCAST_shadow.json` (`mode: SHADOW`, `quality: NOWCAST`)

## Tests

`python -m unittest discover -s tests`: **833 OK, 11 omitidas**. Son las 830 del C1 auditado más las 3 de `tests/test_b3_chf_nowcast.py` (la corrección de pares no añade tests: reescribe los 10 de `tests/test_c1_shadow.py`). Las 11 omitidas son las condicionales que ya tenía la suite.

`tests/test_b3_chf_nowcast.py`: el peso 0.8/0.2 y 0.5/0.5; un festivo y un sábado no salen; el cubo, el SARON, el 10Y y `fetch_chf_snb.py` no cambian de hash; el ancla del repo es 2026-09-30.

`scripts/s01b.py` sigue en `0c43d6dd54677974bf4ffde880de049396f4b0d9cdd1c6e39964d796ef132ee5`.

Con esto se cierra el plan firmado. No hay lote siguiente.

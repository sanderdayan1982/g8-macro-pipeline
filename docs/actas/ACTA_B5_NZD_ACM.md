# ACTA B5 — Muestra corta del ACM de NZD, en SHADOW (17-oct-2026)

**Origen:** Argon 4 aprobó A3 (`32c37d33`) sin observaciones y dio luz verde a B5. Enmienda v2 #2.
**Base:** `32c37d33` (`A3: relleno ACM a 8 días y contagio de §01-b en SHADOW`).
**Vía elegida:** (b). La vía (a) queda cerrada: no hay una curva homogénea de bonos anterior a 2009 que empalmar.

## 1. Vía (a) — lo que publica el RBNZ

Página oficial consultada el 17-oct-2026, release del 16-oct-2026:
`https://www.rbnz.govt.nz/statistics/series/exchange-and-interest-rates/wholesale-interest-rates`.

Ofrece tres ficheros y no otro:

- B2 Daily close (2018-current)
- B2 Monthly close (2018-current)
- B2 Daily close (1985-2017), `hb2-daily-close-1985-2017.xlsx`

La frase «selected series beginning in 1985» no es la curva de bonos. La propia tabla de benchmarks de esa página fija el inicio de cada plazo y pone `---` cuando no hay bono de referencia:

| Plazo | Primer benchmark |
| --- | --- |
| 1Y | 10-jul-2008, con huecos largos (`---`) |
| 2Y | 11-jul-2008 |
| 5Y | 10-jul-2008 |
| 10Y | 15-nov-2006 |

No existe, por tanto, una curva 2Y+5Y+10Y homogénea anterior a julio de 2008. El 10Y suelto desde 2006 no es una curva. El fichero 1985-2017 ya está empalmado en el repositorio (`fetch_nzd_b2.py` v1.4, `HIST_URL`). El resultado de ese empalme, leído en los CSV:

| Serie | Primera impresión | Última | Filas |
| --- | --- | --- | --- |
| `NZD_CASH_ON.csv` | 1985-01-04 | 2026-10-15 | 9332 |
| `NZD_BOND_2Y.csv` | 2009-01-29 | 2026-10-15 | 3868 |
| `NZD_BOND_5Y.csv` | 2009-01-29 | 2026-10-15 | 4445 |
| `NZD_BOND_10Y.csv` | 2009-01-29 | 2026-10-15 | 4445 |
| `NZD_BOND_1Y.csv` | 2009-01-29 | 2026-10-15 | 3180 |
| `NZD_BILL_90D.csv` | 2009-01-29 | 2026-10-15 | 4441 |

Antes de 2009 el histórico abierto es el efectivo interbancario, no los bonos. El comentario de `scripts/acm_g8.py` (v2.4) dice lo mismo. Desde este entorno el xlsx responde HTTP 403 (WAF del RBNZ, igual que en el README): no se reabre el libro aquí. Aunque el tramo julio-2008 a enero-2009 estuviera en ese libro y el empalme lo hubiera soltado, son unos seis meses. No llegan a `MIN_OBS_MONTHLY = 240` y no justifican un segundo empalme.

La muestra común del ACM, tras `dropna` de los cinco plazos, es **137 meses** (2009-01-29 → 2026-10-15, 2683 observaciones diarias). El 1Y, con menos filas por los huecos de benchmark, es el que acorta el panel. 137 < 240: la marca sigue siendo `SHORT_SAMPLE`. Empalmar el efectivo de 1985 sobre los bonos fabricaría un salto de nivel. No se hace. No hay test de salto porque no hay frontera de unión nueva.

## 2. Vía (b) — corrección de Φ, solo en SHADOW

El sesgo de Kendall (1954) de un autorregresivo persistente empuja el MCO hacia cero en muestra corta. Es el mismo término 1/T que Bauer-Rudebusch-Wu estiman por bootstrap. Aquí va en forma cerrada, sobre cada autovalor λ de Φ, para que la medida no dependa de una semilla:

```
λ' = λ + (1 + 3λ) / N
```

N es el número de transiciones del VAR(1), meses − 1. Después se recorta `|λ'| ≤ 0.995`, el mismo candado de estacionariedad que ya aplica `acm_estimate`. No es un bootstrap de residuos ni un encogimiento distinto de ese candado.

`acm_estimate` acepta `phi_adjust`. Con `None`, que es la llamada publicada, el VAR, los residuos y los precios del riesgo no cambian: el residuo sigue saliendo del Φ de MCO sin recortar, como hasta ahora. Con el gancho, el residuo y los precios del riesgo se reconstruyen bajo la Φ corregida y el candado se vuelve a aplicar. `run_currency` no usa el gancho. `data/ACM_G8_NZD.csv` no se reescribe.

La medida vive en `scripts/nzd_phi_shadow.py` y en `data/ACM_G8_NZD_phi_shadow.json` (`mode: SHADOW`). Las dos patas usan el motor actual, con `FFILL_LIMIT = 8`. La única diferencia es Φ. No es una resta contra los bytes del CSV publicado.

Medido el 17-oct-2026, última impresión diaria 2026-10-15. N = 136.

| | MCO | Φ corregida | Diferencia |
| --- | --- | --- | --- |
| máx \|autovalor\| | 0.9389698163327759 | 0.9670353269871743 | la raíz se acerca a 1 y no toca el candado |
| TP10 | 1.920282587795075 | 1.9970863097905651 | **+7.6803721995490015 pb** |
| RNY10 | 3.039250714222293 | 2.9625053999987543 | **−7.6745314223538585 pb** |
| Y10 | 4.959533302017368 | 4.959591709789319 | **+0.0058 pb** |

Y10 casi no se mueve: el ajuste reparte el nivel entre prima y tipo libre de riesgo. En la ventana diaria 2021-10-29 → 2026-10-15 la media de \|ΔTP\| es 13.702204475183521 pb y la de \|ΔRNY\| es 13.703372630622255 pb.

SHA-256 de `data/ACM_G8_NZD.csv` antes y después de la medida: `47f9049859fb7aa7e86c543f1307d9e189b06038ff71d98595c9ef8dd31b2640` (67921 bytes). La serie publicada queda intacta. El nivel de NZD sigue en baja confianza (`SHORT_SAMPLE`, 137 meses).

## Lo que no cambia

`scripts/s01b.py` no se toca. SHA-256 `0c43d6dd54677974bf4ffde880de049396f4b0d9cdd1c6e39964d796ef132ee5`. Paso C, sombra A3, contrato A2, relleno de 8 días, CSV de ACM del resto de divisas y el proxy AUD de `nzd_tp_synth.py`. NZD sigue `EXCLUIDA_B5` en la sombra de contagio. No se abre B6.

## Tests

`tests/test_b5_nzd_phi.py`: los bonos empalmados no empiezan antes del 2009-01-29 y el efectivo sí empieza en 1985; la fórmula de Kendall en una diagonal y el candado 0.995; `phi_adjust=None` reproduce el MCO y la corrección mueve Φ; un Monte Carlo corto (φ = 0.95, 80 pasos, 300 réplicas, semilla 5) acerca la media corregida a la raíz; `measure()` no cambia el hash del CSV, deja `n_months < 240`, sube el autovalor y mueve Y10 menos que TP. Los puntos básicos de esta medida no van fijos en el test: el próximo CSV de NZD los movería.

`ACM_G8_NZD_phi_shadow.json` entra en las exclusiones de frescura de `tests/test_f1_config.py` y `tests/test_f3_freshness.py`. No es una serie publicada.

Medido en este lote: `python -m unittest discover -s tests` → **813 OK** (6 omitidas). Son las 808 de A3 más las 5 de `test_b5_nzd_phi`. Equivalencias E2 del 21-sep y del 22-sep, reales y sintéticas: EQUIVALENT ×4. El SHA-256 de `scripts/s01b.py` no cambió.

## Siguiente paso

Parar aquí para la auditoría de Argon 4. El orden que queda es B6 → C1 / CB_EVENT_48H (SHADOW) → B3.

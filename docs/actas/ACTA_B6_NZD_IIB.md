# ACTA B6 — IIB de Nueva Zelanda ya vencido, historia cerrada (17-oct-2026)

**Origen:** Argon 4 aprobó A3 (`32c37d33`) y B5 (`89b93336`) sin observaciones y dio luz verde a B6.
**Base:** `5c209de` (`g8-port: state update 2026-10-17T12:54Z [POS]`, padre `89b9333`). No se vuelve a `89b9333` ni a `243a8d0`. El commit de estado que cayó después de B5 no se reescribe.
**Qué cambia:** solo `scripts/fetch_nzd_b2.py` (v1.5 → v1.6), más `tests/test_b6_nzd_iib.py` y esta acta.

## Regla

Los IIB neozelandeses vencen el 20 de septiembre del año del fichero. Es la misma fecha que ya usa `build_nzd` para los años al vencimiento. El calendario es el de Auckland, no el UTC.

- El día del vencimiento la pasada todavía puede escribir el último cierre.
- Desde el día siguiente el fichero es historia cerrada. La pasada diaria no lo abre, no lo reescribe y no lo crea si no existe.
- `NZD_IIB_2025.csv` está vencido (20-sep-2025). Se queda como está.
- `NZD_IIB_2030.csv`, `NZD_IIB_2035.csv`, `NZD_IIB_2040.csv` y `NZD_IIB_2050.csv` siguen escribiéndose. El 17-oct-2026 su último cierre en el repo es 2026-10-15.

Si la columna del bono vencido sigue saliendo en el xlsx de la B2, se reconoce y se deja pasar. No entra en `write_csv`. Por eso un número nuevo en esa columna no pisa el CSV, y el registro `state/fetch_nzd.json` no la marca `WRITTEN`. Un año vencido sin CSV (no hay `NZD_IIB_2024.csv`) tampoco se crea.

## Lo que no se toca

| Fichero | SHA-256 | Hecho |
| --- | --- | --- |
| `data/NZD_IIB_2025.csv` | `ee316afdc5ea236f011858959a119fbcde40ae55a2ecb154883199449e28f484` | 1952 cierres, 2018-01-03 → 2025-10-20, última fila `2025-10-20,0.88`. No se borra. |
| `data/ACM_G8_NZD.csv` | `47f9049859fb7aa7e86c543f1307d9e189b06038ff71d98595c9ef8dd31b2640` | B5 intacto. |
| `scripts/s01b.py` | `0c43d6dd54677974bf4ffde880de049396f4b0d9cdd1c6e39964d796ef132ee5` | A3 intacto. |

`scripts/real_yields_g8.py` no se modifica. `build_nzd` sigue haciendo glob de todo `data/NZD_IIB_*.csv`. El 2025 entra en la interpolación los días en que tiene impresión. Un día posterior, en el que esa serie ya no imprime, sale del cálculo de ese día (`NaN`) y no tapa al IIB vivo. Medido en el test con un 2025 que acaba el 2024-09-20 y un 2035 que sigue el 2026-10-15: el REAL10 de ese día es el 2.50 del 2035, y la descripción sigue citando las dos madureces.

Frescura, sin cambios de reglas:

- `NZ_IIB_2025` sigue `input_ended`, frecuencia `ended`, estado `SIN_FRESCURA`, modelo `not_monitored`. `evaluate` devuelve `NOT_MONITORED`. No pasa a vencido de frescura por dejar de reescribirse.
- `NZ_B2` sigue vigilando 2030, 2035, 2040 y 2050. No incluye `NZD_IIB_2025.csv`.
- `RY_NZD` sigue derivado de `NZD_BOND_10Y.csv` y de esos cuatro IIB vivos. No exige el 2025.

`mac/push_nzd_to_github.py` no se toca: el patrón `NZD_IIB_*.csv` puede seguir subiendo el histórico que ya está en disco. La pasada diaria es la que deja de reescribirlo.

El xlsx del RBNZ responde HTTP 403 desde este entorno (WAF). El test no lo descarga. El script sigue siendo solo del Mac (`launchd`), no de GitHub Actions.

## Tests

`python -m unittest discover -s tests` en esta base: **820 OK, 11 omitidas**. Las 813 de B5 más las 7 de `tests/test_b6_nzd_iib.py`. Las 11 omitidas son las condicionales que ya tenía la suite (red `--live`, herramientas o copias locales que aquí no están). No se abre C1 ni `CB_EVENT_48H`.

## Lo que no entra

C1, B3, el ACM publicado, la sombra de Φ, el contagio de §01-b, el contrato de la suite y los CSV de metales.

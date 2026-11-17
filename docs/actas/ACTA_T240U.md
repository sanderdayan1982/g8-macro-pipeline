# ACTA — t240u tarea 5 (validador de votes.csv)

Orden de Sander, 17-nov-2026 t240u tarea 5. Este PR no se fusiona.

## Hallazgo
No había un test de CI sobre el contrato de `data/votes.csv`: esquema y orden de columnas, un solo escritor por `(fecha_cierre, script, ccy)`, filas duplicadas, `valor` vacío fuera de la lista blanca, voto en una fila `MISSING`, y `report_date` copiado de `fecha_cierre`.

Comprobado el 17-nov-2026 sobre `main` `8b9c8239`, y otra vez en `dafc9b59` (los dos commits de feeds intradía no tocan `votes.csv`). Aún no habían entrado #54, #56, #55 ni CURVA_MOTION. `data/votes.csv` tiene 296 filas. La cabecera coincide con `votes_ingest.COLUMNS` (17 nombres, sin `report_date`). Cero claves repetidas y cero filas idénticas. Las 97 filas `MISSING` traen `voto` `NA`. Desde el corte `2026-11-17` el único script con `valor` no vacío es `E21`, que ya está en `ALLOWED_VALOR_MODULES`. No se imprime ningún valor ni ningún id de correo.

## Decisión
- Módulo nuevo `scripts/tools/votes_csv_validate.py` y test nuevo `tests/test_votes_csv_validator.py`. El descubrimiento de `unittest` lo mete en el smoke sin tocar el workflow.
- El esquema esperado es `votes_ingest.COLUMNS`, en ese orden. Cuando `report_date` está en esa lista o en el fichero, es la última columna. Hoy `main` no la tiene; al entrar #56 el mismo test la exige al final porque la lista del escritor la nombra.
- Un solo escritor: como mucho una fila por `(fecha_cierre, script, ccy)`. Cada fila de una clave repetida se lista con la regla `one_writer`.
- Sin filas duplicadas: dos filas iguales en todas las columnas se listan con `duplicate_row`.
- `valor` vacío fuera de la lista blanca. La lista es el objeto `votes_ingest.ALLOWED_VALOR_MODULES`; no hay una copia. El corte es `tests/test_allowed_valor_modules.py` `FUSION` (`2026-11-17`): una fila con `fecha_cierre` anterior no se juzga, la misma comparación que ese test.
- Una fila `MISSING` no lleva voto. La convención del lector es el token `NA` (`docs/VOTES_RULES_v1.md`: «NA es ausencia de voto»; `votes_ingest.missing_rows` escribe `voto` `NA`). Una celda vacía no es esa convención: también falla.
- `report_date` no vacío e igual a `fecha_cierre` falla (`report_date_copied`). Vacío no es una copia. Sin la columna, la regla no se aplica.
- El mensaje de fallo lista `fecha_cierre`, `script`, `ccy` y la regla, más el número de línea. No lleva `valor`, `voto` ni id de correo.
- Si el `votes.csv` publicado incumple, el test queda rojo. Este PR no corrige el CSV.

## Sin cambios
Ningún test ya existente. No se toca `scripts/votes_ingest.py`, ni las reglas de voto, ni umbrales, ni `data/votes.csv`. Sin fórmulas, calibraciones, ventanas de z, ACM, s01b ni factor USD.

## Pruebas
`python3 -m unittest tests.test_votes_csv_validator` (17-nov-2026, Python 3.12): 16 tests, todos verdes, incluido el fichero publicado de esta punta.

Los casos sintéticos cubren un fichero válido, cabecera distinta, columna de más o de menos, `report_date` fuera del final, recuento de campos, dos escritores de la misma clave, fila duplicada, `valor` antes y después del corte, el objeto de la lista blanca, `MISSING` con `NA` / vacío / `+1` / `0` / `-1`, voto en una fila que no es `MISSING`, y `report_date` copiado, distinto o ausente.

`python3 -m unittest discover -s tests -p 'test_*.py'`: 1148 tests, 6 skipped, 8 failed. Ninguno es el validador nuevo. Los ocho ya fallan en esta punta de `main` y no se tocan aquí: `test_allowed_valor_modules` (el catálogo de sondas no incluye `CRD_G8`, que entró en #57), `test_flujos01_pr2.Rv21Vectors.test_live_eur_matches_the_oracle_and_the_roll_session_is_na`, `test_fxvol_shadow.FxvolShadow.test_additive_fields_do_not_recompute_existing_ones`, y cinco de `test_private_data` porque esta máquina no tiene `rsync`.

## Siguiente
Rebase sobre `main` cuando entren #54, #56, #55 y CURVA_MOTION. No fusionar. Si tras el rebase el CSV incumple, se informa la lista y no se reescribe.

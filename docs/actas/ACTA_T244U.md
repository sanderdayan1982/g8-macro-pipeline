# ACTA T244U — BOOK_RISK, opción 1

**Decisión:** Sander, 18-nov-2026, orden t244u punto 6. Un PR contra `main`. No se fusiona.

## Hallazgo

§07 existe en `docs/index.html` (`section-factor`, título `07 — FACTOR USD · RESIDUALES · LIBRO (CONTEXT)`). El módulo pedía `data/BOOK_RISK.json` y, si el JSON traía posiciones, pintaba par, lado, nominal, cubos, VaR y la tarjeta PRE-TRADE (`data/candidate.csv`). `docs/js/` no tiene esa sección y no lee ningún fichero de libro.

En el árbol público no hay `data/book.csv`, ni otro `data/book*.csv`, ni `book/positions.csv`. Sí hay `data/book.csv.example`, `data/candidate.csv.example` y `data/BOOK_RISK.json` con `book.status = SIN LIBRO` (generado 2026-11-17). `scripts/tools/votes_csv_validate.py` no está en `main`.

## Decisión

Diseño en `docs/design/BOOK_RISK_opcion1.md`. El cálculo, a partir de `book/positions.csv`, corre en un workflow del repo privado `sanderdayan1982/g8-macro-data` y escribe `BOOK_RISK_private.json` solo allí. Secretos, solo por nombre: `G8_DATA_READ_CI` (lectura) y `G8_DATA_WRITE` (escritura). Ningún valor.

En §07 público el hueco del libro muestra `LIBRO PRIVADO` y no pide ningún fichero de libro. El factor USD sigue leyendo `USD_FACTOR.json`: es mercado publicado, no el libro. El panel completo no se abre en esta página.

Test nuevo `tests/test_no_public_book.py`, con el comentario `orden Sander 18-nov t244u`. Falla si existe `data/book.csv`, cualquier `data/book*.csv` o `book/positions.csv`.

## Sin cambios

Fórmulas, calibraciones, umbrales, ventanas de z, ACM, s01b, factor USD (`scripts/usd_factor.py`, `scripts/book_risk.py`), motor de alertas, reglas de voto, scripts Pine, workflows, `data/` y el resto de tests. No hay datos con licencia. No se implementa el workflow privado ni el cálculo. No se fusiona.

## Pruebas

`tests/test_no_public_book.py`. La sintaxis del inline de `docs/index.html` se comprueba con el mismo `new Function` que usa `scripts/tools/validate_smoke.sh`. El número de la suite y los fallos exactos son los del run `Validate (smoke)` de este PR.

## Siguiente

Sander revisa el diseño. El workflow de g8-macro-data y el panel local o protegido quedan para otra orden. Este PR no se fusiona solo.

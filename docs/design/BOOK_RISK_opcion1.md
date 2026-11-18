# BOOK_RISK — opción 1

Orden Sander 18-nov t244u, punto 6. Diseño. Este documento no crea el workflow del repo privado ni el cálculo.

Repo público: `sanderdayan1982/g8-macro-pipeline`. Repo privado: `sanderdayan1982/g8-macro-data`.

## Qué decide la opción 1

El riesgo del libro se calcula en un workflow de GitHub Actions de **g8-macro-data**. La entrada de posiciones es `book/positions.csv` de ese repo. La salida es `BOOK_RISK_private.json`, escrita solo allí.

En el dashboard público, el hueco del libro dentro de §07 muestra el texto `LIBRO PRIVADO`. No muestra par, tamaño, entrada, stop, % de uso ni semáforo. El panel completo (esas cifras) solo se abre en local o detrás de acceso protegido. Este PR no abre esa vista.

## Dónde corre

| Pieza | Dónde | En este PR |
|---|---|---|
| Cálculo a partir de `book/positions.csv` | Workflow del repo privado `sanderdayan1982/g8-macro-data` | No se implementa |
| `BOOK_RISK_private.json` | Solo en ese repo privado | No se escribe aquí |
| Texto `LIBRO PRIVADO` | `docs/index.html`, sección `section-factor` (§07) | Sí, el hueco del libro |
| Panel completo | Local, o una vista con acceso protegido | No se implementa |

La web pública la publica Netlify desde `docs/`. Esa página no es el sitio del cálculo y no recibe el JSON privado.

`docs/js/` no tiene §07 y no lee ningún fichero de libro (comprobado en el árbol del 18-nov-2026). No se crea una sección nueva ahí.

## Entradas

- `book/positions.csv` en g8-macro-data. Es el libro. No se copia a g8-macro-pipeline.
- La fórmula que ya está en `scripts/book_risk.py` (no se toca en este PR) lee además la Σ y las betas de `data/USD_FACTOR.json`. Eso es mercado ya publicado, no un fichero de libro. Mover el job al privado no cambia la fórmula. Este PR no la ejecuta ni la reescribe.
- `data/candidate.csv` es la entrada de PRE-TRADE del script actual. En la página pública no se lee. El panel completo, si un día se abre en local o con acceso protegido, es el único sitio donde esa tarjeta puede volver.

## Salidas

- `BOOK_RISK_private.json` en g8-macro-data, y en ningún otro repo.
- En §07 público, el texto fijo `LIBRO PRIVADO`.
- El bloque de factor USD de la misma sección sigue leyendo `data/USD_FACTOR.json`. No es el libro: son los tipos de referencia del BCE ya publicados (betas, residuales, dispersión, matriz de cruces). El título de la sección no cambia.

## Secretos

Solo nombres. Ningún valor en este documento, en el código, en los tests ni en los logs.

| Nombre | Papel |
|---|---|
| `G8_DATA_READ_CI` | Lectura de g8-macro-data. Los workflows de este repo lo inyectan hoy como `G8_DATA_TOKEN` en `scripts/tools/private_data.py pull`. No autoriza a pintar el libro en la web pública. Una vista local o protegida que leyera `BOOK_RISK_private.json` usaría este nombre de secreto, fuera de `docs/index.html`. |
| `G8_DATA_WRITE` | Escritura en g8-macro-data. Los workflows de este repo lo inyectan hoy como `G8_DATA_TOKEN` en `private_data.py push`. `BOOK_RISK_private.json` solo se escribe en el repo privado. No se empuja a g8-macro-pipeline. |

El token entra por el entorno (`G8_DATA_TOKEN`), como ya hace `private_data.py`: no por argv, no por la URL, no por la salida.

## Qué se prohíbe en el público

- `data/book.csv`
- cualquier `data/book*.csv`
- `book/positions.csv`
- commitear `BOOK_RISK_private.json`
- que §07 pida `BOOK_RISK.json`, `book.csv`, `candidate.csv`, `book/positions.csv` o `BOOK_RISK_private.json`
- mostrar par, tamaño, entrada, stop, % de uso o semáforo
- pegar el valor de `G8_DATA_READ_CI` o de `G8_DATA_WRITE`
- datos con licencia
- implementar aquí el workflow privado o el cálculo

`tests/test_no_public_book.py` falla si aparece `data/book.csv`, cualquier `data/book*.csv` o `book/positions.csv`.

## Qué queda fuera de este PR

`scripts/book_risk.py` sigue en el árbol con su huella congelada. `.github/workflows/usd_factor.yml` sigue llamándolo y puede commitear `data/BOOK_RISK.json`. Eso es el camino viejo, no la opción 1. Hoy ese JSON público está en `SIN LIBRO` porque no hay `data/book.csv`. Este PR no borra `data/` (`data/BOOK_RISK.json`, `data/book.csv.example`, `data/candidate.csv.example` siguen). El ejemplo no casa con `book*.csv`.

`scripts/dashboard_alerts.py` sigue leyendo `data/BOOK_RISK.json` para el brief. También está congelado por huella. No se edita. Con el libro en `SIN LIBRO` no añade par, tamaño, entrada, stop, % de uso ni semáforo.

`scripts/tools/votes_csv_validate.py` (PR #59) no está en `main`. Este diseño y el test nuevo no dependen de él.

# ACTA U2 — cierre de los 6 hallazgos del dictamen sobre `6c387bea` (17-oct-2026)

**Origen:** dictamen del dashboard sobre `6c387bea77c1e7ae8ff88e675a9122d8cb79882e` (`U1: numeración 00–07, sombras en la web y cron diario`).
**Base:** esa misma punta. No se rebobina `main`.
**Qué cambia:** `docs/index.html`, `scripts/cme_options_shadow.py`, `data/CME_OPTIONS_C1_shadow.json`, `tests/test_u2_dictamen_fixes.py` y esta acta.

No se tocan los siete ficheros congelados en `tests/test_exclusions.py`. `scripts/dashboard_alerts.py` sigue en `56e46654e7cc86b0be45e9d5d1792476169db3426d38d12aacb93d50056b5ebb`. Telegram sigue saliendo de ese script. Esta acta es la tabla que el próximo cambio autorizado de ese motor tiene que respetar.

## Equivalencia Telegram → web

El generador congelado nombra las secciones viejas. La página, desde U1, pinta otra numeración. `briefSec` solo reescribe el campo `sec` de las balas del brief. El texto de Telegram no pasa por esa función.

| Lo que escribe Telegram / `dashboard_alerts.py` | Lo que debe leer el operador en la web |
| --- | --- |
| §03 tipo oficial | §02 Money market floor spreads · tipos oficiales |
| §04 term premium | §03 Term premium ACM |
| §06/§07 metales | §04 Gold / §05 Silver |
| §08 muros | §06 Options surface |
| §09 COT | Fuera de la web. POS-G8. La bala pintada dice `COT · fuera de la página` |
| §10 factor USD | §07 Factor USD |

§01, §01-b y §02 no cambian de número.

## Los seis cierres

1. `briefSec` reescribe `§09 COT` como `COT · fuera de la página` antes de tocar `§04`. Ninguna bala pintada en §00 enseña `§09`.
2. La tabla de arriba queda escrita aquí. El script congelado no se edita.
3. `CME_OPTIONS_C1_shadow.json` dice que los niveles y `dist_pct` son nativos, y que `dashboard_alerts.py` (§00) y `docs/index.html` (§06) invierten una sola vez. En §06 el bloque de la sombra lleva la marca `SHADOW` y el tooltip `CME_OPTIONS_C1_shadow.json · sesión <session>`. Si `shadow.session` no es `doc.latest_session`, esos muros no se mezclan con los chips de `OPTIONS_SURFACE.json`.
4. §02 imprime `CB_EVENT_48H_shadow.json · as-of <as_of>` en el tooltip. El entero y el booleano de la sombra se usan solo si `as_of` es el día UTC de hoy, o si la edad desde las 21:30 UTC de ese día cabe en el tope del brief (30 h entre semana, 78 h en domingo y lunes). Si no, la celda marca `sombra atrasada` y el recuento vuelve a lunes–viernes. El encabezado de `Próxima reunión` dice que, con sombra vigente, el ámbar lo gobierna `CB_EVENT_48H` (0..2 d.h. del calendario del banco).
5. `FEED_REGISTRY` usa la numeración visible: tipos oficiales `02`, ACM TP `03` (incluye `NZD_ACM`, `CHF_ACM` y `ACM_TP_10Y`), oro `04`, plata `05`, opciones `06`, factor `07`, COT `00`. Ya no hay `section` `08`, `09` ni `10`.
6. `tests/test_u2_dictamen_fixes.py` ejecuta `briefSec` (el de la página, vía node) sobre las balas reales de `data/alerts/brief.json`, recalcula la distancia invertida de JPY y exige `SHADOW`, `as_of` y los `section` nuevos. Los siete hashes siguen en la prueba.

## Nota sobre la prueba de `briefSec`

Las balas reales, después del renombrado correcto, contienen `§04/§05` (metales) y `§07` (factor). Una prohibición ciega de las cadenas `§04` y `§07` rechazaría la numeración visible. La prueba prohíbe `§06`, `§08`, `§09` y `§10` en el resultado, exige `§04/§05` y `§07`, y exige que `§09 COT` salga como `COT · fuera de la página`.

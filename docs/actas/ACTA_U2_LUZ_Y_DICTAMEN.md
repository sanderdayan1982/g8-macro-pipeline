# ACTA U2 — luz en la web para los datos diarios y cierre del dictamen sobre `6c387bea` (17-oct-2026)

**Origen:** dictamen del dashboard sobre `6c387bea77c1e7ae8ff88e675a9122d8cb79882e` (`U1: numeración 00–07, sombras en la web y cron diario`).
**Base:** esa misma punta. No se rebobina `main`.
**Qué cambia:** `docs/index.html`, `scripts/cme_options_shadow.py`, `data/CME_OPTIONS_C1_shadow.json`, `scripts/s01b_shadow_a3.py`, `data/S01B_shadow_a3.json` (nuevo), `.github/workflows/daily_update.yml`, `tests/test_u2_dictamen_fixes.py`, `tests/test_f1_config.py`, `tests/test_f3_freshness.py` y esta acta.

`data/S01B.json`, `data/OPTIONS_SURFACE.json` y `data/MFV_G8_state.json` no se tocan.

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
3. `CME_OPTIONS_C1_shadow.json` dice que los niveles y `dist_pct` son nativos, y que `dashboard_alerts.py` (§00) y `docs/index.html` (§06) invierten una sola vez. En §06 el bloque de la sombra lleva la etiqueta visible `C2 · sesión <session> (CME_OPTIONS_C1_shadow.json)`, con el mismo texto en el tooltip. Si `shadow.session` no es `doc.latest_session`, esos muros no se mezclan con los chips de `OPTIONS_SURFACE.json`.
4. §02 imprime `CB_EVENT_48H_shadow.json · as-of <as_of>` en el tooltip. El entero y el booleano de la sombra se usan solo si `as_of` es el día UTC de hoy, o si la edad desde las 21:30 UTC de ese día cabe en el tope del brief (30 h entre semana, 78 h en domingo y lunes). Si no, la celda marca `sombra atrasada` y el recuento vuelve a lunes–viernes. El encabezado de `Próxima reunión` dice que, con sombra vigente, el ámbar lo gobierna `CB_EVENT_48H` (0..2 d.h. del calendario del banco).
5. `FEED_REGISTRY` usa la numeración visible: tipos oficiales `02`, ACM TP `03` (incluye `NZD_ACM`, `CHF_ACM` y `ACM_TP_10Y`), oro `04`, plata `05`, opciones `06`, factor `07`, COT `00`. Ya no hay `section` `08`, `09` ni `10`.
6. `tests/test_u2_dictamen_fixes.py` ejecuta `briefSec` (el de la página, vía node) sobre las balas reales de `data/alerts/brief.json`, recalcula la distancia invertida de JPY, ejecuta `b2Line` y las celdas A3 sobre los JSON reales, compara sesiones y exige `as_of` y los `section` nuevos. Los siete hashes siguen en la prueba.

## Nota sobre la prueba de `briefSec`

Las balas reales, después del renombrado correcto, contienen `§04/§05` (metales) y `§07` (factor). Una prohibición ciega de las cadenas `§04` y `§07` rechazaría la numeración visible. La prueba prohíbe `§06`, `§08`, `§09` y `§10` en el resultado, exige `§04/§05` y `§07`, y exige que `§09 COT` salga como `COT · fuera de la página`.

## B2 · metales diarios en §04 y §05

El módulo de metales carga `data/MFV_G8_shadow_state.json` en paralelo (`renderDailyB2`, sin `await` delante de `renderMetal`). Solo escribe una línea en la cabecera de cada sección:

`DIARIO (B2): MDP-Z <mdp_z> · precio/regresores <mdp_as_of> · ancla COT <cot_as_of> · β <beta_state>`

Las tarjetas, el semáforo y los gráficos siguen saliendo de `MFV_G8_state.json` (titular semanal). Si la sombra no carga o su `mode` no es `SHADOW`, la línea dice `DIARIO (B2): sin datos`.

La sombra la regenera `metals_update.yml` (lunes a viernes 22:45 UTC y sábado 07:00 UTC), no `daily_update.yml`. Hoy `mdp_as_of` y `cot_as_of` valen `2026-10-13`; la línea enseña esa fecha tal cual.

## A3 · contagio de §01-b en SHADOW

`scripts/s01b_shadow_a3.py` gana un `__main__`. La sesión es el último día TARGET de `data/usd_factor/canonical.csv`, que es la misma que lee §01-b; se puede forzar con un argumento `AAAA-MM-DD`. Escribe `data/S01B_shadow_a3.json` de forma atómica. Si no hay lectura, no escribe y sale con código 0. El módulo sigue sin nombrar las salidas del Paso C (la prueba de tokens de `test_a3_shadow.py` sigue en pie).

`_by_date` guarda en caché el índice por fecha de cada lista de filas. Sin caché, una corrida sobre los datos reales no terminaba en 10 min; con caché tarda unos 15 s. Los resultados no cambian (`test_a3_shadow.py` en verde).

`daily_update.yml` añade el paso `Shadow S01B A3` con `continue-on-error: true` y `g8step --phase post --cap 40`, y añade `data/S01B_shadow_a3.json` al `git add`. La reserva pasa de 660 s a 700 s (40 s más); la ventana de descarga queda en 890 s, por encima de los 660 s que exige `test_job_budget.py`.

§01-b carga el JSON junto a `S01B.json`. Solo pinta si `t` es igual al `as_of` de `S01B.json`. Entonces añade bajo cada fila:

`A3 · local: <dtp22_local> pb (β <beta_usd>) · ρ22: <rho_tp_fx_22>`

NZD dice `A3 · EXCLUIDA_B5`, USD dice `A3 · referencia ΔTP22_USD`. Una divisa sin dato local muestra `·` y su estado (por ejemplo `FECHAS_NO_CUADRAN`). Si las sesiones no cuadran, la tabla no lleva sub-filas y aparece `A3 SHADOW omitida · … t X ≠ as-of Y`. A3 no entra en CTF, θ ni Persist.

`S01B_shadow_a3.json` entra en las listas `extra` de `test_f1_config.py` y `test_f3_freshness.py`: es una sombra, no una serie de frescura.

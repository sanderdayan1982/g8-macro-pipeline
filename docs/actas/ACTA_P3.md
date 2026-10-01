# ACTA P-3 — AUD «congelado» desde el 23-sep: diagnóstico y fecha real en §01 (1-oct-2026)

## Hallazgo
- **No es una caída.** Actions descarga `f02d.xlsx` con 200 OK (Daily Data Update del 1-oct, paso `acm_g8`);
  la tabla de la RBA termina el 23-sep.
- **Verificado en la fuente primaria** (rba.gov.au/statistics/tables, 1-oct-2026 15:50 hora de Bata):
  `csv/f2-data.csv` → Publication date 25-Sep-2026, Last-Modified vie 25-sep 03:54 GMT, última fila 23-Sep-2026
  (2Y 4,947 · 10Y 5,245 · indexado 2,862). F16 (bonos AGS uno a uno) y F16.1 (semis) salieron a la misma hora
  con el mismo último día. F2.1 (mensual) y F17 (cupón cero) van todavía más atrás (31-ago).
- **Ritmo de publicación:** una vez por semana, el viernes, con datos hasta el miércoles. Lo confirma el historial
  del repo (RY_G8_AUD: 17-jun, 24-jun … 16-sep, 23-sep, siempre con commit del viernes). La auditoría del 24-sep
  ya lo había anotado para el presupuesto de AUD_NOM_2Y en §05 (8 días hábiles). Próxima publicación prevista:
  vie 2-oct, con datos hasta el 30-sep.
- **No hay fuente diaria más fresca de la misma serie en la RBA**: todas las tablas de bonos AGS se publican juntas
  cada semana. La alternativa diaria sería otro instrumento (futuros de bonos ASX), no la misma serie (ver «Pendiente de OK»).
- **ACM_G8_AUD «relleno»:** el panel diario de `acm_g8.py` junta las letras F1 (diarias, hasta el 30-sep) con
  los bonos F2 (hasta el 23-sep) y hace `ffill(limit=5)`. Las filas del 24 al 30-sep llevan los bonos del 23
  repetidos, con QUALITY `ACM_K3_380m` y sin marca. En §01-b ya existía la bandera `ACM_FFILL`; en §01, no.
- **§01 engañaba:** debajo de «AUD» ponía 2026-09-30 (fecha del ACM). El aviso ⧗ solo salía con más de 15 días
  naturales de atraso, así que el AUD (6 días hábiles) no llevaba ninguna marca.
- **Fallo de calendario en el navegador (afecta a §05):** `isG8Holiday` buscaba el festivo con `toISOString()`
  sobre la medianoche local. En Bata (UTC+1) eso es el día anterior en UTC: el jueves 24-sep se miraba como 23-sep
  (festivo en Japón) y se perdía un día hábil. Al oeste de UTC pasaba lo contrario, porque `new Date('aaaa-mm-dd')`
  es medianoche UTC.

## Decisión (presentación y corrección de calendario; sin cambios de metodología)
1. §01, línea de cada divisa con ficheros en el repo (USD, EUR, JPY, GBP, CAD, AUD): «dato AAAA-MM-DD»
   = última fila NOM10, la fecha real de mercado. Si el ACM va por delante, se añade «ACM mm-dd ⚠ curva mm-dd»
   en ámbar, con la explicación del arrastre (ffill ≤ 5 sesiones).
2. §01, celda nominal: marca «⧗ mm-dd · Nd» con días hábiles G8 desde 2 días de atraso. En ámbar si §05 la da por LIVE
   con el presupuesto del registro (`<CCY>_RY`); en rojo, y con el estado, si pasa a DEGRADED, STALE o DEAD.
   Para AUD, el tooltip explica el ritmo de la RBA y da la próxima publicación prevista. Con 15 días o más sigue
   saliendo también el aviso de siempre.
3. G8DQM: `localDay()` interpreta aaaammdd y aaaa-mm-dd como día local; `isG8Holiday` usa la fecha local.
   Se expone `G8DQM.businessDays`. Cuentas: 23-sep → 1-oct = 6 días hábiles en Bata, UTC y Nueva York.
4. `sources/registry.csv` (AUD_NOM_2Y): nota de ritmo «to be confirmed» → «weekly: Fri publication, data to Wed
   (verified …)».

## Sin cambios
`acm_g8.py` (incluido el `ffill(limit=5)`), fórmulas, umbrales, z, ACM, s01b, factor USD y presupuestos de §05.

## Pendiente de OK (metodología)
- ~~A. Marcar las filas ACM arrastradas en el propio CSV~~ → autorizada e implementada (sección siguiente).
- **B. Nowcast diario AUD con futuros de bonos ASX (3Y YT, 10Y XT)**: desplazar la última curva F2 con el cambio
  diario implícito en los futuros hasta que llegue el viernes. Precedente: CHF. Es otra fuente y otro instrumento,
  así que antes habría que verificar en la ASX el acceso a la liquidación diaria desde Actions y su licencia.
  → **Aprobada por el propietario (1-oct-2026), bloqueada por fuente.** No hay publicación gratuita y verificable de la
  liquidación diaria de XT/YT: la web de la ASX solo publica la liquidación al vencimiento
  (asx.com.au/…/interest-rate-derivatives-settlement-history) y los precios diarios forman parte de sus productos de pago
  (ASX Information Services → price data). Databento declara cobertura de CME/ICE/EEX/Eurex, no de la ASX.
  Se retoma cuando haya una fuente con licencia (feed ASX de pago, o el que use el bróker del operador).

## Opción A — autorizada por el propietario (1-oct-2026, en el chat)
- `acm_g8.py`: `build_daily_panel` anota qué filas llevan algún tramo arrastrado (`panel.attrs["ffilled"]`) y
  `tag_ffill()` añade `_FFILL` a su QUALITY (ej. `ACM_K3_380m_FFILL`). Y10_FIT, RNY10 y TP10 no cambian; el
  `ffill(limit=5)` sigue igual. Vale para todas las divisas: también marca festivos en los que un tramo no cotizó.
- `s01b.py`: `quality_ok()` quita el sufijo `_FFILL` antes de evaluar, así que emisión y badge son idénticos. La lectura
  ACM_FFILL de §01-b sigue saliendo de E2. Equivalencias 21-sep y 22-sep (reales y sintéticas): EQUIVALENT.
- `dashboard_alerts.py`: si la última fila ACM de la divisa lleva `_FFILL`, la fila del libro de brief.json añade el flag
  «TP curva arrastrada (ffill)» (visible en §00).
- `docs/js/data-loader.js`: la etiqueta K del ACM añade «última fila con curva arrastrada (ffill)».
- Huellas nuevas autorizadas: `dashboard_alerts.py` y `s01b.py` (`tests/test_exclusions.py`, `tests/test_f3_freshness.py`).
- Los CSV se regeneran en la próxima ejecución de Daily Data Update (desde el Mac la RBA devuelve 403).
- Tests: `tests/test_p3_acm_ffill.py` (6) — panel con letras diarias y bonos hasta el miércoles → 5 filas marcadas y
  valores intactos; sufijo solo en las filas marcadas; sin máscara no cambia nada; `quality_ok` idéntico con y sin
  sufijo; lectura del sufijo en el brief.

## Pruebas
`tests/test_p3_s01_asof.py` (6): funciones reales de `docs/index.html` ejecutadas en Node con TZ Africa/Malabo,
UTC y America/New_York. Días hábiles iguales en las tres zonas; marca AUD ámbar con nota de publicación y fecha
del viernes; rojo con estado cuando se pasa del presupuesto; sin marca si el dato es fresco; línea «dato … · ACM …»;
comprobaciones estáticas. Suite completa (Python 3.11): 623 OK (5 omitidos); con la opción A, 629 OK (5 omitidos).
En el navegador (servidor local, 1-oct): AUD «dato 2026-09-23 · ACM 09-30 ⚠ curva 09-23», «⧗ 09-23 · 6d» en ámbar;
USD, GBP y CAD «⧗ 09-29 · 2d»; EUR y JPY sin marca; §05 con 45 LIVE y 2 MANUAL; consola sin errores.

## Para el agente de mantenimiento
Que el AUD vaya atrasado entre miércoles y viernes es normal: no se arregla nada mientras §05 diga LIVE (8 días hábiles).
Hay que actuar si llega el sábado sin publicación del viernes, si `f2-data.csv` cambia de cabecera o de Series ID
(FCMYGBAG2D/3D/5D/10D/ID), o si la URL `xls/f02d.xlsx` deja de responder. Respaldo de la misma serie:
`csv/f2-data.csv` (mismo contenido, publicado a la misma hora).

## Siguiente
Punto 3: fuentes directas de tipos oficiales (RBA, BoE, SNB) por delante del BIS.

# ACTA FAMILIAS-01 M1 — crédito FRED en sombra (bloque A)

**Orden:** FAMILIAS-01 Fase 2, módulo M1. Diseño firmado y anexo de Sander. Un PR contra `main`. No se fusiona y no se despliega.

## Hallazgo
- No había cálculo de CRD. `sources/registry.csv` ya nombra `HY_OAS` (FRED `BAMLH0A0HYM2`) y no hay script que lo lea.
- Comprobado el 16-nov-2026 en la fuente primaria, sin anotar ninguna cifra de spread:
  - https://fred.stlouisfed.org/series/BAMLH0A0HYM2 (ICE BofA US High Yield OAS)
  - https://fred.stlouisfed.org/series/BAMLHE00EHYIOAS (ICE BofA Euro High Yield OAS)
  - https://fred.stlouisfed.org/series/BAMLC0A0CM (ICE BofA US Corporate OAS, solo informativo)
  - HTTP 200. Unidad: Percent, Not Seasonally Adjusted. Frecuencia: Daily, Close.
  - El CSV de `fredgraph.csv?id=<ID>` trae la cabecera `observation_date,<ID>`.
  - Copyright ICE Data Indices: la reproducción está prohibida y el «Top Level Data» es solo para uso interno. Por eso ninguna cifra entra en el repo, en `docs/`, en `data/` público ni en el registro de Actions.
  - El hueco de festivo, en el archivo de hoy, es una celda vacía (no el punto `.`) en Navidad, Año Nuevo y Viernes Santo. El diseño cubre las dos formas.
  - La ventana que guarda FRED es la de unos tres años que ya decía el diseño.

## Decisión
- `scripts/credit_fred_shadow.py`. Paso nuevo en `daily_update.yml`, después de las sombras ya existentes, con `if: always()`, `continue-on-error: true` y tope 40 s. No cambia ningún paso anterior.
- `CRD_PUBLISH = False`. Con False el cálculo es en memoria y no se escribe. Las rutas de más adelante (PR-C, repo privado) quedan como constantes: `data/credit/CRD_shadow.json` y `data/credit/oas_vintage.csv`.
- `VOTE = 0`. `blocked_entry = false`. No entra en lo ya publicado.
- USD: el CRD sale solo del HY. El IG se guarda como informativo y no entra en la fórmula. EUR: solo HY. GBP, JPY, AUD, NZD, CAD y CHF salen `NA` con `NO_FRED_SPREAD`.
- `crd = −oas_z`, una sola vez. z252 es el del dashboard (`zScore`): desviación poblacional ÷N, ventana de hasta 252 observaciones válidas, hoy incluido. No es la desviación muestral N−1 de FXVOL: aquel módulo lo decía y este texto dice z252.
- Umbrales constantes, sin búsqueda: SANO entra en ≥ +0.5 y sale si < +0.3; TENSO entra en ≤ −1.0 y sale si > −0.7; ROTURA entra en ≤ −2.0 y `crd_d1w < 0`, y sale si > −1.5. Al salir de ROTURA, TENSO si `crd ≤ −0.7`, si no NEUTRO. Precedencia ROTURA > TENSO > SANO > NEUTRO. Histéresis: 2 cierres. AISLADO = UNAVAILABLE. La parte `dd ≤ p10` = NA.
- `[PARCIAL-A]` en todas las filas USD/EUR (BK_z es Fase 3). `[PROV]` de ventana si 60 ≤ n < 252. n < 60 → UNAVAILABLE. El rótulo `[PROV]` sin calibrar se queda en `method.crd` hasta GATE.
- Sesión D = fecha civil WAT (UTC+1, sin horario de verano). Se usa el OAS con fecha anterior a D, nunca el OAS fechado en D.
- STALE si el as-of es más viejo que 2 días hábiles US: con edad 2 todavía se clasifica; con edad 3 el estado es STALE y no hay régimen.
- Celda vacía o `.`: no es sesión, no se rellena. Día hábil ausente que no sea festivo de esta serie: UNAVAILABLE, sin arrastrar valor ni estado, y la histéresis vuelve a empezar. Un cero no sustituye a un dato que falta.
- Revisiones: la primera cifra se queda (`rev` 0, `first_seen_utc`). Si cambia, se añade una fila (`rev` 1, y las siguientes suben). No se reescribe. El estado usa la primera cifra. La lógica corre en memoria; el CSV solo se escribiría con la bandera en True.
- El registro del paso son palabras y conteos (`publish=off`, `status`, `why`, `obs`). Si una línea llevara un decimal o un nombre de régimen, se sustituye por `LOG_BLOCKED` y no sale.
- Para que el paso quepa en `test_job_budget` sin acortar la fase de descarga: reserva 740 → 780 s y timeout del job 30 → 31 min. El presupuesto de descarga pasa de 970 s a 990 s.
- Inventario (`sources/ingest_inventory.csv`): `EXCLUIDO`. El test de inventario exige fila para todo script de workflow que abre la red. No es `CUBIERTO_3A`: ese estado obliga a `Ingest.publish` y prohíbe `urllib`, y publicar aquí escribiría el dato en el repo público.

## Sin cambios
- Fórmulas, umbrales, ventanas y scripts ya congelados. Ningún test que ya existía. El `git add data/` del Daily no se toca: con la bandera apagada no hay fichero bajo `data/credit/`.

## Lecturas del texto, para que no queden calladas
- «Older than 2 US business days» se tomó al pie de la letra: edad > 2. T-CR7 fija el borde (2 sigue, 3 es STALE).
- Día hábil US para la edad: laborable que no sea Año Nuevo, Viernes Santo o Navidad, que son los festivos que el CSV de FRED deja en blanco (16-nov-2026, solo fechas). Columbus Day y Veterans Day vienen con cifra, así que cuentan. No se traslada un festivo de fin de semana al viernes o al lunes: en esta ventana de tres años no aparece ese traslado.
- Δ5 son 5 observaciones válidas (un hueco no cuenta). 257 = 252 + 5. El `[PROV]` mira la ventana z (60 / 252), que es lo que nombra T-CR8.
- Un fallo HTTP del IG no apaga el CRD del HY. Un fallo del HY sí deja la región UNAVAILABLE.
- La descarga es un solo GET de la URL firmada, timeout 10 s, biblioteca estándar. El reintento 10/40/90 de g8http no cabe en el tope de 40 s y no queremos una traza del cuerpo.
- En STALE el régimen no se publica. La cifra se queda en memoria (no se imprime ni se escribe con la bandera apagada) para que el archivo privado pueda enseñar el as-of viejo.
- Encender `CRD_PUBLISH` en este repo público haría que el `git add data/` ya existente subiera los ficheros. No se enciende aquí.

## Pruebas
- `tests/test_familias01_m1_crd.py`: T-CR1..T-CR14, fixtures sintéticas. Pasan.
- `tests/test_inventory.py` y la parte estática de `tests/test_job_budget.py`: pasan.
- Suite local completa, sin las dependencias de `requirements.txt` (faltan pandas, requests, openpyxl): no es comparable con CI. Con lo que sí corre, los fallos de lógica son los tres de FXVOL ya rojos en `main`, el de inventario (corregido con la fila EXCLUIDO) y `test_argos_rates` de paridad.
- `test_argos_rates.ArgosRatesParity.test_file_matches_a_fresh_render_at_the_same_utc_clock` falla en este `main` (commit `3ea3ee84`, datos intradía del 16-nov) y no lee ningún fichero de este PR. El Validate de `main` en `b7414dd1` (run 43780290360, 16-nov-2026) tuvo 1098 tests, 3 fallos y 11 skipped: `test_flujos01_pr2` live_eur, `test_fxvol_shadow` additive_fields y five_sessions. `idempotent_and_under_60s` no falló en esa pasada.

## Siguiente
- Sander decide si fusiona. La bandera sigue en False. El bloque no vota.

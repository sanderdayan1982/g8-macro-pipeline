# F1 — cierre técnico B2, AUD, evidencias y mantenimiento

Fecha de verificación: **17-oct-2026**. Base local autorizada: `d1bdfed48efca24ea2453ae4f4d469392a002185`.
Main comprobado durante el trabajo: `843918b332186ce340e34b8e35d78b35097fffaa`.
Autorización OWNER: el propietario confirmó en el chat «SI, PROCEDE» al alcance F1 del adjunto `gemini-code-1792277301788.txt`, incluida la edición de `dashboard_alerts.py` y la renovación de su SHA-256. No autoriza publicación automática de esta rama.

## 1. B2: el diario alcanza el último precio real

`build_panel_xau` y `build_panel_xag` pasan ahora `max(price.index.max(), panel.index.max())` al ensamblador diario. El panel semanal permanece exactamente en W-TUE. `metals_shadow_daily.project` no se modifica: martes usa β predicha y Z semanal; días siguientes usan β filtrada, con un máximo de cuatro días hábiles y sin futuro.

Regeneración independiente de las dos sombras con insumos reales, especificación y δ de la publicación vigente. Se reconstruyó el filtro semanal para comprobar la equivalencia, sin seleccionar otro modelo ni recalibrar δ. Los Z semanales reconstruidos coinciden a cuatro decimales en toda la historia: error máximo **0.0** para XAU y XAG. Los cinco archivos semanales (CSV XAU/XAG, state y dos walkforward) conservaron exactamente sus bytes y hashes.

| Metal | Último MDP diario | MDP-Z | Ancla COT | β | δ conservado |
| --- | --- | --- | --- | --- | --- |
| XAU | 2026-10-16 | 0.7401 | 2026-10-13 | filt | 0.995 |
| XAG | 2026-10-16 | 0.2109 | 2026-10-13 | filt | 0.999 |

Ambos CSV añaden únicamente las tres filas 14/15/16-oct; las anteriores no cambian. El nuevo test de `test_b2_metals_shadow.py` ejecuta los dos ensambladores con series controladas hasta viernes y exige que la semana termine martes y el diario viernes. También ejecuta la proyección y comprueba β filtrada en los tres días y Z del martes intacto.

Insumos: precios Yahoo GC=F/SI=F/HG=F hasta 16-oct, RY USD del repo, FRED DGS10/EFFR hasta 15-oct, DTWEXBGS hasta 9-oct, WRESBAL/WTREGEN hasta 14-oct y deuda diaria del Tesoro hasta 15-oct. La cadena TLS de Treasury falló con el almacén de confianza Python local; se leyó el **mismo endpoint HTTPS** con curl del sistema y verificación de certificado activa, conservando el JSON público en la evidencia. No se desactivó TLS ni se cambió la fuente o el código de transporte productivo. La regeneración publica solo las dos sombras y su state; el manifiesto semanal no se reescribe.

## 2. AUD: varianza cero y numeración coherente

`band_z` acepta opcionalmente la serie que produjo Z. Si Z es None y hay al menos 60 observaciones dentro de la ventana 252 con desviación <1e-9, limpia la banda a `""`. Si faltan datos, conserva el estado previo. Se suministra la serie en suelos y diferenciales nominales/reales; el MDP escalar conserva su comportamiento anterior ante datos ausentes. Los umbrales de entrada/salida no cambian.

La reproducción real de AUD sigue teniendo 252 spreads cero; después de evaluar suelos, `zband` pasa HI → vacío. El estado y brief se regeneran sin enviar Telegram ni avanzar el ledger de entregas CTF. El diff de state solo contiene esa banda y la hora de actualización. La bala actual de §02 es `CAD +2.0 bp TIGHT · NZD +3.0 bp TIGHT`.

Telegram y brief adoptan §02 política/suelos, §03 TP, §04 oro, §05 plata, §06 opciones y §07 factor. COT dice `COT · fuera de la página`; salud ya no se presenta como la antigua §05. Se retira `gates.ffva`, sin consumidor activo en el HTML del dashboard. El resto de gates conserva su contrato.

El brief lleva `section_numbering: "00-07"`. `briefSec(sec, numbering)` deja intactas las etiquetas de ese contrato y mantiene la traducción anterior para briefs sin marcador. Esto impide que el oro §04 se renumere una segunda vez como §03. Se conservan pruebas de ambos formatos.

**Huella OWNER de `scripts/dashboard_alerts.py`:**

- Anterior: `56e46654e7cc86b0be45e9d5d1792476169db3426d38d12aacb93d50056b5ebb`.
- Nueva: `e7554e4451330ff9b8e47e1d17d23da19c11a349f311a4665b91b5d6463ca477`.

Se actualizan los seis contratos de prueba que repetían esa huella: exclusions, freshness F3, U1, U2, C1 y C2. Los otros seis archivos congelados permanecen intactos. En `test_feed_provenance.py` cambian únicamente las huellas de los dos ensambladores B2; los demás cuerpos de cálculo protegidos conservan sus huellas.

## 3. Mantenimiento: turno nominal, fallos y propuestas OWNER

- `watch_cadence` acepta `--nominal-date` o `--event-at` + `--cron`. Para los crons diarios toma la última hora nominal anterior a la creación del run en UTC y obtiene su día de Malabo. Los dos workflows consultan `createdAt` del run con Actions de solo lectura. El run del 12-oct a las 23:38Z corresponde así al turno 12-oct 17:17Z y permanece activo, aunque ya sea 13-oct en Malabo o el runner arranque más tarde. Un timestamp inválido falla visiblemente; no decide que el turno está apagado. Manual sigue siendo explícito.
- `publish` comprueba también el outcome de Claude. Failure/cancelled/vacío impiden AUTO y no se registran como NO_CHANGES. Un bundle existente sin head verificado se conserva para REVIEW, nunca para integrar automáticamente.
- `maintenance_due --record` admite `agent_outcome`, `verification` y `gate_decision`, separando estado del agente, verificación y resultado de integración. No se inventan intentos productivos en el JSON durante estas pruebas.
- `agent_recovery.busy` devuelve ocupado ante error de gh, JSON vacío/malformado, esquema inesperado, estado desconocido o error de proceso, y lo explica en stderr. No se despacha otra ejecución cuando su ocupación es desconocida.
- `policy_evidence.json` sigue siendo OWNER. Se hace explícito en DENY y en el motivo de revisión. Si el agente modifica el JSON y olvida el último commit, Package work lo confirma por ruta exclusiva antes de crear el bundle. No arrastra otros archivos staged. La prueba crea un repositorio git temporal real, ejecuta el bloque del workflow, comprueba el bundle y verifica que la compuerta exige OWNER. Las instrucciones exigen URLs, contraste con serie/decisión y acta antes de proponer renovación.

Límite declarado: la API de eventos schedule no aporta aquí una fecha nominal original inequívoca para retrasos de más de un periodo completo. La derivación cubre el cruce de medianoche comprobado y las colas del runner; `--nominal-date` permite fijar explícitamente el turno cuando se conozca. El token de creación solo lee Actions y, si esa lectura falla, el job falla en lugar de saltarse silenciosamente la revisión.

## 4. Evidencias oficiales renovadas

Consulta real realizada en esta sesión. `verified_at=2026-10-17T22:56:21Z`; caducidad máxima escrita 24-oct a la misma hora, limitada además por el monitor a una decisión posterior. Ninguna tasa se modifica y todas coinciden con la serie vigente del checkout. Las próximas decisiones coinciden con `data/manual/cb_meetings.csv`.

| País | Tipo vigente | Decisión / efectividad contrastada | Próxima decisión |
| --- | --- | --- | --- |
| GB | 3.75% | Publicación 17-sep; tipo mantenido | 05-nov-2026 |
| JP | 1.25% | Decisión 18-sep; efectiva 24-sep según nota del PDF | 30-oct-2026 (segunda jornada) |
| CH | 0.00% | Decisión 24-sep | 10-dic-2026 |
| AU | 4.60% | Decisión 29-sep; efectiva 30-sep según tabla de cash rate | 03-nov-2026 |
| NZ | 2.75% | Decisión 02-sep; serie efectiva ya registrada | 28-oct-2026 |

Fuentes comprobadas:

- GB: [comunicado y actas de septiembre](https://www.bankofengland.co.uk/monetary-policy-summary-and-minutes/2026/september-2026), [tipo vigente y próxima decisión](https://www.bankofengland.co.uk/monetary-policy/the-interest-rate-bank-rate).
- JP: [comunicado PDF, página 1 y nota de efectividad](https://www.boj.or.jp/en/mopo/mpmdeci/mpr_2026/k260918a.pdf), [índice de decisiones 2026](https://www.boj.or.jp/en/mopo/mpmdeci/mpr_2026/index.htm), [calendario](https://www.boj.or.jp/en/mopo/mpmsche_minu/index.htm).
- CH: [comunicado 24-sep](https://www.snb.ch/en/publications/communication/press-releases-restricted/pre_20260924), [índice de decisiones](https://www.snb.ch/en/the-snb/mandates-goals/monetary-policy/decisions), [calendario](https://www.snb.ch/en/services-events/digital-services/event-schedule).
- AU: [comunicado 29-sep](https://www.rba.gov.au/media-releases/2026/mr-26-27.html), [tabla de tipos efectivos](https://www.rba.gov.au/statistics/cash-rate/), [reuniones](https://www.rba.gov.au/schedules-events/board-meeting-schedules.html).
- NZ: [comunicado septiembre](https://www.rbnz.govt.nz/news-and-events/news/2026/09/ocr-increased-by-25-basis-points-to-2-75), [historial de decisiones](https://www.rbnz.govt.nz/monetary-policy/monetary-policy-decisions), [OCR vigente y próxima actualización](https://www.rbnz.govt.nz/monetary-policy/about-monetary-policy/the-official-cash-rate).

RBNZ fue accesible mediante la herramienta de consulta de esta sesión. Eso no demuestra recuperación del descargador BIS ni del ejecutor de Actions. `health.json` pasa de cinco incidencias a **una**: GB/JP/CH/AU quedan VERIFIED_POLICY; NZ conserva ERROR y LAST_FETCH_FAILED. Su evidencia se renueva, pero no se borra el fallo de ingestión ni se certifica un arreglo inexistente.

`AUD_RY.frequency` cambia daily → weekly, coherente con AUD_F2 y la [frecuencia oficial de publicación RBA](https://www.rba.gov.au/statistics/tables/frequency-statistical-releases.html). Presupuestos, umbrales y calendarios no se amplían.

## 5. Pruebas y alcance

Se añaden regresiones ejecutables de B2, histéresis completa/insuficiente, brief, reloj nominal, JSON de ocupación, fallo de Claude, trazabilidad y empaquetado OWNER. Las pruebas históricas de `health_evidence` cargan una fixture fijada al 4-oct, pues su reloj es de ese día; ya no dependen de la evidencia viva renovada el 17-oct. Las mismas aserciones de caducidad y discordancia se conservan. Otra prueba contrasta la evidencia viva con series y calendarios oficiales y comprueba que un ERROR de ingestión no se oculta.

Validación final completada al comenzar el **18-oct-2026 en Malabo**: `validate_smoke.sh` termina con **rc 0**, **875 tests en 229.423 s, 0 fallos y 6 omitidos** (omisiones ya existentes). Pasan las cuatro equivalencias S01B, YAML, 125 filas de registry, JavaScript (incluidos 11 scripts inline), dry-run, imports y Chromium: **7 paneles pintados, sin excepciones ni errores visibles**. Entorno local: Python 3.12.14, Node 24.18 y Playwright 1.62.1; no sustituye una ejecución remota bajo las versiones declaradas en CI.

El log completo y las comprobaciones de aplicación de los dos parches constan en el informe de validación entregado junto al parche. El código se prepara localmente para revisión; no se envía Telegram, no se despachan workflows de producción y no se publica a main ni Netlify durante este lote.

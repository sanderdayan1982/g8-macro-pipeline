# ACTA_S01B — §01-b «lectura a 22 sesiones + CTF» · construcción autorizada 19-sep-2026

Estado: **CONSTRUIDO, NO ACTIVADO**. Se activa cuando Sander suba el lote y el workflow corra; desde ese
día empieza C (63 sesiones mín., ≥ 3 encendidos por divisa como mínimo de observación, sin aceptación
automática). RTF10 v3.6.4 y sus 12 alertas siguen operativos hasta que C pase y D se ejecute.
Especificación de referencia: ESPEC_S01B_v1.md v1.1 (en lote_rtf10_v364/). Decisiones D1–D5 confirmadas por Sander el 19-sep.

## Decisiones fijadas (no se tocan durante C)
- D1 P = 80 sobre ΔTP₂₂ **firmado**, expanding hasta t−1, ≥ 252 obs.; **ΔTP > 0 obligatorio**; histéresis p60; apagado si ΔTP ≤ 0.
- D2 ΔFX₂₂ = ln(S[t]/S[t₀]) ≤ −0,5 % en nativo XXX/USD (BCE); USD contra el factor f de la §10 (Σ f ≤ −0,5 %).
- D3 CHF sin CTF; conserva la lectura de componentes (NOWCAST marca la fila).
- D4 C: 63 sesiones mín. + ≥ 3 encendidos, por divisa; divisas sin evidencia se declaran.
- D5 §04: la bandera TP/NOM ≥ 0,60 (salida 0,55) pasa a «TP/NOM supera el umbral configurado», sin «fiscal» ni
  referencia a RTF10; 0,60/0,55 etiquetados HEURÍSTICOS. Nota cuando nominal < 0,5 % (incluidos negativos) (cociente poco informativo).

## Los tres detalles de implementación pedidos antes del primer run
1. **Una evaluación por sesión, alertas sin duplicados.** Sesión = fecha UTC si es día hábil TARGET. s01b.py
   solo evalúa si `data/s01b/log/<t>.json` no existe; las corridas posteriores del mismo día no recomputan
   ni avanzan contadores (recuperan estado, libro de eventos y S01B.json desde los logs). El EVENTO
   (OFF→ON) lo escribe s01b.py en el log y en `data/s01b/events.jsonl`; el ENVÍO lo hace
   dashboard_alerts.py v2.4 (`check_s01b`) con su propio libro `state["s01b"]["sent"]` con clave
   CCY:fecha:tipo. Probado: mismo evento en tres corridas → un solo mensaje. Primer run = baseline: los
   estados iniciales se marcan `baseline: true` y se anuncian como "estado inicial", no como encendido.
2. **Replay reproducible.** `data/s01b/snap/<t>/` guarda los 8 ACM (.gz), `calendar.txt.gz` (calendario
   TARGET completo del canonical) y `fx_window.json` (los 22 log-retornos por divisa y f realmente usados,
   fecha efectiva del FX). El log guarda `state_before` por divisa, θ/θ_off/θ₉₅, n de la población, los dos
   extremos con sus as-of, versiones y SHA-256 de cada entrada. `s01b.py --replay <t>` reconstruye las
   entradas desde `inputs.json.gz` y compara todas las filas y eventos; el snap incluye copia del motor
   y se verifican sus SHA-256. La suite comprueba replay exacto y rechazo de snapshots alterados. La histéresis se reconstruye porque state_before va en el log.
3. **Publicación atómica y control de disponibilidad.** Orden: snap (staging → os.replace del directorio)
   → log (temp + os.replace) → events.jsonl → state.json → S01B.json (último, con el run_id del log). Un
   corte a mitad deja S01B.json antiguo; la siguiente corrida detecta run_id ≠ log y lo reconstruye
   (probado borrando S01B.json). Disponibilidad del FX: la sesión t solo se evalúa cuando la última fecha
   del canonical del BCE == t; si no, `PENDING_FX` y exit 0 (corridas de 15:30/17:30 UTC). El paso de
   18:00 UTC usa `--final`: si el FX de t sigue sin publicarse, mantiene t₀ y lee el extremo final as-of disponible, con bandera
   DESFASE (ESPEC §3.3), una sola vez. Atraso > 3 días hábiles en cualquier extremo → NO_DATA.

## Lo que hace cada fichero del lote
| Fichero | Destino en el repo | Qué hace |
|---|---|---|
| s01b.py | scripts/ | motor §01-b (stdlib): evaluación, estado de dos ejes, log, snap, replay, anexo retrospectivo |
| test_s01b.py | tests/ | 8 pruebas de la máquina de estados y ventanas (NO_CAL, umbral a t−1, ΔTP>0, ventana (t₀,t], histéresis p60, racha FX 3, disponibilidad congela la señal, calidad/CHF, DESFASE) — 8/8 |
| patch_s01b.py | raíz (ejecutar una vez) | parchea in situ dashboard_alerts.py → v2.4, docs/index.html (bloque §01-b + fila DQM S01B), daily_update.yml (paso `s01b.py --final` antes de alerts), usd_factor.yml (paso `s01b.py` + add condicional), sources/registry.csv (fila S01B, consumidores de *_ACM). Idempotente; `--check` no escribe. |
| ESPEC_S01B_v1.md · ACTA_S01B.md | docs/actas/ | especificación v1.1 y esta acta |
| resultado_anexo_retrospectivo.txt | docs/actas/ | anexo §7, etiqueta RETROSPECTIVO |

## Anexo §7 — replay RETROSPECTIVO (cargas ACM de hoy; no valida nada)
Regla completa aplicada a la historia 2021-09 → 2026-09-18 con estado encadenado y FX del canonical.
| Episodio | previstos | cubiertos | CTF ON | NO_CAL | 1er ON |
|---|---|---|---|---|---|
| abr-2022 USD | 19 | 19 | 0 | 19 | — (aún sin 252 obs.) |
| oct-2023 USD | 22 | 22 | 0 | 0 | — |
| abr-2025 USD (fiscal) | 16 | 16 | **11** | 0 | 2025-04-10 |
| sep-2022 GBP | 16 | 16 | 0 | 16 | — (NO_CAL) |
| mar-2020 USD | 17 | 0 | — | — | sin ACM diario |
| mar-2023 USD | 16 | 16 | 0 | 0 | — |
| ago-2024 USD | 12 | 12 | 0 | 0 | — |
| may-2026 JPY | 20 | 20 | **0** | 0 | — |
| ago-2024 JPY (ep. 9) | 17 | 17 | 0 | 0 | — (TP_SAFE no es objeto de §01-b) |
Encendidos OFF→ON en toda la historia: USD 8 · EUR 8 · GBP 5 · JPY 26 · AUD 11 · NZD 10 · CAD 12.
Lectura sin veredicto: abr-2025 USD se enciende el 10-abr con la regla completa. **may-2026 JPY no se
enciende**: ΔTP +25…+39 bp (muy por encima de θ ≈ 13,5) pero el yen NO cayó en las 22 sesiones
(ΔFX₂₂ entre +0,2 % y +2,1 %); RTF10 lo etiquetó TP_FISCAL por la correlación a 63 sesiones (régimen),
no por el cambio puntual. Es la primera discrepancia esperada de C (régimen vs cambio puntual, ESPEC §5
tabla 2), pre-registrada aquí antes de observarla en vivo. JPY concentra 26 encendidos con θ bajo (≈ 13 bp):
se anota para la tabla de sensibilidad p75/p90 de C, sin tocar nada.
Ensayo retrospectivo con fecha 18-sep-2026 (NO inicia C; no es un run publicado): NZD ON (ΔTP +25,2 ≥ θ 24,1; NZD −2,92 %), resto OFF, CHF NO_QUAL
(cola NOWCAST del ACM CHF). En producción, cada divisa se anuncia como estado inicial al obtener su primera evaluación válida.

## Retención
Los snapshots de C no se borran nunca; al terminar C se comprimen por meses en `data/s01b/archive/` o se
mueven a un release del repo. El tamaño final debe medirse con los snapshots completos de producción; 150 KB × 63 serían 9,45 MB, no 3,5 MB.

## Integración local revisada — 19-sep-2026

Motor v1.1. Las ocho pruebas del lote se conservan y se añaden nueve regresiones operativas.
Se corrigen ventana FX con DESFASE, rechazo de FX antiguo, calendario TARGET completo,
baseline por divisa, recuperación tras publicación interrumpida y reintento de eventos no enviados.
Los dos productores comparten el grupo de concurrencia de Actions; Validate ejecuta las 17 pruebas.
El envío se confirma solo con respuesta satisfactoria de Telegram. Un fallo ambiguo de transporte
puede causar repetición al reintentar; no se promete entrega exactamente una vez.
Los números del anexo anterior proceden del lote original v1.0; se conservan como antecedente
y no son una nueva validación del motor corregido. C empieza con el primer log prospectivo publicado.

Ensayo aislado con los CSV del clon (18-sep): replay exacto de todas las filas y eventos, sin publicación
ni Telegram. Snapshot completo: 992.528 bytes; orden de magnitud 63 MB por 63 sesiones, variable.

## Enmienda E1 — patas de contexto completadas · 21-sep-2026 (sesión 1 de C) · lote v2 tras revisión

**Alcance.** Solo columnas de CONTEXTO (Δ2Y, ΔBE, RESID) y sus metadatos. No cambia ninguna entrada, umbral, horario,
regla ni salida del detector (D1–D4 intactas). **C no se reinicia**; el BASELINE del 21-sep y su log no se tocan. La enmienda
rige desde la primera sesión publicada con `s01b v1.2` (campo `version` del S01B.json y del log). Sin reconstrucción retrospectiva.
Las sesiones publicadas con v1.1 se reproducen con el motor archivado en su snap (`--replay` lo exige por SHA); las de v1.2 con el actual.

**Diagnóstico revisado contra el repo.**
| Campo | Causa del hueco | Reparación | Fuente | Fecha disponible | Limitación restante |
|---|---|---|---|---|---|
| AUD Δ2Y | `acm_g8.py` ya descargaba el 2Y (DAILY_SOURCE_EXTRA, RBA F2 daily `FCMYGBAG2D`) sin persistirlo; §01-b no lo tenía en `Y2_FILES` | `acm_g8.py` v2.6 `persist_extra()` → `data/AUD_NOM_2Y.csv` (Date,Value,Source) desde el MISMO conector; `s01b.py` v1.2 lo lee | RBA tabla F2 daily | primera pasada de `daily_update.yml` con v2.6 (o `--live` en el Mac) | cadencia de refresco de la tabla F2 por confirmar; los días fuera de la tolerancia (3 d.h.) la celda dice «atrasado <fecha>»: se muestra, no se rellena |
| CAD Δ2Y | ídem: Valet `BD.CDN.2YR.DQ.YLD` descargado, no persistido, no conectado | `data/CAD_NOM_2Y.csv` por v2.6; `s01b.py` v1.2 lo lee | Banco de Canadá, Valet | ídem | T+1 |
| NZD ΔBE | `RY_G8_NZD.csv` (BE10 = NOM10 − REAL10, IIB interpolados a 10Y, `real_yields_g8.py`) existía; §01-b lo excluía de `BE_FILES` | NZD en `BE_FILES` (col. BE10); etiqueta `IIB_PROXY_THIN_MARKET` | RBNZ B2 (fetch local Mac) | **ya**: 2.180 obs., último 17-sep; congelado 21-sep → **+18,88 pb** | proxy de mercado fino, etiquetado |
| CHF Δ2Y, RESID | `CHF_SPOT_2Y/10Y.csv` conectados; terminan 31-08-2026 (curva SNB mensual) → 15 d.h. de atraso > tolerancia | ninguna sobre el dato: se muestra «atrasado 2026-08-31» con el último valor y `SNB_CURVE_MONTHLY`. No se fabrica un 2Y diario desplazándolo con el 10Y | SNB cubo rendeiduebd | publicación SNB de septiembre (1-oct) | mensual por construcción; CHF sin emisión CTF (D3) |
| CHF ΔBE, ΔFX | exclusión deliberada (sin mercado de linkers; D3) | etiqueta `NO_MARKET` / «sin feed», distinguible de un atraso | — | — | por diseño |

**Cambios de código (lote v2, con las cuatro correcciones de la revisión).**
- `scripts/acm_g8.py` v2.6 `persist_extra()`: valida ANTES de tocar disco (≥ 1 fila, fechas parseables, valores finitos); FUSIONA con el CSV
  existente (unión de fechas, la descarga fresca gana en el solape) para que una respuesta parcial o truncada nunca acorte la historia; ante
  respuesta vacía/inválida conserva intacto el último archivo válido y lo registra; nunca lanza excepción. ACM (panel, estimación, `ACM_G8_*.csv`) idéntico.
- `scripts/s01b.py` v1.2: `Y2_FILES` += AUD, CAD; `BE_FILES` += NZD; `context_status()` elige la última observación con fecha ≤ sesión (no `ser[-1]`) y
  distingue **OK** (Δ calculado) / **STALE** (dato atrasado: extremo final fuera de la tolerancia) / **SHORT** (historia insuficiente: extremo final
  bien, sin dato válido en t₀ — un dato de hoy sin historia es SHORT, nunca «atrasado hoy») / **NONE** (sin observación ≤ sesión, sin feed o sin mercado).
  `context_asof` conserva su forma; el Δ sigue sujeto a MAX_LAG_BD en ambos extremos. `evaluate_ccy` no cambia fuera del bloque de contexto.
- `docs/index.html` §01-b: Δ2Y/ΔBE en blanco → «atrasado <fecha>» (ámbar) / «sin historia» / «sin feed», con tooltip de procedencia y último dato;
  RESID en blanco por falta de ΔFIT → «sin ACM» (no se atribuye al nominal); DQM filas `AUD_NOM_2Y`, `CAD_NOM_2Y`. `sources/registry.csv`: dos filas.
- `tests/test_s01b_e1.py` (12 pruebas; entra en el discover de Validate) y `tests/equiv_s01b_e1.py` (+ modo `--synthetic`), ambos en `validate.yml`.

**Qué queda comprobado y con qué.**
| Nivel | Qué | Resultado |
|---|---|---|
| Datos reales (snapshot congelado del 21-sep + `RY_G8_NZD.csv` del repo) | equivalencia motor v1.2 vs log v1.1 | EQUIVALENT: todas las filas y eventos idénticos salvo `NZD.d_be` None → +18,88 y su `context_asof[be]`; `context_last` nuevo en las 8 |
| Datos reales (repo) | CHF nominal/2Y `context_last` | STALE 2026-08-31, 15 d.h., 2Y 0,078 % / 10Y 0,469 %, `SNB_CURVE_MONTHLY`; BE NONE `NO_MARKET` |
| Datos de prueba | P1–P4 `persist_extra` (vacío / NaN / parcial-fusión / primera escritura) | 4/4: el CSV válido no se toca ante respuesta vacía o inválida; la fusión conserva la historia |
| Datos de prueba | C1–C4 `context_last` (STALE / SHORT hoy sin historia / NONE vacío y serie posterior a t / OK y RESID sin ACM) | 4/4 |
| Datos de prueba | X1 AUD/CAD sintéticos: `persist_extra` → `read_series` → `evaluate_ccy` → Δ2Y = (v_t − v_t₀)·100; X2 la conexión no mueve ningún campo del detector | 2/2 |
| Datos de prueba (snapshot real + AUD/CAD sintéticos) | `equiv_s01b_e1.py --synthetic` | EQUIVALENT (AUD/CAD Δ2Y +6,4 sintético, resto idéntico) |
| Suite previa | `tests/test_s01b.py` | 8/8 · discover CI: 20 pruebas, 0 fallos, 2 saltadas (`--live`) |
| Datos reales (revisión externa de Sander, 21-sep) | CAD Valet: descarga real → CSV → Δ2Y | pasa. AUD: comprobado con el archivo F2 oficial; la descarga por Python falló por certificados en el entorno de revisión (no se desactiva TLS: usar un Python con bundle CA/certifi, no `verify=False`) |
| **Pendiente de la primera ejecución** | AUD/CAD 2Y publicados por Actions, fechas y columnas visibles en la §01-b | primera pasada de `daily_update.yml` con v2.6; verificación posterior por Claude sobre el remoto (CSV, fechas, S01B.json v1.2, columnas). `--live [--session]` da dos veredictos separados: «descarga y cálculo correctos» y «vigente / vigencia pendiente para esa sesión TARGET» |
| **Pendiente de publicación externa** | CHF 2Y/10Y septiembre | SNB, 1-oct |

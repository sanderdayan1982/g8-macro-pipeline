# ACTA UF-1 — SECCIÓN §10 "FACTOR USD · RESIDUALES · LIBRO"
**G8 Macro Pipeline · 17-sep-2026 · estado CONTEXT permanente · v1.0**

Origen: brief v1 (17-sep-2026) triangulado con Kimi K3, Cursor y ChatGPT Astra; especificación consolidada v2 aprobada por el operador ("procede", 17-sep-2026). Este acta congela las decisiones. Cualquier cambio de las secciones 2-5 exige un acta nueva; los números de la sección 6 se calculan una sola vez y no se recomputan.

## 1. Qué es y qué no es

Capa de **composición y riesgo**: cuánto dólar hay en el movimiento de cada pata, qué le queda a cada divisa sin el dólar, si el mercado está en régimen común o idiosincrásico, cuánto riesgo común acumula el libro y qué le hace una operación candidata al libro. **No vota**, no entra en la confluencia ni en el ranking jefe, no emite dirección (números con signo y bandas sí; adjetivos no). Promover el rango de residuales a señal exigiría un acta de gate nueva sobre la ventana virgen.

## 2. Datos

| Decisión | Valor congelado | Motivo |
|---|---|---|
| Fuente primaria | Tipos de referencia diarios del BCE (SDMX `EXR/D.{USD,GBP,JPY,CHF,CAD,AUD,NZD}.EUR.SP00.A`) | Gratuita, diaria, un solo concierto (~14:15 CET) para las 7 patas: sin basis entre patas por construcción |
| Convención nativa | `P_i` = dólares por una unidad de i; `P_EUR = EURUSD`, `P_i = EURUSD / (i por EUR)` | JPY, CAD, CHF invertidos respecto a la cotización de mercado; el display no reconvierte |
| Calendario y reloj | Calendario TARGET; el "día" es de concertación a concertación (14:15 CET); bandera **DESFASE_US** los días con NFP (regla primer viernes) o fechas del fichero manual `data/usd_factor/us_calendar.csv` | El dato USA de las 14:30 CET entra en el retorno del día siguiente (Cursor, Astra) |
| Fechas comunes | Factor NA si falta cualquier pata; filas idénticas a la anterior en las 7 patas se eliminan (arrastre, no observación); festivo TARGET = hueco, nunca retorno cero ni relleno con futuros | Astra B.3 |
| Twins | Futuros CME (`data/futures/canonical`, 7 raíces, front por OI, retorno solo con el mismo símbolo): corr 252 con y sin días DESFASE_US · `DTWEXAFEGS` FRED semanal (opcional) · β EWMA vs OLS-252 · cesta equi vs inv-vol · correlación mínima entre factores LOO | **Números, sin pasa/falla** (Cursor, Astra); el twin de futuros no condiciona el cálculo y se detiene si el colector Databento está pausado |
| Identidades de ingestión (sí pasa/falla) | fechas ordenadas y únicas; tipos > 0; `P_i × (i por EUR) = EURUSD` con error < 1e-9; saltos > 10 % listados, no corregidos | Aritmética, no juicio |
| Histórico | Desde 1999-01-04 (primeras 60 sesiones = inicialización de la EWMA); calibración 2000-01-03 → 2023-12-31; 2024+ reserva no examinada | Coherencia con el resto del sistema |
| Libro | `data/book.csv`: snapshot de exposiciones lineales FX en nominal USD actual (`id,fecha_snapshot,par,lado,nominal_usd`); no contabilidad ni margen; instrumentos no lineales excluidos; `LIBRO DESACTUALIZADO` si el snapshot tiene > 5 sesiones | Astra 8.1 |
| Candidato | `data/candidate.csv`, mismo formato | — |

## 3. Cálculo

| Magnitud | Definición congelada | Origen de la decisión |
|---|---|---|
| Factor | `f_t = −(1/7) Σ_i r_i,t`, pesos 1/7 congelados; f > 0 = dólar más fuerte | Unánime. Inversa de vol: solo diagnóstico (corr 63d en DIAG). Turnover BIS: descartado |
| Σ | EWMA recursiva, λ = 0,97, media cero, inicializada con la covarianza muestral de las 60 primeras sesiones, **sin truncar** (semivida ≈ 22,8 s., tamaño efectivo ≈ 66). Se deja de llamar "63d" | Astra 7; λ = 0,97 declarada como decisión de estabilidad (RiskMetrics diario usa 0,94; Kimi 7) |
| β | `β_i = −Cov(r_i, f)/Var(f)` desde Σ_{t−1}; **factor completo**, α = 0; signo: β > 0 = la divisa se deprecia cuando f sube. Identidad `Σβ_i = 7` por construcción: no es sesgo individual y se escribe en el pie | Astra 2 (LOO incoherente con la matriz: cada β miraría un factor distinto). Cursor 2 (α = 0, Merton 1980). Un solo reloj: Cursor 3 — **apartado del quórum 2-1** porque la carga del libro sobre la cesta ya es la β EWMA agregada; OLS-252 sin intercepto queda en canonical como twin |
| β LOO | `β_i^{LOO}` contra `f_(−i)` desde Σ_{t−1}: solo diagnóstico en canonical; correlación mínima dos a dos de los 7 factores LOO en DIAG | Kimi 2 |
| Residual | `ε_i,t = r_i,t + β_i,t−1 · f_t`; `E_h = Σ ε` en 5/21/63; **21 = horizonte de mesa** (tabla); 5/63 en canonical y tarjeta del par | Unánime; 21 es convención de coherencia, no óptimo (Kimi 4) |
| z21 | `(E21 − media)/desv` de los **252 valores anteriores** (excluye el actual); descriptivo: ventanas solapadas, no probabilidad gaussiana | Astra 4 |
| Rango | 1 = mayor E21; empates rango medio; z y rango separados, nunca combinados | — |
| Dispersión | `D = sd(E21)` transversal, **denominador 7** (universo completo); MAD en canonical; **UN-NOMBRE** si `max(E21²)/ΣE21² > 0,5`, con la divisa en DIAG | Astra 5 (sd, n=7 población); Kimi 5 (UN-NOMBRE) cubre la objeción al outlier de Cursor 5 |
| Bandas | Percentil causal 252 de D: ≤ p30 COMÚN · p30-p70 MIXTO · ≥ p70 DISPERSO. **Convención simétrica 30/40/30 pre-registrada, no umbral empírico**; histéresis 3 sesiones consecutivas; percentil continuo siempre visible; sin frases interpretativas en el dashboard | Cursor 5; Astra 5 (retira interpretación); histéresis 2-1 |
| S | Cuota de varianza del primer componente de Σ_t; **sin bandera de discordancia con D** (miden objetos distintos y pueden subir a la vez) | Astra 5 |
| PCA | Sobre Σ EWMA (retornos brutos); solo S y cargas de PC1 en el dashboard, signo anclado a Cov(PC1, f) > 0; PC2/PC3 en canonical; **ROTACIÓN** si cos(u1_t, u1_t−21) < 0,8 (convención); componentes numerados, nunca bautizados | Cursor 7, Astra 7 (PC2/PC3 fuera); Kimi 7 (rotación) |
| Matriz | 21 cruces: `leak = β_A − β_B` (> 0 ⇒ largo A/B se comporta como corto USD) y `σ_cruce = √(Σ_AA + Σ_BB − 2Σ_AB)`; ρ y `E21_A − E21_B` en la tarjeta del par. Pie: `|leak|` es carga de retorno por unidad de factor, no riesgo en dólares. Color de celda por magnitud, nunca por signo | Cursor 10 |
| Percentil del nivel | **Eliminado** del dashboard (valoración, insinúa dirección); `I_level` queda en canonical | Cursor 10, Astra 4 |

## 4. Libro y PRE-TRADE

`q` = exposiciones netas de las 7 patas no-USD en USD; `w = 1/7`; Σ la EWMA del día.

| Magnitud | Definición | Origen |
|---|---|---|
| Cubos | 8; `Σ cubos = 0` es identidad de la representación, no validación | Astra 8.1 |
| V, σ | `V = q'Σq`, `σ = √V` | — |
| VaR | **VaR 95 % 1d paramétrico** `1,645·σ` y **VaR 95 % 1d histórico** 252 s. (mismo nivel, comparables); ES 97,5 % paramétrico `2,338·σ` solo en JSON; ES histórico eliminado (≈ 6 observaciones) | Astra 8.3, Cursor 8 |
| COLA | `|param − hist|/param > 0,30`: **convención** pre-registrada (2-1 frente a Astra); se publica también la diferencia en USD | Kimi 8, Cursor 8 |
| Carga / cuota sobre la cesta | `carga = −q'Σw/(w'Σw)`; `cuota = (q'Σw)²/(w'Σw · V)` ∈ [0,1]. Rotulada "cuota sobre la cesta USD", **nunca** "% del riesgo que es dólar"; sustituye a la cuota PC1 del libro (que queda en JSON) | Astra 8.2, Cursor 7 |
| Concentración | Contribuciones de Euler `RC_k = q_k'Σq/V` por posición (suman 1; negativas = cobertura); sustituye a "cuota del par mayor" | Astra 8.4 |
| Estrés (3 líneas, sin umbral) | (1) cesta ±1 %: `r_i = −β_i · 0,01`; (2) **DOLLAR+**: mismo cálculo con el máximo de \|f\| a 1 sesión en 2000-2023 (sección 6); (3) cota `σ_max = Σ|q_i|σ_i`. **Retirado** el estrés "correlación → 1" (para un libro largo/corto no es conservador: corr +1 anula el riesgo del spread) | Astra 8.4, Cursor 8 |
| Carry | **Eliminado** (el tipo oficial no es el carry ejecutable; Du-Tepper-Verdelhan 2018) | Unánime |
| Backtest | VaR/ES son previsiones: se archivan `book_snapshots/YYYY-MM-DD.csv` y `forecasts.csv` desde el día 1; backtest **PENDIENTE** hasta ≥ 250 previsiones | Astra 8.3 |
| PRE-TRADE | Por candidato, contra el libro (o libro vacío): Δcubos; `ΔV = 2q_c'Σq + q_c'Σq_c`; ΔVaR; `ρ = q_c'Σq/√(V_c·V)`; leak `β_A − β_B` (USD: β = 0 por numerario); σ del cruce; `E21_A − E21_B`; Δcuota; P&L del candidato en los tres escenarios | Unánime (la pieza que faltaba) |

## 5. Alertas y presentación

Alertas Telegram (`dashboard_alerts.py` v2.3, `check_factor`), solo por cambio y con histéresis Schmitt, todas **convenciones** pre-registradas (2-1 frente a Astra): cambio de banda de dispersión (ya con histéresis de 3 sesiones); |z21| del factor cruza 2 (sale en 1,5); libro con cuota sobre la cesta > 0,80 (sale en 0,70); COLA ON/OFF; libro DESACTUALIZADO. Bullet §10 en el brief §00.

Dashboard §10 (tecla `u`): cabecera (f 5/21/63 con z; banda + percentil + UN-NOMBRE; S con ROTACIÓN) · tabla 7 (β, Δβ21, E21, z21, rango, σ, PC1, β OLS) · matriz 7×7 (leak / σ) · libro · PRE-TRADE · DIAG · pie de fórmulas. `SIN LIBRO` / `SIN CANDIDATO` cuando no hay fichero. El navegador solo pinta.

## 6. Números congelados (una sola vez)

`data/usd_factor/frozen_2000_2023.json` se escribe en la **primera pasada completa con fuente BCE** (n ≥ 5000 sesiones en 2000-2023) y nunca se recomputa: máximo de |f| a 1 sesión y su fecha (shock DOLLAR+), desviación de f, mediana de la corr 63d cesta equi/inv-vol, mediana de S, mediana de D, días por banda y frecuencia de UN-NOMBRE en la ventana de calibración. Hasta que exista, el panel muestra DOLLAR+ "sin congelado". Registrar aquí los valores cuando el workflow los escriba:

| Magnitud | Valor | Fecha de cálculo |
|---|---|---|
| máx \|f\| 1d (2000-2023) | _pendiente_ | |
| fecha del máximo | _pendiente_ | |
| mediana S (cuota PC1) | _pendiente_ | |
| mediana corr63 equi/inv-vol | _pendiente_ | |
| frecuencia UN-NOMBRE | _pendiente_ | |

## 7. Aceptación (`scripts/validate_usd_factor.py`)

22 pruebas, todas identidades/causalidad/calendario, ninguna de alpha: factor = −media; Σβ = 7 cada día; ε = r + β·f; β_t usa Σ_{t−1} (recomputado en 5 fechas); Σ del JSON = EWMA recomputada; Σ semidefinida positiva; reconstrucción espectral; z21 y percentil de D causales; D con denominador 7; rangos suman 28; histéresis respetada; sin días rellenados a cero; PC1 norma 1 con ancla de signo; S = λ1/traza; libro: Σ cubos = 0, V = q'Σq, Euler suma 1, cuota ∈ [0,1], ΔV de cada candidato; sin adjetivos de dirección en el JSON.

Resultado en local (17-sep-2026, fuente de prueba H.10 vía espejo GitHub, etiquetada TEST_H10 porque el sandbox no alcanza al BCE): **22/22 PASS**. El twin BCE-futuros sobre esa fuente de prueba dio corr 252 de 0,84-0,93 por divisa (relojes distintos, esperado), Σβ = 7,000, S = 0,67 (dentro del 50-70 % habitual), factores LOO corr mín 0,94. La primera pasada con BCE real la hace el workflow; el validador se vuelve a correr entonces y su salida se pega debajo.

| Pasada | Fuente | Resultado |
|---|---|---|
| 2026-09-17 local | TEST_H10 (espejo H.10, 1999-2026) | 22/22 PASS |
| _primera pasada BCE_ | ECB | _pendiente_ |

## 8. Ficheros

`scripts/usd_factor.py` v1.0 · `scripts/book_risk.py` v1.0 · `scripts/validate_usd_factor.py` v1.0 · `scripts/dashboard_alerts.py` v2.3 · `scripts/pos_g8_cot_collector.py` v1.1.0 (P3y/EXT) · `.github/workflows/usd_factor.yml` · `docs/index.html` (§10, nav `u`, registro DQM `USD_FACTOR`, §09 columna P3y) · `sources/registry.csv` (`USD_FACTOR`) · `data/usd_factor/us_calendar.csv` (manual) · `data/book.csv.example`, `data/candidate.csv.example`.

## 9. POS-G8 columna P3y (§E de la especificación)

`x = neto/OI` del mismo informe (LF en divisas, MM en metales, Futures-Only, como ya hace el colector); percentil frente a las **156 observaciones semanales anteriores** (actual excluida; empates cuentan como ≤); z sobre la misma ventana; **EXT** si ≤ p10 o ≥ p90 (banda descriptiva de dos colas por convención, ni peligro ni reversión); NA con historia < 156; disponibilidad por publicación CFTC (viernes). Excepción declarada a la regla de dato diario: COT es semanal y se arrastra. Ningún delta, estado ni umbral existente cambia. La réplica en el script Pine POS-G8 queda para un lote posterior.

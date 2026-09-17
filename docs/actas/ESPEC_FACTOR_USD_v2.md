# ESPECIFICACIÓN CONSOLIDADA — SECCIÓN §09 "FACTOR USD, RESIDUALES Y LIBRO"
**v2 · 17-sep-2026 · tras triangulación (Kimi K3, Cursor, ChatGPT Astra) · pendiente de "procede"**

Cambios respecto al brief v1 marcados con **[Δ]** y con la revisión que los origina. Donde las tres revisiones discrepan, se indica el criterio de adjudicación. Todo lo no marcado se mantiene como en v1.

---

## A. Veredicto de la ronda

| Revisión | Veredicto | Nota v1 → con cambios | Ranking de utilidad |
|---|---|---|---|
| ChatGPT Astra | VALE CON CAMBIOS | 5 → 8 | **1º** — cuatro correcciones matemáticas que cambian el diseño (LOO incoherente con la matriz, corr→1 no es estrés para un libro long/short, fórmulas de proyección sobre la cesta, semántica del libro) y una comprobación de fuente primaria (el colector no recoge 6N) |
| Cursor | VALE CON CAMBIOS | 7,5 → 9 | **2º** — un reloj de covarianza, α = 0, bandera DESFASE_US, tarjeta PRE-TRADE, escenario DOLLAR+ congelado, recorte de PC2/PC3 y del percentil de nivel |
| Kimi K3 | VALE CON CAMBIOS | 8 → 9 | **3º** — bandera UN-NOMBRE (adoptada), correlación entre factores LOO y rotación de PC1 en DIAG (adoptadas), buenas fuentes; pero mantiene LOO para la matriz y propone el estrés corr→1, que Astra refuta |

Unanimidades: cesta equiponderada congelada; BCE como primaria; 21 sesiones; carry APROX fuera; la pieza que faltaba es el solapamiento candidato-libro; ningún índice que mezcle factor, residual y dispersión.

Único punto en que esta consolidación se aparta del quórum (2-1): la ventana de la beta (sección C.2). Se explica allí.

---

## B. Datos

**B.1 Primaria:** tipos de referencia diarios del BCE, triangulados a USD en convención nativa `P_i = EURUSD / EUR_i` (dólares por unidad de i). Sin cambios.

**B.2 [Δ Cursor, Astra, Kimi] Calendario y reloj.** El calendario del sistema es el calendario TARGET del BCE. El "día" es de concertación a concertación (~14:15 CET). Se escribe en el pie de la sección y en DIAG: `FIX 14:15 CET`. Bandera **DESFASE_US** en DIAG los días en que el calendario público estadounidense (NFP, CPI, PCE, FOMC) publica después de las 14:15 CET: ese dato entra en el retorno del día siguiente. Festivo TARGET = hueco, no retorno cero, no relleno con futuros. El workflow distingue festivo / pendiente de publicación / atrasado / fallo; solo el último es ruidoso.

**B.3 [Δ Astra] Fechas comunes.** El factor es NA si falta cualquiera de las siete patas ese día; nunca se reponderan las presentes. Un intervalo que abarque varias sesiones por fallo de datos no se trata como un retorno de una sesión.

**B.4 [Δ Astra, Cursor] Twin-tests sin umbral pasa/falla.** Se publican números, no vereditos:
- BCE vs futuros CME: correlación 252d de retornos diarios por divisa, con y sin días DESFASE_US (Cursor). Astra verificó en el repo (commit d33fca1) que el colector recoge 6E/6J/6B/6A/6C/6S, **no 6N**; y que Databento no es fuente gratuita. Por tanto el twin es **opcional**, seis divisas, y no condiciona el cálculo.
- f_USD vs DTWEXAFEGS (índice Fed frente a economías avanzadas): correlación de retornos semanales alineados por fecha de publicación; divergencia de nivel esperada por pesos distintos, no es bandera.
- Identidades de ingestión (Astra): inversión exacta, triangulación cerrada, unidades, tolerancia de redondeo. Estas sí son pasa/falla porque son aritmética.

**B.5 [Δ Astra] Libro.** `data/book.csv` = **snapshot de exposiciones lineales FX en nominal USD actual**, no contabilidad. Campos: `id, fecha_snapshot, par, lado, nominal_usd`. La suma de los ocho cubos = 0 es la identidad de esta representación, no una validación de datos. Instrumentos no lineales excluidos. El panel muestra la fecha del snapshot; sin archivo, `SIN LIBRO`. **Se archiva un snapshot diario del libro** desde el primer día: es lo único que permite un backtest de VaR en el futuro (Astra).

**B.6 [Δ Astra] Candidato.** `data/candidate.csv` con el mismo formato, una o varias filas. Sin archivo: `SIN CANDIDATO`.

**B.7 Histórico** desde 2000-01-03. Calibración de todo número congelado sobre 2000-2023. **[Δ Astra]** La ventana 2024+ es reserva solo si ninguna regla se elige mirándola; los números del acta (máximo de f_USD a 1 sesión, correlaciones típicas) se calculan una vez sobre 2000-2023 y se escriben.

---

## C. Cálculo

**C.1 Factor.** `f_USD,t = −(1/7) Σ r_i,t`, pesos congelados. Acumulados 5/21/63 en logarítmico (si se muestran en %, `exp(Σ)−1`, Astra). z de cada acumulado frente a los 252 valores anteriores del mismo horizonte, con la nota de que las ventanas solapan: el z es distancia descriptiva, no probabilidad gaussiana (Astra). **[Δ Cursor, Astra] Se elimina el percentil del nivel a 1/3/5 años**: es valoración, insinúa dirección y no cambia la expresión. Queda en canonical.csv. Cesta inversa de vol: solo en canonical.csv como sensibilidad archivada (Astra); en DIAG, correlación 63d entre las dos cestas, sin umbral (Cursor).

**C.2 [Δ] Beta: factor COMPLETO, α = 0, un solo reloj.**

Tres cambios sobre v1:

1. **Factor completo, no leave-one-out** (Astra, adjudicado por matemática). `β_A − β_B` solo es la sensibilidad del cruce A/B a un factor si las dos betas se estiman contra el mismo regresor; con LOO cada beta mira un factor distinto y la diferencia mide otra cosa (contraejemplo de Astra: siete retornos incorrelados con Var(A)=4, resto 1: todas las betas LOO son 0, mientras contra el factor completo β_A=2,8, β_B=0,7). Kimi y Cursor tenían razón en que el factor completo arrastra la beta media a 1 por construcción (Σβ_i = 7 con OLS e intercepto); esa identidad se escribe en el pie y no se interpreta como sesgo individual. La beta LOO pasa a canonical.csv como diagnóstico, con la correlación dos a dos de los siete factores LOO en DIAG (Kimi). El brief v1 presentaba LOO como "más limpio"; era incorrecto para el uso principal.
2. **α = 0** (Cursor; Merton 1980). El residual del día es `ε_i,t = r_i,t + β_i,t−1 · f_USD,t`. Signo congelado en el acta: β > 0 = la divisa se deprecia cuando f_USD sube.
3. **β de la misma covarianza EWMA que el libro** (Cursor). `β_i = −Cov_EWMA(r_i, f) / Var_EWMA(f)`. Aquí la consolidación se aparta del quórum (Kimi y Astra mantenían OLS 252d): la decisión que sirve la sección es la expresión y el solapamiento *ahora*, y la carga del libro sobre la cesta (C.6, fórmula de Astra) ya es la beta EWMA agregada; publicar β_252 al lado de un VaR_EWMA invita a cubrir con un número que no es el del riesgo. Un Σ, un reloj. La OLS 252d se guarda en canonical.csv como twin de estabilidad, con la correlación entre las dos series de β en DIAG, sin umbral.

**C.3 [Δ Astra] EWMA definida sin ambigüedad.** Recursiva, λ = 0,97, media cero, inicialización con el primer año de datos (2000), sin truncar a 63. Semivida ≈ 22,8 sesiones; tamaño efectivo ≈ 66. Se deja de llamar "63d". λ = 0,97 se declara como decisión deliberada de estabilidad (RiskMetrics diario usa 0,94; Kimi).

**C.4 Residuales.** Acumulado 21d como horizonte de mesa; z frente a sus 252 valores anteriores; rango 1-7 (1 = mayor acumulado; empates por rango medio, Astra). 5d y 63d en canonical.csv y en la tarjeta del par, no en la tabla (Cursor). Sin lectura de convergencia: un residual acumulado no es error de precio (Astra).

**C.5 [Δ] Dispersión.**
- `D_t` = desviación estándar transversal de los siete residuales 21d, **denominador 7** (universo completo, no muestra; Astra). MAD en canonical.csv.
- **Bandera UN-NOMBRE** (Kimi): si `ε_max² / Σ ε_i² > 0,5`, la etiqueta lleva el sufijo `(UN NOMBRE)` y DIAG dice cuál. Responde a "¿dispersión del sistema o una divisa con noticia?" y cubre la objeción de Cursor al outlier sin cambiar de estimador.
- Bandas por percentil causal 252d, **30/40/30 declaradas como convención simétrica pre-registrada, no como umbral empírico** (Cursor). Nombres COMÚN / MIXTO / DISPERSO se mantienen como nombres de banda; **[Δ Astra] se eliminan del dashboard las frases interpretativas** ("el dólar explica el movimiento", "los cruces son aritmética", "información propia"). Se muestra también el percentil continuo.
- Histéresis 3 sesiones: se mantiene (Kimi, Cursor; 2-1).
- `S_t` (cuota de PC1) sigue en cabecera. **[Δ Astra] Se elimina la bandera de "deben moverse en sentido contrario"**: miden objetos distintos y pueden subir a la vez.

**C.6 [Δ] Matriz de cruces.** Modos: `β_A − β_B` (leak de dólar del cruce; misma Σ) y volatilidad del cruce `√(Var_A + Var_B − 2Cov_AB)`. ρ_AB y `ε_A − ε_B` 21d pasan a la tarjeta del par (Cursor). Pie: `|β_A − β_B|` es carga de retorno por unidad de factor, no riesgo en dólares; el riesgo necesita nominal y Σ (Astra).

**C.7 [Δ] PCA.** Sobre la Σ EWMA (retornos brutos). En el dashboard solo `S_t` y las cargas de PC1, signo anclado a Corr(PC1, f) > 0. **PC2/PC3 salen del dashboard** (Cursor, Astra; 2-1) y quedan en canonical.csv con autovalores y separación entre autovalores. DIAG: correlación de las cargas de PC1 con las de hace 21 sesiones; **bandera ROTACIÓN** si < 0,8 (Kimi; convención). Ningún componente se bautiza.

**C.8 [Δ] Libro.** Con `q` = vector de exposiciones de las siete patas no-USD, `Σ` la EWMA, `w = (1/7,…,1/7)`:

- Ocho cubos; neto USD = cubo USD.
- `V = q'Σq`; `σ_libro = √V`.
- **Carga sobre la cesta** `= −q'Σw / (w'Σw)` y **cuota sobre la cesta** `= (q'Σw)² / (w'Σw · V)` (Astra). Esta cuota **sustituye** a "% del riesgo que es dólar vía PC1"; se rotula "cuota sobre la cesta USD", nunca "es dólar". La cuota PC1 del libro `λ₁(u₁'q)²/V` va a canonical.csv.
- **VaR 95 % 1d paramétrico** `1,645·σ_libro` y **VaR 95 % 1d histórico** 252d, comparados entre iguales (Astra). ES 97,5 % paramétrico `2,338·σ_libro` en canonical.csv; ES histórico fuera (Cursor: seis observaciones no son una cifra de mesa). Bandera **COLA** si `|param − hist| / param > 0,30`, declarada convención (Kimi, Cursor; 2-1 frente a Astra), y se publica también la diferencia en USD.
- **[Δ Astra] Concentración por contribuciones de Euler** `RC_k = q_k'Σq / V` por posición (suman 1; pueden ser negativas por coberturas). Sustituye a "cuota del par mayor".
- **[Δ] Estrés, tres líneas, ninguna con umbral:**
  1. Sensibilidad a un movimiento unitario de la cesta ±1 % trasladado a las patas (Astra).
  2. **ESCENARIO DOLLAR+**: el mismo cálculo con el máximo de |f_USD| a 1 sesión en 2000-2023, calculado una vez y escrito en el acta (Cursor).
  3. **Cota de dependencia adversa** `σ_max = Σ |q_i| σ_i` (Astra). Sustituye al "corr → 1" de Kimi, que para un libro long/short no es conservador (corr +1 anula el riesgo del spread).
- **[Δ Astra] Validación del riesgo**: VaR y ES son previsiones y necesitan backtest, no gate direccional. Desde el día 1 se archivan snapshot del libro y previsión; el backtest (excepciones de VaR 95 % contra P&L hipotético con posiciones congeladas) se declara PENDIENTE hasta ≥ 250 snapshots.

**C.9 [Δ] Tarjeta PRE-TRADE (la pieza que faltaba; unánime).** Para cada fila de candidate.csv, contra el libro actual (o contra libro vacío):
- `Δcubo` por moneda y Δneto USD.
- `ΔV = 2·q_c'Σq + q_c'Σq_c` y ΔVaR paramétrico (Astra).
- ρ riesgo-candidato vs riesgo-libro `= q_c'Σq / √((q_c'Σq_c)(q'Σq))` (Kimi).
- Leak `β_A − β_B`, vol del cruce, `ε_A − ε_B` 21d (Cursor).
- Δcuota sobre la cesta y P&L del candidato en los tres escenarios.
Sin adjetivos, sin ranking, sin voto.

**C.10 Carry APROX: eliminado** (unánime; Du-Tepper-Verdelhan 2018 como referencia de por qué el tipo oficial no es el carry).

---

## D. Qué enseña la §09 (versión recortada)

1. **Cabecera**: f_USD acumulado 5/21/63 con z; banda de dispersión + percentil continuo + sufijo UN NOMBRE si procede; `S_t`.
2. **Tabla 7**: β (EWMA), residual 21d, z, rango.
3. **Matriz 7×7**: `β_A − β_B` · vol del cruce. Tarjeta del par: ρ, `ε_A − ε_B`, residual 5/63.
4. **Libro**: ocho cubos, neto USD, carga y cuota sobre la cesta, VaR 95 % param/hist + COLA + diferencia, Euler por posición, tres líneas de estrés, fecha del snapshot. `SIN LIBRO` / `LIBRO DESACTUALIZADO` (snapshot > 5 sesiones; convención).
5. **PRE-TRADE**: una tarjeta por candidato. `SIN CANDIDATO`.
6. **DIAG**: fecha BCE, antigüedad en días de calendario y TARGET, `FIX 14:15 CET`, DESFASE_US, twins numéricos (BCE-CME 6 divisas con/sin días US, f vs DTWEXAFEGS, cestas equi/inv-vol, β EWMA vs OLS-252, factores LOO), ROTACIÓN PC1, identidades de ingestión, backtest VaR PENDIENTE (n/250), versión.
7. **Pie de fórmulas**, con: Σβ_i = 7 por construcción; residuales no son confirmaciones independientes; z descriptivo; `|β_A − β_B|` no es riesgo en dólares; proyección sobre la cesta ≠ componente dólar causal.

**Alertas Telegram** (solo por cambio; convenciones pre-registradas, no umbrales empíricos; 2-1 frente a Astra, que las quitaría): cambio de banda de dispersión; |z| 21d del factor cruza 2; cuota del libro sobre la cesta > 0,80; COLA.

---

## E. POS-G8 columna P3y — especificación breve [Δ Astra]

- Grupo: la misma categoría y modalidad de informe (futures-only o combined) que POS-G8 ya usa; no se mezclan categorías.
- `x_t = (largos − cortos) / OI` del mismo informe y contrato.
- Percentil frente a las 156 observaciones semanales **anteriores** (empates explícitos); z sobre la misma muestra; < 156 → NA.
- Disponibilidad por fecha de publicación del COT (viernes), no por fecha económica (martes).
- Bandera **EXT** en ≤ p10 / ≥ p90, escrita en el acta como banda descriptiva de dos colas, no como peligro ni reversión.
- Excepción a la regla de dato diario declarada: COT es semanal y se arrastra; no se convierte en dato diario nuevo.

---

## F. Implementación y aceptación

`scripts/usd_factor.py` → `data/USD_FACTOR.json` + `data/usd_factor/canonical.csv`. `scripts/book_risk.py` → `data/BOOK_RISK.json` + `data/book_snapshots/YYYY-MM-DD.csv`. Workflow diario cron 15:30 UTC con reintentos y calendario TARGET. §09 en `index.html`. `docs/actas/ACTA_UF1.md` con: signo de β, λ y su justificación, 30/40/30, 0,30, 0,80, 0,8 de rotación, 5 sesiones de snapshot, máximo histórico de f_USD, correlaciones típicas medidas en 2000-2023, y la doble lectura Σβ=7.

**Pruebas de aceptación antes de publicar** (Astra; no es backtest de alpha): Σ semidefinida positiva cada día; reconstrucción de V con los siete componentes; suma de cubos = 0; identidades de inversión y triangulación; trazabilidad causal (forecast_origin / target_date en canonical); ausencias de calendario correctamente etiquetadas; POS-G8 sin cambio en ningún delta ni estado existente.

**Esfuerzo revisado**: 2,5-3 días (Astra tenía razón en que la aritmética es pequeña y la validación no).

**Estado**: CONTEXT permanente. No vota, no entra en confluencia ni en ranking jefe. Cualquier promoción del rango de residuales a señal exige acta de gate nueva sobre la ventana virgen.

# ESPECIFICACIÓN §01-b — Lectura a 22 sesiones del 10Y y detector «compatible con tensión fiscal»
G8 Macro Pipeline · v1.1 · 19-sep-2026 (v1.0 corregida con los cinco puntos de Sander) · Paso B de la secuencia A→B→C→D (acta RTF10 v3.6.4)
Estado: ESPECIFICACIÓN. No se construye ni se activa hasta "procede". RTF10 v3.6.4 y sus 12 alertas siguen operativos.

## 0. Propósito y límites
Trasladar al dashboard la única función de RTF10 que no existe en él: vigilar el MOVIMIENTO del 10Y a 22
sesiones y avisar cuando la prima de plazo sube con la divisa cayendo. La §01 actual muestra NIVELES
(nominal = real + BE; ACM: yield ajustado = RNY + TP); no vigila cambios ni emite alertas.
- Estado permanente CONTEXT: no vota, no entra en el embudo, no lleva adjetivos de dirección.
- La bandera se llama «compatible con tensión fiscal» (CTF). ΔTP > 0 con depreciación es compatible con
  tensión fiscal; no identifica la causa. El ACM estima una prima; no observa flujos fiscales.
- El paso A (resultado_A_ler_acm.txt) respalda ESTUDIAR el reemplazo; no demuestra superioridad.
- Retrospectividad declarada: las cargas ACM se estiman con toda la historia mensual (acm_g8.py Stage 1) y
  se aplican a los últimos 5 años diarios. El expanding del umbral evita anticipación EN EL UMBRAL, no en
  las cargas. Todo replay sobre historia es retrospectivo; solo el registro diario de C es evidencia
  "tal como estuvo disponible".

## 1. Fuentes (todas ya en el repo; ninguna nueva)
| Serie | Fichero | Columnas | Reloj |
|---|---|---|---|
| ACM por divisa | data/ACM_G8_<CCY>.csv | DATE, Y10_FIT, RNY10, TP10, QUALITY | diario, as-of de la curva |
| Nominal 10Y observado | los CSV de curva que ya alimentan la §01 | close 10Y | diario |
| FX nativo XXX/USD | canónico de la §10 (tipos de referencia del BCE, publicados ≈ 16:00 CET; las 14:15 son la concertación, no la publicación) | log-retorno diario por divisa; factor dólar f | calendario TARGET |
| 2Y y BE (solo contexto) | las series que ya usa la §01 | — | diario |
Unidades: bps para todo lo de tipos; % para FX. Ninguna serie se rellena antes de su primera fecha.

## 2. Lectura a 22 sesiones (por divisa, tabla nueva dentro de la §01)
Ventana ÚNICA por fechas para todas las series: t = fecha de evaluación; t₀ = fecha de la 22.ª sesión
anterior en el calendario TARGET (el de la §10, común a las 8 divisas). Cada serie se lee as-of: última
observación con fecha ≤ t y ≤ t₀, con atraso máximo de 3 días hábiles en cada extremo; si un extremo no
existe o excede el atraso, esa magnitud es "·" y la señal pasa a NO_DATA. Así ACM, nominal, FX y contexto
cubren exactamente el mismo periodo [t₀, t]. (v1.0 usaba 22 filas ACM y 22 sesiones TARGET: ventanas
distintas; corregido. El paso A se repitió con ventanas por fecha: resultado_A_ler_acm_v11.txt.)
- ΔRNY₂₂ = (RNY10[t] − RNY10[t₀]) × 100
- ΔTP₂₂  = (TP10[t] − TP10[t₀]) × 100
- ΔFIT₂₂ = ΔRNY₂₂ + ΔTP₂₂ (identidad del modelo, se muestra, no se calcula aparte)
- RESID₂₂ = Δnominal observado₂₂ − ΔFIT₂₂ (error de ajuste del ACM sobre el movimiento; columna propia)
- Contexto, NO aditivo: Δ2Y₂₂ (bps), ΔBE₂₂ (bps; NZD/CHF constantes → "·"), ΔFX₂₂ (%, §3.2)
- Persist: sesiones consecutivas con el mismo estado CTF (§3.5)
Columnas: CCY | ΔRNY | ΔTP | ΔFIT | RESID | Δ2Y | ΔBE | ΔFX | θ | CTF | Persist | Calidad | As-of
Colores de ΔTP: blanco si ΔTP ≤ 0 o < θ · ámbar si ΔTP > 0 y ≥ θ · rojo si ΔTP > 0 y ≥ θ₉₅. Solo la cola
positiva se colorea: una caída de prima nunca se pinta como tensión (v1.0 coloreaba |ΔTP|; corregido).
Las caídas grandes se ven en el número, no en el color.

## 3. Detector CTF — definición (fijada antes de mirar datos)
### 3.1 Condición de prima
**ΔTP₂₂[t] > 0 Y ΔTP₂₂[t] ≥ θ[t]**, con θ[t] = percentil **P = 80** de la población de ΔTP₂₂ **con signo**
(ambas direcciones, no absolutos) desde el inicio de la serie ACM diaria hasta **t−1** (expanding; el
movimiento evaluado en t no entra en su propio umbral). Requisito: **≥ 252 observaciones válidas** antes
de t; si no, CTF = NO_CAL. La condición ΔTP > 0 es explícita porque el p80 de una población firmada NO
garantiza un umbral positivo (depende de la distribución; v1.0 lo afirmaba, era incorrecto).
P80/p60 son parámetros INICIALES DE PRUEBA, fijados para C; no se tocan durante C. θ₉₅ (p95, mismo
expanding) solo colorea. Sensibilidad p75/p90 se INFORMA en C, no se conmuta.
### 3.2 Condición cambiaria
ΔFX₂₂ = ln(S[t] / S[t₀]) en nativo XXX/USD (retorno LOGARÍTMICO, el mismo que usa la §10; equivale a la
suma de los log-retornos diarios entre t₀ y t), leído as-of en los dos extremos de la ventana única del §2.
- G7: divisa frente a USD en nativo XXX/USD (BCE). Condición: ΔFX₂₂ ≤ **−0,5 %** (divisa cae).
- USD: frente al **factor dólar f de la §10** (cesta equiponderada de las 7, congelada). Condición:
  Σ f entre t₀ y t ≤ −0,5 % (el dólar cae contra la cesta). No existe "USD frente a USD".
- CHF: sin condición cambiaria y sin CTF, solo lectura (§2). Misma doctrina que RTF10/r2.5 (FXsup): el
  franco no lee prima fiscal vía FX. Declarado, revisable por acta.
El −0,5 % es el umbral de "plano" del protocolo C4 (misma cifra, mismo motivo: evitar que un ±0,1 %
decida una bandera). Sin ρ ni correlación: el detector nuevo usa el cambio puntual, no un régimen.
### 3.3 Disponibilidad y atraso
- ACM: se usa la última fila con DATE ≤ t (y ≤ t₀ para el otro extremo). Si el atraso en cualquiera de los
  dos extremos supera **3 días hábiles** → NO_DATA (sin señal). La tabla enseña siempre el As-of real.
- FX: el BCE publica ≈ 16:00 CET; si en t aún no hay tipo publicado, se usa t−1 con bandera **DESFASE**
  (igual que la §10) y la señal se calcula igualmente (el atraso es de un día y se ve). En el replay
  retrospectivo, la disponibilidad se fija a las 16:00 CET, nunca antes.
- Nominal observado: si falta en t, RESID = "·"; la señal no depende de él.
- Nunca se recalcula hacia atrás: un dato que llega tarde actualiza la lectura de HOY, no la de ayer.
### 3.4 Calidad (QUALITY del ACM) — quién puede emitir
| QUALITY | Emite CTF | Nota |
|---|---|---|
| ACM_K3_<n>m con n ≥ 240 | sí | muestra ≥ 20 años (MIN_OBS_MONTHLY) |
| ACM_K3_SHORT_SAMPLE_<n>m (NZD, 136m) | sí, con badge SHORT | el nivel es de baja confianza; aquí se usan cambios. Declarado. |
| ACM_K3_NOWCAST_PARALLEL (CHF fin de mes) | no | CHF no emite de todos modos; la lectura lleva badge NOWCAST |
| cualquier otra / vacía | no | estado NO_QUAL |
### 3.5 Estado y "nuevo encendido" (anti-repetición)
Dos ejes separados por divisa, evaluados una vez por sesión:
**Eje de disponibilidad** (se muestra siempre, manda sobre la señal): OK · NO_DATA (un extremo de la
ventana falta o excede el atraso) · NO_CAL (< 252 obs.) · NO_QUAL (§3.4) · DESFASE (FX de t−1).
**Eje de señal** (solo se evalúa si disponibilidad = OK o DESFASE): OFF / ON.
- OFF → **ON** cuando 3.1 y 3.2 se cumplen. Solo esta transición genera alerta.
- ON → OFF cuando ΔTP₂₂ ≤ 0, **o** ΔTP₂₂ < θ_off[t] (percentil **60** del mismo expanding, histéresis),
  **o** la condición cambiaria falla **3 sesiones OK seguidas**. Sin alerta al apagarse.
- Si la disponibilidad deja de ser OK, la señal NO cambia y NO se evalúa: la tabla muestra el último
  estado con la etiqueta «último ON/OFF · dato atrasado (as-of dd-mm)» y ningún contador avanza. Las
  sesiones sin dato no cuentan como sesiones apagadas ni como sesiones encendidas.
- Recuperación: al volver a OK se evalúa 3.1/3.2 sobre la ventana actual. Si el resultado coincide con el
  último estado, se restaura sin alerta. Si el último era OFF y ahora cumple → ON con alerta (encendido
  nuevo). Si el último era ON y ahora no cumple → OFF sin alerta. Desde NO_CAL (primera vez que hay 252
  obs.) o NO_QUAL (calidad recuperada) se arranca en OFF y se evalúa igual.
- Persist = sesiones OK consecutivas en el mismo estado de señal; se reinicia en cada transición y no
  avanza en sesiones sin dato.
Nada de esto existe en RTF10 (allí la alerta es "onset" barra a barra); se declara como diferencia de diseño.

## 4. Salidas
- data/S01B.json: por divisa, todos los campos de §2 + θ, θ_off, θ₉₅, n_población, estado, persist,
  as-of de cada fuente, banderas (SHORT, NOWCAST, DESFASE, NO_DATA, NO_CAL, NO_QUAL), versión.
- Dashboard: tabla §2 debajo de la matriz de niveles de la §01, etiqueta CONTEXT, tecla de navegación
  existente, registro en el DQM (Data Quality Monitor) con as-of.
- Telegram (dashboard_alerts.py): un bloque solo en OFF→ON: divisa, ΔTP vs θ, ΔFX, as-of, calidad,
  texto fijo "compatible con tensión fiscal — CONTEXT, sin voto".
- **Registro diario para C** (§5): data/s01b/log/YYYY-MM-DD.json escrito por el workflow y NUNCA
  reescrito. Debe permitir REPRODUCIR el cálculo sin acceso a las series de hoy: para cada divisa, los
  DOS extremos de la ventana (t y t₀ con sus fechas as-of reales) de TP10, RNY10, Y10_FIT, nominal, S y f;
  QUALITY en ambos extremos; θ, θ_off, θ₉₅ y n de la población; los resultados (ΔRNY, ΔTP, ΔFIT, RESID,
  ΔFX, estado de ambos ejes, persist); versión de acm_g8.py, mes de su última estimación y n de meses;
  versión de s01b.py; y el SHA-256 de cada fichero ACM_G8_<CCY>.csv leído, junto con una copia diaria
  comprimida de esos CSV en data/s01b/snap/YYYY-MM-DD/ (≈ 60 KB × 8). Como el ACM retrospectivo puede
  revisar los extremos, sin la copia el registro no sería verificable.

## 5. Paso C — validación en paralelo (criterio escrito ahora)
Duración mínima **63 sesiones** desde el primer registro diario, prorrogable hasta acumular **≥ 3
encendidos** (CTF o TP_FISCAL de RTF10, el que ocurra) si en 63 no los hay.
Datos: registro diario de §4 + export semanal del data-window de RTF10 v3.6.4 (Sander, mismo gesto que
el twin-test de POS).
Tablas de C (por divisa, USD y G7 sin CHF):
1. Encendidos CTF (OFF→ON) y onsets TP_FISCAL de RTF10; coincidencias a ±5 sesiones; huérfanos de cada lado.
2. Para cada huérfano, la descomposición de la diferencia: ¿umbral (ΔTP < θ con LER > banda)? ¿condición
   cambiaria (cambio puntual vs ρfx)? ¿POL vs ΔRNY (JPY)? ¿disponibilidad/calidad? Una fila por caso.
3. Días NO_DATA / NO_CAL / NO_QUAL / DESFASE sobre días totales.
4. Sensibilidad p75/p90 del umbral: cuántos encendidos añade o quita (informativo).
Cada discrepancia de la tabla 2 termina con un veredicto de tres valores: **ACEPTABLE** (diferencia de
diseño asumida, sin pérdida de vigilancia), **DEFECTO** (pérdida de vigilancia o error; bloquea D hasta
corregir y re-observar) o **PENDIENTE** (sin datos para decidir). "Explicado" no es un veredicto.
Aceptación (juicio de Sander sobre las tablas, no un umbral): (a) ningún fallo de disponibilidad
sistemático (NO_DATA > 10 % de las sesiones en alguna divisa emisora = no pasa); (b) ningún DEFECTO
abierto; (c) ningún PENDIENTE sobre un onset TP_FISCAL de RTF10. 63 sesiones y 3 encendidos son el
MÍNIMO de observación, no prueba suficiente: la evidencia se cuenta POR DIVISA, y una divisa sin ningún
encendido de ninguno de los dos lados en el periodo queda "sin evidencia" — D puede ejecutarse solo si
Sander acepta expresamente retirar RTF10 con esas divisas sin evidencia, o C se prorroga para ellas.

## 6. Paso D — retirada de RTF10 (solo si C pasa)
Documental (RTF10 no tiene puente a otros scripts): Checklist de Lectura, Cheat Sheet + Anexo A, Guía
Completa y Edición Consolidada pasan a citar §01-b en el paso "tesis de tasas". Alertas de TradingView
de RTF10 se borran; v3.6.4, candidato v3.6.5, actas, validadores y resultados se archivan.
Lo que se pierde y no se replica: cambios de driver por divisa, TP_SAFE/TP_FX_STRONG/INFL_GLOBAL/GROWTH/
OFFSET, KW, TIPS_LIQ (replicable en USD con BE/real si se pide después). Se lista en el acta de retirada.

## 7. Anexo previsto: replay retrospectivo sobre los 8 episodios cubiertos
Se aplica la REGLA COMPLETA (umbral expanding a t−1 con ≥ 252, condición cambiaria, calidad,
disponibilidad) a la historia ACM 2021-09 → hoy y se informa por episodio: **días cubiertos / días
previstos** de la ventana original, días con CTF ON, y el driver moda de RTF10 en la misma intersección.
Etiqueta obligatoria: RETROSPECTIVO (cargas ACM de hoy). ΔTP > 0 en el paso A no equivale a CTF ON.
Consecuencia del requisito de 252 observaciones: el primer día evaluable es ≈ oct-2022 (+22 sesiones de
warm-up) → abr-2022 USD y sep-2022 GBP quedan NO_CAL en el replay; se dice, no se relaja.

## 8. Fuera de alcance de esta versión
Voto en el embudo; niveles de TP (ya en §01/§06); TP_SAFE y demás etiquetas de RTF10; ρ/correlaciones;
OIS-IS; cambios en acm_g8.py (la retrospectividad de las cargas es un tema aparte, no de §01-b).

## 9. Decisiones que quedan a Sander antes de programar
D1. P = 80 firmado con ΔTP > 0 explícito y θ_off = p60 (histéresis) — confirmar como parámetros iniciales de prueba; una vez fijados no se tocan en C.
D2. Umbral cambiario −0,5 % en retorno logarítmico a 22 sesiones — confirmar.
D3. CHF sin CTF, con lectura de componentes — confirmar.
D4. Duración de C: 63 sesiones mín. + ≥ 3 encendidos, contados por divisa como mínimo de observación, no como prueba suficiente — confirmar.
Esfuerzo: se estima después de leer index.html (§01), js del cargador y dashboard_alerts.py v2.3; no antes.

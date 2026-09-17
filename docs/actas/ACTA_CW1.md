# ACTA CW-1 · CROSS WALLS v1.0 — PRE-REGISTRO DEL GATE
16-sep-2026 · Bata, GQ · congelada ANTES de evaluar dato alguno

> **RETIRADA el 17-sep-2026** tras las auditorías de Cursor y ChatGPT/Astra y la medición propia (ECDF cambia el significado del signo; G y RR25 anticorrelados en AUD/GBP; kernel penaliza los muros lejanos). El gate CW-1 nunca llegó a evaluar (74 < 126). Sustituida por **ACTA_CW2.md** (Cross Walls v2.0, la lectura manual escrita), reloj a cero. Se conserva íntegra como registro.

## 1. Qué se construye
Sección §08b del G8 Macro Pipeline: un **score de posicionamiento de opciones por divisa** (convención nativa CME XXX/USD) a partir de la cadena de opciones que ya baja la §08 (Databento GLBX.MDP3, settlement + OI por strike), del que se derivan el **factor dólar**, la **dispersión** (régimen CROSS / DOLLAR_PURE) y la **dirección de los 15 cruces G8** (NZD excluido). Los cruces reciben dirección y ranking, **nunca niveles**.

Origen: idea de Sander (leer los muros de la §08 no solo contra el dólar sino entre divisas), triangulada el 16-sep-2026 con ocho AI (Gemini, Perplexity, DeepSeek, Qwen, Kimi K3, CodeWords, ChatGPT, Cursor). Consenso incorporado: el OI no tiene signo (el conteo call/put y el ΔOI total no votan), la geometría del OI es lo que sostiene la lectura manual, el skew da el signo, FAIL se excluye del todo, la resta de la media no cambia ningún cruce, y el gate se evalúa contra el retorno futuro, no contra la coincidencia con BREADTH/POS.

## 2. Definiciones congeladas (cross_walls.py v1.0)
| Pieza | Regla |
|---|---|
| Cadena de análisis | Primer vencimiento con DTE ≥ 10 (el front mientras tenga ≥ 10 días; el siguiente mensual en los últimos 9 días del front). Gate 0 se juzga sobre el front de la §08. |
| Subyacente F | ~~Settlement del primer futuro que vence en/después de la opción~~ **(v1.1 [F1], 16-sep): settlement del primer futuro TRIMESTRAL (mar/jun/sep/dic) que vence en/después de la opción — el contrato en que CME ejerce las opciones mensuales de FX.** Guardia: forward por paridad put-call (mediana de K + (C − P)/df en strikes a ±1 % de F con ambos settlements); si \|F − F_parity\| > 0,10 % ⇒ F_MISMATCH, la divisa no vota ese día. La misma regla define la referencia del Gate 0 en §08 (build_options_summary v1.1). |
| Vol implícita | Solo opciones OTM con settlement ≥ 3 ticks; bisección Black-76 en [0,5 %, 300 %] con SOFR del día; ATM por interpolación en strike **(v1.0 → v1.0.1, 16-sep: ATM = recta de mínimos cuadrados σ(ln K/F) sobre los strikes con \|ln K/F\| ≤ 0,75·σ_seed·√T, ≥ 4 puntos a ambos lados de F, evaluada en F; si no hay puntos suficientes, la interpolación de dos puntos)**; σ25Δ call/put por interpolación en delta (≥ 8 vols invertidas, si no RR = NA). |
| G (geometría) | Σ OI_k·e^(−x_k²/2)·x_k / Σ OI_k·e^(−x_k²/2), x_k = ln(K/F)/(σ_ATM·√T), OI = call + put, todos los strikes de la cadena. |
| RR25 | σ25c − σ25p en puntos de vol (nativo: + = el mercado paga XXX arriba). |
| ΔG5 | G_t − G_{t−5} si la cadena de análisis es la misma; si no, NA. |
| Normalización | Z = 2·ECDF_causal_propia − 1 por divisa y componente (la historia incluye el día). LOW_HISTORY mientras n < 126. |
| Score | mediana{Z(G), Z(RR25), Z(ΔG5)} (media de dos si una es NA). |
| Factor USD | −mediana de los scores de las divisas CLEAN. |
| Dispersión | 1,4826·MAD de los scores CLEAN; percentil causal; DOLLAR_PURE si < p20 con ≥ 4 CLEAN, si no CROSS. |
| Cruce A/B | dirección = signo(S_A − S_B); fuerza = percentil causal de \|gap\| del par; pata que manda = mayor \|residual\|. |
| Elegibilidad | Ambas patas CLEAN (Gate 0 §08) y DTE ≥ 10. FAIL/NA fuera de score, factor y dispersión (nunca ponderado). PROXY visible, sin voto. |
| Banderas | NO_VOTE · PIN (spot a < 0,3σ√T del muro mayor de una pata) · FLOW (\|ΔOI\| > 15 % en una sesión) · NEXT (cadena = siguiente mensual). |
| Diagnóstico sin voto | C/P nativo (OI ≥ 100), ΔOI total, ΔOI neto por lado (ΔC − ΔP), OI arriba %, concentración del mayor strike. |

Cambio material de cualquiera de estas reglas ⇒ nueva versión, nueva acta, reloj del gate a cero.

## 3. Hipótesis y gate (gate_cross_walls.py v1.0)
- **H1 (primaria):** en la sesión t, el signo de gap_t = S_A − S_B de un cruce ELEGIBLE se asocia fuera de muestra con el signo del retorno logarítmico del cruce en las 5 sesiones siguientes.
- **Secundaria (veto):** horizonte 21 sesiones.
- **Target:** ln(F_A,t+h/F_A,t) − ln(F_B,t+h/F_B,t) con el MISMO contrato de futuro en t y t+h (sin contaminación de roll).
- **Métrica:** IC de Spearman cross-sectional diario entre gap_t y el retorno futuro sobre los cruces elegibles (≥ 4 en el día); media de IC.
- **Dependencia:** bootstrap por bloques de 21 sesiones sobre FECHAS, resampleando la matriz completa de cada fecha (15 cruces de 6 patas no son observaciones independientes); 2.000 remuestreos.
- **Diagnóstico de señal fuerte:** hit rate de signo en cruces con fuerza ≥ p66,7.
- **Muestra:** 126 sesiones de calentamiento (excluidas) + ≥ 252 sesiones de evaluación ⇒ primer veredicto a N ≥ 378.
- **PASS (5D):** IC_5 medio ≥ 0,05 y límite inferior del IC95 bootstrap > 0, y hit rate fuerte ≥ 54 % con límite inferior > 50 %.
- **VETO:** IC_21 medio < 0.
- **Incrementalidad (necesaria para PASS completo):** el IC del gap residualizado sobre BREADTH y POS-G8 se mantiene > 0 (requiere los exports de TradingView; PENDING hasta tenerlos).
- Antes de N ≥ 378 el script imprime un informe interino etiquetado RESEARCH y **se niega a emitir veredicto**. Está prohibido mirar el acumulado con ánimo de veredicto antes de N.

## 4. Uso permitido hasta el gate
Contexto de posicionamiento en el Gate 0 de las dos patas de un cruce, al nivel del BREADTH: nunca gatillo, nunca tamaño, nunca nivel. Las alertas Telegram de la §08b (cambio de régimen; cruce elegible que gira con fuerza ≥ p80) llevan la etiqueta RESEARCH.

## 5. Estado
- 16-sep-2026: v1.0 construida y verificada en local sobre 71 sesiones canónicas (2026-06-01 → 2026-09-10). Inversión Black-76 verificada con opciones sintéticas (recupera la vol a 6 decimales). Caso 10-sep: ranking GBP > EUR > AUD > JPY entre CLEAN, régimen CROSS, GBPJPY ▲ p78 el cruce elegible más fuerte, JPY la pata más débil (coherente con la lectura manual del 14-sep). Gate: RESEARCH · 71/378.
- 16-sep-2026 · twin-test CVOL (leído en las páginas públicas de producto de CME, 16-sep 04:18 CT, índice a 30 días constantes; §08b = settlement 14-sep, cadena 09-oct, DTE 25): EUVL 5,30 vs ATM v1.0 6,15 (Δ +0,85, dentro de ±1,0 pero con sonrisa invertida: ATM > σ25c 5,78 > σ25p 4,85); JPVL 10,13 vs 9,86 (Δ −0,27). PASA en nivel. Hallazgo: el ATM de dos puntos era ruidoso (cambio diario mediano EUR 0,28 / JPY 0,53 pt, máximos 2,65 / 4,43) por redondeo a tick de dos settlements. Sensibilidad medida sobre 71 sesiones con σ×0,7 y ×1,3: signo de G estable en 378/426 lecturas (89 %), orden por pares de G estable en 946/1.065 (89 %).
- 16-sep-2026 · **v1.0.1 [A1]**: ATM por ajuste lineal (regla arriba). Efecto sobre 73 sesiones: cambio diario mediano EUR 0,28 → 0,13, JPY 0,53 → 0,45, CAD 0,36 → 0,17; ATM 14-sep EUR 5,37 (vs CVOL 5,30, Δ +0,07), JPY 9,99 (vs 10,13, Δ −0,14). Ranking 14-sep idéntico (CAD > CHF > EUR > GBP > AUD > JPY), las seis direcciones elegibles idénticas, fuerzas ±7 pt; 26 de 1.050 signos de cruce cambian en la historia (2,5 %), 91 celdas de score de 438. **Decisión: el reloj del gate NO se reinicia** — el score no usa el ATM directamente (solo como escala del kernel de G y en la bandera PIN), el RR25 no cambia y ninguna dirección vigente cambia; queda escrito aquí para que un auditor pueda discrepar. Saltos residuales del ATM en 29-jul y 03-sep son cambios de cadena (legítimos); JPY 15/16/18-jun y 30-jul/04-ago son días de settlement anómalos del colector temprano, no de la fórmula.
- 16-sep-2026 · twin-test completo (Claude web, logueado en CME; widget `/services/cvol`, campo PreviousDay = cierre 15-sep, +1 día hábil sobre el settlement 14-sep; Skew de CME = UpVar − DnVar, misma convención que el RR25): EUR ATM 5,37 vs 5,17 PASA, **skew −0,19 vs RR25 +0,92 NO PASA**; JPY 9,99 vs 9,37 / +2,18 vs +4,50 PASA; GBP 5,46 vs 5,46 / −0,37 vs −0,32 PASA; AUD 7,46 vs 7,28 / −1,05 vs −1,33 PASA.
- 16-sep-2026 · diagnóstico del fallo EUR: la cadena del 14-sep viola la paridad put-call en el dinero (vol put 1,1550 = 4,37 %, vol call 1,1575 = 6,30 %). Forward por paridad = 1,15982 = settlement del 6EZ6 (diciembre), no del 6EV6 (serial de octubre, 1,1573) que la regla v1.0 tomaba como "primer futuro en/después de la opción". Las opciones mensuales de FX de CME se ejercen en el futuro trimestral siguiente. Sesgo sistemático por carry: EUR +0,22 %, JPY +0,45 %, CAD +0,26 %, AUD −0,08 %, GBP ≈ 0 (por eso GBP pasaba), CHF 0 (solo trimestrales). Inflaba el RR25 de las divisas con carry positivo contra USD y desplazaba G ~0,15σ; contaminaba dos de las tres componentes. La §08 usaba además como referencia el futuro más próximo (el 14-sep, el 6EU6 que vencía ese día, 0,55 % bajo el subyacente real).
- 16-sep-2026 · **v1.1 [F1]** (cross_walls.py v1.1 + build_options_summary.py v1.1, misma regla en ambos): F y referencia = futuro trimestral; guardia de paridad publicada (`f_parity_pct`, `F_sym`) y F_MISMATCH sin voto. Verificación: \|F − F_parity\| ≤ 0,014 % en las 438 divisa-sesiones (mediana ≤ 0,006 %), cero F_MISMATCH; veredictos Gate 0 §08 sin cambio (EUR/JPY/GBP/AUD CLEAN, CAD PROXY 78 %, CHF NA). Twin-test 14-sep vs CME 15-sep con v1.1: EUR 5,17 vs 5,17 / −0,31 vs −0,19; JPY 10,05 vs 9,37 / +1,89 vs +2,18; GBP 5,45 vs 5,46 / −0,41 vs −0,37; AUD 7,51 vs 7,28 / −0,78 vs −1,05 — **4/4 PASA en ATM y signo**. Efecto en el dashboard 14-sep: orden de las CLEAN idéntico (EUR > GBP > AUD > JPY), las seis direcciones elegibles idénticas, fuerzas cambian (EURGBP 62 → 20, EURAUD 87 → 48, EURJPY 91 → 84), factor USD +0,14 → +0,03, dispersión p87 → p62 (sigue CROSS). En la historia cambian 195 de 1.080 signos de cruce (18 %): el RR25 de EUR pasa de mediana +0,9 a −0,4, el de JPY de +4,5 a +1,6. **Enmienda material del pre-registro** (la definición del subyacente estaba mal). Reloj del gate: no hay nada evaluado que reiniciar (73 sesiones < 126 de calentamiento, toda la historia se recalcula desde los canónicos con la regla nueva), así que el contador sigue en 73/378; queda declarado como enmienda, no como retoque, para que un auditor pueda exigir el reinicio.
- Lección para la doctrina: el twin-test contra una fuente externa independiente es lo que cazó un error que el propio sistema no podía ver (la normalización por ECDF propia absorbe cualquier sesgo constante). Repetir el twin-test tras cada cambio del colector o de los contratos de futuros.
- Pendiente: el histórico EOD del CVOL por fecha está solo en CME DataMine; el contraste día a día queda como manual (PreviousDay del widget).

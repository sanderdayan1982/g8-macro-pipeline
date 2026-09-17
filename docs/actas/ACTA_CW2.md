# ACTA CW-2 · CROSS WALLS v2.0 — PRE-REGISTRO DEL GATE
17-sep-2026 · Bata, GQ · congelada ANTES de evaluar dato alguno · sustituye a CW-1 (retirada)

## 0. Por qué hay una acta nueva
Cross Walls v1.x (ACTA_CW1) construyó un score por divisa = mediana{Z(G), Z(RR25), Z(ΔG5)} con ECDF causal propia. Dos auditorías independientes (Cursor, ChatGPT/Astra, 16-sep) y una medición propia coincidieron en que la infraestructura era buena (subyacente trimestral, paridad, nativo, twin-test contra CVOL 4/4) pero la lógica de dirección no sostenía el título: el ECDF convertía "más lejos que las otras hoy" en "más raro que su propio pasado" (EUR 15-sep: RR25 −0,11 y Z +0,69), geometría y skew estaban anticorrelados en AUD (−0,69) y GBP (−0,64), ΔG5 faltaba un día de cada cuatro, y el kernel gaussiano penalizaba precisamente los muros lejanos que el operador lee. Ambas recomendaron no retocar el score sobre la marcha: acta nueva y reloj a cero. Las 74 sesiones de v1 se conservan como desarrollo; no son validación de reglas decididas después de verlas.

Tres rondas de triangulación (16/17-sep): (1) ocho AI sobre la idea original; (2) Cursor y Astra sobre la formalización del método manual (% frente a σ, tres muros, muros a ambos lados, imán o barrera); (3) Cursor y Astra sobre si la gamma de los dealers es identificable con datos públicos (veredicto unánime: no).

## 1. Qué se mide (la lectura manual del operador, escrita)
El operador traza en cada par contra el dólar los tres muros mayores de la §08, mira de qué lado del precio están (mayoría del lado del dólar ⇒ dólar alcista) y cuánto recorrido hay hasta ellos (el par con más recorrido, con todos los muros al mismo lado, señala la divisa más débil: "le costará más llegar"). Es cross-sectional (compara divisas entre sí hoy) y usa niveles, no estadística de la propia historia.

## 2. Definiciones congeladas (cross_walls.py v2.0)
| Pieza | Regla |
|---|---|
| Convención | Nativa CME XXX/USD para todo cálculo. Conversión a la cotización del operador exacta: d' = −d/(1+d). Solo presentación. |
| Cadena de análisis | Primer vencimiento con DTE ≥ 10. |
| Subyacente F | Settlement del primer futuro TRIMESTRAL que vence en/después de la opción (el contrato en que se ejerce). Guardia: forward por paridad put-call (mediana verdadera de K + (C−P)/df en strikes a ±1 % de F con ambos settlements); \|F − F_parity\| > 0,10 % o paridad no disponible ⇒ F_MISMATCH, sin voto. |
| Candidatos a muro | Strikes de la cadena con OI (call+put) ≥ 100 y ≥ 5 % del OI de la cadena, no MUERTOS. MUERTO = OI ≥ 100 sin variar en las 10 últimas sesiones de ese (vencimiento, strike); se muestra tachado. Los primeros 9 días de cada cadena no pueden declarar muerto nada (limitación escrita). |
| Muros | Los 3 candidatos de mayor OI. Empates en el corte: los r asientos restantes se reparten entre los m empatados (factor r/m); nunca decide el orden del fichero. |
| Distancia | d_k = 100·(K/F − 1), en % del futuro. + = por encima de F en nativo = favorece a XXX; − = por debajo = favorece al USD. NO en unidades σ (decisión del operador: mide en precio). |
| Lado | U = OI ponderado de muros con K < F / OI ponderado de muros. V análogo por encima. A⁻, A⁺ = distancias medias de cada lado. Publicados siempre. |
| Voto | Solo si max(U, V) ≥ ⅔. D = Σ w·d sobre los muros del lado mayoritario. Si no, MIXED, D = NA, sin voto. |
| Universo | Congelado {EUR, GBP, JPY, AUD}. CAD (PROXY) y CHF (NA) visibles, nunca votan, nunca entran en el sesgo del dólar. |
| Elegibilidad de pata | En el universo, Gate 0 CLEAN calculado CAUSALMENTE (cobertura acumulada hasta t, mínimo 20 sesiones; antes PENDING), DTE ≥ 10, ATM disponible, sin F_MISMATCH, no MIXED. |
| Sesgo del dólar | Media de U entre las patas que votan, con lista de disidentes (U < 0,5). Etiqueta: "USD alcista" si ≥ ⅔, "USD bajista" si ≤ ⅓, "sin mayoría" entre medias, INSUFICIENTE con < 2 votantes. Nunca un percentil de su historia. |
| Cruce A/B | Dirección = signo(D_A − D_B) desde el día 1. Fuerza = \|D_A − D_B\| en puntos porcentuales. "Rareza" = percentil causal de la fuerza para ese par, publicado aparte y nunca llamado confianza. Pata que manda = mayor \|D\|. Elegible solo si ambas patas votan. |
| Diagnósticos sin voto | D_σ (mismos muros, distancia en σ√T), ATM (ajuste lineal) y RR25 (Black-76 en casa), agree_RR (signo D = signo RR25), C3 (cuota del OI de la cadena en los muros), M34 (margen 3º vs 4º), d_near (muro más cercano), PIN ("proximidad a concentración de OI": F a < 0,3 % del muro mayor; no es gamma medida), FLOW (\|ΔOI\| > 15 %), NEXT, SOFR con antigüedad en días hábiles (nunca tasa futura). Tick mínimo por contrato (tabla CME), no inferido. |
| Lenguaje congelado | DESTINO = el precio camina hacia los muros lejanos (hipótesis de v2, la prueba el gate). PIN = el futuro se queda alrededor del muro. THROUGH = cierra al otro lado y se queda. Esta sección mide DESTINO; wall_behaviour.py registra PIN/THROUGH; nada de esto identifica la gamma de los dealers. |

Cambio material de cualquiera de estas reglas ⇒ nueva versión, nueva acta, reloj del gate a cero.

## 3. Lo que esta sección NO puede saber (declarado)
- Si un muro atrae o frena: con OI agregado y settlements no se identifica quién está largo ni cómo cubre. Un muro de puts "vendidos" es soporte solo si se sabe quién los vendió. Si el gate falla, no se invierte el signo para llamarlo modelo de barreras: sería CW-3 con validación nueva.
- Aunque todos los muros atrajeran, "más lejos" no implica "más débil en 5 sesiones" si las velocidades de convergencia difieren entre divisas (argumento λ de Astra). El gate lo mide, no lo supone.
- El % mezcla la vol de cada divisa en el ranking (a igual distancia en σ, el % del JPY es casi el doble del EUR). El operador lo acepta porque su lectura es en precio; D_σ se registra para que el día del veredicto se reporten los dos IC sin elegir el que gane.
- Delta Black-76 sobre futuros (no premium-adjusted), opciones americanas valoradas como europeas (OTM, sesgo pequeño), T calendario/365: limitaciones de los diagnósticos, escritas.

## 4. Hipótesis y gate (gate_cross_walls.py v2.0)
- **Disponibilidad**: settlement y OI de la sesión t se conocen la mañana de t+1. Todo target empieza en el settlement de t+1.
- **H0 (primaria, por pata)**: en la sesión t, el signo de D_c,t (pata que vota) se asocia fuera de muestra con el signo del retorno logarítmico nativo del futuro trimestral XXX/USD (F_sym de t) de t+1 a t+6. Métrica: hit rate de signo agregado por FECHA (cada fecha aporta la media de sus patas), bootstrap por bloques de 21 fechas (2.000). PASS-H0: hit ≥ 54 % y límite inferior IC95 > 50 %. Veto: hit a 21 sesiones < 50 %.
- **H1 (cruces)**: gap_t = D_A − D_B de los cruces elegibles vs retorno del cruce t+1 → t+6 con los mismos contratos. Métrica: IC de Spearman cross-sectional diario sobre ≥ 3 cruces elegibles, media, bootstrap por fechas; hit rate de signo en cruces con rareza ≥ p66,7, bootstrap por fechas. PASS-H1: IC5 ≥ 0,05 con IC95 inferior > 0 y hit fuerte ≥ 54 % con inferior > 50 %. Veto: IC21 < 0.
- **Retornos cero**: excluidos. Contrato ausente en un extremo: excluido.
- **Muestra**: 126 sesiones de calentamiento (excluidas; el percentil de rareza las necesita, la dirección no; se mantienen por comparabilidad) + ≥ 252 fechas evaluables a 5D ⇒ ~384 sesiones. **Evaluación única**: la primera ejecución con ≥ 252 fechas evaluables escribe `data/cross_walls/verdict_cw2.json`; las siguientes solo lo reimprimen. Con parámetros distintos de los registrados el script hace DRY RUN y no escribe veredicto.
- **Incrementalidad (PASS completo)**: IC del gap residualizado sobre BREADTH y POS-G8 > 0. Formato fijado: `data/cross_walls/breadth_export.csv` y `pos_export.csv` con columnas `date,ccy,value` (una fila por fecha y divisa, exportadas de TradingView). PENDING mientras falten.
- Nota de honestidad: el 17-sep se ejecutó una prueba mecánica del script con `--warmup 20 --min-eval 30` (parámetros no registrados) para comprobar que corre; su salida es nula a efectos de veredicto y el fichero que escribió se borró; desde entonces el script no escribe con parámetros no registrados.

## 5. Uso permitido hasta el gate
Contexto de posicionamiento en el Gate 0 de las dos patas de un cruce, al nivel del BREADTH: nunca gatillo, nunca tamaño, nunca nivel. Alertas Telegram §08b con etiqueta RESEARCH: cambio de etiqueta del sesgo del dólar; giro de un cruce elegible con rareza ≥ p80 y sin PIN/FLOW; F_MISMATCH siempre (Ley 2, fallo ruidoso). El estado de alertas guarda solo direcciones de cruces elegibles.

## 6. Capa "imán o barrera" (wall_behaviour.py v1.0) — solo canónico
Registrada desde el día 1 para un eventual CW-3, nunca votante, nunca mostrada como dirección: por (sesión, divisa, muro): lado, OI, distancia, MUERTO, ΔOI call/put vs la sesión anterior del mismo (vencimiento, strike), `near` (\|d\| ≤ 0,5 %), residuo de sonrisa (IV del strike menos IV interpolada en varianza total entre los vecinos válidos excluyendo el propio strike; NA sin vecinos), y a 5 sesiones el resultado THROUGH / PIN / AWAY (entre settlements; nunca intradía), con bandera DTE < 5 (pin mecánico de vencimiento). GEX por convención de signo: descartado (no identificable en FX listado; el "cliente" de CME es a menudo un banco cubriendo OTC). Ningún muro se reclasifica por el resultado del gate.

## 7. Estado
- 17-sep-2026: v2.0 construida sobre 74 sesiones canónicas (2026-06-01 → 2026-09-15). Cuadro de reconocimiento entregado al operador (no validación): sesgo del dólar 25-33 % en junio, 60-100 % desde el 20-ago; JPY la más débil desde el 2-sep (−3 % a −4,2 %); GBP MIXED 37/74 días. Filtro MUERTO probado: elimina 21 muro-días de 888 (la call AUD 0,7800 con 4.017 contratos a +9/+12 % del futuro y un put GBP a −6 %), nada más. Sesión 15-sep: JPY −3,07 % (U = 1), EUR −0,98 % (U = 0,68), GBP y AUD MIXED, sesgo USD alcista 84 % con 2 votantes; único cruce elegible EUR/JPY ▲ (+2,09 pp, rareza p44). Gate CW-2: RESEARCH 74/384, 0 fechas evaluables.
- Con la regla de ⅔ las cuatro patas del universo votan a la vez 27 de 74 días; por eso H0 (por pata) es la hipótesis primaria y H1 exige ≥ 3 cruces, no 4. Queda escrito antes de evaluar.
- Pendiente: exports BREADTH/POS para la incrementalidad; contraste CVOL periódico (solo ATM y signo del skew, diagnósticos).

# GATE M — Pre-registro del examen de la capa macro · v0.1

**Estado:** SELLADO 02-sep-2026. Sander delegó la redacción y aprobó sin objeciones. `sha256` en `gates/GATE_M_seal.txt`. No se edita más; cualquier cambio = v0.2 con acta.
**Se corre en:** Fase 6 (semana 16+), solo con los diez twin-tests cerrados. **No se corre antes.**
**Cierra:** hallazgos (a) y (b) de la auditoría del 02-sep-2026 (fecha de corte por script; cero gates sobre la capa macro).

---

## 1. Pregunta que examina

> ¿La capa macro del G8 (embudo 7+1 alimentado por los diez generadores), evaluada **fuera de muestra y punto-en-el-tiempo**, produce pares y dirección con más acierto del que se esperaría por azar, con un tamaño de muestra suficiente para afirmarlo?

Gate M **no** examina la cinta (cockpit 4H) ni la ejecución del operador: eso es el journal + Gate F. Gate M examina solo lo que el embudo entrega en §00: par, dirección, setup S1–S6.

## 2. Hipótesis (fijadas antes de ver un solo resultado)

- **H0:** la dirección del embudo a horizonte *h* no se distingue de una moneda al aire (p = 0,50).
- **H1:** p > 0,50.
- Se evalúa por horizonte *h* ∈ {5, 10, 20} días hábiles (`FORWARD_WINDOWS_BD` en cada `state.json`). **El horizonte primario es 10 bd.** 5 y 20 son secundarios y se reportan; no deciden.

## 3. Muestra

### 3.1 Fecha de corte por script (resuelve hallazgo a)
Cada script declara su propia `CUTOFF_DATE` en el generador. Todo lo posterior es OOS para ese script. **Propuesta inicial — Sander confirma o corrige con la fecha real de la última calibración de cada Pine:**

| Script | Pine version | Última calibración empírica (fuente: cabecera del script) | CUTOFF_DATE |
|---|---|---|---|
| IYDT_2Y | v1_5_3_EN | 10-jul-2026 (F1Y1Y, calibrate_f1y1y.py, 2016-26) | 2026-07-10 |
| IYDT_10Y | v1_4_1 | 10-jun-2026 (calibrate_iydt_xccy.py v2.0, 2016-26) | 2026-06-10 |
| CURVA | v1_4_2 | SIN CALIBRAR — umbrales de diseño (v1.4.2 solo añade plots de export) | 2024-06-30 |
| PSI | v2_2_3_EN | 06-jun-2026 (psi_classifier_calibration_2026-06-06) | 2026-06-06 |
| XCCY | v2_6_1 | 10-jun-2026 (ventana sep-2022→jun-2026) | 2026-06-10 |
| POS | v1_1_3_EN | 19-jun-2026 metales (CFTC 2006-26); DIVISAS PROVISIONALES ±1.5/±2.0 | 2026-06-19 |
| POL | v1_2_4_EN | 11-jun-2026 (calibrate_pol.py, ventana 2026) | 2026-06-11 |
| RISK | v1_1_1 | 11-jun-2026 (calibrate_risk.py, 2014-26, n=2976) | 2026-06-11 |
| RTF10 | v1_0-ADJ-r2_5 | SIN CALIBRAR — umbrales de diseño (r2.5 solo añade plots de export) | 2024-06-30 |
| FFVA | v1_3_2_EN | 29-jun-2026 (calibrate_ffva.py --multi, ~21 años) | 2026-06-29 |

**Consecuencias declaradas:** (i) la ventana OOS del embudo completo empieza el **2026-07-10**; con la regla de 6 meses mínimos, el Gate M no puede correrse antes de **2027-01-10**. (ii) CURVA, RTF10 y las divisas de POS entran con umbrales de diseño, no empíricos; se examinan igual y así consta. No se calibran antes del port (D10).

Regla: si un script se recalibró después de 2024-06-30 (como XCCY y RISK), su OOS empieza en esa recalibración. **La ventana OOS del embudo completo es la intersección**: desde la CUTOFF_DATE más tardía hasta la fecha de examen. Si esa intersección tiene menos de 6 meses, el examen se **aplaza**, no se relaja.

### 3.2 Reconstrucción punto-en-el-tiempo
- El histórico se reconstruye con `sources/registry.csv`, usando `data_date` **más el `publish_lag` declarado**: una observación solo existe para el embudo a partir del momento en que estaba publicada. Un dato T+1 publicado a las 20:15 UTC no entra en el run de las 16:05 de ese día.
- Inputs manuales (NZD, CHF): se usa el último valor con fecha ≤ run; si estaba caducado en ese momento, entra como `MANUAL_EXPIRED` y el generador hace lo que haría en vivo (NA = pausa).
- Feeds `NEW_PORT` sin histórico suficiente antes de su CUTOFF: el script entra en NA para esas barras. **No se rellena con proxies no declarados.**

### 3.3 Unidad de observación
Una fila por **run del embudo** (cada 4 h) en la que §00 emite un par con dirección ≠ NONE. Runs consecutivos con el mismo par+dirección+setup se colapsan en **una decisión** (la primera). Esto evita inflar *n* con autocorrelación.

## 4. Métricas y umbrales (congelados)

| Métrica | Definición | Umbral de paso |
|---|---|---|
| **Hit rate @10bd** (primaria) | % de decisiones donde el spot del par se movió en la dirección emitida a 10 bd (cierre ECB ref) | > 0,55 **y** binomial unilateral p < 0,05 contra 0,50 |
| n mínimo | decisiones colapsadas en OOS | ≥ 60. Con n < 60 el resultado se reporta como **INCONCLUSO**, no como fallo ni éxito |
| Hit rate @5bd, @20bd | idem | se reportan; no deciden |
| Expectancy @10bd | media del retorno del par en la dirección emitida (en % y en unidades ATR-20 del par) | > 0 |
| Abortos correctos | % de runs con ABORT/Hard Stop 9 en los que el par "abortado" tuvo |retorno@10bd| < 0,5 ATR | se reporta |
| Asimetría por familia | hit rate por familia de confluencia y por setup S1–S6 | se reporta; ninguna familia con n ≥ 20 puede tener hit rate < 0,40 sin acta |
| Sensibilidad al DQM | hit rate en runs con `global_health` ≥ 80 vs < 80 | se reporta |

**Resultado del gate:**
- **PASA** si primaria cumple y expectancy > 0.
- **FALLA** si n ≥ 60 y (hit rate ≤ 0,50 o expectancy ≤ 0).
- **INCONCLUSO** en cualquier otro caso → se repite en 6 meses, sin tocar umbrales.

## 5. Lo que está prohibido durante el examen
1. Cambiar cualquier umbral de `THRESHOLDS` de cualquier generador (Regla 32).
2. Cambiar `CUTOFF_DATE` después de sellar.
3. Excluir periodos ("ese mes fue raro") — si hay un evento de datos (feed muerto), se documenta y la fila queda con su `dqm`, pero no se elimina.
4. Correr el examen a medias para "ver cómo va". Se corre una vez, completo, y el resultado va a `gates/actas/GATE_M_acta_v0.1.md` con el commit del histórico reconstruido.
5. Probar horizontes o métricas alternativas y quedarse con la mejor.

## 6. Qué cambia según el resultado
- **PASA:** la política de riesgo (Fase 6) autoriza tamaño mínimo real; el journal empieza a acumular track record. Nada más cambia.
- **FALLA:** el sistema sigue corriendo en modo observación (no se apaga); se abre acta por familia de confluencia para localizar dónde falla; ninguna recalibración sin twin-test posterior.
- **INCONCLUSO:** modo observación, re-examen a 6 meses.

## 7. Firmas
- Redactado por: Claude, 02-sep-2026.
- Objeciones de Sander: ninguna (02-sep-2026, delegación explícita).
- Sellado: 02-sep-2026 — hash en gates/GATE_M_seal.txt

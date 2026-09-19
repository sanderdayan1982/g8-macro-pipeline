# ACTA — Enmienda del ancla temporal del twin-test POS

- **Fecha:** 2026-09-19
- **Decisión de:** Sander ("OK" al cambio propuesto, 2026-09-19)
- **Alcance:** `scripts/twin_test.py`, adaptador `_pos_g8`. No toca umbrales calibrados ni pesos congelados.
- **Tipo:** enmienda material al procedimiento del test (no a la lógica de POS-G8, el script de posicionamiento COT de la suite G8; COT = Commitments of Traders).

## 1. Hecho que originó el acta

El run #57 de G8 Port Run (2026-09-19) dio `FAILED` en el twin-test POS: 26,67 % de discrepancia de estado (8 de 30 comparaciones), 3 fechas de reporte (25-ago, 01-sep, 08-sep). Desglose: JPY 100 %, NZD 66,67 %, AUD/CHF/EUR 33,33 %, resto 0 %.

Reproducido en local con `twin_test.py` y los archivos reales (`pos_g8_pine.csv`, `pos_g8_py_history.csv`): resultado idéntico.

## 2. Causa

`_pos_g8` comparaba el reporte de Python de la fecha R (martes del reporte) con la barra del Pine en **R+9**, bajo el supuesto de que esa barra era "estable e inequívoca".

En el export histórico, el Pine estampa cada reporte en su **propia fecha R** (R+0): los valores cambian en las barras 25-ago, 01-sep, 08-sep y 15-sep. A R+9 la barra ya contiene el reporte R+7. Por eso se comparaba el reporte R contra el reporte siguiente.

## 3. Evidencia (copia de trabajo, archivos reales, script real)

| Ancla | Estado | Discrepancia | Fechas |
|---|---|---|---|
| R+0 a R+5 | RUNNING | 2,5 % (1 de 40) | 4 (25-ago → 15-sep) |
| R+6 | RUNNING | 0 % | 3 (falta la barra del 21-sep) |
| R+7 en adelante | FAILED | 26,67 % | 3 (igual que el run #57) |

## 4. Cambio

En `_pos_g8`: `pd.Timedelta(days=9)` → `pd.Timedelta(days=0)`, más comentarios. Nada más. La tolerancia de `merge_asof` (3 días), `MIN_DATES`, `MIN_WEEKS` y `MAX_DISCREPANCY_PCT` no cambian.

## 5. Resultado antes / después

| | Ancla R+9 (original) | Ancla R+0 (enmendada) |
|---|---|---|
| Estado | FAILED | RUNNING |
| Discrepancia | 26,67 % | 2,5 % |
| Comparaciones | 30 (3 fechas) | 40 (4 fechas) |

El resultado original a R+9 queda archivado en esta acta y **no se borra**.

## 6. Discrepancia residual, abierta (no es de alineación)

**AUD, reporte 2026-09-15:** Python = `STRETCH+`; Pine = `CROWD LONG` (z del Pine ≈ 2,03–2,05 en las barras 15 a 17-sep, con umbral CROWD = 2,0). Implica que el z de Python queda justo por debajo de 2,0. Causa **no determinada**. Pendiente: leer el z de AUD de Python para esa fecha (`data/pos_g8_cot.json`) y comparar con el del Pine.

## 7. Reglas de operación tras la enmienda

1. El export del Pine debe hacerse a partir del sábado siguiente al reporte (R+4 o más), cuando el espejo de TradingView ya lo ingirió. Si se exporta antes, la barra R aún trae el reporte anterior y produce un falso desacuerdo.
2. El reloj de 4 semanas **no se reinicia**: sigue contando desde 2026-08-25 (primer run del twin-test). Con esta ancla el estado podría pasar a PASSED en cuanto `weeks_elapsed` ≥ 4 (2026-09-22) si la discrepancia se mantiene ≤ 10 %.
3. Un PASSED bajo esta ancla queda etiquetado como obtenido bajo la enmienda de esta acta.

## 8. Límites de esta acta

- La causa (el Pine estampa en R+0) está observada en el export del 2026-09-19; no se ha verificado el código Pine ni el comportamiento en tiempo real de TradingView.
- Un export histórico coloca el dato en la fecha del martes aunque el reporte se publique el viernes. Esa historia **no sirve** para medir retornos hacia adelante sin ajustar por la fecha de publicación.

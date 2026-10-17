# ACTA A3 — Relleno ACM a 8 días y diagnósticos de contagio en SHADOW (17-oct-2026)

**Origen:** Argon 4 aprobó A2 (`71f0c92`, aplicado en `0a6c11f`) sin observaciones y dio luz verde a A3. Enmienda v2 #1 (relleno) y #2 (NZD fuera hasta B5).
**Base:** `0a6c11f` (`A2: contrato permanente entre la suite y el dashboard`).
**Dueño de la señal de §01-b:** sigue siendo el Paso C (`scripts/s01b.py`, `data/s01b/state.json`, `data/s01b/log/*.json`, `data/S01B.json`). Este lote no los modifica.

## 1. Relleno ACM, 5 → 8

`scripts/acm_g8.py` deja el arrastre en una sola constante, `FFILL_LIMIT = 8`. La usan el panel diario y el panel de historia. La marca `_FFILL` sigue igual: solo dice que la fila se construyó con algún tramo arrastrado. Y10, RNY y TP de una fila ya observada no se reescriben por alargar el límite; el panel gana las filas de un hueco de 6, 7 u 8 sesiones y sigue soltando la novena.

`sources/nowcast_aud.json` no entra. Su `max_horizon_bd` ya es 10 y es el horizonte de la estimación diaria AUD, no el `ffill` del ACM. Bajarlo a 8 sería otro modelo. Se queda en 10.

Este lote no regenera los CSV de ACM ni los logs del Paso C. El límite nuevo actúa en la próxima ejecución de `acm_g8.py`. `scripts/s01b.py` no se toca: su SHA-256 sigue siendo `0c43d6dd54677974bf4ffde880de049396f4b0d9cdd1c6e39964d796ef132ee5`. El comentario interno que aún dice «ffill ≤ 5» queda congelado con el archivo. La bandera `ACM_FFILL` sigue saliendo cuando la sonda va detrás de la fila, sea el arrastre de 5 o de 8. El tooltip de `docs/index.html` dice 8 sesiones, que es el límite del motor.

## 2. Diagnósticos en SHADOW

Viven en `scripts/s01b_shadow_a3.py`. No llama a `evaluate_ccy` y no nombra `state.json`, `S01B.json` ni `events.jsonl`.

Divisas leídas: EUR, GBP, JPY, AUD, CAD, CHF. USD es la referencia. NZD sale con estado `EXCLUIDA_B5` y no aporta observaciones al percentil. CHF se lee con el mismo signo XXX/USD del canónico; no enciende CTF y este módulo tampoco.

### Fecha real de la curva

- AUD y CAD: la sonda es `AUD_NOM_2Y.csv` / `CAD_NOM_2Y.csv`, la misma del Paso C. `acm_input_asof` es la última observación oficial ≤ fecha de la fila. Manda la sonda, no el sufijo.
- El resto: si la fila no lleva `_FFILL`, la fecha real es la de la fila. Si lo lleva, es la impresión real anterior.
- El TP que entra en la cuenta es el de esa impresión real. El TP escrito en una fila `_FFILL` posterior no se usa, aunque sea distinto porque otros tramos sí se movieron.

### `dtp22_local`

`ΔTP22_i − β_{i,USD} · ΔTP22_USD`, en puntos básicos. Las dos diferencias se miden entre `acm_input_asof_t0` y `acm_input_asof_t` de la divisa i.

USD tiene que tener una impresión real (fila con esa fecha y sin `_FFILL`) en las dos fechas. Si falta una, o la fila de esa fecha es un arrastre, el resultado es `None`. No hay join por fecha de CSV. El caso prohibido queda en el test: la curva del día 7 contra la fila del día 15 daría −30 pb y el módulo devuelve vacío.

Si las dos fechas reales coinciden, también es `None`: no hay dos curvas que restar.

`β_{i,USD}` es la pendiente MCO sin constante de los cambios de TP entre impresiones reales compartidas, en pb. Sin constante porque la fórmula firmada no lleva α. El corte es la fecha real final: el paso que termina ese día no entra en la pendiente con la que se lee. Hacen falta 60 pasos. Por debajo, `SIN_BETA` y `dtp22_local` vacío.

### `rho_tp_fx_22`

Correlación de Pearson de las 22 variaciones diarias `(ΔTP_d, Δln FX_d)` de la ventana del Paso C (ancla t₀ más 22 sesiones TARGET).

`ΔTP_d` usa el TP de la curva real vigente ese día. Una racha arrastrada con el mismo TP real da variación 0; si no hay varianza, ρ es `None`. `Δln FX` es el retorno del canónico `r_<CCY>`: positivo significa que la divisa se fortalece frente al USD (XXX/USD). No se invierte el signo. Es el mismo convenio con el que §01-b pide `ΔFX22 ≤ −0,5 %` para decir que la divisa cae. CHF usa esa cotización solo como lectura.

### Percentil

`pct_dtp22_local` es el p80 lineal, el mismo interpolador que `s01b.pct`, de los `dtp22_local` anteriores de esa divisa. Mínimo 60. No es θ, no gatea CTF y no mezcla a NZD ni a USD.

## Lo que no cambia

Umbrales, histéresis, población de `ΔTP22`, estados CTF / PRE_CTF, FX del Paso C, archivos congelados por SHA-256, contrato A2, MDP semanal y la sombra B2. No se abre B5.

## Tests

`tests/test_a3_shadow.py` y el panel de `tests/test_p3_acm_ffill.py` (el hueco de 9 días deja 8 filas marcadas y suelta la novena).

Medido en este lote: `python -m unittest discover -s tests` → **808 OK** (6 omitidas). Equivalencias E2 del 21-sep y del 22-sep, reales y sintéticas: EQUIVALENT ×4. El SHA-256 de `scripts/s01b.py` no cambió.

## Siguiente paso

Parar aquí para la auditoría de Argon. El orden que queda es B5 → B6 → C1 / CB_EVENT_48H (SHADOW) → B3.

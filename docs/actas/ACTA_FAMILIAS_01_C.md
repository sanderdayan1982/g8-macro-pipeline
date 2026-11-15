# ACTA FAMILIAS-01 C — estados del FXVOL en sombra

**Orden:** FAMILIAS-01, módulo C. Diseño firmado. Un PR contra `main`. No se fusiona desde aquí. No se despliega.

## Hallazgo
- `scripts/cme_fxvol_shadow.py` publica `data/CME_FXVOL_shadow.json` y la historia `data/options/fxvol_history.csv`. El régimen ya publicado (`vol_regime`) y el percentil `iv_pct` se quedan como están.
- No había estados con histéresis de la IV, de la prima (IV30 − RV21) ni del sesgo con signo R11. Esos cortes no estaban en el código.
- El canónico de opciones tiene 617 sesiones (2024-06-03 … 2026-11-12). El JSON commiteado sigue en la sesión 2026-11-12 con la foto anterior (116 sesiones en el fichero). Regenerar esa historia queda fuera de este lote.
- `data/OPTIONS_SURFACE.json`, `generated_utc` 2026-11-15T21:29:40Z: el veredicto de CHF es PROXY; el de CAD es CLEAN. La etiqueta de proxy sigue ese veredicto, no una lista fija.

## Decisión
- La lógica vive en `scripts/fxvol_states.py`. Es una función de toda la historia: no guarda el estado de ayer. Cada pasada recorre la serie, así que añadir una sesión no reescribe las anteriores.
- IV = `iv30_atm` de esa divisa, sin mezclar otra serie en la ventana. Sesgo = `rr25` con el signo R11 aplicado una sola vez (JPY, CAD y CHF). RV21 = settle del futuro CME del mismo contrato, 21 rendimientos log, desviación muestral, √252, la regla que ya usa el shadow.
- Cortes constantes, los de la tabla firmada: 20/30, 80/70, 95/85 con el cambio a 5 sesiones > 0, prima 90/80, z ±1,5 / ±1,0, percentil 90 del valor absoluto del cambio de sesgo a 5 sesiones. Histéresis de 2 cierres para entrar y para salir. El giro del sesgo es del día, sin histéresis.
- Precedencia en volatilidad: estrés, luego compra, luego calma, luego normal. Al salir de estrés, si el percentil sigue en 70 o más se va a compra; si no, a normal.
- Ventana 252. Con menos de 60 observaciones válidas, no disponible. Entre 60 y 251, el estado usa la ventana que hay y lleva la marca provisional. La cabecera del bloque de método, si algún día se publica, mantiene la marca de umbrales sin calibrar hasta un acta de GATE.
- Falta IV, RR o BF: las cuatro columnas no disponibles, sin arrastrar el valor ni el estado, y al día siguiente se parte de normal / columna vacía. Un cero presente se conserva. La sesión ausente no entra en la ventana y no se rellena. Si falta una punta del cambio a 5 sesiones, esa columna no se clasifica ese día. Sin 21 rendimientos del mismo contrato, la prima no se clasifica; el resto puede.
- Sesión con más de un día hábil de retraso (lunes a viernes, sin calendario de festivos: el texto no nombra uno): la fila que se publicaría queda marcada vieja y no se clasifica. Las filas anteriores no se tocan.
- NZD sale siempre NA (`NO_CME_OPTIONS`).
- Bandera `FXVOL_STATES_PUBLISH = False` en el generador del JSON. Los estados se calculan. Con la bandera apagada no se copia ningún campo nuevo al JSON, ni a `docs/`, ni a un fichero de salida.

## Puntos leídos así, para no inventar un segundo diseño
- «252 VRP ⇒ ≥ 272 sesiones». El umbral que manda es 252 observaciones de prima. Con la regla actual de RV21 hacen falta 22 settles (21 rendimientos), así que la observación 252 cae en la sesión 273 si no hay huecos. No se ha puesto un corte distinto en 272.
- El percentil 90 del cambio de sesgo es la interpolación lineal que ya usa el percentil de la casa (`dashboard_alerts.pct`). El z usa desviación muestral (N−1), la misma que RV21.
- Día hábil = lunes a viernes.
- CHF lleva la etiqueta de proxy porque su veredicto lo es. CAD, hoy CLEAN, se clasifica sin esa etiqueta. Si el veredicto pasa a PROXY, la etiqueta entra sola.

## Sin cambios
- Fórmulas, `vol_regime`, `iv_pct` y el resto de claves ya publicadas. Ningún test ya existente. Pine, workflows, `docs/index.html`, Netlify. No se ha regenerado `data/CME_FXVOL_shadow.json` ni `data/options/fxvol_history.csv`.
- `SHOW_06B_EXT` sigue en false.

## Pruebas
- `tests/test_familias01_c_fxvol_states.py`, nuevo. Fixtures sintéticas, sin cifras de CME. T-C1 … T-C14 y la etiqueta de proxy. En verde (Python 3.12.3).
- Diff del `build()` en memoria, antes y después de este cambio, misma máquina, mismos canónicos: igualdad de diccionario (claves y valores). El RV21 de la función nueva coincide con el del shadow en una muestra cada 25 sesiones y en la última, seis divisas, 0 diferencias.
- La foto de claves de ese `build()` de `main` está en `tests/fixtures/familias01_c/main_build_keys.txt` (solo nombres de clave).
- `python3 -m unittest discover -s tests -p 'test_*.py'` el 15-nov-2026, 611 s: 994 tests, 4 fallos, 165 errores, 22 skipped. Los 165 errores son `ModuleNotFoundError` (`requests`, `openpyxl`, `pandas`): esta máquina no tiene el `requirements.txt` completo. No salen de este lote.
- Fallos de código, los tres que ya estaban en `main`: `test_flujos01_pr2` live_eur, `test_fxvol_shadow` additive_fields y five_sessions. `idempotent_and_under_60s` pasó (43,5 s). El cuarto fallo del pase largo fue T-C12 con un escáner que aún no acotaba el token; corregido, T-C12 pasa. Los ficheros que la suite reescribió (`data/CME_FXVOL_shadow.json`, `data/options/fxvol_history.csv`) se restauraron y no entran en el commit.

## Siguiente
- Sander fusiona si el módulo le encaja. La bandera sigue apagada: nada de esto entra en la web ni en el JSON público. Encenderla es otro paso, con su acta.

# ACTA DATOS-ARGOS-01 — 13-nov-2026

Orden firmada por Sander 13-nov-2026 19:01 WAT. Sin merge.

## Hallazgo

- `dominant_driver` en el raw de main (`data/argos_rates.json`, `generated_at` 2026-11-13T02:07:35Z) no está vacío: USD/AUD/NZD `REAL_LED`, el resto `BE_LED`.
- PR #16 usa lookback 21. §01 diario pinta 1M con `deltaBps(lb=21)`; §01-b usa `H=22`. Solo USD cambia de etiqueta entre 21 y 22, y el `d_be` pintado no coincide con un lookback fijo en todas las divisas.
- C3: el render no tiene cuadrante 2×2. Se deja `UNAVAILABLE`.
- CHF real −0,98 % = nominal SNB − IPC BFS 1,50 % (oct-2026). El 50 es pb del paso mensual, no un error de signo ni de unidad.
- `health.json` `DEGRADED` (2026-11-13T15:53:09Z): evidencia de JP/AU/NZ/CH caducada el 2026-11-11T14:05Z (tope de 7 días) y CAD 2Y / RY sin la sesión 2026-11-12 que el BoC ya publicó. El 11-nov no hay observación (Remembrance Day; el calendario CA del repo no lo lista). `NZD_CASH_ON` sigue `UNKNOWN`.

## Decisión

- Driver publicado = `|d_be|` de S01B frente a `|Δreal|` en los mismos `t`/`t0`. CHF → `UNAVAILABLE`.
- Renovar `verified_at` de JP, AU, NZ y CH tras leer la decisión vigente (tipo sin cambio).
- Escribir CAD 2Y y `RY_G8_CAD` con el conector oficial (BoC Valet, 2026-11-12).
- No se toca el cron: la publicación diaria de los últimos hábiles queda antes de las 05:30 WAT.
- No se toca `docs/index.html` ni tests existentes.

## Sin cambios

Metodología de la suite (Pine, umbrales, pesos, ACM, s01b, z). El test A3 EUR `dtp22_local` (issue #15).

## Pruebas

`python -m unittest discover -s tests -p 'test_*.py'`: 908 pruebas, 6 skips previos, 0 fallos en este checkout. `test_a3_rows_on_the_real_files` queda en verde porque `S01B.json` as_of `2026-11-13` ≠ `S01B_shadow_a3.json` t `2026-11-12` (no entra en el formateo de `dtp22_local`, que sigue `null` / `FECHAS_NO_CUADRAN`). El test no se ha tocado. Issue #15 abierto.

## Siguiente

Merge lo decide Sander. C3 queda pendiente hasta que el panel calcule el cuadrante.

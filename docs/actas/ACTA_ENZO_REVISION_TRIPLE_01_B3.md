# ACTA — ORDEN_ENZO_REVISION-TRIPLE-01, bloque 3 (14-nov-2026)

**Orden:** Sander, ORDEN_ENZO_REVISION-TRIPLE-01, firmada 14-nov-2026 19:16 WAT. Bloque 3.
**Alcance:** un lado COT ausente no se rellena con 0. El neto pasa a null. Un cero presente sigue siendo 0.

## Hallazgo
`scripts/pos_g8_cot_collector.py` (antes, líneas 350-353) calculaba el neto LF y el neto AM así:

```text
(largo or 0) − (corto or 0)   si el largo no es None; si no, None
```

`or 0` convierte un corto ausente en 0. Un marcador no numérico, incluido `STALE`, ya sale de `_f` como None y quedaba rellenado igual. Un cero presente también es falsy, pero `0 or 0` sigue siendo 0: el cero no era el fallo.

## Decisión
Nueva `_side_net`: si falta el largo o el corto, el neto de esa semana es `None` (null en el JSON). `STALE` no se rellena. Si los dos lados están, el neto sigue siendo largo − corto. Ventanas, umbrales, tope de Z y el estado no cambian. `build_metal` (metales, mismo `or 0`) queda fuera: la orden nombra las líneas 350-353.

## Sin cambios
- `data/`, scripts Pine y workflows. No se regenera ni se sube `pos_g8_cot.json`.
- Ningún test existente. No hay huella congelada en `tests/` que cubra `scripts/pos_g8_cot_collector.py` (SALUD-1: no había hash que actualizar).
- Metodología: la resta, cuando los dos lados existen, es la de antes.

## Impacto sobre lo publicado [MEDIDO, 14-nov-2026]
El repo no guarda las filas crudas de CFTC (solo el neto ya calculado). Se volvió a pedir a la fuente primaria la misma consulta del colector y se recalculó en local, sin escribir en `data/`.

- Fuente: `https://publicreporting.cftc.gov/resource/gpe5-46if.json` (TFF Futures-Only). Comprobado el 14-nov-2026.
- Ventana del colector: 7 divisas × 175 semanas, 2023-07-03 → 2026-11-03 (1225 filas). El informe publicado en `data/pos_g8_cot.json` es el 2026-11-03, el mismo cierre que devolvió la fuente.
- Lados ausentes o `STALE`: 0. Netos que pasarían de número a null: **ninguno**.
- El documento recalculado coincide con `data/pos_g8_cot.json` en neto, Z, estado, percentiles, deltas y el proxy USD (0 diferencias). Los netos de `data/state/pos_g8.json` coinciden.
- Historial publicado en git de `data/pos_g8_cot.json`: 67 commits, 469 filas de divisa, 77 combinaciones distintas de fecha/divisa/neto. Todas esas fechas caen dentro de la ventana cruda. Ninguna pasaría de número a null.

## Pruebas
- Nuevas, en `tests/test_pos_g8_cot_missing_side.py`: corto ausente → null; largo ausente → null; los dos presentes → igual que la resta anterior; cero presente → 0. `STALE`, cadena vacía y clave omitida cuentan como ausente. El `None` se serializa como `null`. Ningún test existente se ha modificado.
- Suite completa (`python3 -m unittest discover -s tests -p 'test_*.py'`, Python 3.12.3 con `requirements.txt`; en esta máquina no hay 3.11): **998 tests, 3 fallos, 0 errores, 6 omitidos, 409 s**. Los 3 fallos son los ya conocidos del backfill de opciones en curso: `tests/test_fxvol_shadow.py` (`test_additive_fields_do_not_recompute_existing_ones`, `test_five_sessions_within_tolerance`) y `tests/test_flujos01_pr2.py` `Rv21Vectors.test_live_eur_matches_the_oracle_and_the_roll_session_is_na`. No hay ningún otro rojo.

## Siguiente
El PR queda abierto contra `main`. No se fusiona.

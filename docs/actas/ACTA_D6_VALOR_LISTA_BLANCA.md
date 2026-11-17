# ACTA D-6 — lista blanca de `valor` (orden t234u, 17-nov-2026)

## Hallazgo

`data/votes.csv` es público. `scripts/votes_ingest.py` copiaba a `valor` el número que trae el correo (o el E21 del factor) para varios módulos. La fuente primaria de los módulos de correo no está en el repo (edición ARGOS v1.0.1 §2, búsqueda ya registrada el 13-nov-2026). E21 sí: residual de `scripts/usd_factor.py` a partir de los tipos de referencia del BCE (`data-api.ecb.europa.eu`, EXR `D.*.EUR.SP00.A`).

## Decisión

`valor` queda vacío salvo `ALLOWED_VALOR_MODULES`. Entra solo E21. Fuera, hasta que Sander diga lo contrario: `RISK_G8`, `POL_G8`, `FFVA_G8`, cualquier id `XCCY`, y `13-CRD` / `CRD_G8`. El resto de módulos de correo queda fuera como fuente dudosa. No se reescriben filas ya publicadas. El corte del test es `2026-11-17`. El workflow de ingest sigue desactivado; este cambio no lo toca. Umbrales, Pine y `_vote` no cambian.

## Sin cambios

`data/votes.csv`, `.github/workflows/votes_ingest.yml`, tests ya existentes, hashes de `tests/test_exclusions.py`.

## Pruebas

`tests/test_allowed_valor_modules.py`. La suite arrastra a rojo los tests ya existentes que esperan el número del correo dentro de `valor`. No se editan. Lo decide Sander.

## Siguiente

Sander dice qué módulos de correo, si alguno, entran en la lista. D-7 sigue aparcado.

# ACTA P-10 — CHF: real ex post con el IPC oficial (BFS, mensual) (6-oct-2026)

Autorizado por el propietario (6-oct-2026): «¿el real y el breakeven no son fórmulas?… SÍ, AÑÁDELO».

## Hallazgo
- Nominal = real + breakeven es una identidad con tres incógnitas. En USD, EUR, GBP, CAD, JPY, NZD y AUD se observan el
  nominal y el real (bonos indexados) y el BE sale de la resta. **Suiza no emite bonos ligados a la inflación**: solo se
  observa el nominal (SNB, diario). Cualquier real/BE de mercado CHF sería inventado.
- Medida estándar y transparente disponible con datos oficiales: **real ex post** = nominal − inflación interanual del IPC.
  Es otro concepto (inflación pasada, no expectativas) y se etiqueta así; la sesión del 4-oct lo había descartado por eso y
  el propietario lo aprueba ahora explícitamente.

## Fuente (verificada 6-oct-2026)
- BFS/OFS, «LIK/IPC Indexierungstabelle», nº de pedido `cc-e-05.02.08`, hoja `Index_m`, columna `% m-12` (oficial, 1 decimal).
  Septiembre 2026 publicado el 1-oct-2026 08:30 CEST: **1,0 %**. Último fichero resuelto por nº de pedido:
  `https://dam-api.bfs.admin.ch/hub/api/dam/assets?orderNr=cc-e-05.02.08` → `…/assets/36878073/master` (xlsx, 119 KB).
- Contraste: SNB `plkopr` VVP (publica 3 semanas más tarde): agosto 2026 0,807 % frente a BFS 0,8 % ✓.

## Decisión
1. `scripts/fetch_chf_cpi.py` → `data/CHF_CPI_YOY.csv` (OHLCV, fecha = fin del mes de referencia, desde 2000; 321 meses
   hasta 2026-09-30). Ingesta segura; formato cambiado → error explícito.
2. Pasadas: *Daily Data Update* (`chf_cpi`, tope 120 s) e intradía EU (13:17Z; la BFS publica hacia las 06:30Z).
3. §01: real CHF = nominal SNB − IPC a/a (serie mensual, ffill); BE = IPC a/a. Etiquetas propias «REAL ex post · mm/aa» e
   «IPC a/a · mm/aa», con el aviso de que NO es breakeven. Solo si el IPC es del mes en curso o de los 2 anteriores (publicación).
4. §00: `real`/`be` CHF con el flag «REAL ex post (nom − IPC a/a BFS mm/aaaa); BE = IPC a/a, no breakeven».
   Diferenciales vs USD y alertas sin cambios.
5. §05: feed `CHF_CPI_YOY` (mensual, 25 d.h.); registro de fuentes; regla de frescura `CHF_CPI` (monthly:BD1, 08:30
   Zúrich, margen 4 días, consultas EU|FINAL).
6. Agente: el descargador es reparable (fetch_*); instrucciones actualizadas: nunca presentar el IPC como breakeven.
7. Huella autorizada: `dashboard_alerts.py`.

## Sin cambios
s01b (CHF sigue «solo lectura», ΔBE CHF sin feed), ACM, fórmulas y umbrales; resto de divisas.

## Pruebas
`tests/test_p10_chf_cpi.py` (7):
- lector BFS (columna, fin de mes, formatos cambiados que dan error);
- elección del asset por nº de pedido;
- §00 con real ex post y flag; IPC viejo → no se usa;
- etiquetas y tope en §01; compuerta y pasadas.
Descarga en vivo (copia aparte): 321 meses, 2026-09 = 1,00 %. Monitor de salud: `CHF_CPI_YOY.csv` CURRENT (monthly:BD1).
Suite completa + `validate_smoke.sh` (con renderizado en Chromium): verde.

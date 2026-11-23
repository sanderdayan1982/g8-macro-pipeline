# Fixtures t344u

Datos fijos para desacoplar de los CSVs vivos los 7 tests que rompía
la publicación oficial B2 del 20-nov. Sin series CME/ICE ni OPTIONS_SURFACE.

## nzd/

- `NZD_CASH_ON.csv`: recorte 9–20 nov 2026. Oficiales 9, 12 y 20-nov;
  PROXY (OCR) el 13 y 16–19 nov, como en el B2 del RBNZ (la columna
  Overnight interbank cash rate sale «–» esos días).
- Fuente primaria: https://www.rbnz.govt.nz/statistics/series/exchange-and-interest-rates/wholesale-interest-rates
  Released: 23 November 2026. Valor oficial 20 Nov 2026: 2.84 % pa.
  12 Nov: 2.80. OCR (INM.DP1.N) sigue en 2.75.
- `NZD_CASH_ON_2026.csv` + `NZD_OCR_2026.csv` + `NZD_CASH_ON_proxy_band.json`:
  banda official 2026 (n=95, last=2026-11-20, mean=7.758) excluyendo PROXY.

## argos/

Instantánea pública coherente (árbol `f4a3873f`, 23-nov 09:29 WAT) en la que
el overnight sigue en PROXY y el floor NZD pinta NA / SIN DATO, más el
`argos_rates.json` generado por `argos_rates.render` sobre ese `data/`.

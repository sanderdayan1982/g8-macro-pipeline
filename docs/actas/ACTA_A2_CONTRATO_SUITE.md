# ACTA A2 — Contrato de coherencia Suite G8 ↔ dashboard (17-oct-2026)

**Origen:** Argon 4 aprobó la validación de A5 y el lote B2, y abrió A2. La regla de verificación pedida es que `FLOOR_EUR.csv` salga de la serie oficial DFR del BCE.
**Base:** validación A5 + B2 (`d54c2fe`), encima de `243a8d0`.
**Dueño:** `sources/suite_contract.json`. No es un panel. Si el código se aparta, `tests/test_suite_contract.py` falla.

## Qué queda escrito

| Pieza | Dónde vive en el código, medido |
|---|---|
| POS-G8 v1.2.6, `fx_ccy`, USD 0,79/1,15, XAU 1,68/2,29, XAG 1,61/2,25 | `pos_g8_cot_collector.py`, `generators/pos_g8.py`, `twin_test.py` |
| `am_thresholds_shadow`, ventanas 156 / 156 / 104 | El mismo colector. La AM sigue en SHADOW. |
| §02 AMPLE ≤ 2,0 bp, TIGHT por encima de 2,0 y hasta 10,0, PRESSURE por encima de 10,0 | `dashboard_alerts.py` (`floor_badge`) y `docs/index.html` (`last > 2`, `last > 10`). El 2,0 exacto es AMPLE y el 10,0 exacto es TIGHT. |
| Suelos | USD IORB → `FLOOR_USD.csv`. EUR DFR → `FLOOR_EUR.csv`. CAD objetivo BoC → `FLOOR_CAD.csv`. GBP, JPY, AUD, CHF y NZD usan el tipo oficial ya publicado (`GB_POLICY`, `JP_POLICY`, `AU_POLICY`, `CH_POLICY`, `NZD_OCR`). |
| 28 pares | Prioridad de base EUR, GBP, AUD, NZD, USD, CAD, CHF, JPY. Cada combinación de las 8 divisas, una sola vez. |
| Opciones CME | JPY, CAD y CHF se invierten: CME cotiza XXX/USD y el operador usa USD/XXX. NZD no tiene superficie en el dashboard. |
| COT | Informe con fecha del martes. Publicación de la CFTC el viernes. El dashboard corre el viernes 21:05 UTC y reintenta el sábado 09:05 UTC. No hay pasada del miércoles en el repo. |

## Regla de verificación del DFR

`scripts/fetch_floor_spreads.py`, función `fetch_eur`, pide a la API del BCE la serie `FM/D.U2.EUR.4F.KR.DFR.LEV` y la escribe en `FLOOR_EUR.csv`. Es la Deposit Facility Rate. El test exige esa clave dentro del script y dentro del contrato. No se ha cambiado el descargador.

## Lo que no cambia

Umbrales, Kalman, §01, §01-b, §06, §07, §08 y el libro. Los archivos congelados por SHA-256 no se han tocado. No se abre A3.

## Siguiente paso

Parar aquí para la auditoría de Argon. El orden que queda es A3 (SHADOW) → B5 → B6 → C1 / CB_EVENT_48H (SHADOW) → B3.

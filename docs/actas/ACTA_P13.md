# ACTA P-13 — Intradía: un FAIL_TRANSIENT del BCE no tumba el job ni deja CAD/AUD sin intentar

Autorizado por el propietario (27-oct-2026): cambio mínimo de orquestación (reintento del paso / más
presupuesto en el reintento / un fallo parcial no tumba el job si no oculta otras fuentes). Sin tocar el
motor ACM ni fórmulas.

## Hallazgo
- **2026-10-27** *Intraday official feeds* run [40436083046](https://github.com/sanderdayan1982/g8-macro-pipeline/actions/runs/40436083046)
  (commit `7db9be0`, job `refresh`, 02:02–02:06Z, cron LATE encolado). `acm_g8` **FAILED rc=1 en 164,8 s de
  180 s** (`--cap 180` fijo en `scripts/tools/refresh_feeds.py`).
  - EUR (BCE SDW `data-api.ecb.europa.eu`, curva AAA `YC/B.U2.EUR…SR_3M`, comprobado en el log del run):
    intento 1 `FAIL_TRANSIENT`, intento 2 `200 OK`. EUR se escribió.
  - USD, JPY, GBP OK. **CAD y AUD no se intentaron en red**: `HTTP DEFERRED … presupuesto agotado`
    (Valet `www.bankofcanada.ca`, RBA `www.rba.gov.au/statistics/tables/xls/f01hist.xlsx`).
  - `acm_nzd`, `acm_chf`, `aud_nowcast` OK. `refresh_feeds.py` salió 1. Brief, salud y *Publish valid data*
    corrieron (`continue-on-error`); *Surface collection failure* hizo `exit 1` y el job quedó rojo.
- **2026-10-22** run [39705899708](https://github.com/sanderdayan1982/g8-macro-pipeline/actions/runs/39705899708)
  (01:28Z) **no fue el mismo fallo**: `us_bills`, `floors` y `real_yields` también fallaron; en ACM, USD
  (`Expecting value: line 1 column 1`) y 5/6 divisas OK. Reintentar solo `acm_g8` habría ocultado esas
  fuentes.
- El Daily ya concede a la misma orden `acm_g8.py USD…AUD` **`--cap 660`** (máximo observado ≤ 326 s;
  EUR AAA ≤ 225 s; `daily_update.yml`). El tope intradía de 180 s es el que deja a CAD/AUD sin red
  cuando el BCE gasta el presupuesto en un reintento interno.

## Decisión
1. `refresh_feeds.py` acepta `--cap` (por defecto **180**: el primer pase no cambia). Orquestación de
   g8step; no cambia `acm_g8.py` ni g8http.
2. Tras `id: feeds` (`continue-on-error`), si `steps.feeds.outcome == 'failure'`, un paso `feeds_retry`
   relanza **el mismo grupo** con `--cap 360` (sitúa el tope por encima de los 326 s observados y deja
   margen para CAD+AUD). `continue-on-error: true`. Va **antes** de brief/publish para que lo recuperado
   se publique.
3. *Surface collection failure* solo pone el job en rojo si el primer pase falló **y** el reintento no
   fue `success`. Un fallo persistente de otra fuente (caso 22-oct) vuelve a fallar en el segundo pase y
   sigue en rojo.

## Sin cambios
Fórmulas, calibraciones, umbrales, ventanas de z, ACM (modelo y `acm_g8.py`), s01b, factor USD, g8http,
presupuestos del Daily, cron, secretos, `mesa-macro-fx`. El primer pase intradía sigue en 180 s.

## Pruebas
`tests/test_p13_intraday_retry.py`: `--cap` por defecto 180 / override 360 incluye `acm_g8`; un colector
≠0 sigue siendo fallo del grupo; el YAML tiene `feeds_retry` justo después de `feeds`, `--cap 360`,
mismo `group` por `GITHUB_OUTPUT`, y la condición de Surface exige ambos outcomes.
Suite completa + `bash scripts/tools/validate_smoke.sh` antes de pedir integración.

## Siguiente
Si un LATE con BCE lento agota también 360 s, subir solo el `--cap` del reintento (o alinear el primer
pase de `acm_g8` con los 660 s del Daily). No hacerlo sin ver `actions__job_intraday.json` de un
segundo pase.

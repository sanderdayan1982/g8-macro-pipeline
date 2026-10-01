# ACTA P-4 — Tipos oficiales directos del banco central por delante del BIS (1-oct-2026)

Autorizado por el propietario («procede con el punto 3», 1-oct-2026); pendiente desde el acta P-1.

## Hallazgo (fuentes verificadas el 1-oct-2026)
| País | BIS WS_CBPOL (último dato) | Banco central directo | ¿Llega desde Actions? |
|---|---|---|---|
| AU | 24-sep (4,35) | RBA F1 `FIRMMCRTD` «Cash Rate Target», diaria: 4,60 desde el 30-sep (publicación 01-Oct-2026) | Sí: misma tabla que AONIA y las letras AUD |
| GB | 28-sep (3,75) | BoE IADB `IUDBEDR` «Official Bank Rate», diaria: 3,75 hasta el 30-sep | Sí: mismo endpoint que SONIA |
| CH | 29-sep (0,00) | SNB data.snb.ch | **No**: 403 a IPs de datacenter (documentado en `fetch_chf_snb.py`) |
| JP / NZ | 29-sep / 25-sep | BoJ / RBNZ | **No** (acta P-1) |

Prueba en vivo de la BoE (copia aparte del repo, 1-oct): 1260 fechas que coinciden con el BIS sin una sola discrepancia;
+2 filas (29 y 30-sep). La RBA no se puede probar desde el Mac del operador: la red local rompe el TLS de rba.gov.au
(certificado autofirmado en la cadena). Se prueba con un extracto real de `f1-data.csv` copiado desde el navegador
(`tests/fixtures/rba_f1_excerpt_2026-10-01.csv`).

## Decisión
1. `scripts/g8common/cb_direct.py`: lectores de RBA F1 (por Series ID, celdas vacías fuera) y BoE IADB. Un cambio de
   formato da `DirectError`.
2. `scripts/fetch_bis_policy.py` (AU, GB): orden BIS → banco central → decisiones verificadas (P-1).
   - El banco central se consulta **después** del BIS y con **un solo intento** (`ingest.requests(..., max_attempts=1)`,
     parámetro nuevo y opcional en `g8common/ingest.py`). Así el BIS conserva intactos su presupuesto y sus reintentos
     (10/40/90 s) dentro del límite de 240 s del paso. Un Retry-After del banco central se respeta igual que el resto.
   - Fecha que publican ambos: manda el banco central (fuente primaria) y se avisa de la discrepancia.
   - Por delante del BIS: prolonga el banco central.
   - Banco central caído o con formato cambiado: AVISO en el log y se sigue como antes (BIS + decisiones); el rc no cambia.
   - BIS caído y banco central al día: se publica la serie del banco central y el rc sigue en 1 (la incidencia del BIS sigue abierta).
   - Todo pasa por la misma ingesta segura (plausibilidad, salto máximo, no regresión).
3. `sources/registry.csv`: fuente primaria de GB_POLICY y AU_POLICY = banco central directo > BIS.
4. CH, JP y NZ sin cambios: BIS + `data/manual/policy_decisions.csv`. CH directo queda para el job del Mac (punto 4),
   que ya descarga data.snb.ch.

## Sin cambios
Fórmulas, umbrales de §02, z, regla de coherencia de §05 (P-1), presupuestos de frescura, nombres de los pasos del workflow
(siguen diciendo «via BIS»; solo es la etiqueta). La decisión AU del 30-sep en `policy_decisions.csv` queda como
verificación cruzada; ya no hace falta para prolongar.

## Pruebas
`tests/test_p4_cb_direct.py` (11):
- lectores: RBA por Series ID, extracto real de la RBA (4,60 el 30-sep; 01-Oct vacío se ignora), BoE, y un cambio de formato que da error;
- `merge`: prolonga por delante y en la misma fecha manda el banco central;
- integración con el script real y transporte simulado: AU con la RBA por delante (rc 0), GB con la BoE por delante (rc 0);
  banco central caído = mismo resultado que solo BIS; BIS caído + RBA publica con rc 1; JP y CH no llaman a ningún banco central;
  503 persistente del banco central = 1 llamada, sin esperas, después del BIS.
`tests/test_lote3_fetchers.py`: en F1/F5 de `fetch_bis_policy` las cuentas de llamadas y aplazamientos se limitan a la fuente
principal (stats.bis.org); el contrato del BIS no cambia (4 intentos, esperas 10/40/90, Retry-After).
Suite completa (Python 3.11): 640 OK (5 omitidos). Equivalencias s01b: EQUIVALENT. `dashboard_alerts.py --dry-run`: rc 0.
Prueba en vivo GB (copia aparte, orden definitivo): BoE +2 filas hasta el 30-sep = 3,75, publicado.

## Para el agente de mantenimiento
Si el log de «Fetch BoE Bank Rate via BIS» o «Fetch RBA Cash Rate via BIS» muestra
`AVISO: fuente directa … no disponible`, hay que mirar si cambió la URL, la cabecera de `f1-data.csv`
(fila «Series ID», FIRMMCRTD) o el CSV de IADB (`DATE,IUDBEDR`). La serie sigue publicándose con BIS + decisiones
mientras tanto.

## Siguiente
Punto 4: job launchd del Mac (NZD/CHF/TONA) a las 19:30 de Bata + SNB directo (CH_POLICY).

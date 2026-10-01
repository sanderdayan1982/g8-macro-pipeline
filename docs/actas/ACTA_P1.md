# ACTA P-1 — Tipos oficiales por delante del BIS (1-oct-2026)

## Hallazgo
- El BIS (WS_CBPOL) publica los tipos oficiales con días o semanas de retraso.
- **JPY:** el BoJ subió a 1,25 % (decisión 18-sep, vigencia 24-sep). TONA lo recoge desde el 24-sep (1,227); JP_POLICY.csv seguía en 1,00 (última fila 22-sep) y el fetch del BIS falla desde el 1-oct.
- **AUD:** la RBA subió a 4,60 % (decisión 29-sep, efectivo 30-sep). AONIA 4,60 desde el 30-sep; AU_POLICY.csv seguía en 4,35 (última fila 17-sep) con el fetch en verde.
- Efecto: §02 publicaba JPY +22,7 bp y AUD +25,0 bp PRESSURE (z extremo), falsos; también en brief.json y Telegram. El DQM de frescura no lo veía (presupuestos de 35–40 días hábiles para series por evento).

## Decisión
1. `data/manual/policy_decisions.csv`: una fila por decisión verificada en la fuente primaria (país, fecha efectiva, tipo, comunicado, quién verificó).
2. `g8common/policy_decisions.py` + `fetch_bis_policy.py` / `fetch_bis_ocr.py`:
   - el BIS manda en toda fecha que ya publica;
   - una decisión solo prolonga la serie por delante del último dato del BIS, lunes a viernes, desde su fecha efectiva y hasta hoy (nunca fechas futuras); sin decisión, no se rellena nada;
   - BIS caído → último CSV publicado + decisiones; el rc sigue en 1 (la incidencia del BIS sigue abierta);
   - discrepancia BIS vs decisión ya cubierta → aviso, manda el BIS;
   - la prolongación pasa por la misma ingesta segura (plausibilidad, salto máximo 0,5, no regresión).
3. `dashboard_alerts.py` v2.6 · §05 coherencia: si el diferencial tipo a un día − oficial se aparta > 15 bp de su mediana de 20 obs durante 2 obs seguidas → `POLICY_<CCY> SUSPECT` (línea DQM, brief.dqm, transición en Telegram). Con los datos del 1-oct marca JPY SUSPECT; con las decisiones aplicadas, OK.
4. `sources/registry.csv`: fallback de GB/JP/CH/AU/NZ = decisión verificada (`data/manual/policy_decisions.csv`).

## Sin cambios
Fórmulas, umbrales de §02 (2/10 bp), z, ACM, s01b, factor USD. La nueva huella de `dashboard_alerts.py` se autoriza en esta acta (`test_exclusions`, `test_f3_freshness`).

## Pruebas
`tests/test_policy_decisions.py` (12): sin fichero = sin cambios; prolongación lunes-viernes; fecha futura no aplica; discrepancia; fichero inválido; BIS caído + decisión (rc 1, publica); BIS atrasado + decisión (rc 0); salto implausible retenido; BIS caído sin decisión (fichero intacto); regla SUSPECT ↔ OK. Suite completa: 607 tests OK (10 omitidos, igual que antes).

## Siguiente (pendiente de autorizar)
Fuentes primarias directas donde GitHub Actions llega (RBA F1.1, BoE IADB, SNB) delante del BIS; BoJ y RBNZ bloquean Actions → decisiones verificadas. Mantenimiento de este fichero: agente de mantenimiento (lunes, miércoles, viernes).

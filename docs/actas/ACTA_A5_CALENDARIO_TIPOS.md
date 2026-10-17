# ACTA A5 — Tipos oficiales y calendario de bancos centrales en la §02; §03 y §09 fuera (17-oct-2026)

**Origen:** plan de armonía, enmienda v2, punto #4 (A5) y complemento #5, firmados por Claude y Argon 4. Sander: "procede con A5, de manera institucional y manteniendo la lógica macroeconómica del sistema".
**Base:** A4 (main c13df9d).
**Dueños únicos:**
- La pregunta "¿dónde está el tipo oficial y cuándo decide el banco central?" la contesta la §02.
- La pregunta "¿qué posicionamiento COT hay por divisa?" la contesta POS-G8 en TradingView.

## Lógica macro
- El suelo del mercado monetario (RFR menos el tipo de depósito o de referencia) solo se lee bien junto al tipo oficial vigente y a la fecha de la próxima decisión.
  - Un TIGHT a 2 días de una reunión no significa lo mismo que un TIGHT a 39 días.
- Por eso la §02 muestra en una sola fila tres cosas:
  - el spread;
  - el tipo oficial, tomado de **la misma serie** que define el suelo (sin una segunda fuente que pueda contradecirla);
  - la próxima decisión, tomada del calendario oficial.
- Con eso no hace falta una §03 aparte.

## Cambios
| Archivo | Cambio |
|---|---|
| `data/manual/cb_meetings.csv` (nuevo) | Calendario OFICIAL de los 8 bancos centrales: 68 decisiones. Cada fila lleva `source` (URL del banco central) y `verified` = 2026-10-16. La fecha es el día del anuncio (BoJ y RBA: el 2.º día de reunión). El 2027 está completo salvo el de la RBA, que aún no lo ha publicado. RBNZ llega hasta feb-2028. Es una entrada manual: se edita a mano solo al revisar el calendario, nunca un dato calculado. |
| `docs/index.html` §02 | Título: "Money Market Floor Spreads · Tipos oficiales G8". Columnas nuevas: **Tipo oficial · Último cambio · Próxima reunión** (días hábiles; ámbar y "evento ≤48h" cuando faltan 2 días hábiles o menos). `policyInfo()` ignora filas con fecha futura. Si la serie no ha cambiado nunca, marca "≤ fecha". La fila NZD·90d (de plazo) lleva "·" en las tres columnas, porque el tipo oficial va en la fila overnight. Si falta el calendario: "calendario n/d"; si se agota: "SIN FECHA". |
| `docs/index.html` §03 | Se retira la sección y su enlace del menú. Se funde en la §02. |
| `docs/index.html` §09 | Se retira el módulo COT entero (estilo, sección y script) y su enlace del menú. `data/state/pos_g8_cot.json` **se sigue generando**: lo leen el back-end, la matriz MDP×COT de metales y la §00. |
| `docs/index.html` G8DQM | Se elimina el código muerto (obs. 2 de Argon en B1): `checkFeed`, `runAll`, `render`, `init`, `syncHeader`, `healthScore`, `feedWeight`, `persistAudit`, `auditStrip`, `attentionStrip`, `extractLastDate` y `fmtDate`. Quedan `report`, `computeStaleness`, `businessDays`, `FEED_REGISTRY` y `state`, que usan las marcas de as-of. |
| `docs/js/charts.js` | Deja de cargar el gráfico de la §03. `renderPolicyChart` queda sin llamadas (inerte). |
| `scripts/health_monitor.py` | `cb_calendar_items()` añade un control por banco central: CURRENT/CALENDAR_OK si hay una decisión futura, **LATE/CALENDAR_EXHAUSTED** si no la hay, MISSING si falta el archivo. Así un calendario agotado activa `needs_repair` y el agente. |
| `scripts/pos_g8_cot_collector.py` | El USD° se calcula con las Z **sin redondear** de las 7 divisas (`_z_lf`/`_z_am`, que se descartan antes de escribir). Motivo: en el informe del 13-oct el USD° salía exactamente en 0,79, el umbral; con Z redondeadas a 2 decimales, el estado podía depender del redondeo. |
| `scripts/tools/agent_gate.py` | La lista de funciones JS protegidas ya no incluye las tres eliminadas. |
| `scripts/tools/dashboard_render_check.cjs` | Exige `#fls-table` con la columna "Próxima reunión" en lugar de `#cot-panel`. |
| `tests/test_a5_cb_calendar.py` (nuevo) | 7 pruebas: calendario bien formado (8 bancos, URL oficial, sin duplicados); próximas fechas conocidas; coherencia con `policy_evidence`; salud con calendario agotado (AU pasa a LATE el 2027-01-05); §03 y §09 retiradas de la web; código muerto eliminado; USD° con Z sin redondear. |

## Próximas decisiones [MEDIDO, calendario oficial]
| Fecha | Banco central | Días hábiles (desde el lunes 19-oct, incluido el día de la decisión) |
|---|---|---|
| 28-oct | Fed (FOMC, Comité Federal de Mercado Abierto) · BoC · RBNZ | 8 |
| 29-oct | BCE | 9 |
| 30-oct | BoJ | 10 |
| 03-nov | RBA | 12 |
| 05-nov | BoE | 14 |
| 10-dic | SNB | 39 |

**Racimo 28–30 oct:** 5 de los 8 bancos centrales deciden en 3 días. Es la base de la futura bandera `CB_EVENT_48H` de la §00 (C1, SHADOW).

## Verificación [MEDIDO]
- `python -m unittest discover -s tests`: **780 OK** (11 omitidas).
- Sintaxis JS correcta.
- `health_monitor` sobre los datos de hoy:
  - DEGRADED con 5 incidencias, las mismas que en B1 (GB/JP/CH/AU con la verificación caducada; NZD_OCR con fallo de descarga).
  - Los 8 calendarios salen CALENDAR_OK.
- Render en Chromium (captura `a5_fls.png`):
  - la §02 pinta 9 filas con tipo, último cambio y reunión;
  - USD 3,90 % IORB (17-sep); EUR 2,50 % DFR; CHF 0,00 %;
  - NZD·90d en una sola línea;
  - sin errores de página.

## Lo que no cambia
Modelos, señales, umbrales del POS, la §01, la §01-b, la §08, la matriz MDP×COT, las alertas y la generación de `pos_g8_cot.json`.

## Pendiente
- **Revisión trimestral del calendario:** añadir el 2027 de la RBA en cuanto se publique. Si no se hace, el monitor avisa el 2027-01-05 (CALENDAR_EXHAUSTED).
- **Siguiente paso:** B2 (metales diarios en SHADOW).
- **Cerrada el 17-oct-2026** (nota `NOTA_A5_VALIDACION_CALENDARIO.md`, con el consentimiento de Argon previo a B2): `health_evidence` usa `cb_meetings.csv` como dueño de la reunión, y el chequeo de render acepta la cabecera en mayúsculas.

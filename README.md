# G8 Macro Pipeline

Dashboard de **composición macro** del Sistema Institucional G8 de Sander: atribución del tramo largo, fontanería monetaria doméstica, metales, opciones y exposición al factor USD.

- **Dashboard:** [g8-institutional.netlify.app](https://g8-institutional.netlify.app/)
- **Interfaz actual:** `docs/index.html`, **v2.7.1**.
- **Documentación revisada:** 23-oct-2026. La numeración de esta página sigue los títulos visibles del dashboard, no los identificadores históricos de las actas.
- **Universo:** USD, EUR, GBP, JPY, AUD, NZD, CAD y CHF; oro y plata en sus bloques propios.

> **Función del dashboard:** aportar contexto de composición para formar y revisar la tesis macro. **No es un paso adicional del embudo ni una señal de entrada.** El embudo operativo vive en TradingView: RISK-G8 → XCCY/PSI → Curva/RTF10 → POL → IYDT → POS → FFVA → cockpit. Los walls aportan contexto/permiso (Gate 0), nunca el disparador de ejecución.

## Mapa actual de la pantalla

| Sección visible | Qué contiene | Fuente o motor principal |
|---|---|---|
| **00 · Brief** | Lectura resumida, fechas por capa, gates, libro G8, diferenciales nominales y reales frente a USD, extremos y walls. Comparte la lectura generada para Telegram. | `data/alerts/brief.json` · `scripts/dashboard_alerts.py` |
| **01 · Long-End Attribution Matrix G8** | Dos descomposiciones: NOM = REAL + BE; Y10 = RNY + TP. Incluye lectura de 22 sesiones **§01-b**, CTF y persistencia, con contexto y calidad por pata. | `ACM_G8_*.csv`, `RY_G8_*.csv`, `S01B.json` · `acm_g8.py`, `real_yields_g8.py`, `s01b.py` |
| **02 · Money Market Floor Spreads / tipos oficiales G8** | Diferencia RFR menos suelo o referencia administrada; selector por divisa. Incluye tipo oficial, último cambio y próxima reunión. | CSV de RFR, `FLOOR_*.csv`, `*_POLICY.csv`, `NZD_OCR.csv` y calendario/decisiones oficiales |
| **03 · Term Premium ACM 10Y G8** | Serie temporal de la prima por plazo a 10 años; selector de las ocho divisas y avisos de calidad del ajuste. | `ACM_G8_*.csv` · `scripts/acm_g8.py` |
| **04 · Gold Fair Value / MDP** | Valor de referencia variable y residual del oro bajo el modelo Kalman TVP; drivers, validación y cruce con posicionamiento. | `MFV_G8_XAU.csv`, `MFV_G8_state.json` · `scripts/metals_fairvalue_g8.py` |
| **05 · Silver Fair Value / MDP** | Modelo de plata con canal monetario y, si la validación lo admite, canal industrial de cobre depurado del oro. | `MFV_G8_XAG.csv`, `MFV_G8_state.json` · `scripts/metals_fairvalue_g8.py` |
| **06 · Options Surface / CME FX Walls** | Settlement y open interest por strike de la cadena frontal; convención de cotización del operador y clasificación Gate 0. | `OPTIONS_SURFACE.json`, `options/canonical/` · colector CME y `build_options_summary.py` |
| **07 · Factor USD / residuales / libro** | Factor común, betas, residuales, dispersión, matriz de cruces, riesgo del libro y PRE-TRADE. Contexto, sin voto. | `USD_FACTOR.json`, `BOOK_RISK.json` · `usd_factor.py`, `book_risk.py` |
| **Salud · barra superior** | Fecha observada, publicación esperada, disponibilidad e incidencias de las fuentes. | `data/_ingest/health.json` · `scripts/health_monitor.py`, `docs/js/health.js` |

**Navegación:** teclas `0`–`7` para los bloques y `s` para Salud. El botón **↻ datos** borra la caché local de datos y recarga; no obliga a la fuente original a publicar una observación nueva.

### Cambios de organización ya aplicados

- Tipos oficiales quedó integrado en **§02**; el impulso de política pertenece a **6-POL-G8** en TradingView.
- El antiguo Data Quality Monitor independiente se retiró: hay un único monitor de **Salud** en la cabecera.
- COT ya no tiene una sección propia en la web: su dueño operativo es **0-POS-G8**. `data/pos_g8_cot.json` permanece como insumo del brief y del cruce MDP × COT de metales.
- FFVA fue retirado del pipeline del dashboard. Su dueño operativo es **9-FFVA** en TradingView.
- El antiguo XCCY G8 Command Center quedó cancelado. El código legado no define el XCCY operativo de la suite.
- Algunas actas y comentarios conservan la numeración anterior: metales §06/§07, walls §08 y factor USD §10 corresponden hoy a **§04/§05, §06 y §07**, respectivamente. No son bloques adicionales.

## Lectura operativa y relación con la suite

1. Comprueba **Salud** y las fechas de las capas que vas a utilizar. La fecha de actualización del sitio no sustituye a la fecha de observación de cada serie.
2. Usa **§00** para localizar qué merece revisión; abre el bloque de origen antes de construir una explicación detallada.
3. En **§01/§03**, separa expectativas de tipos, prima por plazo, rendimiento real y breakeven. No sumes las dos descomposiciones entre sí: son lecturas distintas del mismo nominal.
4. En **§02**, interpreta la presión doméstica respecto al ancla de cada moneda. El contrato fija AMPLE ≤ 2 pb, TIGHT > 2 y ≤ 10 pb, PRESSURE > 10 pb.
5. En **§04/§05**, distingue precio, referencia del modelo y residual MDP. El valor del modelo no es una orden ni una promesa de convergencia.
6. En **§06**, verifica sesión, vencimiento, cotización e integridad de la cadena antes de interpretar un wall. El OI de CME no representa todo el mercado OTC de FX.
7. En **§07**, comprueba cuánto riesgo común acumularía una nueva posición. `SIN LIBRO` y `SIN CANDIDATO` indican ausencia de esos inputs; no un riesgo calculado de cero.
8. Contrasta la tesis con la suite de TradingView y aplica allí las reglas de ejecución y riesgo del operador.

Las condiciones de cálculo viven en los motores y contratos del repositorio. Las actas documentan las decisiones; no deben utilizarse para atribuir a un bloque actual una función retirada.

## Frecuencias, calidad y límites de interpretación

**Prioridad:** diario, después semanal y mensual únicamente como último recurso documentado. Fuentes trimestrales o más lentas quedan excluidas, incluso como respaldo. Consultar diariamente una serie mensual no la convierte en diaria.

- **ACM:** aplicación diaria del modelo; su muestra de estimación mensual no es la frecuencia de publicación del resultado. El número de factores y la calidad se declaran en cada archivo.
- **NZD ACM:** muestra corta documentada (`ACM_K3_SHORT_SAMPLE_*`). Forma, z y variaciones son la lectura prevista; nivel de baja confianza. Respaldo sintético AUD solo cuando no hay un ajuste real fresco admisible.
- **CHF ACM:** entre publicaciones de la curva SNB puede usar desplazamiento paralelo anclado al 10Y diario (`ACM_K3_NOWCAST_PARALLEL`). Es NOWCAST, no una nueva curva oficial completa.
- **AUD:** las patas oficiales de la RBA pueden publicar semanalmente. Cuando se usa `EST_AUD_V2`, la interfaz lo marca como estimación; la estimación no sustituye la identidad del dato oficial.
- **BE/REAL:** las antiguas constantes trimestrales CHF/NZD están desactivadas. NZD utiliza sus IIB; si faltan o caducan, muestra NO DISPONIBLE. En CHF, la interfaz actual puede mostrar **REAL ex post = nominal − IPC interanual mensual oficial del BFS** y el IPC en la columna BE, con aviso explícito: **no es un breakeven de mercado**. El diferencial REAL vs USD no incorpora esa pata ex post como si fuera comparable a un linker. Si falta un input admisible, se declara la ausencia.
- **Metales:** el modelo principal es semanal. Hay procesos de actualización y una comparación diaria SHADOW; una ejecución del workflow no cambia por sí sola la frecuencia del modelo.
- **Opciones:** settlement y OI deben corresponder a la misma sesión T; OI llega T+1. USD/JPY, USD/CAD y USD/CHF invierten el strike y permutan call/put respecto al contrato CME.
- **SHADOW:** una etiqueta de estimación o ensayo no autoriza a mezclar esa lectura con el cálculo principal. Revisa el estado, la sesión y el alcance declarado.

## Fuentes y dependencias de publicación

Los CSV/JSON se publican en `data/`; la interfaz los consume desde el repositorio y aplica las reglas de calidad. Fuentes y presupuestos se registran en `sources/registry.csv`. El contrato entre suite y dashboard está en [`sources/suite_contract.json`](sources/suite_contract.json).

Continúan existiendo fuentes dependientes del **Mac** del operador, en particular recogidas de NZD y CHF que no se obtienen de forma equivalente desde Actions. Actions consume los CSV publicados. Si la recogida o publicación se interrumpe, los datos pueden caducar; consultar el monitor de Salud y su evidencia, no asumir disponibilidad por el mero hecho de que una tabla conserve números.

`data/manual/manual_inputs.json` conserva los inputs manuales admitidos. Los BE trimestrales retirados siguen desactivados. En las claves con edición local, un valor del navegador solo prevalece según la regla de fecha implementada; no crea una nueva fuente oficial.

## Automatizaciones configuradas

Horarios **UTC** del código de workflows revisado el 23-oct-2026. En **Africa/Malabo** se suma una hora. Son horarios programados, no garantía de finalización o de dato nuevo: GitHub puede retrasar un cron y una fuente puede no publicar.

| Workflow | Programación UTC | Función |
|---|---|---|
| `daily_update.yml` | 21:30, lunes a viernes | Recogidas diarias, motores de tipos/ACM/real yields, alertas y publicación. |
| `intraday_fetch.yml` | 00:23, 03:17, 13:17, 15:47 y 23:17, lunes a viernes | Recogidas adicionales de fuentes oficiales. |
| `usd_factor.yml` | 15:30 y 17:30, lunes a viernes | Factor USD, riesgo del libro, alertas y publicación. Un festivo TARGET puede producir NO_NEW_DATA. |
| `cme_options.yml` | 13:30 y 17:00, todos los días en el cron | Colector y resumen de opciones con guardas de calendario, disponibilidad y coste. |
| `g8_port_run.yml` | Viernes 21:05 y sábado 09:05 | COT, generación POS y controles asociados. No ejecuta un FFVA web. |
| `metals_update.yml` | Sábado 07:00; lunes a viernes 22:45 | Actualización de metales y procesos asociados; distinguir la salida principal semanal de la salida diaria SHADOW. |
| `ingest_watch.yml` | 17:13, lunes/miércoles/viernes | Vigilancia de publicaciones y evidencias de salud. |
| `maintenance_agent.yml` | 17:17, lunes/miércoles/viernes | Evaluación y mantenimiento sujeto a compuerta. |
| `validate.yml` | Push / PR | Validación de humo del repositorio. |
| `acm_validate.yml`, `ry_validate.yml` | Manual | Validaciones de modelos/series correspondientes. |

El informe de vigilancia tiene un límite operativo de 56 horas. Fuentes sin calendario confirmado se muestran UNKNOWN; las estimaciones no sustituyen publicaciones oficiales. El mantenimiento tiene compuertas de alcance y pruebas; no garantiza que una fuente externa publique ni autoriza cambios de metodología.

## Brief y alertas Telegram

`scripts/dashboard_alerts.py` **v2.6** genera `data/alerts/brief.json` y procesa las alertas a partir de los archivos del repositorio. El brief y Telegram comparten la lectura generada; la web no recalcula ese resumen.

Las alertas usan cambios de estado e histéresis según el código; la llegada de una nueva sesión de opciones tiene su tratamiento propio. La sección de factor incluye cambios de banda de dispersión, extremos del factor y condiciones de riesgo del libro. El silencio de Telegram no certifica frescura ni equivale a permiso de operación. Revisar el as-of de cada capa y el monitor de Salud.

Las credenciales se gestionan como secrets o fuera del repositorio. Este README no contiene valores de claves.

## Estructura del repositorio

| Directorio | Contenido |
|---|---|
| `docs/` | Interfaz estática publicada por Netlify: `index.html`, CSS y JS. |
| `docs/actas/` | Decisiones, especificaciones y evidencia histórica; pueden conservar numeración anterior. |
| `scripts/` | Recogidas, motores, alertas y herramientas de validación. |
| `data/` | Series, estados, informes de salud y resultados publicados. |
| `sources/` | Registro, calendarios, presupuestos y contrato de la suite. |
| `mac/` | Recogidas y publicación que dependen del Mac. |
| `tests/` | Pruebas de lógica, equivalencia, contratos y fiabilidad. |
| `gates/` | Compuertas y decisiones preregistradas. |
| `.github/workflows/` | Automatizaciones de Actions. |

## Trabajar y publicar cambios

- Lee [`CLAUDE.md`](CLAUDE.md) y las actas aplicables antes de cambiar el sistema.
- No modificar fórmulas, calibraciones, ventanas, pesos o metodología sin autorización explícita del propietario.
- Verificar fuentes primarias, unidades y fechas; los fallos deben ser visibles.
- Para cambios funcionales, ejecutar las pruebas y controles exigidos por el repositorio, incluida la validación de humo:

```bash
python -m unittest discover -s tests -p 'test_*.py'
bash scripts/tools/validate_smoke.sh
```

Netlify publica **`docs/` sin build**. `netlify.toml` evita reconstrucciones cuando no cambian los archivos que se publican; los cambios únicamente en este README no requieren modificar el dashboard ni sus motores.

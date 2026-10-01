# ACTA P-8 — AUD diario (estimación EST_AUD_V2: nominal 2Y/10Y, breakeven y real 10Y) en §01, §01-b y §00 (1-oct-2026)

Autorizado por el propietario (1-oct-2026): «quiero los nominales diario por divisa… o sus estimaciones y el cálculo,
así sección 1 y 1b estarán siempre frescos… ok procede, y que el agente sea capaz de arreglarlo».

## Hallazgo
- De las 8 divisas, **solo AUD** no tiene nominal oficial diario: la RBA publica F2 (y F16) una vez por semana, viernes,
  con datos hasta el miércoles. El resto es diario (T+1 por la hora de publicación). Por eso §01-b (Δ2Y, ΔBE, RESID)
  quedaba en blanco de martes a jueves (regla `MAX_LAG_BD = 3`, igual que el 22–24-sep) y §01/§00 mostraban el dato del miércoles.
- Lo diario «oficial» es de pago (Yieldbroker, ASX, LSEG). TradingView (`TVC:AU02Y/AU10Y`) es diario, pero los dos repos y
  el dashboard son **públicos** y republicar datos con licencia no es aceptable. Los agregadores gratuitos están fuera de la regla.

## Decisión
1. **Modelo EST_AUD_V1** (`scripts/aud_nowcast.py`, especificación en `sources/nowcast_aud.json`). Nivel estimado = último
   dato RBA + Σ β·Δdrivers (MCO sin constante, últimos 3 años de variaciones diarias). Drivers oficiales y públicos:
   - 2Y: letra a 6 meses RBA F1 (día *d*) + Treasury 2Y FRED (día hábil anterior en NY);
   - 10Y: letra 6M + Treasury 10Y.
   Calibración del 1-oct: 2Y β = 0,90 / 0,30, R² 0,45; 10Y β = 0,56 / 0,54, R² 0,46.
2. **Validación fuera de muestra** (calibración 2021-10..2024-12, prueba 2025-01..2026-09; error de la suma de *h* días, pb):

   | h | 2Y estimación | 2Y repetir dato | 10Y estimación | 10Y repetir dato |
   |---|---|---|---|---|
   | 1 | 3,5 | 4,6 | 3,8 | 4,8 |
   | 3 | 5,3 | 7,8 | 4,9 | 7,6 |
   | 6 | 7,6 | 11,3 | 6,6 | 10,5 |

   Mejora de alrededor de un 35 %. **Límite conocido**: en semanas con un movimiento propio de Australia, el error puede
   superar la banda. Contraste con TradingView (solo verificación, no se publica) en la semana 24–30-sep, de decisión de la
   RBA: el 10Y estimado sube unos 16 pb desde el 23-sep y el mercado unos 3 pb; el 2Y estimado unos +4 pb y el mercado unos −11 pb.
3. **Salidas**
   - `data/AUD_NOWCAST.csv`: historial que solo crece. Las filas posteriores al último dato RBA se recalculan; las demás
     quedan como registro para medir el error real.
   - `data/AUD_NOWCAST.json`: estado, betas, R², error esperado por horizonte, avisos. rc 1 si falta o se atrasa un insumo.
4. **Consumidores** (siempre con la etiqueta EST):
   - §01 (`docs/index.html`): el nominal AUD se prolonga con la estimación. La línea de la divisa muestra
     «dato <fecha> EST ±x pb · RBA <fecha>». Real y BE siguen con la RBA. Los diferenciales vs USD de la matriz usan la serie prolongada.
   - §01-b (`s01b.py` v1.3.3): solo las patas de CONTEXTO nominal y 2Y. Celdas en ámbar con «ᵉ», bandera `CTX_EST`,
     calidad `EST_AUD_V1`. La sonda de `ACM_FFILL` mira solo datos oficiales. ACM, θ, CTF, persistencia y señal sin cambios
     (equivalencias 21/22-sep: EQUIVALENT). Las fechas estimadas viajan en la foto de entrada, así que la sesión se reproduce igual.
   - §00 (`dashboard_alerts.py`): el nominal del libro se muestra estimado, con el flag «NOM EST dd-mm ±x pb (RBA dd-mm;
     vs USD/real/BE con RBA)». Diferenciales, z y **alertas de Telegram siguen sobre datos oficiales**: una estimación
     nunca dispara un aviso.
   - §05: feed `AUD_NOWCAST` (diario, presupuesto 3 d.h.); registro de fuentes y reglas de frescura (derivado).
5. **Workflow**: paso `aud_nowcast` en *Daily Data Update* (tope 60 s), después de todas sus entradas y antes de s01b y del brief.
6. **Agente**: instrucciones nuevas (comprobar estado y alcance; reparar insumos y la lectura/escritura del script;
   medir el error real cada lunes y viernes; si el error duplica el esperado 2 semanas → REQUIERE OK).
   Compuerta: `scripts/aud_nowcast.py` = AUTO; `sources/nowcast_aud.json` y `tests/test_p8_aud_nowcast.py` = OWNER.
7. Huellas nuevas autorizadas: `s01b.py`, `dashboard_alerts.py`.

## V2 (1-oct-2026, a petición del propietario: «¿por qué no lo hace la misma fórmula sobre la estimación?»)
- Breakeven 10Y estimado con la misma fórmula (letra 6M RBA F1 + breakeven 10Y de EE. UU., T10YIE de FRED, del día hábil
  anterior); real 10Y = nominal − breakeven (identidad). Calibración del 1-oct: β = 0,13 / 0,41, R² 0,29.
- Fuera de muestra (último año): BE h1 1,8 frente a 2,2 pb; h3 3,1 frente a 3,5; h6 4,6 frente a 5,3 (mejora ~12–15 %, menor que en
  el nominal). El real implícito (nominal − BE) no se ha medido por separado; como referencia, un modelo directo del real
  (TIPS de EE. UU. + letra) daba h3 4,5 frente a 6,1 pb.
- Consumidores: §01 (real y BE también EST), §01-b (ΔBE también, con `CTX_EST`), §00 (nominal, real y BE EST; diferenciales vs
  USD, z y alertas siguen oficiales). `EST_AUD_V2` sustituye a `EST_AUD_V1` en `AUD_NOWCAST.csv` (las filas viejas lo conservan).
- Cada estimación parte del último dato RBA publicado; cuando llega un dato nuevo, las siguientes se calculan sobre él.
- Agente: mapa sección por sección (§00–§10) con ficheros y ritmo normal, y la regla común «dato oficial o estimación etiquetada».

## Sin cambios
Fuentes oficiales y su preferencia: el dato RBA siempre sustituye a la estimación en cuanto llega. Tampoco cambian ACM,
umbrales, z, señal CTF, alertas ni el resto de divisas, que ya tienen nominal oficial diario.
(V1 dejaba ΔBE semanal; V2 lo estima — ver arriba.)

## Pruebas
`tests/test_p8_aud_nowcast.py` (8):
- la especificación solo vive en el json;
- en un mundo sintético exacto recupera β y la verdad;
- retardo de EE. UU.; driver ausente → estado y nada se borra; historial que solo crece;
- con datos reales, fuera de muestra (último año, h = 3) la estimación es mejor que repetir el dato (< 0,9×);
- s01b: `CTX_EST`, calidad, sonda oficial y detector idéntico;
- etiquetas en §00/§01, orden del workflow y compuerta.
Suite completa y `validate_smoke.sh`: verde.

## Siguiente
Medir el error real en las próximas semanas (lo hace el agente). Si se consigue una fuente diaria con licencia
(Yieldbroker, ASX o LSEG), sustituir la estimación por el dato.

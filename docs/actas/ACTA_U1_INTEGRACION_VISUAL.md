# ACTA U1 — integración visual, numeración 00–07 y sombras en la web (17-oct-2026)

**Origen:** Argon 4 auditó `PARA_ARGON_4_ESPECIFICACION_ESTADO_2026-10-17.txt` sobre `57f8439` y firmó el lote U1.
**Base real:** `fe74beabb4fdd803a5a177bbd52cdd23d2fa4fa1` (`chore: options surface update 2026-10-17`), padre `57f8439`. El bot solo cambió tres sellos de tiempo (`OPTIONS_SURFACE.json`, `brief.json`, `state.json`). El lote se apoya en esa punta para no rebobinar `main`.

No se tocan los siete ficheros de `tests/test_exclusions.py`. `scripts/s01b.py` no se toca. `.github/workflows/cme_options.yml` no se toca.

## Qué cambia

- `docs/index.html`. Títulos `<h2 class="section-title">` del `00` al `07`, sin saltos. La barra usa `data-key` `0`–`7` y `s` para `#publication-health`, con el texto `teclas 0–7 · s`. Los `id` de sección se quedan: `#section-brief`, `#section-attribution`, `#section-floors`, `#section-acm`, `#section-gold`, `#section-silver`, `#section-options`, `#section-factor`.
- El brief sigue saliendo de `brief.json` (lo escribe `dashboard_alerts.py`, congelado). Al pintarlo, `briefSec` renombra las balas visibles: `§04` → `§03`, `§06/§07` → `§04/§05`, `§08` → `§06`, `§10` → `§07`. La bala COT sigue en `§09`: esa sección se retiró de la página y no tiene número nuevo.
- §02. La próxima reunión usa `data/CB_EVENT_48H_shadow.json`: `currencies[ccy].days_to_next_cb_meeting` y `CB_EVENT_48H`. Si el JSON no carga, vuelve a `bdBetween` (lunes a viernes). Con el fichero del 17-oct, NZD enseña `7 d.h.` (Labour Day NZ del 2026-10-26). La fila 90d no lleva reunión.
- §01-b. Si la celda Δ2Y de CHF sale `mensual SNB`, al lado va el badge de `data/CHF_NOWCAST_shadow.json`: `nowcast 2Y: 0.30% · +1e pb al 10-16` (0,3002 frente al oficial 0,287, redondeado a 1 pb). `s01b.py` sigue leyendo el cubo mensual.
- §06 (antes 08). Además de `OPTIONS_SURFACE.json` carga `data/CME_OPTIONS_C1_shadow.json`. Junto al vencimiento: `dte_calendar` d / `dte_bd` d.h., y `ROLL ≤7D` si `roll_window_7d`. En CALL WALL y PUT WALL: nivel, `dist_pct`, `pct_oi_1d`, chips `near_pin_05pct` y `chip_15pct_strike`. Si el muro cercano tiene otro strike, se pinta `near`. `map_status` va en la fila (`READABLE`, `THIN_ONE_SIDED`, `NO_CME_OPTIONS`). En JPY, CAD y CHF el nivel es `1/k`, call y put se cruzan, y la distancia se recalcula sobre ese nivel para que el porcentaje acompañe al número pintado. El chip de 0,5 % y el de 15 % son los del JSON.
- `.github/workflows/daily_update.yml`. Cada día, antes del commit: `cb_event_shadow.py`, `cme_options_shadow.py`, `chf_nowcast_shadow.py`. El `git add` nombra `CB_EVENT_48H_shadow.json`, `CME_OPTIONS_C1_shadow.json`, `options/shadow_history.json`, `CHF_NOWCAST_shadow.json` y los dos CSV del nowcast. La reserva del job pasa de 540 s a 660 s (tres pasos de 40 s) para seguir cabiendo en el plazo ya medido.

## Prueba

`tests/test_u1_dashboard_integration.py`.

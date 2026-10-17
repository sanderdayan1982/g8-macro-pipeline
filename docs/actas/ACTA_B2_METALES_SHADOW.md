# ACTA B2 — MDP diario de oro y plata en SHADOW (17-oct-2026)

**Origen:** enmienda v2, lote B2, especificación firmada por Argon y Claude. Sander: el markdown es el trabajo; la foto es la validación de A5, que se corrige en la nota del mismo día.
**Base:** A5 (`4c43ba1`) más la nota de validación y calendario.
**Dueño de la señal que pinta §06 y §07:** sigue siendo el MDP semanal (`MFV_G8_XAU.csv`, `MFV_G8_XAG.csv`, `MFV_G8_state.json`). El diario no lo sustituye.

## Regla

El filtro de Kalman no cambia. Sigue siendo forward-only, sin smoother, y las betas se reestiman solo en la rejilla W-TUE.

Sobre esa rejilla, en paralelo:

- El martes τ el diario usa el estado predicho β_{τ|τ−1}. El `MDP_Z` publicado ese día es el semanal, al decimal (4 cifras).
- De miércoles a lunes, como máximo 4 días hábiles después de τ, se usa el estado filtrado β_{τ|τ} con as-of causal (τ ≤ t).
- El Z de esos días es un z-score rolling de los residuos diarios anteriores. No se divide un residuo diario entre la varianza de innovación semanal.
- Un día a más de 4 días hábiles del último martes conocido no se emite. El fin de semana no se emite.
- `mdp_as_of` (día del precio y de los regresores) y `cot_as_of` (último martes COT de la rejilla) salen por separado.

## Salidas (no entran en el contrato semanal)

| Archivo | Papel |
|---|---|
| `data/MFV_G8_XAU_daily_shadow.csv` | Proyección diaria del oro. Cabecera `SHADOW`. |
| `data/MFV_G8_XAG_daily_shadow.csv` | Proyección diaria de la plata. El `HG_ORTH` diario hereda el OLS semanal: no se reestima. |
| `data/MFV_G8_shadow_state.json` | `mode=SHADOW`, y por metal `mdp_z`, `mdp_as_of`, `cot_as_of`, `beta_state`. |

Esas claves no se escriben dentro de `MFV_G8_state.json`. No entran en `metals_provenance.OUTPUTS` ni en el registro de salud: un fallo de la sombra no restaura ni bloquea la publicación semanal, y no abre una incidencia. `tests/test_f1_config.py` y `tests/test_f3_freshness.py` los dejan fuera de las reglas de frescura para cuando el workflow los publique.

## Archivos

| Archivo | Cambio |
|---|---|
| `scripts/metals_shadow_daily.py` (nuevo) | Ensamblado diario y proyección. No calibra δ. |
| `scripts/metals_fairvalue_g8.py` | `build_panel_xau`, `build_panel_xag` y `build_metal` preparan el panel diario y escriben la sombra después del CSV semanal. `main` vacía el estado de sombra y escribe el JSON aparte. |
| `.github/workflows/metals_update.yml` | Se mantiene el sábado 07:00 UTC. Se añade lunes–viernes 22:45 UTC (el daily ya ocupa las 21:30). El commit añade los tres archivos de sombra solo si existen. |
| `mac/dispatch_schedule.json`, `sources/schedules.csv` | El mismo cron `45 22 * * 1-5`, para que el Mac y el inventario no se aparten del workflow. |
| `tests/test_b2_metals_shadow.py` (nuevo) | Martes = Z semanal a 4 decimales y residuo con β predicho; miércoles con β filtrado del martes ya visto; se descarta el día a 5 días hábiles; no se usa una beta de un martes futuro; el sábado no sale; el Z no martes es el z-score diario causal. |
| `tests/test_feed_provenance.py` | Se actualizan solo los tres hashes de las funciones que ahora ensamblan el diario. |

## Hashes [MEDIDO]

Sin cambio: `asof_weekly`, `load_slope`, `forward_returns`, `evaluate_delta`, `calibrate_delta`, `cot_activation`.

Cambian, porque el panel diario se ensambla ahí:

- `build_panel_xau` `adcabe6d9e4d117083ca4d6ce50c732f6b007d33100b97d22a9657c7fc5c0d31`
- `build_panel_xag` `b3b627d913a4154805b997eb31587284cb0aab88a3d1c6523db2d9bf7010ad19`
- `build_metal` `aa8955af15f50eaa8ca62d947b7cb1bd9dd69a6a7dfd726a5de32d26f1eda0cd`

## Lo que no cambia

La fórmula del Kalman, la calibración de δ, el CSV semanal, el fair value que pinta §06/§07, el POS, la §01, la §01-b y la §08. No se construye el par asimétrico de Asset Managers. No se abre el lote A2.

## Siguiente paso

Parar aquí para la auditoría de Argon. El orden que queda es A2 → A3 (SHADOW) → B5 → B6 → C1 / CB_EVENT_48H (SHADOW) → B3.

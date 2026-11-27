# ACTA T399U — walls del brief privado de metals

**Decisión:** Sander, 26-nov-2026, orden t399u punto 4.3. PR contra `main`. No se fusiona.

## Hallazgo

`vps/metals_vps.py` copiaba `data/alerts/brief.json` del workdir (ya recortado por `BRIEF_KEYS`) al clon privado de g8-macro-data. Si `refresh_brief_snapshot` fallaba, restauraba ese path desde el HEAD del clon público (sin walls) y lo empujaba igual. El privado perdió walls el 25-nov (commit `7a3cc713` en g8-macro-data).

El pull del binario traía las filas 6,11,12. Sin la fila 1 (`data/options/canonical`) el turno de las 23:45 (sombra, 22:45 UTC) no tiene la cadena para reescribir walls.

## Decisión

Mismo patrón que `vps/usd_factor_vps.py` (t385u):

1. Tras el snapshot, `apply_public_alerts`. El push público pasa por `exclude_forbidden_public_alerts`: si quedan claves `BRIEF_KEYS`, `brief.json` no se publica.
2. El push privado sale de `data/alerts/private/{brief,state}.json`.
3. Si el refresh falla, el path público vuelve a HEAD. Ese restore no nombra el brief privado.
4. El pull pasa a filas 1,6,11,12. Antes del push público se borran del workdir `OPTIONS_SURFACE` y `data/options/canonical` (y los otros dos basenames de figura). `LICENSED_UNTRACKED` y `cleanup` hacen lo mismo. El verify privado sigue en 11,12.

`infra/vps/deploy.sh` de g8-macro-data instala todo `bin/`, incluida la copia de `metals_vps.py`. Esa copia se alinea en un PR aparte, como el #72.

## Sin cambios

Fórmulas, umbrales, calibraciones, `dashboard_alerts.py`, el guardián `BRIEF_KEYS` y el resto de tests. `.github/workflows/metals_update.yml` no se toca: `tests/test_metals_private_state_stash.py` línea 15 exige `--rows 6,11,12` en ese workflow. El turno real es el binario del VPS. No hay datos CME/ICE ni cifras de fuente en el diff.

## Pruebas

Test nuevo `tests/test_t399u_metals_walls_private.py`:

- refresh ok: el brief público queda con 0 claves `BRIEF_KEYS` y el privado conserva walls;
- refresh falla: el privado no pierde walls y el path público vuelve a HEAD;
- el árbol que se añadiría al público no lleva basenames de figura, ni `data/alerts/private/`, ni ficheros CME.

`python -m unittest discover -s tests -p 'test_*.py'`: 1541 tests. En esta máquina, sin `rsync`, fallan 5 de `tests/test_private_data.py` (el binario no está); con `rsync` instalado, esos 5 pasan. Otros 6 dan `FileNotFoundError` porque el checkout no trae `data/OPTIONS_SURFACE.json` ni `data/CME_OPTIONS_C1_shadow.json` (no están en git; el guardián lo prohíbe). No los he creado. El resto pasa, incluidos los tests nuevos y los de metals que ya existían.

## Siguiente

Sander revisa. No se fusiona ni se despliega desde aquí.

# ACTA — cme_options.yml solo empuja al privado (orden t247u)

**Decisión:** Sander, 18-nov-2026. Un PR contra `main` (incluye #63 `aa2bc238`). No se fusiona. No se dispara el workflow. No se crea ni se pide ningún secreto.

## Hallazgo
`.github/workflows/cme_options.yml` hacía `git add` de `data/options`, `data/OPTIONS_SURFACE.json` y `data/alerts`, y a continuación commit y push al repo público. El push privado (`private_data.py`, secreto `G8_DATA_WRITE`) iba después, sin `if:`, así que un fallo de ese `git add` se saltaba el push privado.

`.gitignore` de t245u ignora `data/OPTIONS_SURFACE.json`. Con el PR #64 abierto, esas tres rutas JSON dejan de estar ignoradas. Un `git add` que siga en el job volvería a subir el árbol al público.

## Decisión
Se quita el paso de commit y push público. El job no ejecuta `git add`, `git commit` ni `git push` al repo público, aunque esas rutas dejen de estar en `.gitignore`. `permissions` pasa a `contents: read`.

El push privado se mantiene, con `G8_DATA_WRITE`, igual que el de `daily_update`. Su `if` solo mira el resultado del colector y del resumen. No espera a ningún paso de git público.

## Sin cambios
Fórmulas, calibraciones, umbrales, `cme_options_collector.py`, `build_options_summary.py`, `cme_options_backfill.yml`. De los tests, solo la huella de `cme_options.yml` en `test_exclusions`, `test_u1_dashboard_integration`, `test_u2_dictamen_fixes` y `test_c2_options_shadow`.

## Pruebas
La suite y el run `Validate (smoke)` quedan en el PR. Dos tests que no estaba autorizado a editar siguen exigiendo el push público retirado: `tests.test_p7_push_retry.Workflows.test_R4_data_workflows_use_retry` y `tests.test_cme_options_volume_shadow.ClearedVolumeShadow.test_workflow_uploads_raw_outside_git_and_consumers_ignore_volume`.

## t249u
El push del clon privado lo hace `scripts/tools/git_push_retry.sh` (`--no-push` en `private_data.py` deja el commit local). `test_R4_data_workflows_use_retry` no se ha editado. `test_workflow_uploads_raw_outside_git_and_consumers_ignore_volume` queda en la regla sin push ni artefacto en el público.

## Siguiente
No fusionar. No disparar `cme_options.yml`.

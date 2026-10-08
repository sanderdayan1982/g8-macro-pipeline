# ACTA — Netlify: deploys solo cuando cambia docs/ (2026-10-08)

**Hallazgo.** Ciclo 10 sep–9 oct: 1.067 deploys de producción en el equipo = 16.005 créditos (15 por deploy; plan Pro incluye 10.000),
lo que pausó todos los proyectos. En este repo hubo 579 commits desde el 10-sep; solo 71 tocan `docs/` (lo único que Netlify publica).
El resto son commits de bots/datos (`data/`, `mac/`, workflows) que el dashboard lee desde raw.githubusercontent.com, sin necesidad de deploy.

**Decisión.** `netlify.toml` → `[build].ignore`: se omite el deploy si no cambia `docs/` ni `netlify.toml`.

**Sin cambios.** Metodología, fuentes, datos, workflows y el dashboard.

**Pruebas.** `bash scripts/tools/validate_smoke.sh` en verde (2026-10-08, Python 3.11.15 vía uv en el Mac): 753 tests OK (5 skipped), equivalencia S01B, YAML, registry.csv, JS y render del dashboard OK. Efecto real: comprobar en Netlify → Deploys que los commits de datos salen «Skipped».

**Siguiente.** Aplicar lo equivalente en mesa-macro-fx (publica la raíz y lee `/data` de su propio sitio; requiere decisión del propietario).

## Adenda — excluir `docs/actas/` (2026-10-08)
**Hallazgo.** 10-sep→8-oct: 73 commits tocan `docs/`; 22 solo `docs/actas/` (registros, no forman parte del dashboard).
**Decisión.** `ignore` → `-- docs ':!docs/actas' netlify.toml`. Probado en repo temporal: solo-actas → exit 0 (skip); cambio en `docs/x` → exit 1 (build).
**Efecto esperado.** ~51 deploys/mes en lugar de ~73.

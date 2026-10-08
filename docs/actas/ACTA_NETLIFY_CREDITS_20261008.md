# ACTA — Netlify: deploys solo cuando cambia docs/ (2026-10-08)

**Hallazgo.** Ciclo 10 sep–9 oct: 1.067 deploys de producción en el equipo = 16.005 créditos (15 por deploy; plan Pro incluye 10.000),
lo que pausó todos los proyectos. En este repo hubo 579 commits desde el 10-sep; solo 71 tocan `docs/` (lo único que Netlify publica).
El resto son commits de bots/datos (`data/`, `mac/`, workflows) que el dashboard lee desde raw.githubusercontent.com, sin necesidad de deploy.

**Decisión.** `netlify.toml` → `[build].ignore`: se omite el deploy si no cambia `docs/` ni `netlify.toml`.

**Sin cambios.** Metodología, fuentes, datos, workflows y el dashboard.

**Pruebas.** `bash scripts/tools/validate_smoke.sh`. Efecto real: comprobar en Netlify → Deploys que los commits de datos salen como «Skipped».

**Siguiente.** Aplicar lo equivalente en mesa-macro-fx (publica la raíz y lee `/data` de su propio sitio; requiere decisión del propietario).

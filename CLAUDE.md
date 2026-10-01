# CLAUDE.md — g8-macro-pipeline

Reglas de trabajo (CLAUDE_G8). Se aplican a cualquier sesión de Claude Code en este repo,
incluido el agente de mantenimiento.

## Método
1. **Diagnóstico → propuesta → implementación.** Primero se describe el problema con evidencia
   (fichero, fecha, valor), luego se propone el cambio y solo después se implementa.
2. **Nada sin verificar en la fuente primaria.** Ninguna fuente, umbral, tipo oficial ni precio entra
   en el repo sin comprobarlo en la fuente primaria (banco central, oficina estadística, bolsa).
   Indicar la URL y la fecha de la comprobación.
3. **Metodología intocable sin OK explícito del propietario.** No cambiar fórmulas, calibraciones,
   umbrales, ventanas de z, ACM, s01b, factor USD ni ninguna otra metodología sin su aprobación en el chat.
   Arreglar feeds, scrapers, proxies y la presentación del dashboard sí está permitido.
4. **Fuente más fresca disponible.** Si hay una fuente primaria más reciente que la actual (p. ej. el
   banco central por delante del BIS), se prefiere esa; las fechas reales del dato se muestran siempre.
5. **Fallos ruidosos.** Nunca rellenar en silencio ni mostrar datos viejos sin marcarlos con su fecha.

## Cada cambio
- Con tests. Suite completa antes de subir:
  `python -m unittest discover -s tests -p 'test_*.py'`
  (Python 3.11 con `requirements.txt` + `pyyaml`; el Python de Homebrew del Mac no trae las dependencias.)
- Con acta en `docs/actas/ACTA_<lote>.md`: hallazgo, decisión, sin cambios, pruebas, siguiente.
- Antes de subir, las mismas comprobaciones que «Validate (smoke)»: `bash scripts/tools/validate_smoke.sh`
  (suite, equivalencia s01b, YAML, `registry.csv` bien formado —sin comas sueltas—, JS del dashboard, brief en seco).
- Commit + push a `main` solo con todo en verde.

## Mapa rápido
- `scripts/` — fetchers, `dashboard_alerts.py` (brief.json, DQM, Telegram), `g8common/`.
- `data/` — CSV/JSON publicados (el dashboard los lee de raw.githubusercontent.com);
  `data/manual/policy_decisions.csv` — decisiones de tipos verificadas (acta P-1).
- `docs/index.html` — dashboard (Netlify publica `docs/`); capa de red `G8NET`; proxies en `docs/_redirects`.
- `mac/` — jobs locales del Mac (feeds que GitHub Actions no alcanza).
- `sources/registry.csv` — registro de fuentes y fallbacks.
- `.github/agent/mantenimiento.md` — instrucciones del agente de mantenimiento (acta P-6);
  `scripts/tools/agent_gate.py` — lista de ficheros que el agente puede integrar sin OK.
- `scripts/aud_nowcast.py` + `sources/nowcast_aud.json` — estimación diaria AUD 2Y/10Y entre publicaciones semanales de la
  RBA (acta P-8). Siempre etiquetada «EST»; el modelo (json) solo se cambia con OK del propietario.

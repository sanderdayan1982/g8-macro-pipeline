# ACTA P-6 — Agente de mantenimiento (Claude Code en GitHub Actions) · g8-macro-pipeline (1-oct-2026)

Autorizado por el propietario (punto 5 del plan; «procede», 1-oct-2026). Objetivo, en sus palabras: que el agente
«cuide los datos frescos siempre y sea capaz de arreglar o sustituir lo que se caiga o se cambie por las instituciones»,
sin tocar nunca la metodología.

## Hallazgo (verificado en code.claude.com/docs/en/github-actions, 1-oct-2026)
- `anthropics/claude-code-action@v1` admite la **suscripción** (Pro, Max, Team, Enterprise) con el secreto
  `CLAUDE_CODE_OAUTH_TOKEN`, generado con `claude setup-token`; las ejecuciones gastan la cuota del plan, no API.
  No hace falta la alternativa por API con tope de $10/mes.
- Con `prompt` funciona en «automation mode» con cualquier evento, también `schedule`; las herramientas se conceden con
  `--allowedTools` en `claude_args`. `github_token` evita instalar la GitHub App de Claude.
- Validate (smoke) tenía sus comprobaciones en línea en el YAML: la compuerta no podía repetirlas sin duplicarlas.

## Decisión
1. `.github/workflows/maintenance_agent.yml`: lunes, miércoles y viernes `0 19 * * 1,3,5` (19:00 UTC = 20:00 Bata) y a mano.
   - **job `agent`**: token de solo lectura (`contents: read`, `actions: read`), checkout sin credenciales
     (`persist-credentials: false`), `--disallowedTools` para `git push`, `gh pr`, `gh api`, `gh secret`, `gh workflow`;
     `--max-turns 80`, 50 min. **No puede escribir en el repo.** Entrega sus commits como `git bundle` + `.agent/report.md`.
   - **job `gate`** (sin Claude, con permiso de escritura): carga el bundle y
     `scripts/tools/agent_gate.py` clasifica los ficheros cambiados:
     - **AUTO** (solo `scripts/fetch_*.py`, `g8common/cb_direct.py`, `sources/registry.csv`,
       `data/manual/policy_decisions.csv`, `docs/_redirects`, `docs/actas/ACTA_AGENTE_*`, `tests/test_agent_*`,
       `tests/fixtures/agent/*`) + smoke verde → rebase sobre `main`, smoke otra vez y push.
     - **OWNER** (cualquier otro fichero; la lista DENY incluye acm_g8, s01b, dashboard_alerts, usd_factor, book_risk,
       metales, nzd_tp_synth, real_yields, `sources/freshness_*`, huellas congeladas y `.github/`), smoke rojo o
       integración fallida → **PR para el propietario**. Nunca se integra solo.
   - Informe en el resumen del job y por **Telegram** en cada ejecución (también si Claude falla: token o límite del plan).
2. `.github/agent/mantenimiento.md`: instrucciones del agente. Reglas de CLAUDE.md, límites (nunca metodología,
   verificación en fuente primaria, nada de datos inventados), diagnóstico (freshness, watch_state, registros de ingesta,
   DQM, logs de Actions), ritmos que no son fallos, decisiones de tipos oficiales, tests, acta e informe.
3. `scripts/tools/validate_smoke.sh`: las comprobaciones de Validate en un solo script. `validate.yml` y la compuerta lo
   usan los dos. Mismo contenido que antes, ejecutado en local: verde.
4. `.gitignore`: `.agent/`. `CLAUDE.md`: usar `validate_smoke.sh` antes de subir y referencia al agente.

## Pendiente del propietario (no lo hace Claude: credenciales)
1. `claude setup-token` en la Terminal (abre el navegador; da un token de larga duración).
2. `gh secret set CLAUDE_CODE_OAUTH_TOKEN -R sanderdayan1982/g8-macro-pipeline` y pegar el token.
3. Primera prueba: Actions → *Maintenance agent* → *Run workflow*.
Sin el secreto, la ejecución programada falla en el paso de Claude y lo avisa por Telegram (no toca nada).

## Riesgos asumidos
- Consume la cuota del plan Pro (la misma que el chat). Tope por ejecución: 80 turnos y 50 min.
- Lo que la compuerta integra sola no lo revisa nadie antes. Lo acotan la lista permitida, la suite completa y la
  equivalencia s01b; el acta del agente y Telegram dan la traza.

## Pruebas
`tests/test_p6_agent_gate.py` (7):
- clasificación AUTO, OWNER y NADA, y códigos de salida con git real;
- el job del agente es de solo lectura, sin credenciales y con `git push` prohibido, y la compuerta no ejecuta Claude;
- horario y autenticación OAuth;
- smoke compartido entre Validate y la compuerta.
El traspaso por bundle (commit en un clon, carga sobre la base, rebase cuando `main` ya avanzó) se simuló en local.
`bash scripts/tools/validate_smoke.sh` en local: verde.

## Siguiente
El mismo agente para `mesa-macro-fx` (su propia lista de ficheros permitidos: la metodología vive en `config/*.json`).

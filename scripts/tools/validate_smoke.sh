#!/usr/bin/env bash
# validate_smoke.sh — acta P-6: las comprobaciones de «Validate (smoke)» en un solo sitio.
# Las usan .github/workflows/validate.yml y la compuerta del agente de mantenimiento (maintenance_agent.yml).
# Requiere: Python 3.11 con requirements.txt + pyyaml, y node. rc ≠ 0 al primer fallo.
set -euo pipefail
cd "$(dirname "$0")/../.."

echo '::group::Python syntax (scripts/)'
python -m compileall -q scripts
echo '::endgroup::'

echo '::group::Regression tests (isolated fixtures, no sending)'
python -m unittest discover -s tests -p 'test_*.py'
echo '::endgroup::'

echo '::group::S01B E1+E2 equivalence (frozen 2026-09-21 and 2026-09-22 snapshots, detector fields identical)'
python tests/equiv_s01b_e2.py 2026-09-21
python tests/equiv_s01b_e2.py 2026-09-21 --synthetic
python tests/equiv_s01b_e2.py 2026-09-22
python tests/equiv_s01b_e2.py 2026-09-22 --synthetic
echo '::endgroup::'

echo '::group::Workflow YAML parses'
python - <<'EOF'
import glob, sys, yaml
bad = []
for f in glob.glob('.github/workflows/*.yml'):
    try:
        yaml.safe_load(open(f))
    except Exception as e:
        bad.append((f, str(e)))
for f, e in bad:
    print('INVALID', f, e)
sys.exit(1 if bad else 0)
EOF
echo '::endgroup::'

echo '::group::sources/registry.csv well-formed'
python - <<'EOF'
import csv, sys
rows = list(csv.DictReader(open('sources/registry.csv', encoding='utf-8')))
req = {'feed_id', 'primary_access', 'max_staleness_bd'}
assert rows and req <= set(rows[0].keys()), 'registry header missing required columns'
assert all(None not in r and all(v is not None for v in r.values()) for r in rows), 'registry column count mismatch'
ids = [r['feed_id'] for r in rows]
dup = {i for i in ids if ids.count(i) > 1}
assert not dup, 'duplicate feed_id: %s' % sorted(dup)
bad = [r['feed_id'] for r in rows if r['max_staleness_bd'] and not r['max_staleness_bd'].replace('.', '', 1).isdigit()]
assert not bad, 'non-numeric max_staleness_bd: %s' % bad
print(len(rows), 'registry rows OK')
EOF
echo '::endgroup::'

echo '::group::JS syntax (docs/js/*.js + inline scripts of docs/index.html)'
node - <<'EOF'
const fs = require('fs'), path = require('path');
let bad = 0;
for (const f of fs.readdirSync('docs/js').filter(x => x.endsWith('.js'))) {
  try { new Function(fs.readFileSync(path.join('docs/js', f), 'utf8')); console.log('OK', f); }
  catch (e) { console.log('SYNTAX ERROR', f, e.message); bad++; }
}
const h = fs.readFileSync('docs/index.html', 'utf8');
const re = /<script(?![^>]*src)[^>]*>([\s\S]*?)<\/script>/g; let m, i = 0;
while ((m = re.exec(h))) { i++; try { new Function(m[1]); } catch (e) { console.log('SYNTAX ERROR index.html inline script #' + i, e.message); bad++; } }
console.log('checked', i, 'inline scripts');
process.exit(bad ? 1 : 0);
EOF
echo '::endgroup::'

echo '::group::dashboard_alerts.py --dry-run (builds brief without sending)'
python scripts/dashboard_alerts.py --dry-run
echo '::endgroup::'

echo '::group::nzd_tp_synth.py / acm_g8.py import'
python - <<'EOF'
import importlib.util, sys
for name in ('acm_g8', 'real_yields_g8', 'nzd_tp_synth', 'fetch_nzd_b2', 'fetch_chf_snb', 'pos_g8_cot_collector'):
    spec = importlib.util.spec_from_file_location(name, 'scripts/%s.py' % name)
    try:
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        print('import OK', name)
    except SystemExit:
        print('import OK (exits at import)', name)
    except Exception as e:
        print('IMPORT FAILED', name, e); sys.exit(1)
EOF
echo '::endgroup::'

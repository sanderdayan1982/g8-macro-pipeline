#!/usr/bin/env node
/* dashboard_render_check.cjs — acta P-9: el dashboard se pinta de verdad, sección por sección, en un navegador.
 *
 *   NODE_PATH=<dir>/node_modules node scripts/tools/dashboard_render_check.cjs [repo_root]
 *
 * Sirve docs/ en local y abre la página en Chromium (Playwright). Todas las lecturas de data/ del repo
 * (raw.githubusercontent.com/.../main/data/* y /proxy/raw/...) se sirven desde el data/ LOCAL, así se prueba la rama
 * que se va a integrar y no lo que hay en main. El resto de hosts externos se cortan: los respaldos públicos fallan
 * rápido y el dashboard debe mostrar su estado de fallo, nunca romperse.
 *
 * Falla (rc 1) si: hay una excepción de JavaScript no capturada, algún panel muestra «Render error» o
 * «not defined», o una sección obligatoria queda vacía o sin su tabla. La usan validate_smoke.sh y la compuerta del
 * agente: un arreglo del dashboard solo se integra si pasa.
 */
const http = require('http');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = path.resolve(process.argv[2] || path.join(__dirname, '..', '..'));
const DOCS = path.join(ROOT, 'docs');
const DATA = path.join(ROOT, 'data');
const RAW_PREFIX = '/sanderdayan1982/g8-macro-pipeline/main/data/';
const TYPES = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json',
                '.csv': 'text/csv', '.svg': 'image/svg+xml', '.png': 'image/png' };

// Sección → condición mínima de «pintada». Texto prohibido en cualquier panel.
const REQUIRED = [
  ['brief-body', el => el.innerText.trim().length > 200 && !/Cargando brief/.test(el.innerText)],
  ['attr-matrix', el => !!el.querySelector('table.attr-table tbody tr')],
  ['s01b-panel', el => !!el.querySelector('table tbody tr')],
  ['dqm-panel', el => el.innerText.trim().length > 100],
  ['uf-panel', el => el.innerText.trim().length > 50],
  ['cot-panel', el => el.innerText.trim().length > 50],
  ['opt-surface', el => el.innerText.trim().length > 50],
];
const FORBIDDEN = /Render error|is not defined|undefined is not|Cannot read propert|NaN%|\[object Object\]/;

function serve() {
  return new Promise(resolve => {
    const srv = http.createServer((req, res) => {
      let u = decodeURIComponent(req.url.split('?')[0]);
      let file;
      if (u.startsWith('/proxy/raw' + RAW_PREFIX)) file = path.join(DATA, u.slice(('/proxy/raw' + RAW_PREFIX).length));
      else file = path.join(DOCS, u === '/' ? 'index.html' : u);
      if (!file.startsWith(DOCS) && !file.startsWith(DATA)) { res.writeHead(403); return res.end(); }
      fs.readFile(file, (err, buf) => {
        if (err) { res.writeHead(404); return res.end('not found'); }
        res.writeHead(200, { 'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream',
                             'Access-Control-Allow-Origin': '*' });
        res.end(buf);
      });
    });
    srv.listen(0, '127.0.0.1', () => resolve(srv));
  });
}

(async () => {
  const srv = await serve();
  const port = srv.address().port;
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
  const errors = [];
  page.on('pageerror', e => errors.push('excepción JS: ' + e.message));
  await page.route('**/*', route => {
    const u = new URL(route.request().url());
    if (u.hostname === '127.0.0.1') return route.continue();
    if (u.hostname === 'raw.githubusercontent.com' && u.pathname.startsWith(RAW_PREFIX)) {
      const file = path.join(DATA, decodeURIComponent(u.pathname.slice(RAW_PREFIX.length)));
      if (!file.startsWith(DATA) || !fs.existsSync(file)) return route.fulfill({ status: 404, body: 'not found' });
      return route.fulfill({ status: 200, body: fs.readFileSync(file), headers: { 'Access-Control-Allow-Origin': '*',
        'Content-Type': TYPES[path.extname(file)] || 'text/plain' } });
    }
    // Fuentes de estilo/librerías (CDN) permitidas; todo lo demás (proxies públicos, APIs) se corta.
    if (/^(cdnjs\.cloudflare\.com|cdn\.jsdelivr\.net|unpkg\.com|cdn\.plot\.ly|fonts\.(googleapis|gstatic)\.com)$/.test(u.hostname))
      return route.continue();
    return route.abort();
  });
  await page.goto('http://127.0.0.1:' + port + '/', { waitUntil: 'domcontentloaded', timeout: 60000 });
  const deadline = Date.now() + 90000;
  let status = [];
  while (Date.now() < deadline) {
    status = await page.evaluate(([req]) => req.map(([id, fn]) => {
      const el = document.getElementById(id);
      if (!el) return [id, 'falta el contenedor'];
      // eslint-disable-next-line no-new-func
      return [id, (new Function('el', 'return (' + fn + ')(el)'))(el) ? 'ok' : 'vacío'];
    }), [REQUIRED.map(([id, fn]) => [id, fn.toString()])]);
    if (status.every(s => s[1] === 'ok')) break;
    await page.waitForTimeout(1000);
  }
  const bad = await page.evaluate(re => {
    const out = [];
    document.querySelectorAll('section.dashboard-section').forEach(sec => {
      const m = sec.innerText.match(new RegExp(re));
      if (m) out.push(sec.id + ': «' + m[0] + '»');
    });
    return out;
  }, FORBIDDEN.source);
  await browser.close();
  srv.close();
  const fails = errors.concat(bad, status.filter(s => s[1] !== 'ok').map(s => s[0] + ': ' + s[1]));
  status.forEach(s => console.log('  ' + (s[1] === 'ok' ? 'OK   ' : 'FAIL ') + s[0] + (s[1] === 'ok' ? '' : ' (' + s[1] + ')')));
  if (fails.length) {
    console.log('RENDER: FAIL');
    fails.forEach(f => console.log('  ✗ ' + f));
    process.exit(1);
  }
  console.log('RENDER: OK — ' + status.length + ' paneles pintados, sin excepciones ni errores visibles');
})().catch(e => { console.log('RENDER: ERROR ' + e.message); process.exit(1); });

#!/usr/bin/env node
/* Ejecuta el render real de §01, §01-b y §02 (los IIFE de docs/index.html)
   con los CSV/JSON locales y Papa Parse 5.4.1 (el mismo del dashboard).
   Escribe en stdout el HTML que esos módulos asignan a innerHTML.
   Reloj: --now ISO UTC. El proceso tiene que arrancar con TZ=UTC. */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

if (new Date().getTimezoneOffset() !== 0) {
  console.error('argos_render exige TZ=UTC');
  process.exit(1);
}

const ROOT = path.resolve(__dirname, '..');
const RAW = 'https://raw.githubusercontent.com/sanderdayan1982/g8-macro-pipeline/main/data/';

function argNow() {
  const i = process.argv.indexOf('--now');
  if (i < 0) return new Date().toISOString().replace(/\.\d+Z$/, 'Z');
  const v = process.argv[i + 1];
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(v)) {
    console.error('--now debe ser YYYY-MM-DDTHH:MM:SSZ');
    process.exit(2);
  }
  return v;
}

const NOW = argNow();
const FIXED = Date.parse(NOW);
const OrigDate = Date;

function FakeDate(...args) {
  if (!new.target) return OrigDate(FIXED).toString();
  if (args.length === 0) return new OrigDate(FIXED);
  return new OrigDate(...args);
}
FakeDate.now = function () { return FIXED; };
FakeDate.parse = OrigDate.parse;
FakeDate.UTC = OrigDate.UTC;
FakeDate.prototype = OrigDate.prototype;
global.Date = FakeDate;

const store = Object.create(null);
global.localStorage = {
  getItem(k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
  setItem(k, v) { store[k] = String(v); },
  removeItem(k) { delete store[k]; },
  key(i) { return Object.keys(store)[i] || null; },
  get length() { return Object.keys(store).length; },
};

function element(id) {
  return {
    id: id,
    innerHTML: '',
    style: {},
    classList: { toggle() {}, add() {}, remove() {} },
    querySelectorAll() { return []; },
    querySelector() { return null; },
    addEventListener() {},
    setAttribute() {},
    getAttribute() { return null; },
  };
}
const els = {
  'attr-matrix': element('attr-matrix'),
  's01b-panel': element('s01b-panel'),
  'fls-table': element('fls-table'),
};
global.document = {
  readyState: 'complete',
  getElementById(id) { return els[id] || element(id); },
  querySelector() { return null; },
  querySelectorAll() { return []; },
  addEventListener() {},
};
global.window = global;
global.location = { protocol: 'https:', href: 'https://g8-institutional.netlify.app/' };
global.console = console;

const papaSrc = fs.readFileSync(path.join(__dirname, 'vendor', 'papaparse-5.4.1.min.js'), 'utf8');
vm.runInThisContext(papaSrc, { filename: 'papaparse-5.4.1.min.js' });
if (typeof global.Papa === 'undefined' && typeof Papa !== 'undefined') global.Papa = Papa;

function scriptContaining(html, anchor) {
  const i = html.indexOf(anchor);
  if (i < 0) throw new Error('ancla no encontrada: ' + anchor);
  const s = html.lastIndexOf('<script>', i);
  const e = html.indexOf('</script>', i);
  if (s < 0 || e < 0) throw new Error('script no cerrado: ' + anchor);
  return html.slice(s + '<script>'.length, e);
}

const html = fs.readFileSync(path.join(ROOT, 'docs', 'index.html'), 'utf8');
const blocks = [
  ['G8NET', 'G8NET — shared network layer'],
  ['G8DQM', 'G8DQM — DATA QUALITY MONITOR'],
  ['S01B', 'S01B.json unreachable'],
  ['ATTR', 'ATTRIBUTION MATRIX v2'],
  ['FLOORS', 'FLOOR SPREADS — self-contained'],
];

vm.runInThisContext(scriptContaining(html, blocks[0][1]), { filename: 'g8net.js' });
global.G8NET.fetchChain = async function (urls) {
  const u = (urls[0] && urls[0].url) || urls[0];
  if (typeof u !== 'string' || u.indexOf(RAW) !== 0) return { ok: false, error: 'fuera del repo ' + u };
  const rel = u.slice(RAW.length);
  const file = path.join(ROOT, 'data', rel);
  if (!file.startsWith(path.join(ROOT, 'data')) || !fs.existsSync(file)) return { ok: false, error: 'ausente ' + rel };
  return { ok: true, text: fs.readFileSync(file, 'utf8') };
};

for (let i = 1; i < blocks.length; i++) {
  vm.runInThisContext(scriptContaining(html, blocks[i][1]), { filename: blocks[i][0] + '.js' });
}

function ready() {
  return els['attr-matrix'].innerHTML.indexOf('<table') >= 0 &&
    els['s01b-panel'].innerHTML.indexOf('<table') >= 0 &&
    els['fls-table'].innerHTML.indexOf('<tbody>') >= 0;
}

const t0 = Date.now();
(function poll() {
  if (ready()) {
    process.stdout.write(JSON.stringify({
      now: NOW,
      attr: els['attr-matrix'].innerHTML,
      s01b: els['s01b-panel'].innerHTML,
      floors: els['fls-table'].innerHTML,
    }));
    return;
  }
  if (Date.now() - t0 > 15000) {
    console.error('render incompleto', {
      attr: els['attr-matrix'].innerHTML.slice(0, 180),
      s01b: els['s01b-panel'].innerHTML.slice(0, 180),
      floors: els['fls-table'].innerHTML.slice(0, 180),
    });
    process.exit(1);
  }
  setTimeout(poll, 25);
})();

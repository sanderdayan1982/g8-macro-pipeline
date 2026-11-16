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

function argVal(flag) {
  const i = process.argv.indexOf(flag);
  if (i < 0) return null;
  return process.argv[i + 1] || null;
}

function argNow() {
  const v = argVal('--now');
  if (!v) return new Date().toISOString().replace(/\.\d+Z$/, 'Z');
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(v)) {
    console.error('--now debe ser YYYY-MM-DDTHH:MM:SSZ');
    process.exit(2);
  }
  return v;
}

const NOW = argNow();
const CUT = argVal('--cut');
if (CUT && !/^\d{4}-\d{2}-\d{2}$/.test(CUT)) {
  console.error('--cut debe ser YYYY-MM-DD');
  process.exit(2);
}
const DATA = argVal('--data') ? path.resolve(argVal('--data')) : path.join(ROOT, 'data');

/* --cut: argos_rates.py ignora filas posteriores a la sesión 1D cerrada.
   El HTML compartido no cambia; solo el texto que este render le entrega. */
function isoField(field) {
  const f = String(field || '').replace(/"/g, '').trim();
  if (/^\d{8}$/.test(f)) return f.slice(0, 4) + '-' + f.slice(4, 6) + '-' + f.slice(6, 8);
  if (/^\d{4}-\d{2}-\d{2}/.test(f)) return f.slice(0, 10);
  return null;
}

function cutKind(rel) {
  if (rel === 'TONA.csv' || rel === 'AONIA.csv' || rel === 'AUD_NOWCAST.csv') return 'csv';
  if (/^RY_G8_[A-Z0-9]+\.csv$/.test(rel)) return 'csv';
  if (rel === 'S01B.json') return 's01b';
  return null;
}

function cutCsv(text) {
  const lines = text.split('\n');
  let dropped = false;
  const kept = lines.filter(function (line) {
    const d = isoField(line.split(',')[0]);
    if (d && d > CUT) { dropped = true; return false; }
    return true;
  });
  return dropped ? kept.join('\n') : text;
}

function cutS01B(text) {
  let doc;
  try { doc = JSON.parse(text); } catch (e) { return text; }
  const asOf = String((doc && doc.as_of) || '');
  if (!/^\d{4}-\d{2}-\d{2}$/.test(asOf) || asOf <= CUT) return text;
  return JSON.stringify({
    schema: doc.schema || 's01b-1',
    as_of: '',
    status: doc.status || 'CONTEXT',
    currencies: {},
    events: [],
  });
}

function applyCut(rel, text) {
  if (!CUT) return text;
  const kind = cutKind(rel);
  if (kind === 'csv') return cutCsv(text);
  if (kind === 's01b') return cutS01B(text);
  return text;
}

function readRel(rel) {
  if (!rel || rel.indexOf('\0') >= 0 || rel.indexOf('..') >= 0 || path.isAbsolute(rel)) return null;
  const file = path.resolve(DATA, rel);
  const root = path.resolve(DATA);
  if (file !== root && file.indexOf(root + path.sep) !== 0) return null;
  if (!fs.existsSync(file)) return null;
  return applyCut(rel, fs.readFileSync(file, 'utf8'));
}
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
  const text = readRel(rel);
  if (text == null) return { ok: false, error: 'ausente ' + rel };
  return { ok: true, text: text };
};

for (let i = 1; i < blocks.length; i++) {
  let src = scriptContaining(html, blocks[i][1]);
  if (blocks[i][0] === 'ATTR') {
    src = src.replace('function build(data) {', [
      'function build(data) {',
      '    window.__G8legs = {};',
      '    CCYS.forEach(function (c) {',
      '        var d = data[c];',
      '        function vals(s) {',
      '            if (!s || !s.series) return null;',
      '            return s.series.map(function (x) { return x.v; }).filter(function (v) { return typeof v === "number" && isFinite(v); });',
      '        }',
      '        function pts(s) {',
      '            if (!s || !s.series) return [];',
      '            return s.series.map(function (x) {',
      '                var d = String(x.d);',
      '                if (/^\\d{8}$/.test(d)) d = d.slice(0, 4) + "-" + d.slice(4, 6) + "-" + d.slice(6, 8);',
      '                else d = d.slice(0, 10);',
      '                return { d: d, v: x.v };',
      '            }).filter(function (x) { return typeof x.v === "number" && isFinite(x.v); });',
      '        }',
      '        window.__G8legs[c] = d ? { nom: vals(d.nom), real: vals(d.real), be: vals(d.be), real_pts: pts(d.real) } : null;',
      '    });',
    ].join('\n'));
  }
  vm.runInThisContext(src, { filename: blocks[i][0] + '.js' });
}

function ready() {
  return els['attr-matrix'].innerHTML.indexOf('<table') >= 0 &&
    els['s01b-panel'].innerHTML.indexOf('<table') >= 0 &&
    els['fls-table'].innerHTML.indexOf('<tbody>') >= 0;
}

const t0 = Date.now();
(function poll() {
  if (ready()) {
    var s01bDoc = {};
    try { s01bDoc = JSON.parse(readRel('S01B.json') || ''); } catch (e) { s01bDoc = {}; }
    var legs = global.__G8legs || {};
    Object.keys(legs).forEach(function (c) {
      if (!legs[c]) return;
      var row = (s01bDoc.currencies || {})[c] || {};
      // ΔBE22 que pinta §01-b: d_be de S01B.json (H=22). null si la pata no existe (CHF).
      legs[c].d_be_22 = row.d_be == null ? null : row.d_be;
      legs[c].t = row.t || null;
      legs[c].t0 = row.t0 || null;
    });
    process.stdout.write(JSON.stringify({
      now: NOW,
      attr: els['attr-matrix'].innerHTML,
      s01b: els['s01b-panel'].innerHTML,
      floors: els['fls-table'].innerHTML,
      legs: legs,
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

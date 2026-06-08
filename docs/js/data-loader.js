/* =============================================================================
   G8 MACRO PIPELINE — Data Loader v3 (POLISHED)
   ============================================================================= */

(function (global) {
    'use strict';

    const REPO_RAW_BASE = 'https://raw.githubusercontent.com/sanderdayan1982/g8-macro-pipeline/main/data';

    const RFR_FEEDS = {
        estr:  { file: 'ESTR.csv',    ccy: 'EUR', source: 'ECB',         label: 'ESTR'    },
        sonia: { file: 'SONIA.csv',   ccy: 'GBP', source: 'BoE',         label: 'SONIA'   },
        aonia: { file: 'AONIA.csv',   ccy: 'AUD', source: 'RBA',         label: 'AONIA'   },
        tona:  { file: 'TONA.csv',    ccy: 'JPY', source: 'BoJ',         label: 'TONA'    },
        corra: { file: 'CORRA.csv',   ccy: 'CAD', source: 'BoC',         label: 'CORRA'   },
        ocr:   { file: 'NZD_OCR.csv', ccy: 'NZD', source: 'BIS/RBNZ',    label: 'NZD OCR' },
        sofr:  { file: 'SOFR.csv',    ccy: 'USD', source: 'FRED/NY Fed', label: 'SOFR'    }
    };

    const BILLS_CATALOG = {
        us:  { ccy: 'USD', source: 'FRED',           label: 'US Treasuries',     tenors: ['3M', '6M', '1Y', '2Y'], filePrefix: 'US'         },
        eur: { ccy: 'EUR', source: 'ECB AAA Curve',  label: 'EUR AAA Curve',     tenors: ['3M', '6M', '1Y', '2Y', '5Y', '10Y']             },
        gbp: { ccy: 'GBP', source: 'BoE',            label: 'UK Gilts',          tenors: ['6M', '1Y', '2Y', '5Y', '10Y']                   },
        jpy: { ccy: 'JPY', source: 'MoF Japan',      label: 'JGB',               tenors: ['1Y', '2Y', '3Y', '5Y', '10Y', '20Y']            },
        aud: { ccy: 'AUD', source: 'RBA F1',         label: 'AGS',               tenors: ['1M', '3M', '6M']                                },
        cad: { ccy: 'CAD', source: 'BoC Valet',      label: 'GoC Bills',         tenors: ['3M', '6M', '1Y']                                },
        chf: { ccy: 'CHF', source: 'SNB manual',     label: 'CHF Confederation', tenors: ['3M', '6M', '1Y']                                },
        nzd: { ccy: 'NZD', source: 'RBNZ/NZDM',      label: 'NZ Govt Bonds',     tenors: ['3M', '6M', '1Y']                                }
    };

    function billFile(ccyKey, tenor) {
    const cfg = BILLS_CATALOG[ccyKey];
    const prefix = cfg.filePrefix || cfg.ccy;
    return `${prefix}_BILL_${tenor}.csv`;
    }

    const STALE_DAYS_FRESH = 5;
    const STALE_DAYS_STALE = 10;
    const XCCY_MIN_OBS = 10;

    const csvCache = new Map();
    const loadStats = { ok: 0, fail: 0, byFile: {} };

    async function loadCSV(filename) {
        if (csvCache.has(filename)) return csvCache.get(filename);

        const url = `${REPO_RAW_BASE}/${filename}?t=${Date.now()}`;

        try {
            const response = await fetch(url);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const text = await response.text();

            const parsed = Papa.parse(text, {
                header: true,
                dynamicTyping: true,
                skipEmptyLines: true,
                transformHeader: (h) => h.trim().toLowerCase()
            });

            const rowCount = parsed.data.length;
            csvCache.set(filename, parsed.data);
            loadStats.ok++;
            loadStats.byFile[filename] = `OK (${rowCount} rows)`;
            return parsed.data;
        } catch (err) {
            console.error(`[CSV] ${filename}: ${err.message}`);
            csvCache.set(filename, null);
            loadStats.fail++;
            loadStats.byFile[filename] = `FAIL (${err.message})`;
            return null;
        }
    }

    function parseDate(s) {
        if (s instanceof Date) return s;
        if (s == null) return null;

        if (typeof s === 'number') {
            const str = String(s);
            if (str.length === 8) {
                const yr = parseInt(str.substring(0, 4), 10);
                const mo = parseInt(str.substring(4, 6), 10);
                const da = parseInt(str.substring(6, 8), 10);
                if (yr > 1900 && mo >= 1 && mo <= 12 && da >= 1 && da <= 31) {
                    return new Date(Date.UTC(yr, mo - 1, da));
                }
            }
            return new Date(s);
        }

        if (typeof s !== 'string') return null;
        const trimmed = s.trim();

        const compactMatch = trimmed.match(/^(\d{4})(\d{2})(\d{2})$/);
        if (compactMatch) return new Date(Date.UTC(+compactMatch[1], +compactMatch[2] - 1, +compactMatch[3]));

        const isoMatch = trimmed.match(/^(\d{4})-(\d{2})-(\d{2})/);
        if (isoMatch) return new Date(Date.UTC(+isoMatch[1], +isoMatch[2] - 1, +isoMatch[3]));

        const d = new Date(trimmed);
        return isNaN(d.getTime()) ? null : d;
    }

    function normalizeOHLCV(rows) {
        if (!rows || rows.length === 0) return { dates: [], values: [] };
        const sample = rows[0];
        const dateCol = ['date', 'time', 'timestamp', 'datetime'].find((c) => c in sample);
        if (!dateCol) return { dates: [], values: [] };
        const valueCol = ['close', 'value', 'rate', 'yield', 'price', 'level'].find((c) => c in sample);
        if (!valueCol) return { dates: [], values: [] };

        const dates = [];
        const values = [];

        for (const row of rows) {
            const d = parseDate(row[dateCol]);
            const v = typeof row[valueCol] === 'number' ? row[valueCol] : parseFloat(row[valueCol]);
            if (d && !isNaN(v)) { dates.push(d); values.push(v); }
        }

        const idx = dates.map((_, i) => i).sort((a, b) => dates[a] - dates[b]);
        return { dates: idx.map((i) => dates[i]), values: idx.map((i) => values[i]) };
    }

    async function loadBillsCurve(ccyKey) {
        const cfg = BILLS_CATALOG[ccyKey];
        if (!cfg) return { dates: [], tenors: [], data: {}, available: [] };

        try {
            const sortedTenors = [...cfg.tenors].sort((a, b) => tenorToMonths(a) - tenorToMonths(b));

            const tenorRows = await Promise.all(
                sortedTenors.map(async (tenor) => {
                    try {
                        const rows = await loadCSV(billFile(ccyKey, tenor));
                        const series = rows ? normalizeOHLCV(rows) : null;
                        return { tenor, series };
                    } catch (err) {
                        console.error(`[Curve ${cfg.ccy}] tenor ${tenor} threw: ${err.message}`);
                        return { tenor, series: null };
                    }
                })
            );

            const tenorStatus = tenorRows.map((t) =>
                `${t.tenor}=${t.series && t.series.dates.length > 0 ? t.series.dates.length + 'obs' : 'EMPTY'}`
            ).join(', ');
            console.log(`[Curve ${cfg.ccy}] ${tenorStatus}`);

            const availableTenors = tenorRows.filter((t) => t.series && t.series.dates.length > 0);

            if (availableTenors.length === 0) {
                console.warn(`[Curve ${cfg.ccy}] NO TENORS LOADED`);
                return { dates: [], tenors: [], data: {}, available: [] };
            }

            const allDatesSet = new Set();
            for (const t of availableTenors) {
                for (const d of t.series.dates) allDatesSet.add(d.getTime());
            }
            const allDates = [...allDatesSet].sort((a, b) => a - b).map((ts) => new Date(ts));

            const data = {};
            const tenors = [];

            for (const t of availableTenors) {
                tenors.push(t.tenor);
                const lookup = new Map();
                for (let i = 0; i < t.series.dates.length; i++) {
                    lookup.set(t.series.dates[i].getTime(), t.series.values[i]);
                }
                data[t.tenor] = allDates.map((d) => lookup.get(d.getTime()) ?? null);
            }

            return { dates: allDates, tenors, data, available: tenors };
        } catch (err) {
            console.error(`[Curve ${cfg.ccy}] FATAL:`, err);
            return { dates: [], tenors: [], data: {}, available: [] };
        }
    }

    function tenorToMonths(t) {
        const s = String(t).toLowerCase().trim();
        const m = s.match(/(\d+(?:\.\d+)?)\s*([myd]?)/);
        if (!m) return 9999;
        const n = parseFloat(m[1]);
        const unit = m[2];
        if (unit === 'y') return n * 12;
        if (unit === 'm' || unit === '') return n;
        if (unit === 'd') return n / 30;
        return n;
    }

    function daysSince(date) {
        if (!date) return Infinity;
        const ms = new Date() - date;
        return Math.floor(ms / (1000 * 60 * 60 * 24));
    }

    function staleStatus(lastDate) {
        const days = daysSince(lastDate);
        if (days <= STALE_DAYS_FRESH) return 'fresh';
        if (days <= STALE_DAYS_STALE) return 'stale';
        return 'fail';
    }

    async function loadAllRFR() {
        const out = {};
        const promises = Object.entries(RFR_FEEDS).map(async ([key, cfg]) => {
            const rows = await loadCSV(cfg.file);
            out[key] = { ...cfg, rows, series: rows ? normalizeOHLCV(rows) : null };
        });
        await Promise.all(promises);
        const okCount = Object.values(out).filter((f) => f.series && f.series.dates.length > 0).length;
        console.log(`[RFR Summary] ${okCount}/${Object.keys(RFR_FEEDS).length} feeds loaded`);
        return out;
    }

    async function loadAllBills() {
        const out = {};
        const promises = Object.entries(BILLS_CATALOG).map(async ([key, cfg]) => {
            const curve = await loadBillsCurve(key);
            out[key] = { ...cfg, rows: null, curve };
        });
        await Promise.all(promises);
        const okCount = Object.values(out).filter((f) => f.curve && f.curve.dates.length > 0).length;
        console.log(`[Bills Summary] ${okCount}/${Object.keys(BILLS_CATALOG).length} currencies with curve data`);
        return out;
    }

    global.G8DataLoader = {
        FEEDS: { rfr: RFR_FEEDS, bills: BILLS_CATALOG },
        loadCSV, loadAllRFR, loadAllBills,
        normalizeSeries: normalizeOHLCV,
        daysSince, staleStatus, parseDate, tenorToMonths,
        loadStats, REPO_RAW_BASE, XCCY_MIN_OBS,
        VERSION: 'v3'
    };

})(window);

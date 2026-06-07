/* =============================================================================
   G8 MACRO PIPELINE — Data Loader
   Fetches CSVs from GitHub raw, parses via PapaParse, exposes via G8Dashboard
   ============================================================================= */

(function (global) {
    'use strict';

    const REPO_RAW_BASE = 'https://raw.githubusercontent.com/sanderdayan1982/g8-macro-pipeline/main/data';

    const FEEDS = {
        rfr: {
            estr:  { csv: 'estr.csv',  ccy: 'EUR', source: 'ECB',        label: 'ESTR'    },
            sonia: { csv: 'sonia.csv', ccy: 'GBP', source: 'BoE',        label: 'SONIA'   },
            saron: { csv: 'saron.csv', ccy: 'CHF', source: 'SIX/SNB',    label: 'SARON'   },
            aonia: { csv: 'aonia.csv', ccy: 'AUD', source: 'RBA',        label: 'AONIA'   },
            tona:  { csv: 'tona.csv',  ccy: 'JPY', source: 'BoJ',        label: 'TONA'    },
            corra: { csv: 'corra.csv', ccy: 'CAD', source: 'BoC',        label: 'CORRA'   },
            ocr:   { csv: 'ocr.csv',   ccy: 'NZD', source: 'BIS/RBNZ',   label: 'NZD OCR' },
            sofr:  { csv: 'sofr.csv',  ccy: 'USD', source: 'FRED/NY Fed',label: 'SOFR'    }
        },
        bills: {
            us:  { csv: 'us_bills.csv',  ccy: 'USD', source: 'FRED',           label: 'US Treasuries'       },
            eur: { csv: 'eur_bills.csv', ccy: 'EUR', source: 'ECB AAA Curve',  label: 'EUR AAA Curve'       },
            gbp: { csv: 'gbp_bills.csv', ccy: 'GBP', source: 'BoE',            label: 'UK Gilts'            },
            jpy: { csv: 'jpy_bills.csv', ccy: 'JPY', source: 'MoF Japan',      label: 'JGB'                 },
            aud: { csv: 'aud_bills.csv', ccy: 'AUD', source: 'RBA F1',         label: 'AGS'                 },
            cad: { csv: 'cad_bills.csv', ccy: 'CAD', source: 'BoC Valet',      label: 'GoC Bills'           },
            chf: { csv: 'chf_bills.csv', ccy: 'CHF', source: 'SNB manual',     label: 'CHF Confederation'   },
            nzd: { csv: 'nzd_bills.csv', ccy: 'NZD', source: 'RBNZ/NZDM',      label: 'NZ Govt Bonds'       }
        }
    };

    const STALE_DAYS_FRESH = 3;
    const STALE_DAYS_STALE = 7;

    const csvCache = new Map();

    async function loadCSV(path) {
        if (csvCache.has(path)) {
            return csvCache.get(path);
        }

        const url = `${REPO_RAW_BASE}/${path}?t=${Date.now()}`;

        try {
            const response = await fetch(url);
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            const text = await response.text();

            const parsed = Papa.parse(text, {
                header: true,
                dynamicTyping: true,
                skipEmptyLines: true,
                transformHeader: (h) => h.trim().toLowerCase()
            });

            if (parsed.errors.length > 0) {
                console.warn(`CSV parse warnings for ${path}:`, parsed.errors);
            }

            csvCache.set(path, parsed.data);
            return parsed.data;
        } catch (err) {
            console.error(`Failed to load CSV ${path}:`, err);
            csvCache.set(path, null);
            return null;
        }
    }

    function detectDateColumn(row) {
        if (!row) return null;
        const candidates = ['date', 'time', 'timestamp', 'datetime'];
        for (const key of Object.keys(row)) {
            const k = key.toLowerCase();
            if (candidates.includes(k)) return key;
        }
        return null;
    }

    function detectValueColumn(row) {
        if (!row) return null;
        const candidates = ['rate', 'value', 'close', 'yield', 'price', 'level'];
        for (const key of Object.keys(row)) {
            const k = key.toLowerCase();
            if (candidates.includes(k)) return key;
        }
        const cols = Object.keys(row);
        if (cols.length >= 2) return cols[1];
        return null;
    }

    function parseDate(s) {
        if (s instanceof Date) return s;
        if (typeof s === 'number') return new Date(s);
        if (typeof s !== 'string') return null;

        const isoMatch = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
        if (isoMatch) {
            return new Date(Date.UTC(+isoMatch[1], +isoMatch[2] - 1, +isoMatch[3]));
        }

        const slashMatch = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})/);
        if (slashMatch) {
            return new Date(Date.UTC(+slashMatch[3], +slashMatch[2] - 1, +slashMatch[1]));
        }

        const d = new Date(s);
        return isNaN(d.getTime()) ? null : d;
    }

    function normalizeSeries(rows) {
        if (!rows || rows.length === 0) return { dates: [], values: [] };

        const sample = rows[0];
        const dateCol = detectDateColumn(sample);
        const valueCol = detectValueColumn(sample);

        if (!dateCol || !valueCol) {
            console.warn('Could not detect date/value columns', sample);
            return { dates: [], values: [] };
        }

        const dates = [];
        const values = [];

        for (const row of rows) {
            const d = parseDate(row[dateCol]);
            const v = parseFloat(row[valueCol]);
            if (d && !isNaN(v)) {
                dates.push(d);
                values.push(v);
            }
        }

        const idx = dates.map((_, i) => i).sort((a, b) => dates[a] - dates[b]);
        return {
            dates:  idx.map((i) => dates[i]),
            values: idx.map((i) => values[i])
        };
    }

    function normalizeCurve(rows) {
        if (!rows || rows.length === 0) return { dates: [], tenors: [], data: {} };

        const sample = rows[0];
        const dateCol = detectDateColumn(sample);
        if (!dateCol) return { dates: [], tenors: [], data: {} };

        const tenorCols = Object.keys(sample).filter((k) => k !== dateCol);

        const dates = [];
        const data = {};
        tenorCols.forEach((t) => data[t] = []);

        for (const row of rows) {
            const d = parseDate(row[dateCol]);
            if (!d) continue;
            dates.push(d);
            tenorCols.forEach((t) => {
                const v = parseFloat(row[t]);
                data[t].push(isNaN(v) ? null : v);
            });
        }

        const idx = dates.map((_, i) => i).sort((a, b) => dates[a] - dates[b]);
        const sortedDates = idx.map((i) => dates[i]);
        const sortedData = {};
        tenorCols.forEach((t) => {
            sortedData[t] = idx.map((i) => data[t][i]);
        });

        return {
            dates: sortedDates,
            tenors: tenorCols,
            data: sortedData
        };
    }

    function daysSince(date) {
        if (!date) return Infinity;
        const now = new Date();
        const ms = now - date;
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
        const promises = Object.entries(FEEDS.rfr).map(async ([key, cfg]) => {
            const rows = await loadCSV(cfg.csv);
            out[key] = {
                ...cfg,
                rows,
                series: rows ? normalizeSeries(rows) : null
            };
        });
        await Promise.all(promises);
        return out;
    }

    async function loadAllBills() {
        const out = {};
        const promises = Object.entries(FEEDS.bills).map(async ([key, cfg]) => {
            const rows = await loadCSV(cfg.csv);
            out[key] = {
                ...cfg,
                rows,
                curve: rows ? normalizeCurve(rows) : null
            };
        });
        await Promise.all(promises);
        return out;
    }

    global.G8DataLoader = {
        FEEDS,
        loadCSV,
        loadAllRFR,
        loadAllBills,
        normalizeSeries,
        normalizeCurve,
        daysSince,
        staleStatus,
        REPO_RAW_BASE
    };

})(window);

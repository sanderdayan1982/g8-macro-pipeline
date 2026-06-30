/* =============================================================================
   G8 MACRO PIPELINE — Data Loader v5 (POLICY + ACM)
   v5 changes:
   - POLICY_FEEDS catalog (GB/JP/CH/AU) loaded for new Section 05
   - ACM_FEED for new Section 06 (USD 10Y term premium, NY Fed)
   - loadAllPolicy() and loadACM() orchestration functions
   - Public API exposes new catalogs for charts.js v5
   v4 baseline preserved:
   - XCCY_MIN_OBS=5, forward-fill, CH_POLICY as SARON proxy in XCCY
   ============================================================================= */

(function (global) {
    'use strict';

    const REPO_RAW_BASE = 'https://raw.githubusercontent.com/sanderdayan1982/g8-macro-pipeline/main/data';

    // ─────────────────────────────────────────────────────────────────────────
    // CATALOGS
    // ─────────────────────────────────────────────────────────────────────────

    const RFR_FEEDS = {
        estr:  { file: 'ESTR.csv',         ccy: 'EUR', source: 'ECB',         label: 'ESTR'         },
        sonia: { file: 'SONIA.csv',        ccy: 'GBP', source: 'BoE',         label: 'SONIA'        },
        aonia: { file: 'AONIA.csv',        ccy: 'AUD', source: 'RBA',         label: 'AONIA'        },
        tona:  { file: 'TONA.csv',         ccy: 'JPY', source: 'BoJ',         label: 'TONA'         },
        corra: { file: 'CORRA.csv',        ccy: 'CAD', source: 'BoC',         label: 'CORRA'        },
        ocr:   { file: 'NZD_OCR.csv',      ccy: 'NZD', source: 'BIS/RBNZ',    label: 'NZD OCR', eventDriven: true },
        sofr:  { file: 'SOFR.csv',         ccy: 'USD', source: 'FRED/NY Fed', label: 'SOFR'         },
        // v4: CHF proxy — BIS publishes SNB Policy Rate (SARON ≈ Policy ± 5-10bps)
        // Documented caveat: NOT real SARON, used only for XCCY proxy basis
        chpol: { file: 'CH_POLICY.csv',    ccy: 'CHF', source: 'BIS/SNB',     label: 'SNB Policy (SARON proxy)', isProxy: true }
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

    // v5: NEW — Policy rates catalog
    // Note: USD/EUR/CAD policy rates are NOT in pipeline — they live in TradingView native feeds.
    // Only the 4 BIS-sourced policy rates are wired here.
    const POLICY_FEEDS = {
        gb_policy: { file: 'GB_POLICY.csv', ccy: 'GBP', source: 'BIS/BoE', label: 'BoE Bank Rate'           },
        jp_policy: { file: 'JP_POLICY.csv', ccy: 'JPY', source: 'BIS/BoJ', label: 'BoJ Policy Balance Rate' },
        ch_policy: { file: 'CH_POLICY.csv', ccy: 'CHF', source: 'BIS/SNB', label: 'SNB Policy Rate'         },
        au_policy: { file: 'AU_POLICY.csv', ccy: 'AUD', source: 'BIS/RBA', label: 'RBA Cash Rate Target'    }
    };

    // v6: ACM Term Premium catalog — own K=5 engine, 7 G8 currencies (monthly).
    // CSVs: cols DATE(YYYYMMDD),Y10_FIT,RNY10,TP10 in percentage points.
    const ACM_FEED = {
        usd: { file: 'ACM_G8_USD.csv', ccy: 'USD', source: 'G8 ACM K=5', label: 'USD ACM 10Y TP' },
        eur: { file: 'ACM_G8_EUR.csv', ccy: 'EUR', source: 'G8 ACM K=5', label: 'EUR ACM 10Y TP' },
        gbp: { file: 'ACM_G8_GBP.csv', ccy: 'GBP', source: 'G8 ACM K=5', label: 'GBP ACM 10Y TP' },
        chf: { file: 'ACM_G8_CHF.csv', ccy: 'CHF', source: 'G8 ACM K=5', label: 'CHF ACM 10Y TP' },
        aud: { file: 'ACM_G8_AUD.csv', ccy: 'AUD', source: 'G8 ACM K=5', label: 'AUD ACM 10Y TP' },
        cad: { file: 'ACM_G8_CAD.csv', ccy: 'CAD', source: 'G8 ACM K=5', label: 'CAD ACM 10Y TP' },
        jpy: { file: 'ACM_G8_JPY.csv', ccy: 'JPY', source: 'G8 ACM K=5', label: 'JPY ACM 10Y TP' }
    };

    function billFile(ccyKey, tenor) {
        const cfg = BILLS_CATALOG[ccyKey];
        const prefix = cfg.filePrefix || cfg.ccy;
        return `${prefix}_BILL_${tenor}.csv`;
    }

    const STALE_DAYS_FRESH = 5;
    const STALE_DAYS_STALE = 10;

    // ── G8 market-holiday calendar (mirror of index.html DQM) ──────────────
    // Business-day counting must skip weekends AND G8 settlement holidays,
    // otherwise a long weekend inflates the lag and flips healthy feeds to
    // stale. Union across jurisdictions = safe superset (only ever makes a
    // feed look fresher, never staler → no false alarms). Extend yearly.
    const G8_HOLIDAYS = {
        '2025-01-01':1,'2025-01-20':1,'2025-02-17':1,'2025-04-18':1,'2025-04-21':1,
        '2025-05-05':1,'2025-05-26':1,'2025-06-19':1,'2025-07-04':1,'2025-08-04':1,
        '2025-08-25':1,'2025-09-01':1,'2025-10-13':1,'2025-11-11':1,'2025-11-27':1,
        '2025-12-25':1,'2025-12-26':1,
        '2026-01-01':1,'2026-01-19':1,'2026-02-16':1,'2026-04-03':1,'2026-04-06':1,
        '2026-05-04':1,'2026-05-25':1,'2026-06-19':1,'2026-07-03':1,'2026-08-03':1,
        '2026-08-31':1,'2026-09-07':1,'2026-10-12':1,'2026-11-11':1,'2026-11-26':1,
        '2026-12-25':1,'2026-12-28':1,
        '2027-01-01':1,'2027-01-18':1,'2027-02-15':1,'2027-03-26':1,'2027-03-29':1,
        '2027-05-03':1,'2027-05-31':1,'2027-06-18':1,'2027-07-05':1,'2027-08-02':1,
        '2027-08-30':1,'2027-09-06':1,'2027-10-11':1,'2027-11-11':1,'2027-11-25':1,
        '2027-12-27':1,'2027-12-28':1
    };
    function isG8Holiday(dt) { return !!G8_HOLIDAYS[dt.toISOString().slice(0, 10)]; }

    // Per-feed business-day budgets. Daily market feeds settle T+1 and cross
    // weekends; policy rates are EVENT-DRIVEN (a flat inter-meeting series is
    // correct, not stale) so they get a wide inter-meeting budget.
    const FRESH_BD_DAILY = 5;     // LIVE while ≤ this many business days old
    const STALE_BD_DAILY = 12;    // beyond → fail
    const FRESH_BD_EVENT = 45;    // policy rates: ~inter-meeting gap
    const STALE_BD_EVENT = 110;
    const FRESH_BD_WEEKLY = 12;   // ACM term premium (weekly)
    const STALE_BD_WEEKLY = 30;
    const FRESH_BD_MONTHLY = 30;  // SNB bills etc.
    const STALE_BD_MONTHLY = 75;

    // v4: relaxed from 10 to 5 to allow NZD/CHF with higher lag
    const XCCY_MIN_OBS = 5;

    // v4: max forward-fill gap (days). Beyond this, leave NaN.
    // Policy rates: 30 days (rates change rarely)
    // Market rates: 7 days (must be recent to be valid)
    const FFILL_MAX_DAYS_POLICY = 30;
    const FFILL_MAX_DAYS_MARKET = 7;

    const csvCache = new Map();
    const loadStats = { ok: 0, fail: 0, byFile: {} };

    // ─────────────────────────────────────────────────────────────────────────
    // CSV LOADER
    // ─────────────────────────────────────────────────────────────────────────

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

    // ─────────────────────────────────────────────────────────────────────────
    // DATE PARSER
    // ─────────────────────────────────────────────────────────────────────────

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

    // ─────────────────────────────────────────────────────────────────────────
    // NORMALIZE OHLCV
    // ─────────────────────────────────────────────────────────────────────────

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

    // v4: Forward-fill series to a target end date, filling business-day gaps
    // up to maxGapDays. Returns extended {dates, values} series.
    function forwardFillSeries(series, targetEndDate, maxGapDays) {
        if (!series || series.dates.length === 0) return series;
        if (!targetEndDate) return series;

        const sorted = series.dates.map((d, i) => ({ d, v: series.values[i] }))
            .sort((a, b) => a.d - b.d);

        const lastObs = sorted[sorted.length - 1];
        const lastDate = lastObs.d;
        const lastValue = lastObs.v;

        if (lastDate >= targetEndDate) return series;

        const gapDays = Math.floor((targetEndDate - lastDate) / 86400000);
        const fillDays = Math.min(gapDays, maxGapDays);

        if (fillDays <= 0) return series;

        const filledDates = [...sorted.map(o => o.d)];
        const filledValues = [...sorted.map(o => o.v)];

        let current = new Date(lastDate.getTime() + 86400000);
        let added = 0;
        while (added < fillDays && current <= targetEndDate) {
            const dow = current.getUTCDay();
            if (dow !== 0 && dow !== 6) {
                filledDates.push(new Date(current.getTime()));
                filledValues.push(lastValue);
                added++;
            }
            current = new Date(current.getTime() + 86400000);
        }

        return { dates: filledDates, values: filledValues };
    }

    // ─────────────────────────────────────────────────────────────────────────
    // BILLS CURVE
    // ─────────────────────────────────────────────────────────────────────────

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

    // ─────────────────────────────────────────────────────────────────────────
    // FRESHNESS
    // ─────────────────────────────────────────────────────────────────────────

    function daysSince(date) {
        if (!date) return Infinity;
        const ms = new Date() - date;
        return Math.floor(ms / 86400000);
    }

    // G8 business days between a date and today (skips weekends + G8 holidays)
    function businessDaysSince(date) {
        if (!date) return Infinity;
        const ld = new Date(date); ld.setHours(0, 0, 0, 0);
        if (isNaN(ld.getTime())) return Infinity;
        const today = new Date(); today.setHours(0, 0, 0, 0);
        let bd = 0, cur = new Date(ld), guard = 0;
        while (cur < today && guard++ < 600) {
            cur.setDate(cur.getDate() + 1);
            const day = cur.getDay();
            if (day !== 0 && day !== 6 && !isG8Holiday(cur)) bd++;
        }
        return bd;
    }

    // Budget by feed class. `cls` ∈ {'daily','event','weekly','monthly'}.
    // Defaults to 'daily' so existing callers keep working.
    function staleStatus(lastDate, cls) {
        const bd = businessDaysSince(lastDate);
        let fresh, stale;
        switch (cls) {
            case 'event':   fresh = FRESH_BD_EVENT;   stale = STALE_BD_EVENT;   break;
            case 'weekly':  fresh = FRESH_BD_WEEKLY;  stale = STALE_BD_WEEKLY;  break;
            case 'monthly': fresh = FRESH_BD_MONTHLY; stale = STALE_BD_MONTHLY; break;
            default:        fresh = FRESH_BD_DAILY;   stale = STALE_BD_DAILY;
        }
        if (bd <= fresh) return 'fresh';
        if (bd <= stale) return 'stale';
        return 'fail';
    }

    // ─────────────────────────────────────────────────────────────────────────
    // ORCHESTRATION
    // ─────────────────────────────────────────────────────────────────────────

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

    // v5: NEW
    async function loadAllPolicy() {
        const out = {};
        const promises = Object.entries(POLICY_FEEDS).map(async ([key, cfg]) => {
            const rows = await loadCSV(cfg.file);
            out[key] = { ...cfg, rows, series: rows ? normalizeOHLCV(rows) : null };
        });
        await Promise.all(promises);
        const okCount = Object.values(out).filter((f) => f.series && f.series.dates.length > 0).length;
        console.log(`[Policy Summary] ${okCount}/${Object.keys(POLICY_FEEDS).length} policy feeds loaded`);
        return out;
    }

    // v6: parser for own ACM G8 CSVs (uppercase cols DATE,Y10_FIT,RNY10,TP10).
    // series.values = TP10 (keeps quality-grid / header compat); .fit/.rn carried
    // alongside so the chart can optionally draw fitted yield and risk-neutral.
    function parseACMG8(rows) {
        if (!rows || rows.length === 0) return { dates: [], values: [], fit: [], rn: [] };
        const recs = [];
        for (const row of rows) {
            const d = parseDate(row.DATE);
            const tp = typeof row.TP10 === 'number' ? row.TP10 : parseFloat(row.TP10);
            if (!d || isNaN(tp)) continue;
            const ft = typeof row.Y10_FIT === 'number' ? row.Y10_FIT : parseFloat(row.Y10_FIT);
            const rn = typeof row.RNY10 === 'number' ? row.RNY10 : parseFloat(row.RNY10);
            recs.push({ d, tp, ft, rn });
        }
        recs.sort((a, b) => a.d - b.d);
        return {
            dates:  recs.map((r) => r.d),
            values: recs.map((r) => r.tp),
            fit:    recs.map((r) => r.ft),
            rn:     recs.map((r) => r.rn)
        };
    }

    // v6: NEW — loads the 7 G8 ACM term-premium feeds (own K=5 engine)
    async function loadACM() {
        const out = {};
        const promises = Object.entries(ACM_FEED).map(async ([key, cfg]) => {
            const rows = await loadCSV(cfg.file);
            out[key] = { ...cfg, rows, series: rows ? parseACMG8(rows) : null };
        });
        await Promise.all(promises);
        const okCount = Object.values(out).filter((f) => f.series && f.series.dates.length > 0).length;
        console.log(`[ACM G8 Summary] ${okCount}/${Object.keys(ACM_FEED).length} ACM feeds loaded`);
        return out;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // PUBLIC API
    // ─────────────────────────────────────────────────────────────────────────

    global.G8DataLoader = {
        FEEDS: { rfr: RFR_FEEDS, bills: BILLS_CATALOG, policy: POLICY_FEEDS, acm: ACM_FEED },
        loadCSV, loadAllRFR, loadAllBills, loadAllPolicy, loadACM,
        normalizeSeries: normalizeOHLCV,
        forwardFillSeries,
        daysSince, businessDaysSince, staleStatus, parseDate, tenorToMonths,
        loadStats, REPO_RAW_BASE, XCCY_MIN_OBS,
        FFILL_MAX_DAYS_POLICY, FFILL_MAX_DAYS_MARKET,
        VERSION: 'v5.1'
    };

})(window);

/* =============================================================================
   G8 MACRO PIPELINE — Charts v4 (NZD + CHF FIX)
   v4 changes:
   - XCCY computation applies forward-fill to align all series to common date
   - CHF included with "proxy" visual indicator (CH_POLICY used as SARON proxy)
   - Footer note updated to reflect CHF proxy methodology
   ============================================================================= */

(function (global) {
    'use strict';

    const COLORS = {
        bg: '#090d13', bgPanel: '#0f131a', grid: '#1c2330', gridDim: '#151b25',
        text: '#dce0e8', textDim: '#9ba3b4', textMuted: '#6b7388',
        gold: '#fdb813', green: '#00c853', red: '#ff4b4b', amber: '#ffaa00',
        teal: '#00b4a0', blue: '#4a9eff', purple: '#c77dff',
        chfProxy: '#888888'  // grey-ish for CHF proxy bar
    };

    const CCY_COLOR = {
        USD: COLORS.blue, EUR: COLORS.gold, GBP: COLORS.red, JPY: COLORS.green,
        AUD: COLORS.amber, NZD: COLORS.teal, CAD: COLORS.purple, CHF: COLORS.text
    };

    const PLOTLY_BASE_LAYOUT = {
        paper_bgcolor: COLORS.bg, plot_bgcolor: COLORS.bg,
        font: { family: "'JetBrains Mono', monospace", size: 11, color: COLORS.text },
        margin: { l: 60, r: 30, t: 30, b: 50 },
        xaxis: { gridcolor: COLORS.grid, zerolinecolor: COLORS.grid, linecolor: COLORS.grid, tickfont: { color: COLORS.textDim, size: 10 }, showspikes: false },
        yaxis: { gridcolor: COLORS.grid, zerolinecolor: COLORS.grid, linecolor: COLORS.grid, tickfont: { color: COLORS.textDim, size: 10 }, showspikes: false },
        legend: { font: { color: COLORS.textDim, size: 10 }, bgcolor: 'rgba(15, 19, 26, 0.8)', bordercolor: COLORS.grid, borderwidth: 1, orientation: 'h', x: 0, y: -0.15 },
        hoverlabel: { bgcolor: COLORS.bgPanel, bordercolor: COLORS.gold, font: { family: "'JetBrains Mono', monospace", size: 11, color: COLORS.text } }
    };

    const PLOTLY_CONFIG = {
        responsive: true, displaylogo: false,
        modeBarButtonsToRemove: ['lasso2d', 'select2d', 'autoScale2d', 'toggleSpikelines'],
        displayModeBar: 'hover'
    };

    function setLoading(id) { const el = document.getElementById(id); if (el) el.innerHTML = '<div class="chart-loading">Loading data</div>'; }
    function setError(id, msg) { const el = document.getElementById(id); if (el) el.innerHTML = `<div class="chart-error">⚠ ${msg}</div>`; }

    function formatDate(d) {
        if (!d) return '—';
        return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
    }

    function filterLastNDays(series, days) {
        if (!series || series.dates.length === 0) return series;
        const lastDate = series.dates[series.dates.length - 1];
        const cutoff = new Date(lastDate.getTime() - days * 86400000);
        const dates = [], values = [];
        for (let i = 0; i < series.dates.length; i++) {
            if (series.dates[i] >= cutoff) { dates.push(series.dates[i]); values.push(series.values[i]); }
        }
        return { dates, values };
    }

    function tenorToMonths(t) {
        const s = String(t).toLowerCase().trim();
        const m = s.match(/(\d+(?:\.\d+)?)\s*([myd]?)/);
        if (!m) return 9999;
        const n = parseFloat(m[1]);
        if (m[2] === 'y') return n * 12;
        if (m[2] === 'd') return n / 30;
        return n;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // RFR CHART (Section 1)
    // ─────────────────────────────────────────────────────────────────────────

    function renderRFRChart(rfrData) {
        const traces = [];
        // Note: 'chpol' (CHF proxy) intentionally EXCLUDED from RFR chart
        // to avoid confusion. It's only used in XCCY computation.
        const order = ['estr', 'sonia', 'saron', 'aonia', 'tona', 'corra', 'ocr', 'sofr'];
        for (const key of order) {
            const feed = rfrData[key];
            if (!feed || !feed.series || feed.series.dates.length === 0) continue;
            const f = filterLastNDays(feed.series, 90);
            traces.push({
                x: f.dates, y: f.values, type: 'scatter', mode: 'lines',
                name: `${feed.label} (${feed.ccy})`,
                line: { color: CCY_COLOR[feed.ccy] || COLORS.text, width: 1.8 },
                hovertemplate: `<b>${feed.label}</b><br>%{x|%Y-%m-%d}<br>%{y:.3f}%<extra></extra>`
            });
        }
        if (traces.length === 0) { setError('chart-rfr', 'No RFR data available'); return; }
        const layout = {
            ...PLOTLY_BASE_LAYOUT,
            yaxis: { ...PLOTLY_BASE_LAYOUT.yaxis, title: { text: 'Rate (%)', font: { color: COLORS.textDim, size: 11 } }, tickformat: '.2f' },
            xaxis: { ...PLOTLY_BASE_LAYOUT.xaxis, type: 'date', tickformat: '%b %d' },
            showlegend: true
        };
        Plotly.newPlot('chart-rfr', traces, layout, PLOTLY_CONFIG);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // CURVES GRID (Section 2)
    // ─────────────────────────────────────────────────────────────────────────

    function renderCurvesGrid(billsData) {
        const grid = document.getElementById('curves-grid');
        if (!grid) return;
        const order = ['us', 'eur', 'gbp', 'jpy', 'aud', 'nzd', 'cad', 'chf'];
        grid.innerHTML = '';
        for (const key of order) {
            const feed = billsData[key];
            const ccyLower = feed?.ccy?.toLowerCase() || key;
            const cell = document.createElement('div');
            cell.className = 'curve-cell';
            cell.innerHTML = `
                <div class="curve-header">
                    <span class="curve-ccy ${ccyLower}">${feed?.ccy || key.toUpperCase()}</span>
                    <span class="curve-slope" id="slope-${key}">—</span>
                </div>
                <div class="curve-chart" id="curve-${key}"></div>
            `;
            grid.appendChild(cell);
            renderSingleCurve(`curve-${key}`, feed, key);
        }
    }

    function renderSingleCurve(containerId, feed, key) {
        if (!feed || !feed.curve || feed.curve.dates.length === 0) {
            setError(containerId, 'No curve data');
            return;
        }
        const curve = feed.curve;
        const dates = curve.dates;
        const lastIdx = dates.length - 1;
        const targetDate = new Date(dates[lastIdx].getTime() - 30 * 86400000);
        let prevIdx = lastIdx;
        for (let i = lastIdx; i >= 0; i--) { if (dates[i] <= targetDate) { prevIdx = i; break; } }
        const sortedTenors = [...curve.tenors].sort((a, b) => tenorToMonths(a) - tenorToMonths(b));
        const xLabels = sortedTenors.map((t) => t.toUpperCase());
        const yLatest = sortedTenors.map((t) => curve.data[t]?.[lastIdx] ?? null);
        const yPrev = sortedTenors.map((t) => curve.data[t]?.[prevIdx] ?? null);
        const ccyColor = CCY_COLOR[feed.ccy] || COLORS.text;
        const traces = [
            { x: xLabels, y: yPrev, type: 'scatter', mode: 'lines+markers', name: '30D ago',
              line: { color: COLORS.textMuted, width: 1, dash: 'dot' },
              marker: { size: 4, color: COLORS.textMuted },
              hovertemplate: `30D ago<br>%{x}: %{y:.2f}%<extra></extra>` },
            { x: xLabels, y: yLatest, type: 'scatter', mode: 'lines+markers', name: 'Latest',
              line: { color: ccyColor, width: 2 },
              marker: { size: 6, color: ccyColor },
              hovertemplate: `Latest<br>%{x}: %{y:.2f}%<extra></extra>` }
        ];
        const layout = {
            ...PLOTLY_BASE_LAYOUT,
            margin: { l: 40, r: 15, t: 10, b: 35 },
            xaxis: { ...PLOTLY_BASE_LAYOUT.xaxis, tickfont: { color: COLORS.textDim, size: 9 } },
            yaxis: { ...PLOTLY_BASE_LAYOUT.yaxis, tickfont: { color: COLORS.textDim, size: 9 }, tickformat: '.2f' },
            showlegend: false, height: 180
        };
        Plotly.newPlot(containerId, traces, layout, { ...PLOTLY_CONFIG, displayModeBar: false });
        const validLatest = yLatest.filter((v) => v != null);
        if (validLatest.length >= 2) {
            const slope = (validLatest[validLatest.length - 1] - validLatest[0]) * 100;
            const slopeEl = document.getElementById(`slope-${key}`);
            if (slopeEl) {
                slopeEl.textContent = `slope ${slope >= 0 ? '+' : ''}${slope.toFixed(0)}bps`;
                slopeEl.style.color = slope >= 0 ? COLORS.green : COLORS.red;
            }
        }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // XCCY CHART (Section 3) — v4 with forward-fill + CHF proxy
    // ─────────────────────────────────────────────────────────────────────────

    function renderXCCYChart(rfrData, billsData) {
        const usdRFR = rfrData.sofr;
        const usdBills = billsData.us;

        if (!usdRFR || !usdRFR.series || usdRFR.series.dates.length === 0) {
            setError('chart-xccy', 'USD anchor (SOFR) unavailable'); return;
        }
        if (!usdBills || !usdBills.curve || usdBills.curve.dates.length === 0) {
            setError('chart-xccy', 'USD anchor (Treasuries) unavailable'); return;
        }

        // v4: CHF added using chpol (CH_POLICY as SARON proxy)
        const pairs = [
            { rfrKey: 'estr',  billsKey: 'eur', label: 'EUR',   isProxy: false },
            { rfrKey: 'sonia', billsKey: 'gbp', label: 'GBP',   isProxy: false },
            { rfrKey: 'aonia', billsKey: 'aud', label: 'AUD',   isProxy: false },
            { rfrKey: 'tona',  billsKey: 'jpy', label: 'JPY',   isProxy: false },
            { rfrKey: 'corra', billsKey: 'cad', label: 'CAD',   isProxy: false }
        ];

        const results = pairs.map((p) => {
            const ccyRFR = rfrData[p.rfrKey];
            const ccyBills = billsData[p.billsKey];
            return { 
                label: p.label, 
                isProxy: p.isProxy,
                ...computeXCCYBasis(usdRFR, usdBills, ccyRFR, ccyBills, p.label) 
            };
        }).filter((r) => r.zScore !== null);

        if (results.length === 0) {
            setError('chart-xccy', 'Insufficient overlapping data for XCCY basis'); return;
        }
        console.log(`[XCCY] Computed basis for ${results.length} currencies`);
        results.sort((a, b) => Math.abs(b.zScore) - Math.abs(a.zScore));

        const labels = results.map((r) => r.label);
        const zValues = results.map((r) => r.zScore);
        
        // v4: special color for CHF proxy bar
        const colors = results.map((r) => {
            if (r.isProxy) return COLORS.chfProxy;  // grey for CHF proxy
            const z = r.zScore;
            return Math.abs(z) > 2.0 ? COLORS.red : Math.abs(z) > 1.0 ? COLORS.amber : COLORS.green;
        });

        const traces = [{
            x: labels, y: zValues, type: 'bar',
            marker: { color: colors, line: { color: COLORS.grid, width: 1 } },
            text: results.map((r) => `${r.basisBps >= 0 ? '+' : ''}${r.basisBps.toFixed(0)}bps`),
            textposition: 'outside',
            textfont: { color: COLORS.textDim, size: 10 },
            hovertemplate: results.map((r) => 
                r.isProxy 
                ? `<b>%{x}</b> (proxy: SNB Policy)<br>Z-score: %{y:.2f}σ<br>Basis: %{text}<br><i>Not real SARON</i><extra></extra>`
                : `<b>%{x}</b><br>Z-score: %{y:.2f}σ<br>Basis: %{text}<extra></extra>`
            )
        }];
        const layout = {
            ...PLOTLY_BASE_LAYOUT,
            yaxis: { 
                ...PLOTLY_BASE_LAYOUT.yaxis, 
                title: { text: 'Z-score (252D)', font: { color: COLORS.textDim, size: 11 } }, 
                tickformat: '.1f', 
                zeroline: true, 
                zerolinecolor: COLORS.textMuted, 
                zerolinewidth: 1 
            },
            shapes: [
                { type: 'line', xref: 'paper', x0: 0, x1: 1, y0: 2, y1: 2, line: { color: COLORS.red, width: 1, dash: 'dash' } },
                { type: 'line', xref: 'paper', x0: 0, x1: 1, y0: -2, y1: -2, line: { color: COLORS.red, width: 1, dash: 'dash' } }
            ],
            showlegend: false
        };
        Plotly.newPlot('chart-xccy', traces, layout, PLOTLY_CONFIG);
    }

    function computeXCCYBasis(usdRFR, usdBills, ccyRFR, ccyBills, label) {
        if (!ccyRFR || !ccyRFR.series || !ccyBills || !ccyBills.curve || ccyBills.curve.dates.length === 0) {
            console.warn(`[XCCY ${label}] missing inputs`);
            return { zScore: null, basisBps: null };
        }
        const usdTenor = pickShortTenor(usdBills.curve.tenors);
        const ccyTenor = pickShortTenor(ccyBills.curve.tenors);
        if (!usdTenor || !ccyTenor) { 
            console.warn(`[XCCY ${label}] no short tenor`); 
            return { zScore: null, basisBps: null }; 
        }

        // v4: Apply forward-fill to align series with different lag
        // Determine target end date (most recent observation across all 4 series)
        const dates_pool = [
            usdRFR.series.dates[usdRFR.series.dates.length - 1],
            ccyRFR.series.dates[ccyRFR.series.dates.length - 1],
            usdBills.curve.dates[usdBills.curve.dates.length - 1],
            ccyBills.curve.dates[ccyBills.curve.dates.length - 1],
        ].filter(Boolean);
        const targetEnd = new Date(Math.max(...dates_pool.map(d => d.getTime())));

        // Forward-fill RFR series (max 30 days for policy, 7 for market overnight)
        const FFILL_POLICY = global.G8DataLoader?.FFILL_MAX_DAYS_POLICY ?? 30;
        const FFILL_MARKET = global.G8DataLoader?.FFILL_MAX_DAYS_MARKET ?? 7;
        const ffillFn = global.G8DataLoader?.forwardFillSeries;

        // For RFR proxies (CHF chpol, NZD ocr): use policy ffill window
        // For other RFRs: use market ffill window
        const ccyFfillDays = (label === 'CHF*' || label === 'NZD') ? FFILL_POLICY : FFILL_MARKET;
        const usdRFRFilled = ffillFn ? ffillFn(usdRFR.series, targetEnd, FFILL_MARKET) : usdRFR.series;
        const ccyRFRFilled = ffillFn ? ffillFn(ccyRFR.series, targetEnd, ccyFfillDays) : ccyRFR.series;

        const usdRFRMap = seriesToMap(usdRFRFilled);
        const ccyRFRMap = seriesToMap(ccyRFRFilled);
        const usdBillsMap = curveToMap(usdBills.curve, usdTenor);
        const ccyBillsMap = curveToMap(ccyBills.curve, ccyTenor);

        const commonDates = [...usdRFRMap.keys()]
            .filter((d) => ccyRFRMap.has(d) && usdBillsMap.has(d) && ccyBillsMap.has(d))
            .sort();

        const minObs = global.G8DataLoader?.XCCY_MIN_OBS ?? 5;
        if (commonDates.length < minObs) {
            console.warn(`[XCCY ${label}] only ${commonDates.length} common dates (need ${minObs})`);
            return { zScore: null, basisBps: null };
        }

        console.log(`[XCCY ${label}] computing with ${commonDates.length} common dates`);

        const basis = commonDates.map((d) => {
            const usdLeg = (usdRFRMap.get(d) - usdBillsMap.get(d)) * 100;
            const ccyLeg = (ccyRFRMap.get(d) - ccyBillsMap.get(d)) * 100;
            return ccyLeg - usdLeg;
        });

        const window = Math.min(252, basis.length);
        const windowData = basis.slice(-window);
        const mean = windowData.reduce((a, b) => a + b, 0) / windowData.length;
        const variance = windowData.reduce((a, b) => a + (b - mean) ** 2, 0) / windowData.length;
        const stdev = Math.sqrt(variance);
        const latest = basis[basis.length - 1];
        const zScore = stdev > 0 ? (latest - mean) / stdev : 0;

        return { zScore, basisBps: latest, obs: basis.length };
    }

    function pickShortTenor(tenors) {
        const sorted = [...tenors].sort((a, b) => tenorToMonths(a) - tenorToMonths(b));
        for (const target of ['3m', '6m', '1y']) {
            const match = sorted.find((t) => String(t).toLowerCase().replace(/\s/g, '') === target);
            if (match) return match;
        }
        return sorted[0];
    }

    function seriesToMap(series) {
        const m = new Map();
        for (let i = 0; i < series.dates.length; i++) m.set(formatDate(series.dates[i]), series.values[i]);
        return m;
    }

    function curveToMap(curve, tenor) {
        const m = new Map();
        const values = curve.data[tenor];
        if (!values) return m;
        for (let i = 0; i < curve.dates.length; i++) {
            if (values[i] != null) m.set(formatDate(curve.dates[i]), values[i]);
        }
        return m;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // QUALITY MONITOR (Section 4)
    // ─────────────────────────────────────────────────────────────────────────

    function renderQualityGrid(rfrData, billsData) {
        const grid = document.getElementById('quality-grid');
        if (!grid) return;
        grid.innerHTML = '';
        const allFeeds = [];
        Object.entries(rfrData).forEach(([k, f]) => {
            // Skip CHF proxy from main quality grid (it's tracked separately as proxy)
            if (k === 'chpol') return;
            allFeeds.push({ key: `rfr-${k}`, label: f.label, ccy: f.ccy, source: f.source, type: 'RFR', series: f.series });
        });
        Object.entries(billsData).forEach(([k, f]) => allFeeds.push({ key: `bills-${k}`, label: f.label, ccy: f.ccy, source: f.source, type: 'Bills', series: f.curve ? { dates: f.curve.dates } : null }));

        for (const f of allFeeds) {
            const cell = document.createElement('div');
            cell.className = 'quality-cell';
            const hasData = f.series && f.series.dates.length > 0;
            const lastDate = hasData ? f.series.dates[f.series.dates.length - 1] : null;
            const status = hasData ? global.G8DataLoader.staleStatus(lastDate) : 'fail';
            const days = hasData ? global.G8DataLoader.daysSince(lastDate) : null;
            const obs = hasData ? f.series.dates.length : 0;
            cell.innerHTML = `
                <div class="quality-cell-header">
                    <span class="quality-feed-name">${f.label}</span>
                    <span class="quality-status-badge ${status}">${status}</span>
                </div>
                <div class="quality-cell-meta">
                    <div class="quality-meta-row"><span class="quality-meta-label">CCY · Type</span><span class="quality-meta-value">${f.ccy} · ${f.type}</span></div>
                    <div class="quality-meta-row"><span class="quality-meta-label">Source</span><span class="quality-meta-value">${f.source}</span></div>
                    <div class="quality-meta-row"><span class="quality-meta-label">Last obs</span><span class="quality-meta-value">${hasData ? formatDate(lastDate) : '—'}</span></div>
                    <div class="quality-meta-row"><span class="quality-meta-label">Lag · Records</span><span class="quality-meta-value">${days != null ? days + 'D' : '—'} · ${obs}</span></div>
                </div>`;
            grid.appendChild(cell);
        }
    }

    function updateHeaderStatus(rfrData, billsData) {
        let latestDate = null, freshCount = 0, staleCount = 0, failCount = 0, totalCount = 0;
        const allFeeds = [
            // Skip chpol from header counting (it's a proxy, not direct feed)
            ...Object.entries(rfrData).filter(([k]) => k !== 'chpol').map(([_, f]) => ({ series: f.series })),
            ...Object.values(billsData).map((f) => ({ series: f.curve ? { dates: f.curve.dates } : null }))
        ];
        for (const f of allFeeds) {
            totalCount++;
            if (!f.series || f.series.dates.length === 0) { failCount++; continue; }
            const ld = f.series.dates[f.series.dates.length - 1];
            if (!latestDate || ld > latestDate) latestDate = ld;
            const status = global.G8DataLoader.staleStatus(ld);
            if (status === 'fresh') freshCount++;
            else if (status === 'stale') staleCount++;
            else failCount++;
        }
        const lastUpdateEl = document.getElementById('last-update');
        if (lastUpdateEl) lastUpdateEl.textContent = latestDate ? formatDate(latestDate) : '—';
        const statusCountEl = document.getElementById('status-count');
        if (statusCountEl) statusCountEl.textContent = `${freshCount}/${totalCount}`;
        const dotsEl = document.getElementById('status-dots');
        if (dotsEl) {
            let dots = '';
            for (let i = 0; i < freshCount; i++) dots += '<span>●</span>';
            for (let i = 0; i < staleCount; i++) dots += '<span class="dot-stale">●</span>';
            for (let i = 0; i < failCount; i++) dots += '<span class="dot-fail">●</span>';
            dotsEl.innerHTML = dots;
        }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // INIT
    // ─────────────────────────────────────────────────────────────────────────

    async function init() {
        console.log('[G8 Dashboard v4] Initializing...');
        setLoading('chart-rfr');
        setLoading('chart-xccy');
        document.getElementById('curves-grid').innerHTML = '<div class="chart-loading">Loading curves</div>';
        document.getElementById('quality-grid').innerHTML = '<div class="chart-loading">Loading quality data</div>';
        try {
            const [rfrData, billsData] = await Promise.all([
                global.G8DataLoader.loadAllRFR(),
                global.G8DataLoader.loadAllBills()
            ]);
            updateHeaderStatus(rfrData, billsData);
            renderRFRChart(rfrData);
            renderCurvesGrid(billsData);
            renderXCCYChart(rfrData, billsData);
            renderQualityGrid(rfrData, billsData);
            console.log('[G8 Dashboard v4] Render complete');
            console.log('[G8 Dashboard v4] Load stats:', global.G8DataLoader.loadStats);
        } catch (err) {
            console.error('[G8 Dashboard v4] Init failed:', err);
        }
    }

    global.G8Dashboard = { init, renderRFRChart, renderCurvesGrid, renderXCCYChart, renderQualityGrid, COLORS, CCY_COLOR, VERSION: 'v4' };

})(window);

/* =============================================================================
   G8 MACRO PIPELINE — Charts & Rendering
   Plotly.js configs · Bloomberg-dark theme · 4 sections
   ============================================================================= */

(function (global) {
    'use strict';

    const COLORS = {
        bg:        '#090d13',
        bgPanel:   '#0f131a',
        grid:      '#1c2330',
        gridDim:   '#151b25',
        text:      '#dce0e8',
        textDim:   '#9ba3b4',
        textMuted: '#6b7388',
        gold:      '#fdb813',
        green:     '#00c853',
        red:       '#ff4b4b',
        amber:     '#ffaa00',
        teal:      '#00b4a0',
        blue:      '#4a9eff',
        purple:    '#c77dff'
    };

    const CCY_COLOR = {
        USD: COLORS.blue,
        EUR: COLORS.gold,
        GBP: COLORS.red,
        JPY: COLORS.green,
        AUD: COLORS.amber,
        NZD: COLORS.teal,
        CAD: COLORS.purple,
        CHF: COLORS.text
    };

    const PLOTLY_BASE_LAYOUT = {
        paper_bgcolor: COLORS.bg,
        plot_bgcolor:  COLORS.bg,
        font: {
            family: "'JetBrains Mono', monospace",
            size: 11,
            color: COLORS.text
        },
        margin: { l: 60, r: 30, t: 30, b: 50 },
        xaxis: {
            gridcolor: COLORS.grid,
            zerolinecolor: COLORS.grid,
            linecolor: COLORS.grid,
            tickfont: { color: COLORS.textDim, size: 10 },
            showspikes: false
        },
        yaxis: {
            gridcolor: COLORS.grid,
            zerolinecolor: COLORS.grid,
            linecolor: COLORS.grid,
            tickfont: { color: COLORS.textDim, size: 10 },
            showspikes: false
        },
        legend: {
            font: { color: COLORS.textDim, size: 10 },
            bgcolor: 'rgba(15, 19, 26, 0.8)',
            bordercolor: COLORS.grid,
            borderwidth: 1,
            orientation: 'h',
            x: 0,
            y: -0.15
        },
        hoverlabel: {
            bgcolor: COLORS.bgPanel,
            bordercolor: COLORS.gold,
            font: { family: "'JetBrains Mono', monospace", size: 11, color: COLORS.text }
        }
    };

    const PLOTLY_CONFIG = {
        responsive: true,
        displaylogo: false,
        modeBarButtonsToRemove: ['lasso2d', 'select2d', 'autoScale2d', 'toggleSpikelines'],
        displayModeBar: 'hover'
    };

    function setLoading(containerId) {
        const el = document.getElementById(containerId);
        if (el) {
            el.innerHTML = '<div class="chart-loading">Loading data</div>';
        }
    }

    function setError(containerId, msg) {
        const el = document.getElementById(containerId);
        if (el) {
            el.innerHTML = `<div class="chart-error">⚠ ${msg}</div>`;
        }
    }

    function formatDate(d) {
        if (!d) return '—';
        const yr = d.getUTCFullYear();
        const mo = String(d.getUTCMonth() + 1).padStart(2, '0');
        const da = String(d.getUTCDate()).padStart(2, '0');
        return `${yr}-${mo}-${da}`;
    }

    function filterLastNDays(series, days) {
        if (!series || series.dates.length === 0) return series;
        const lastDate = series.dates[series.dates.length - 1];
        const cutoff = new Date(lastDate.getTime() - days * 24 * 60 * 60 * 1000);
        const dates = [];
        const values = [];
        for (let i = 0; i < series.dates.length; i++) {
            if (series.dates[i] >= cutoff) {
                dates.push(series.dates[i]);
                values.push(series.values[i]);
            }
        }
        return { dates, values };
    }

    function renderRFRChart(rfrData) {
        const containerId = 'chart-rfr';
        const traces = [];

        const order = ['estr', 'sonia', 'saron', 'aonia', 'tona', 'corra', 'ocr', 'sofr'];

        for (const key of order) {
            const feed = rfrData[key];
            if (!feed || !feed.series || feed.series.dates.length === 0) continue;

            const filtered = filterLastNDays(feed.series, 90);

            traces.push({
                x: filtered.dates,
                y: filtered.values,
                type: 'scatter',
                mode: 'lines',
                name: `${feed.label} (${feed.ccy})`,
                line: {
                    color: CCY_COLOR[feed.ccy] || COLORS.text,
                    width: 1.8
                },
                hovertemplate: `<b>${feed.label}</b><br>%{x|%Y-%m-%d}<br>%{y:.3f}%<extra></extra>`
            });
        }

        if (traces.length === 0) {
            setError(containerId, 'No RFR data available');
            return;
        }

        const layout = {
            ...PLOTLY_BASE_LAYOUT,
            yaxis: {
                ...PLOTLY_BASE_LAYOUT.yaxis,
                title: { text: 'Rate (%)', font: { color: COLORS.textDim, size: 11 } },
                tickformat: '.2f'
            },
            xaxis: {
                ...PLOTLY_BASE_LAYOUT.xaxis,
                type: 'date',
                tickformat: '%b %d'
            },
            showlegend: true
        };

        Plotly.newPlot(containerId, traces, layout, PLOTLY_CONFIG);
    }

    function renderCurvesGrid(billsData) {
        const grid = document.getElementById('curves-grid');
        if (!grid) return;

        const order = ['us', 'eur', 'gbp', 'jpy', 'aud', 'nzd', 'cad', 'chf'];

        grid.innerHTML = '';

        for (const key of order) {
            const feed = billsData[key];
            const ccyLower = feed?.ccy?.toLowerCase() || key;

            const cellId = `curve-${key}`;

            const cell = document.createElement('div');
            cell.className = 'curve-cell';
            cell.innerHTML = `
                <div class="curve-header">
                    <span class="curve-ccy ${ccyLower}">${feed?.ccy || key.toUpperCase()}</span>
                    <span class="curve-slope" id="slope-${key}">—</span>
                </div>
                <div class="curve-chart" id="${cellId}"></div>
            `;
            grid.appendChild(cell);

            renderSingleCurve(cellId, feed, key);
        }
    }

    function renderSingleCurve(containerId, feed, key) {
        if (!feed || !feed.curve || feed.curve.dates.length === 0) {
            setError(containerId, 'No curve data');
            return;
        }

        const curve = feed.curve;
        const tenors = curve.tenors;
        const dates = curve.dates;

        const lastIdx = dates.length - 1;
        const targetDate = new Date(dates[lastIdx].getTime() - 30 * 24 * 60 * 60 * 1000);
        let prevIdx = lastIdx;
        for (let i = lastIdx; i >= 0; i--) {
            if (dates[i] <= targetDate) {
                prevIdx = i;
                break;
            }
        }

        const sortedTenors = [...tenors].sort((a, b) => {
            return tenorToMonths(a) - tenorToMonths(b);
        });

        const xLabels = sortedTenors.map((t) => t.toUpperCase());
        const yLatest = sortedTenors.map((t) => curve.data[t]?.[lastIdx] ?? null);
        const yPrev = sortedTenors.map((t) => curve.data[t]?.[prevIdx] ?? null);

        const ccyColor = CCY_COLOR[feed.ccy] || COLORS.text;

        const traces = [
            {
                x: xLabels,
                y: yPrev,
                type: 'scatter',
                mode: 'lines+markers',
                name: '30D ago',
                line: { color: COLORS.textMuted, width: 1, dash: 'dot' },
                marker: { size: 4, color: COLORS.textMuted },
                hovertemplate: `30D ago<br>%{x}: %{y:.2f}%<extra></extra>`
            },
            {
                x: xLabels,
                y: yLatest,
                type: 'scatter',
                mode: 'lines+markers',
                name: 'Latest',
                line: { color: ccyColor, width: 2 },
                marker: { size: 6, color: ccyColor },
                hovertemplate: `Latest<br>%{x}: %{y:.2f}%<extra></extra>`
            }
        ];

        const layout = {
            ...PLOTLY_BASE_LAYOUT,
            margin: { l: 40, r: 15, t: 10, b: 35 },
            xaxis: {
                ...PLOTLY_BASE_LAYOUT.xaxis,
                tickfont: { color: COLORS.textDim, size: 9 }
            },
            yaxis: {
                ...PLOTLY_BASE_LAYOUT.yaxis,
                tickfont: { color: COLORS.textDim, size: 9 },
                tickformat: '.2f'
            },
            showlegend: false,
            height: 180
        };

        Plotly.newPlot(containerId, traces, layout, { ...PLOTLY_CONFIG, displayModeBar: false });

        const validLatest = yLatest.filter((v) => v != null);
        if (validLatest.length >= 2) {
            const slope = (validLatest[validLatest.length - 1] - validLatest[0]) * 100;
            const slopeEl = document.getElementById(`slope-${key}`);
            if (slopeEl) {
                const sign = slope >= 0 ? '+' : '';
                slopeEl.textContent = `slope ${sign}${slope.toFixed(0)}bps`;
                slopeEl.style.color = slope >= 0 ? COLORS.green : COLORS.red;
            }
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

    function renderXCCYChart(rfrData, billsData) {
        const containerId = 'chart-xccy';

        const usdRFR = rfrData.sofr;
        const usdBills = billsData.us;

        if (!usdRFR || !usdRFR.series || !usdBills || !usdBills.curve) {
            setError(containerId, 'USD anchor data unavailable');
            return;
        }

        const pairs = [
            { rfrKey: 'estr',  billsKey: 'eur', label: 'EUR' },
            { rfrKey: 'sonia', billsKey: 'gbp', label: 'GBP' },
            { rfrKey: 'saron', billsKey: 'chf', label: 'CHF' },
            { rfrKey: 'aonia', billsKey: 'aud', label: 'AUD' },
            { rfrKey: 'tona',  billsKey: 'jpy', label: 'JPY' },
            { rfrKey: 'corra', billsKey: 'cad', label: 'CAD' },
            { rfrKey: 'ocr',   billsKey: 'nzd', label: 'NZD' }
        ];

        const results = pairs.map((p) => {
            const ccyRFR = rfrData[p.rfrKey];
            const ccyBills = billsData[p.billsKey];

            const basisData = computeXCCYBasis(usdRFR, usdBills, ccyRFR, ccyBills);

            return {
                label: p.label,
                ...basisData
            };
        }).filter((r) => r.zScore !== null);

        if (results.length === 0) {
            setError(containerId, 'Insufficient data for XCCY basis computation');
            return;
        }

        results.sort((a, b) => Math.abs(b.zScore) - Math.abs(a.zScore));

        const labels = results.map((r) => r.label);
        const zValues = results.map((r) => r.zScore);
        const colors = zValues.map((z) => {
            if (Math.abs(z) > 2.0) return COLORS.red;
            if (Math.abs(z) > 1.0) return COLORS.amber;
            return COLORS.green;
        });

        const traces = [{
            x: labels,
            y: zValues,
            type: 'bar',
            marker: { color: colors, line: { color: COLORS.grid, width: 1 } },
            text: results.map((r) => `${r.basisBps >= 0 ? '+' : ''}${r.basisBps.toFixed(0)}bps`),
            textposition: 'outside',
            textfont: { color: COLORS.textDim, size: 10 },
            hovertemplate: `<b>%{x}</b><br>Z-score: %{y:.2f}σ<br>Basis: %{text}<extra></extra>`
        }];

        const layout = {
            ...PLOTLY_BASE_LAYOUT,
            margin: { l: 60, r: 30, t: 30, b: 50 },
            xaxis: {
                ...PLOTLY_BASE_LAYOUT.xaxis,
                title: ''
            },
            yaxis: {
                ...PLOTLY_BASE_LAYOUT.yaxis,
                title: { text: 'Z-score (252D)', font: { color: COLORS.textDim, size: 11 } },
                tickformat: '.1f',
                zeroline: true,
                zerolinecolor: COLORS.textMuted,
                zerolinewidth: 1
            },
            shapes: [
                {
                    type: 'line', xref: 'paper', x0: 0, x1: 1, y0: 2, y1: 2,
                    line: { color: COLORS.red, width: 1, dash: 'dash' }
                },
                {
                    type: 'line', xref: 'paper', x0: 0, x1: 1, y0: -2, y1: -2,
                    line: { color: COLORS.red, width: 1, dash: 'dash' }
                }
            ],
            showlegend: false
        };

        Plotly.newPlot(containerId, traces, layout, PLOTLY_CONFIG);
    }

    function computeXCCYBasis(usdRFR, usdBills, ccyRFR, ccyBills) {
        if (!ccyRFR || !ccyRFR.series || !ccyBills || !ccyBills.curve) {
            return { zScore: null, basisBps: null };
        }

        const usdTenor = pickShortTenor(usdBills.curve.tenors);
        const ccyTenor = pickShortTenor(ccyBills.curve.tenors);

        if (!usdTenor || !ccyTenor) return { zScore: null, basisBps: null };

        const usdRFRMap = seriesToMap(usdRFR.series);
        const ccyRFRMap = seriesToMap(ccyRFR.series);
        const usdBillsMap = curveToMap(usdBills.curve, usdTenor);
        const ccyBillsMap = curveToMap(ccyBills.curve, ccyTenor);

        const commonDates = [...usdRFRMap.keys()]
            .filter((d) => ccyRFRMap.has(d) && usdBillsMap.has(d) && ccyBillsMap.has(d))
            .sort();

        if (commonDates.length < 30) return { zScore: null, basisBps: null };

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

        return {
            zScore,
            basisBps: latest,
            obs: basis.length
        };
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
        for (let i = 0; i < series.dates.length; i++) {
            m.set(formatDate(series.dates[i]), series.values[i]);
        }
        return m;
    }

    function curveToMap(curve, tenor) {
        const m = new Map();
        const values = curve.data[tenor];
        if (!values) return m;
        for (let i = 0; i < curve.dates.length; i++) {
            if (values[i] != null) {
                m.set(formatDate(curve.dates[i]), values[i]);
            }
        }
        return m;
    }

    function renderQualityGrid(rfrData, billsData) {
        const grid = document.getElementById('quality-grid');
        if (!grid) return;

        grid.innerHTML = '';

        const allFeeds = [];

        Object.entries(rfrData).forEach(([key, feed]) => {
            allFeeds.push({
                key: `rfr-${key}`,
                label: feed.label,
                ccy: feed.ccy,
                source: feed.source,
                type: 'RFR',
                series: feed.series
            });
        });

        Object.entries(billsData).forEach(([key, feed]) => {
            allFeeds.push({
                key: `bills-${key}`,
                label: feed.label,
                ccy: feed.ccy,
                source: feed.source,
                type: 'Bills',
                series: feed.curve ? { dates: feed.curve.dates, values: [] } : null
            });
        });

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
                    <div class="quality-meta-row">
                        <span class="quality-meta-label">CCY · Type</span>
                        <span class="quality-meta-value">${f.ccy} · ${f.type}</span>
                    </div>
                    <div class="quality-meta-row">
                        <span class="quality-meta-label">Source</span>
                        <span class="quality-meta-value">${f.source}</span>
                    </div>
                    <div class="quality-meta-row">
                        <span class="quality-meta-label">Last obs</span>
                        <span class="quality-meta-value">${hasData ? formatDate(lastDate) : '—'}</span>
                    </div>
                    <div class="quality-meta-row">
                        <span class="quality-meta-label">Lag · Records</span>
                        <span class="quality-meta-value">${days != null ? days + 'D' : '—'} · ${obs}</span>
                    </div>
                </div>
            `;

            grid.appendChild(cell);
        }
    }

    function updateHeaderStatus(rfrData, billsData) {
        let latestDate = null;
        let freshCount = 0;
        let staleCount = 0;
        let failCount = 0;
        let totalCount = 0;

        const allFeeds = [
            ...Object.values(rfrData).map((f) => ({ series: f.series })),
            ...Object.values(billsData).map((f) => ({ series: f.curve ? { dates: f.curve.dates } : null }))
        ];

        for (const f of allFeeds) {
            totalCount++;
            if (!f.series || f.series.dates.length === 0) {
                failCount++;
                continue;
            }
            const ld = f.series.dates[f.series.dates.length - 1];
            if (!latestDate || ld > latestDate) latestDate = ld;

            const status = global.G8DataLoader.staleStatus(ld);
            if (status === 'fresh') freshCount++;
            else if (status === 'stale') staleCount++;
            else failCount++;
        }

        const lastUpdateEl = document.getElementById('last-update');
        if (lastUpdateEl) {
            lastUpdateEl.textContent = latestDate ? formatDate(latestDate) : '—';
        }

        const statusCountEl = document.getElementById('status-count');
        if (statusCountEl) {
            statusCountEl.textContent = `${freshCount}/${totalCount}`;
        }

        const dotsEl = document.getElementById('status-dots');
        if (dotsEl) {
            let dots = '';
            for (let i = 0; i < freshCount; i++) dots += '<span>●</span>';
            for (let i = 0; i < staleCount; i++) dots += '<span class="dot-stale">●</span>';
            for (let i = 0; i < failCount; i++) dots += '<span class="dot-fail">●</span>';
            dotsEl.innerHTML = dots;
        }
    }

    async function init() {
        console.log('[G8 Dashboard] Initializing...');

        setLoading('chart-rfr');
        setLoading('chart-xccy');
        document.getElementById('curves-grid').innerHTML = '<div class="chart-loading">Loading curves</div>';
        document.getElementById('quality-grid').innerHTML = '<div class="chart-loading">Loading quality data</div>';

        try {
            const [rfrData, billsData] = await Promise.all([
                global.G8DataLoader.loadAllRFR(),
                global.G8DataLoader.loadAllBills()
            ]);

            console.log('[G8 Dashboard] Data loaded:', {
                rfr: Object.keys(rfrData).length,
                bills: Object.keys(billsData).length
            });

            updateHeaderStatus(rfrData, billsData);

            renderRFRChart(rfrData);
            renderCurvesGrid(billsData);
            renderXCCYChart(rfrData, billsData);
            renderQualityGrid(rfrData, billsData);

            console.log('[G8 Dashboard] Render complete');
        } catch (err) {
            console.error('[G8 Dashboard] Init failed:', err);
        }
    }

    global.G8Dashboard = {
        init,
        renderRFRChart,
        renderCurvesGrid,
        renderXCCYChart,
        renderQualityGrid,
        COLORS,
        CCY_COLOR
    };

})(window);

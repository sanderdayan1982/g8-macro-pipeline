markdown# XCCY G8 — Cross-Currency Basis Command Center

Institutional-grade cross-currency funding stress dashboard for G8 currencies.
Built with Python scrapers from official central bank APIs, automated via
GitHub Actions, deployed as static dashboard on Netlify.

**Live dashboard:** https://xccy-g8.netlify.app *(coming soon)*

## Overview

This project tracks the cross-currency basis (XCCY) proxy across 7 major
currencies, measuring funding stress in the global USD offshore market.
The XCCY basis is constructed as a synthetic proxy using overnight
risk-free rates (RFRs) and short-end sovereign bills, with a rolling
asset swap spread correction for institutional-grade accuracy.

For EUR specifically, the European Central Bank Statistical Data Warehouse
(SDW) is consulted for cross-currency basis indicators as a cross-check
against the proxy methodology.

## Coverage

Seven currencies, each with overnight RFR from official central bank source:

| Currency | Rate    | Central Bank Source         |
|----------|---------|------------------------------|
| EUR      | €STR    | European Central Bank        |
| GBP      | SONIA   | Bank of England              |
| CHF      | SARON   | Swiss National Bank          |
| AUD      | AONIA   | Reserve Bank of Australia    |
| JPY      | TONA    | Bank of Japan                |
| NZD      | OCR     | Reserve Bank of New Zealand  |
| CAD      | CORRA   | Bank of Canada               |

Plus short-end sovereign bills (3M / 6M / 1Y) from FRED API and official
government statistics offices.

## Architecture
Central Bank APIs (7 RFRs + bills)
↓
GitHub Actions (Python scrapers, daily cron 18:00 UTC)
↓
Data quality validation + asset swap correction (rolling 60-90d)
↓
JSON output (latest + history + meta + config)
↓
Netlify static dashboard (Bloomberg dark aesthetic)
↓
Plotly + D3 + SVG visualizations

## Repository Structure
xccy-g8/
├── docs/                       # Netlify deploy root
│   ├── index.html             # Main dashboard
│   ├── css/                   # Bloomberg dark styling
│   ├── js/                    # Chart logic (Plotly + D3)
│   └── data/                  # JSON output from scrapers
│       ├── latest.json        # Today's snapshot (~5KB, instant load)
│       ├── history/           # Per-currency history (lazy-loaded)
│       │   ├── eur_history.json
│       │   ├── gbp_history.json
│       │   └── ... (one per currency)
│       ├── meta.json          # Timestamps, source status, freshness
│       └── config.json        # Tunable thresholds and parameters
├── scripts/                    # Python scrapers
│   ├── fetch_estr.py          # ECB €STR
│   ├── fetch_sonia.py         # BoE SONIA
│   ├── fetch_saron.py         # SNB SARON
│   ├── fetch_aonia.py         # RBA AONIA
│   ├── fetch_tona.py          # BoJ TONA
│   ├── fetch_ocr.py           # RBNZ OCR
│   ├── fetch_corra.py         # BoC CORRA
│   ├── fetch_bills.py         # FRED + sovereign bills
│   ├── compute_basis.py       # Engine: XCCY proxy + asset swap correction
│   └── validate_quality.py    # Data quality checks before commit
├── .github/workflows/
│   └── daily_update.yml       # Cron 18:00 UTC + manual dispatch
├── data/                       # Raw CSV cache (intermediate, ignored from Netlify deploy)
└── README.md

## Methodology

### XCCY Basis Proxy Formula

For each currency `X` vs USD:
XCCY_basis_proxy(X) = (RFR_X - bill_short_X) - (SOFR - bill_short_US)
+ asset_swap_correction_rolling_75d

Where:
- `RFR_X` = overnight risk-free rate for currency X
- `bill_short_X` = 3M sovereign bill yield for currency X
- `SOFR` = US Secured Overnight Financing Rate
- `bill_short_US` = US 3M T-bill yield
- `asset_swap_correction` = rolling adjustment for swap-vs-bill spread

### Calibration

- Rolling windows: 60d / 90d / 252d (1Y) Z-scores
- Stress threshold: |Z-score| > 2.0
- Stale threshold: 3 bars without data update (holiday-robust)
- All values in basis points (bps)

### Cross-validation

EUR proxy is cross-checked against ECB SDW basis indicators when available.
Correlation between proxy and SDW reference is tracked over time as a
quality metric; expected correlation: 0.85-0.92.

## Update Cadence

- **Automated:** Daily at 18:00 UTC via GitHub Actions cron
- **Manual:** Workflow can be triggered on-demand
- **Frontend:** Auto-deploys from `docs/` on every push to main

## Data Quality

Every cron run includes validation checks before commit:

- Each basis must be within ±300 bps (sanity bound)
- Failed sources flagged as `stale: true` in `meta.json`
- If >2 sources fail simultaneously → no commit, manual review required
- Dashboard displays freshness banner per currency

## License & Use

Data sourced from public central bank APIs and official government
statistics. This repository is published for personal macro research.
Original data ownership and licensing remain with respective central
banks and statistical authorities. Users are responsible for compliance
with each source's terms of use.

## Status

- [x] Pine Seeds approach abandoned (TradingView suspended new repos)
- [x] Architecture redesigned: Netlify static dashboard with Plotly/D3
- [x] Repository scaffolded with `data/`, `scripts/`, `.github/workflows/`
- [ ] 7 RFR scrapers implemented
- [ ] Short-end bills scrapers implemented
- [ ] XCCY basis engine + asset swap correction
- [ ] Data quality validation layer
- [ ] GitHub Actions daily cron
- [ ] JSON modular structure (latest + history)
- [ ] Bloomberg dark dashboard frontend
- [ ] Plotly + D3 visualizations
- [ ] Netlify deploy live at xccy-g8.netlify.app

---

**Maintainer:** Sander
**Last structural update:** May 2026
**Repository status:** Active development (Phase 2 — scrapers)

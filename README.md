# XCCY G8 — Seeds Data Repository

Daily-updated risk-free rate (RFR) data and sovereign reference yields
for institutional cross-currency basis analysis on TradingView via Pine Seeds.

## Overview

This repository publishes overnight risk-free rates (RFRs) sourced directly
from official central bank APIs, formatted for ingestion by TradingView's
Pine Seeds platform. The data feeds an institutional-grade XCCY basis
analysis indicator (XCCY G8 v3.0+) that prices cross-currency funding
stress across G7 currencies.

## Data Coverage

Six overnight risk-free rates, one CSV per currency:

| Currency | Rate   | Source                  | Frequency |
|----------|--------|-------------------------|-----------|
| GBP      | SONIA  | Bank of England         | Daily     |
| CHF      | SARON  | Swiss National Bank     | Daily     |
| AUD      | AONIA  | Reserve Bank of Australia | Daily   |
| JPY      | TONA   | Bank of Japan           | Daily     |
| NZD      | OCR    | Reserve Bank of New Zealand | Daily |
| CAD      | CORRA  | Bank of Canada          | Daily     |

Note: USD (SOFR) and EUR (€STR) are not included here because TradingView
already provides them natively via ECONOMICS:USINTR / ECONOMICS:EUINTR
and FRED tickers. This repository focuses on the six RFRs that lack
reliable native TradingView coverage.

## Repository Structure

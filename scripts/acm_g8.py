#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
acm_g8.py v2 — ACM Term Premium decomposition, multi-currency
===============================================================
Adrian-Crump-Moench (2013) three-step OLS estimator.

ARCHITECTURE (two stages — validated by Monte Carlo, see VALIDATION below)
--------------------------------------------------------------------------
Stage 1  ESTIMATE on LONG monthly history (40y+ where available), fetched
         directly from primary sources at run time. A short sample cannot
         identify factor persistence: with 5y of data the recovered TP can
         be ANTI-correlated with the true one. Monte Carlo (this repo,
         test_acm_sim.py): 64 monthly obs -> median corr -0.65;
         500 monthly obs -> median corr +0.93, min 0.90.

Stage 2  APPLY estimated affine loadings to the pipeline's DAILY curve CSVs
         (OHLCV Pine-Seeds format) -> daily Y10_FIT / RNY10 / TP10 series.
         (Same practice as NY Fed: monthly estimation, daily application.)

Coverage v2: USD (FRED, 1985+), EUR (ECB SDW, 2004+ — borderline sample,
flagged WARN). GBP/JPY/CAD/AUD = phase 1b (BoE/MoF/Valet/RBA historical
archives). CHF/NZD = insufficient open long-history curve tenors -> proxy
via beta to USD TP (phase 1c) or manual.

Decomposition:  y10_fitted = RNY10 (expected path) + TP10 (term premium)

Outputs (data/):  ACM_G8_<CCY>.csv   columns: DATE,Y10_FIT,RNY10,TP10  (%)

Usage
-----
  python3 scripts/acm_g8.py USD            # estimate + daily apply + write CSV
  python3 scripts/acm_g8.py USD EUR
  python3 scripts/acm_g8.py --validate-us  # USD run + correlation vs NY Fed
                                           # ACM_TP_10Y.csv (PASS/REVIEW gate)
Run --validate-us BEFORE trusting any other currency: it is the empirical
gate for the whole implementation.

CAVEATS (structural, documented)
--------------------------------
* TP LEVEL depends on sample; use levels as relative inputs (Z-scores, deltas)
  — consistent with how the rest of the G8 system consumes data.
* EUR sample starts 2004 (ECB AAA curve): ~260 monthly obs. Expect higher
  estimation noise than USD. Bundesbank splice (1991+) is the phase 1b fix.
* requirements.txt: numpy, pandas (already present). No scipy.
"""

import io
import os
import sys
import time
import urllib.request

import numpy as np
import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
if not os.path.isdir(DATA_DIR):
    DATA_DIR = "data"

GRID_MONTHS = np.arange(1, 121)                        # monthly grid 1..120
RX_MATS = np.array([6, 12, 24, 36, 48, 60, 84, 120])   # excess-return maturities
K = 3                                                  # PCA factors (= NS dimensionality; K=5 requires Svensson curve — phase 1b)
MIN_OBS_MONTHLY = 240                                  # hard floor (20y) for estimation
WARN_OBS_MONTHLY = 420                                 # below this -> WARN (35y)

UA = {"User-Agent": "Mozilla/5.0 (g8-macro-pipeline acm_g8/2.0)"}


# ============================================================== generic fetchers
def _http_get(url, timeout=90, retries=3):
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:                                     # noqa: BLE001
            last = e
            wait = 5 * (2 ** (attempt - 1))
            print(f"    [http] attempt {attempt} failed ({e}) — retry in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"HTTP failed after {retries} attempts: {url} :: {last}")


def fetch_fred(series_id, start="1962-01-01"):
    """FRED fetch. Primary: official API (needs FRED_API_KEY env var — fast,
    reliable for long histories). Fallback: fredgraph.csv (no key, but times
    out on multi-decade requests from GitHub Actions runners)."""
    api_key = os.environ.get("FRED_API_KEY", "").strip()
    if api_key:
        url = ("https://api.stlouisfed.org/fred/series/observations"
               f"?series_id={series_id}&api_key={api_key}"
               f"&file_type=json&observation_start={start}")
        raw = _http_get(url)
        import json
        obs = json.loads(raw)["observations"]
        df = pd.DataFrame(obs)[["date", "value"]]
        df["date"] = pd.to_datetime(df["date"])
        s = pd.to_numeric(df.set_index("date")["value"], errors="coerce").dropna()
    else:
        url = (f"https://fred.stlouisfed.org/graph/fredgraph.csv"
               f"?id={series_id}&cosd={start}")
        raw = _http_get(url)
        df = pd.read_csv(io.StringIO(raw))
        df.columns = ["DATE", "VAL"]
        df["DATE"] = pd.to_datetime(df["DATE"])
        s = pd.to_numeric(df.set_index("DATE")["VAL"], errors="coerce").dropna()
    print(f"    [FRED {series_id}] {len(s)} obs  "
          f"{s.index[0].date()} → {s.index[-1].date()}")
    return s.sort_index()


def fetch_ecb_yc(tenor_code, start="2004-09-06"):
    """ECB SDW euro-area AAA zero-coupon spot yield (same source as fetch_eur_bills)."""
    key = f"B.U2.EUR.4F.G_N_A.SV_C_YM.SR_{tenor_code}"
    url = (f"https://data-api.ecb.europa.eu/service/data/YC/{key}"
           f"?startPeriod={start}&format=csvdata")
    raw = _http_get(url)
    df = pd.read_csv(io.StringIO(raw))
    df = df[["TIME_PERIOD", "OBS_VALUE"]]
    df["TIME_PERIOD"] = pd.to_datetime(df["TIME_PERIOD"])
    s = pd.to_numeric(df.set_index("TIME_PERIOD")["OBS_VALUE"], errors="coerce").dropna()
    print(f"    [ECB SR_{tenor_code}] {len(s)} obs  {s.index[0].date()} → {s.index[-1].date()}")
    return s.sort_index()


# =================================================== per-currency configuration
# Long-history sources for ESTIMATION (tenor years -> callable)
HIST_SOURCES = {
    "USD": {  # FRED constant-maturity Treasuries; common sample from 1985+
        0.25: lambda: fetch_fred("DGS3MO", "1982-01-04"),
        1:    lambda: fetch_fred("DGS1"),
        2:    lambda: fetch_fred("DGS2", "1976-06-01"),
        3:    lambda: fetch_fred("DGS3"),
        5:    lambda: fetch_fred("DGS5"),
        7:    lambda: fetch_fred("DGS7", "1969-07-01"),
        10:   lambda: fetch_fred("DGS10"),
    },
    "EUR": {  # ECB AAA curve, 2004-09+ (WARN: borderline sample length)
        0.25: lambda: fetch_ecb_yc("3M"),
        0.5:  lambda: fetch_ecb_yc("6M"),
        1:    lambda: fetch_ecb_yc("1Y"),
        2:    lambda: fetch_ecb_yc("2Y"),
        3:    lambda: fetch_ecb_yc("3Y"),
        5:    lambda: fetch_ecb_yc("5Y"),
        7:    lambda: fetch_ecb_yc("7Y"),
        10:   lambda: fetch_ecb_yc("10Y"),
    },
    # Phase 1b: GBP (BoE zero-coupon archive 1979+), JPY (MoF 1974+),
    #           CAD (BoC Valet benchmarks), AUD (RBA F2 1969+)
    # Phase 1c: CHF/NZD via beta-to-USD-TP proxy (insufficient open tenors)
}

# Daily repo CSVs for APPLICATION (tenor years -> filename)
DAILY_FILES = {
    "USD": {0.25: "US_BILL_3M.csv", 0.5: "US_BILL_6M.csv",
            1: "US_BILL_1Y.csv", 2: "US_BILL_2Y.csv"},
    "EUR": {0.25: "EUR_BILL_3M.csv", 0.5: "EUR_BILL_6M.csv", 1: "EUR_BILL_1Y.csv",
            2: "EUR_BILL_2Y.csv", 5: "EUR_BILL_5Y.csv", 10: "EUR_BILL_10Y.csv"},
}
# NOTE — USD daily: repo holds only <=2Y; the long end for daily application is
# fetched from FRED (last 2 years of DGS5/DGS10) inside run_currency().
DAILY_FRED_EXTRA = {
    "USD": {5: "DGS5", 10: "DGS10"},
}


# ============================================================== Nelson-Siegel
def ns_basis(taus, lam):
    taus = np.asarray(taus, dtype=float)
    x = taus / lam
    f1 = (1 - np.exp(-x)) / x
    f2 = f1 - np.exp(-x)
    return np.column_stack([np.ones_like(taus), f1, f2])


def ns_fit_panel(panel, grid_years):
    """Per-day NS fit (pooled lambda via grid search, closed-form OLS betas).
    panel: DataFrame T x tenors(years), percent. Returns (T x grid DataFrame, lambda)."""
    taus_obs = panel.columns.values.astype(float)
    Y = panel.values
    best = (None, np.inf)
    for lam in np.linspace(0.3, 5.0, 48):
        X = ns_basis(taus_obs, lam)
        beta, *_ = np.linalg.lstsq(X, Y.T, rcond=None)
        ssr = np.sum((Y.T - X @ beta) ** 2)
        if ssr < best[1]:
            best = (lam, ssr)
    lam = best[0]
    X = ns_basis(taus_obs, lam)
    beta, *_ = np.linalg.lstsq(X, Y.T, rcond=None)
    Xg = ns_basis(np.asarray(grid_years, dtype=float), lam)
    fitted = (Xg @ beta).T
    return pd.DataFrame(fitted, index=panel.index, columns=grid_years), lam


# ===================================================================== ACM core
def acm_estimate(y_monthly_dec, k=K):
    """ACM three-step on month-end zero yields (decimal annual) on full grid."""
    mats = y_monthly_dec.columns.values.astype(int)
    Y = y_monthly_dec.values
    T = Y.shape[0]

    mu_y = Y.mean(axis=0)
    Yc = Y - mu_y
    U, S, Vt = np.linalg.svd(Yc, full_matrices=False)
    F = U[:, :k] * S[:k]

    r = Y[:, 0] / 12.0                                   # 1-month rate, per month
    Z = np.column_stack([np.ones(T), F])
    d, *_ = np.linalg.lstsq(Z, r, rcond=None)
    delta0, delta1 = d[0], d[1:]

    X0, X1 = F[:-1], F[1:]
    Zv = np.column_stack([np.ones(T - 1), X0])
    Phi_full, *_ = np.linalg.lstsq(Zv, X1, rcond=None)
    mu, Phi = Phi_full[0], Phi_full[1:].T
    # stationarity guard: clip eigenvalues of Phi to |eig| <= 0.995 — an explosive
    # estimated VAR makes the 120-month risk-neutral recursion diverge
    eigval, eigvec = np.linalg.eig(Phi)
    if np.max(np.abs(eigval)) > 0.995:
        scale = np.where(np.abs(eigval) > 0.995, 0.995 / np.abs(eigval), 1.0)
        Phi = np.real(eigvec @ np.diag(eigval * scale) @ np.linalg.inv(eigvec))
        print(f"    [ACM] Phi eigenvalue clip applied "
              f"(max |eig| was {np.max(np.abs(eigval)):.4f})")
    V = X1 - Zv @ Phi_full
    Sigma = (V.T @ V) / (T - 1)

    P = -(mats / 12.0) * Y
    idx = {int(n): j for j, n in enumerate(mats)}
    j_n = [idx[int(n)] for n in RX_MATS]
    j_nm = [idx[int(n) - 1] for n in RX_MATS]
    RX = P[1:, j_nm] - P[:-1, j_n] - r[:-1, None]

    Zr = np.column_stack([np.ones(T - 1), V, X0])
    coef, *_ = np.linalg.lstsq(Zr, RX, rcond=None)
    a = coef[0]
    beta = coef[1:1 + k]
    c = coef[1 + k:]
    E = RX - Zr @ coef
    sigma2 = np.mean(E ** 2)

    N = RX.shape[1]
    BstarSigma = np.array([beta[:, j] @ Sigma @ beta[:, j] for j in range(N)])
    bbT_inv = np.linalg.pinv(beta @ beta.T, rcond=1e-10)
    lam0 = bbT_inv @ beta @ (a + 0.5 * (BstarSigma + sigma2))
    lam1 = bbT_inv @ beta @ c.T

    return dict(mu=mu, Phi=Phi, Sigma=Sigma, sigma2=sigma2,
                delta0=delta0, delta1=delta1, lam0=lam0, lam1=lam1,
                mats=mats, k=k, pca_V=Vt[:k], pca_mu=mu_y)


def affine_yields(params, X, n_months, risk_neutral=False):
    """Affine recursion -> yield (decimal annual) at n_months for factor paths X (T x k)."""
    mu, Phi, Sigma = params["mu"], params["Phi"], params["Sigma"]
    d0, d1, s2 = params["delta0"], params["delta1"], params["sigma2"]
    lam0 = np.zeros_like(params["lam0"]) if risk_neutral else params["lam0"]
    lam1 = np.zeros_like(params["lam1"]) if risk_neutral else params["lam1"]
    k = params["k"]
    A = np.zeros(n_months + 1)
    B = np.zeros((n_months + 1, k))
    for n in range(1, n_months + 1):
        Bp = B[n - 1]
        A[n] = (A[n - 1] + Bp @ (mu - lam0)
                + 0.5 * (Bp @ Sigma @ Bp + s2) - d0)
        B[n] = Bp @ (Phi - lam1) - d1
    return -(A[n_months] + X @ B[n_months]) / (n_months / 12.0)


def factors_from_yields(params, y_grid_dec):
    """Project a yield panel on the LONG-SAMPLE PCA loadings (consistent demeaning)."""
    Yc = y_grid_dec.values - params["pca_mu"]
    return Yc @ params["pca_V"].T


# ==================================================================== IO helpers
def load_ohlcv(path):
    df = pd.read_csv(path)
    df["DATE"] = pd.to_datetime(df["DATE"].astype(str), format="%Y%m%d")
    return df.set_index("DATE")["CLOSE"].astype(float).sort_index()


def build_hist_panel(ccy):
    print(f"  [stage 1] fetching long history for {ccy}…")
    cols = {t: fn() for t, fn in sorted(HIST_SOURCES[ccy].items())}
    panel = pd.DataFrame(cols).sort_index().ffill(limit=5).dropna()
    print(f"  history panel: tenors {list(panel.columns)}  "
          f"{panel.index[0].date()} → {panel.index[-1].date()}  ({len(panel)} daily obs)")
    return panel


def build_daily_panel(ccy):
    cols = {}
    for tenor, fname in sorted(DAILY_FILES.get(ccy, {}).items()):
        path = os.path.join(DATA_DIR, fname)
        if os.path.isfile(path):
            cols[tenor] = load_ohlcv(path)
        else:
            print(f"  [daily] missing {fname} — skipped")
    for tenor, sid in DAILY_FRED_EXTRA.get(ccy, {}).items():
        start = (pd.Timestamp.today() - pd.DateOffset(years=5)).strftime("%Y-%m-%d")
        cols[tenor] = fetch_fred(sid, start)
    panel = pd.DataFrame(cols).sort_index().ffill(limit=5).dropna()
    print(f"  daily panel  : tenors {list(panel.columns)}  "
          f"{panel.index[0].date()} → {panel.index[-1].date()}  ({len(panel)} obs)")
    return panel


# ====================================================================== pipeline
def run_currency(ccy):
    print(f"\n[{ccy}] ============================================================")
    # --- stage 1: estimate on long monthly history
    hist = build_hist_panel(ccy)
    grid_years = GRID_MONTHS / 12.0
    z_hist, lam_h = ns_fit_panel(hist, grid_years)
    z_hist_dec = z_hist / 100.0
    z_hist_dec.columns = GRID_MONTHS
    z_m = z_hist_dec.resample("ME").last().dropna()
    n_m = len(z_m)
    if n_m < MIN_OBS_MONTHLY:
        raise RuntimeError(f"{ccy}: {n_m} monthly obs < {MIN_OBS_MONTHLY} — refuse to "
                           f"estimate (small-sample TP is unreliable; see header)")
    flag = "OK" if n_m >= WARN_OBS_MONTHLY else "WARN (borderline sample)"
    print(f"  estimation sample: {n_m} month-end obs [{flag}]  NS lambda={lam_h:.2f}")
    params = acm_estimate(z_m)

    # in-sample fit sanity
    Fm = factors_from_yields(params, z_m)
    y10_in = affine_yields(params, Fm, 120) * 100
    obs10_in = z_m[120].values * 100
    fit_rmse = float(np.sqrt(np.mean((y10_in - obs10_in) ** 2)))
    print(f"  in-sample 10Y fit RMSE: {fit_rmse:.3f}pp "
          f"({'OK' if fit_rmse < 0.30 else 'REVIEW'})")

    # --- stage 2: apply to daily pipeline curve
    print(f"  [stage 2] applying loadings to daily curve…")
    daily = build_daily_panel(ccy)
    z_d, lam_d = ns_fit_panel(daily, grid_years)
    z_d_dec = z_d / 100.0
    z_d_dec.columns = GRID_MONTHS
    Fd = factors_from_yields(params, z_d_dec)
    # out-of-distribution guard: daily factors must live in the estimation
    # factor space, otherwise the decomposition extrapolates
    Fm_lo, Fm_hi = Fm.min(axis=0), Fm.max(axis=0)
    span = Fm_hi - Fm_lo
    ood = np.mean((Fd < Fm_lo - 0.25 * span) | (Fd > Fm_hi + 0.25 * span))
    if ood > 0.01:
        print(f"  [WARN] {ood:.1%} of daily factor obs outside estimation range — "
              f"history/daily source mismatch, review before trusting output")
    y10 = affine_yields(params, Fd, 120) * 100
    rny = affine_yields(params, Fd, 120, risk_neutral=True) * 100
    tp = y10 - rny

    out = pd.DataFrame({"DATE": z_d.index.strftime("%Y%m%d"),
                        "Y10_FIT": np.round(y10, 4),
                        "RNY10": np.round(rny, 4),
                        "TP10": np.round(tp, 4)})
    out_path = os.path.join(DATA_DIR, f"ACM_G8_{ccy}.csv")
    out.to_csv(out_path, index=False)

    obs10 = daily[10].iloc[-1] if 10 in daily.columns else float("nan")
    print(f"  latest: Y10_FIT {y10[-1]:.3f}% (obs 10Y {obs10:.3f}%)  "
          f"RNY10 {rny[-1]:.3f}%  TP10 {tp[-1]:.3f}%")
    print(f"  wrote : {out_path} ({len(out)} rows)")
    return out


def validate_us():
    out = run_currency("USD")
    ref = load_ohlcv(os.path.join(DATA_DIR, "ACM_TP_10Y.csv"))
    est = out.copy()
    est["DATE"] = pd.to_datetime(est["DATE"], format="%Y%m%d")
    est = est.set_index("DATE")["TP10"]
    both = pd.concat([est, ref], axis=1, keys=["est", "nyfed"]).dropna()
    corr_lvl = both["est"].corr(both["nyfed"])
    m = both.resample("ME").last().diff().dropna()
    corr_chg = m["est"].corr(m["nyfed"])
    offset = float((both["est"] - both["nyfed"]).mean())
    print(f"\n=== VALIDATION vs NY Fed ACM "
          f"({both.index[0].date()} → {both.index[-1].date()}, {len(both)} obs) ===")
    print(f"  corr (levels)          : {corr_lvl:.4f}")
    print(f"  corr (monthly changes) : {corr_chg:.4f}")
    print(f"  mean level offset      : {offset:+.3f}pp "
          f"(sample/K differences — expected, use Z-scores)")
    ok = corr_lvl >= 0.85 and corr_chg >= 0.60
    print(f"  RESULT: {'PASS — methodology validated, proceed to phase 1b' if ok else 'REVIEW — do not extend to other currencies yet'}")
    return ok


def main():
    args = sys.argv[1:]
    if "--validate-us" in args:
        sys.exit(0 if validate_us() else 1)
    ccys = [a.upper() for a in args if not a.startswith("-")] or list(HIST_SOURCES)
    failed = []
    for ccy in ccys:
        if ccy not in HIST_SOURCES:
            print(f"[{ccy}] not configured in v2 (see phase 1b/1c in header)")
            continue
        try:
            run_currency(ccy)
        except Exception as e:                                     # noqa: BLE001
            print(f"[{ccy}] FAILED: {e}")
            failed.append(ccy)
    print(f"\nSummary: {len(ccys) - len(failed)} OK, {len(failed)} failed of {len(ccys)}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

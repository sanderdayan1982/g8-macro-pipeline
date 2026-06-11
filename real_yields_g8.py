#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
real_yields_g8.py v1 — Market real yields & breakevens (G8)
=============================================================
10Y real yields observed in inflation-linked sovereign markets, plus the
implied breakeven (BE10 = nominal 10Y - real 10Y). Pure market data — no
estimation stage (unlike acm_g8.py).

METHOD NOTE (documented tradeoff)
---------------------------------
The OIS - inflation-swap construction is NOT feasible with open sources:
ILS quotes live behind LSEG/Bloomberg/ICE paywalls in every G8 currency
(the ECB itself sources its ILS charts from LSEG). The freely published
alternative is linker-market real yields (TIPS / index-linked gilts /
indexed Bunds / RRBs / indexed AGS). Known caveat: linker real yields
embed a liquidity premium vs swaps. Mitigation: consume as Z-scores and
deltas (system standard) where a slowly-varying premium cancels out.

Coverage v1
-----------
  USD  FRED DFII10 (10Y TIPS real) + DGS10           CLEAN   cross-check: T10YIE
  GBP  BoE IADB IUDMRZC (10Y real ZC) + IUDMNZC      CLEAN   cross-check: IUDMIZC
  EUR  Bundesbank BBSIS indexed-Bund 10Y real        PROXY   label: DE_CORE
  CAD  BoC Valet RRB long-term real return bond      PROXY   label: LT_TENOR (~30Y)
  AUD  RBA F2 indexed AGS 10Y                        PROXY   label: THIN_MARKET
  JPY / CHF / NZD                                    N/A     no open daily linker/BEI feed

Outputs (data/):  RY_G8_<CCY>.csv  columns: DATE,NOM10,REAL10,BE10  (percent)
The QUALITY label per currency is printed to the run log and embedded in the
CSV header comment line.

Usage
-----
  python3 scripts/real_yields_g8.py                 # all configured
  python3 scripts/real_yields_g8.py USD GBP         # subset

Sanity gates (per currency, printed as VALIDATION block):
  - identity BE10 == NOM10 - REAL10 (exact by construction)
  - range: REAL10 in [-4, +6]%, BE10 in [-2, +6]%
  - staleness: last obs <= 7 calendar days old (WARN otherwise)
  - cross-check vs independent published series where available
    (gate: corr >= 0.95 and mean |diff| <= 0.15pp -> PASS)

requirements.txt: numpy, pandas, openpyxl, xlrd (all already present).
"""

import io
import os
import sys
import json
import time
import urllib.request

import numpy as np
import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
if not os.path.isdir(DATA_DIR):
    DATA_DIR = "data"

UA = {"User-Agent": "Mozilla/5.0 (g8-macro-pipeline real_yields_g8/1.0)"}
START = "2003-01-01"          # TIPS 10Y constant-maturity starts 2003 on FRED


# ============================================================== HTTP / fetchers
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


def fetch_fred(series_id, start=START):
    api_key = os.environ.get("FRED_API_KEY", "").strip()
    if api_key:
        url = ("https://api.stlouisfed.org/fred/series/observations"
               f"?series_id={series_id}&api_key={api_key}"
               f"&file_type=json&observation_start={start}")
        raw = _http_get(url)
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


_BOE_CACHE = {}

def fetch_boe(code):
    """BoE IADB: nominal/real/implied-inflation 10Y zero-coupon + spares."""
    key = "GBP_RY"
    if key not in _BOE_CACHE:
        today = pd.Timestamp.today().strftime("%d/%b/%Y")
        codes = "IUDMNZC,IUDMRZC,IUDMIZC"
        url = ("https://www.bankofengland.co.uk/boeapps/database/"
               "_iadb-fromshowcolumns.asp?csv.x=yes"
               f"&Datefrom=01/Jan/2003&Dateto={today}"
               f"&SeriesCodes={codes}&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N")
        raw = _http_get(url)
        df = pd.read_csv(io.StringIO(raw))
        df.columns = [c.strip() for c in df.columns]
        df[df.columns[0]] = pd.to_datetime(df[df.columns[0]],
                                           format="%d %b %Y", errors="coerce")
        df = df.dropna(subset=[df.columns[0]]).set_index(df.columns[0])
        for c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        _BOE_CACHE[key] = df.sort_index()
        print(f"    [BoE IADB] {list(df.columns)}  {len(df)} rows")
    return _BOE_CACHE[key][code].dropna()


_VALET_CACHE = {}

def fetch_valet_group(group, series_id):
    if group not in _VALET_CACHE:
        url = (f"https://www.bankofcanada.ca/valet/observations/group/{group}/csv"
               f"?start_date={START}")
        raw = _http_get(url)
        pos = raw.find('"OBSERVATIONS"')
        df = pd.read_csv(io.StringIO(raw[pos:].split("\n", 1)[1]))
        df.columns = [c.strip().strip('"') for c in df.columns]
        df["date"] = pd.to_datetime(df["date"])
        _VALET_CACHE[group] = df.set_index("date")
    s = pd.to_numeric(_VALET_CACHE[group][series_id], errors="coerce").dropna()
    print(f"    [Valet {series_id}] {len(s)} obs  "
          f"{s.index[0].date()} → {s.index[-1].date()}")
    return s.sort_index()


_RBA_CACHE = {}

def fetch_rba_xls(urls, series_candidates):
    """RBA table -> first series candidate found ('Series ID' or 'Mnemonic',
    any column, any sheet). On miss, logs the available indexed-bond IDs."""
    key = tuple(urls)
    if key not in _RBA_CACHE:
        content, last = None, None
        for url in urls:
            try:
                req = urllib.request.Request(url, headers=UA)
                with urllib.request.urlopen(req, timeout=90) as r:
                    content = r.read()
                print(f"    [RBA] using {url}")
                break
            except Exception as e:                                 # noqa: BLE001
                last = e
                print(f"    [RBA] {url} failed ({e}) — trying next candidate")
        if content is None:
            raise RuntimeError(f"all RBA candidates failed: {urls} :: {last}")
        sheets = pd.read_excel(io.BytesIO(content), header=None, sheet_name=None)
        df = None
        for sname, raw in sheets.items():
            cells = raw.iloc[:40].astype(str).apply(
                lambda col: col.str.strip().str.lower())
            hit = None
            for r in range(min(40, len(raw))):
                for c in range(raw.shape[1]):
                    if cells.iloc[r, c] in ("series id", "mnemonic"):
                        hit = (r, c)
                        break
                if hit:
                    break
            if hit is None:
                continue
            r, c = hit
            sub = raw.iloc[r + 1:, c:].copy()
            sub.columns = raw.iloc[r, c:].astype(str).str.strip()
            sub = sub.rename(columns={sub.columns[0]: "DATE"})
            sub["DATE"] = pd.to_datetime(sub["DATE"], errors="coerce")
            sub = sub.dropna(subset=["DATE"]).set_index("DATE")
            if len(sub) > 0:
                df = sub
                break
        if df is None:
            raise RuntimeError("no 'Series ID'/'Mnemonic' row found in any sheet")
        _RBA_CACHE[key] = df
    df = _RBA_CACHE[key]
    for sid in series_candidates:
        if sid in df.columns:
            s = pd.to_numeric(df[sid], errors="coerce").dropna()
            print(f"    [RBA {sid}] {len(s)} obs  "
                  f"{s.index[0].date()} → {s.index[-1].date()}")
            return s.sort_index()
    avail = [c for c in df.columns if "GBAGI" in str(c) or "FCMY" in str(c)]
    raise RuntimeError(f"none of {series_candidates} in table; available: {avail}")


def fetch_bbk(key_candidates, label):
    """Bundesbank SDMX REST (BBSIS). Tries exact keys; on total miss, runs a
    wildcard discovery on the uncertain dimension and logs available keys."""
    base = "https://api.statistiken.bundesbank.de/rest/data/BBSIS/"
    for k in key_candidates:
        try:
            raw = _http_get(base + k + f"?format=csv&startPeriod={START}",
                            retries=1)
            df = pd.read_csv(io.StringIO(raw))
            cols = {c.upper(): c for c in df.columns}
            tcol, vcol = cols.get("TIME_PERIOD"), cols.get("OBS_VALUE")
            if tcol is None or vcol is None:
                raise RuntimeError(f"unexpected columns: {list(df.columns)[:8]}")
            df[tcol] = pd.to_datetime(df[tcol], errors="coerce")
            s = pd.to_numeric(df.set_index(tcol)[vcol], errors="coerce").dropna()
            if len(s) > 100:
                print(f"    [BBk {label}] key OK: {k}")
                print(f"    [BBk {label}] {len(s)} obs  "
                      f"{s.index[0].date()} → {s.index[-1].date()}")
                return s.sort_index()
            print(f"    [BBk {label}] key {k}: only {len(s)} obs — next")
        except Exception as e:                                     # noqa: BLE001
            print(f"    [BBk {label}] key {k} failed ({e}) — next candidate")
    # discovery: wildcard the rate-type dimension, log what exists
    try:
        wild = key_candidates[0].split(".")
        wild[2] = ""                                  # wildcard dim 3
        raw = _http_get(base + ".".join(wild) +
                        "?format=csv&startPeriod=2024-01-01&detail=serieskeysonly",
                        retries=1)
        keys = sorted({ln.split(";")[0].split(",")[0] for ln in
                       raw.splitlines()[1:60] if ln.strip()})
        print(f"    [BBk {label}] DISCOVERY — candidate keys on server: {keys[:20]}")
    except Exception as e:                                         # noqa: BLE001
        print(f"    [BBk {label}] discovery also failed: {e}")
    raise RuntimeError(f"{label}: no Bundesbank key candidate worked "
                       f"(see DISCOVERY log above)")


# ======================================================= per-currency builders
BBK_NOM_10Y = ["D.I.ZAR.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A"]   # verified
BBK_REAL_10Y = [   # candidates: rate-type dim varies for indexed-Bund real curve
    "D.I.ZAR.ZR.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A",
    "D.I.ZARR.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A",
    "D.I.ZAR.ZI.EUR.S1311.B.A604R.R10XX.R.A.A._Z._Z.A",
]
RBA_F2_DAILY = ["https://www.rba.gov.au/statistics/tables/xls/f02d.xlsx",
                "https://www.rba.gov.au/statistics/tables/xls/f02d.xls"]


def build_usd():
    nom = fetch_fred("DGS10")
    real = fetch_fred("DFII10")
    xchk = fetch_fred("T10YIE")
    return nom, real, xchk, "CLEAN", "TIPS constant-maturity (FRED)"


def build_gbp():
    nom = fetch_boe("IUDMNZC")
    real = fetch_boe("IUDMRZC")
    xchk = fetch_boe("IUDMIZC")
    return nom, real, xchk, "CLEAN", "Index-linked gilts ZC (BoE; RPI basis)"


def build_eur():
    nom = fetch_bbk(BBK_NOM_10Y, "EUR nominal 10Y")
    real = fetch_bbk(BBK_REAL_10Y, "EUR real 10Y")
    return nom, real, None, "PROXY_DE_CORE", "Indexed Bunds (Bundesbank; DE core, not EA)"


def build_cad():
    nom = fetch_valet_group("bond_yields_benchmark", "BD.CDN.LONG.DQ.YLD")
    real = fetch_valet_group("bond_yields_benchmark", "BD.CDN.RRB.DQ.YLD")
    return nom, real, None, "PROXY_LT_TENOR", "RRB long-term ~30Y, not 10Y (BoC)"


def build_aud():
    nom = fetch_rba_xls(RBA_F2_DAILY, ["FCMYGBAG10D"])
    real = fetch_rba_xls(RBA_F2_DAILY,
                         ["FCMYGBAGI10D", "FCMYGBAGID", "FCMYGBAGI10"])
    return nom, real, None, "PROXY_THIN_MARKET", "Indexed AGS (RBA; thin linker market)"


BUILDERS = {"USD": build_usd, "GBP": build_gbp, "EUR": build_eur,
            "CAD": build_cad, "AUD": build_aud}
NOT_AVAILABLE = {"JPY": "no open daily JGBi BEI feed (MoF publishes PDFs only)",
                 "CHF": "no CHF linker market",
                 "NZD": "RBNZ WAF-blocked; NZ IIB feed not open"}


# ====================================================================== runner
def run_currency(ccy):
    print(f"\n[{ccy}] ============================================================")
    nom, real, xchk, quality, desc = BUILDERS[ccy]()
    df = pd.concat([nom, real], axis=1, keys=["NOM10", "REAL10"]).dropna()
    df["BE10"] = df["NOM10"] - df["REAL10"]

    # --- sanity gates
    issues = []
    if not df["REAL10"].between(-4, 6).all():
        issues.append(f"REAL10 out of [-4,6]: "
                      f"[{df['REAL10'].min():.2f},{df['REAL10'].max():.2f}]")
    if not df["BE10"].between(-2, 6).all():
        issues.append(f"BE10 out of [-2,6]: "
                      f"[{df['BE10'].min():.2f},{df['BE10'].max():.2f}]")
    age = (pd.Timestamp.today() - df.index[-1]).days
    if age > 7:
        issues.append(f"stale: last obs {age}d old")

    # --- independent cross-check (where a second published series exists)
    if xchk is not None:
        both = pd.concat([df["BE10"], xchk], axis=1, keys=["calc", "pub"]).dropna()
        corr = both["calc"].corr(both["pub"])
        mad = float((both["calc"] - both["pub"]).abs().mean())
        ok = corr >= 0.95 and mad <= 0.15
        print(f"  cross-check BE10 calc vs published: corr {corr:.4f}, "
              f"mean|diff| {mad:.3f}pp -> {'PASS' if ok else 'REVIEW'}")
        if not ok:
            issues.append("cross-check REVIEW")

    out = pd.DataFrame({"DATE": df.index.strftime("%Y%m%d"),
                        "NOM10": np.round(df["NOM10"].values, 4),
                        "REAL10": np.round(df["REAL10"].values, 4),
                        "BE10": np.round(df["BE10"].values, 4)})
    out_path = os.path.join(DATA_DIR, f"RY_G8_{ccy}.csv")
    with open(out_path, "w") as f:
        f.write(f"# QUALITY={quality} | {desc} | generated by real_yields_g8.py\n")
        out.to_csv(f, index=False)

    print(f"  latest : NOM10 {df['NOM10'].iloc[-1]:.3f}%  "
          f"REAL10 {df['REAL10'].iloc[-1]:.3f}%  BE10 {df['BE10'].iloc[-1]:.3f}%")
    print(f"  quality: {quality} — {desc}")
    print(f"  gates  : {'ALL PASS' if not issues else 'ISSUES: ' + '; '.join(issues)}")
    print(f"  wrote  : {out_path} ({len(out)} rows)")
    return not issues


def main():
    args = [a.upper() for a in sys.argv[1:] if not a.startswith("-")]
    ccys = args or list(BUILDERS)
    failed, warned = [], []
    for ccy in ccys:
        if ccy in NOT_AVAILABLE:
            print(f"\n[{ccy}] N/A in v1 — {NOT_AVAILABLE[ccy]}")
            continue
        if ccy not in BUILDERS:
            print(f"\n[{ccy}] not configured")
            continue
        try:
            if not run_currency(ccy):
                warned.append(ccy)
        except Exception as e:                                     # noqa: BLE001
            print(f"[{ccy}] FAILED: {e}")
            failed.append(ccy)
    n = len([c for c in ccys if c in BUILDERS])
    print(f"\nSummary: {n - len(failed)} OK ({len(warned)} with warnings), "
          f"{len(failed)} failed of {n}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

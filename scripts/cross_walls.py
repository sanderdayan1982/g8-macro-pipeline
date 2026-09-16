#!/usr/bin/env python3
# =============================================================================
# cross_walls.py — v1.1 · CROSS WALLS: per-currency options-positioning score
# (native CME convention) -> USD factor, dispersion regime, direction of the
# 15 G8 crosses (no levels). Section 08b of the G8 Macro Pipeline.
#
# DOCTRINE (pre-registered 16-sep-2026, after an 8-AI triangulation):
#   * Everything is computed in NATIVE CME convention (XXX/USD). Display
#     inversion (USD/JPY, USD/CAD, USD/CHF) is a rendering concern only.
#   * Open interest has NO sign: call/put counts and total dOI NEVER vote.
#     They are published as diagnostics only.
#   * Score components (each normalised by the contract's OWN causal ECDF
#     -> [-1, +1]; LOW_HISTORY while n < 126):
#       G   geometry   : OI (call+put) centre of mass in log-moneyness scaled
#                        by ATM implied move, Gaussian kernel exp(-x^2/2)
#       RR  skew       : 25-delta risk reversal (sigma_25C - sigma_25P), vols
#                        inverted IN-HOUSE from settlements (Black-76, SOFR)
#       dG5 movement   : G_t - G_{t-5} on the same front expiry
#     score = median{ Z(G), Z(RR), Z(dG5) }  (mean of two when dG5 is NA)
#   * USD factor = -median(score of CLEAN currencies); dispersion = MAD-based
#     (1.4826 * MAD) in causal percentile; regime DOLLAR_PURE when the
#     dispersion percentile < 20 with >= 4 CLEAN, else CROSS.
#   * Cross A/B (base/quote, operator convention): direction = sign(S_A - S_B)
#     (subtracting the median cancels — it changes no cross), strength = causal
#     percentile of |S_A - S_B| for that pair, leading leg = larger |residual|.
#     Eligible ONLY if both legs are CLEAN and front DTE >= 10. FAIL/NA legs are
#     excluded from score, factor and dispersion (never down-weighted). PROXY
#     legs are shown, never vote.
#   * Analysis chain = first expiry with DTE >= 10 (the front while it has
#     >= 10 days, the NEXT monthly during the front's last 9 days) so the
#     horizon stays ~10-40 days, CVOL-style; Gate 0 is still judged on the
#     §08 front chain. A dying chain's skew/geometry never enters history.
#   * Flags: NO_VOTE (not eligible), PIN (spot within 0.3 sigma*sqrt(T) of the
#     largest wall of either leg), FLOW (|dOI| > 15 % in one session on the
#     analysis chain), NEXT (analysis chain is front+1 because front DTE < 10).
#   * Status RESEARCH until gate_cross_walls.py passes (N >= 378 sessions).
#     Direction and ranking only — NEVER levels for crosses.
#
# INPUT : data/options/canonical/YYYY-MM-DD/{6E,6B,6J,6A,6C,6S}.csv (futures)
#         and {EUU,GBU,JPU,ADU,CAU,CHU}.csv (options)  — collector v1.1.x
#         data/SOFR.csv (DATE,OPEN,HIGH,LOW,CLOSE,VOLUME; CLOSE in %)
# OUTPUT: data/CROSS_WALLS.json          (latest session, regime, matrix, audit)
#         data/cross_walls/canonical.csv (one row per session — the gate log)
# stdlib only (Apple /usr/bin/python3 3.9 compatible).
# =============================================================================
import csv, json, math, os, sys
from bisect import bisect_left
from datetime import date, datetime, timezone
from pathlib import Path

CANONICAL = Path(os.environ.get("G8_OPT_OUT_DIR", "data/options/canonical"))
SOFR_CSV = Path(os.environ.get("G8_SOFR_CSV", "data/SOFR.csv"))
OUT_JSON = Path(os.environ.get("G8_CW_JSON", "data/CROSS_WALLS.json"))
OUT_CSV = Path(os.environ.get("G8_CW_CSV", "data/cross_walls/canonical.csv"))

CCY = {"EUR": ("EUU", "6E"), "GBP": ("GBU", "6B"), "JPY": ("JPU", "6J"),
       "AUD": ("ADU", "6A"), "CAD": ("CAU", "6C"), "CHF": ("CHU", "6S")}
ORDER = ["EUR", "GBP", "JPY", "AUD", "CAD", "CHF"]
INV = {"JPY", "CAD", "CHF"}                     # displayed as USD/XXX
# operator-convention crosses (base first) — 15 with NZD excluded
CROSSES = ["EURGBP", "EURJPY", "EURAUD", "EURCAD", "EURCHF",
           "GBPJPY", "GBPAUD", "GBPCAD", "GBPCHF",
           "AUDJPY", "CADJPY", "CHFJPY", "AUDCAD", "AUDCHF", "CADCHF"]

# Gate 0 (same rule as build_options_summary.py — replicated to stay standalone)
MIN_OI, MIN_STRIKES, BAND_PCT, COVERAGE = 100, 5, 0.03, 0.80
# pre-registered constants
MIN_DTE = 10            # eligibility
LOW_HISTORY = 126       # ECDF resolution warning
PIN_SIGMA = 0.3         # PIN flag: spot within 0.3 sigma*sqrt(T) of largest wall
ATM_FIT_X = 0.75        # [A1] ATM fit window: |ln K/F| <= 0.75 * seed_sigma * sqrt(T)
ATM_FIT_MIN = 4         # [A1] minimum strikes (both sides of F) for the ATM fit
PARITY_BAND = 0.01      # [F1] strikes within +-1 % of F used for the put-call-parity forward
PARITY_MAX_PCT = 0.10   # [F1] |F - F_parity| above this (%) => F_MISMATCH, currency does not vote
QUARTERLY = ("03", "06", "09", "12")
FLOW_PCT = 15.0         # FLOW flag: |dOI front| > 15 % in one session
DG_LAG = 5              # movement component lag (sessions)
DOLLAR_PCT = 20         # dispersion percentile below which regime = DOLLAR_PURE
MIN_CLEAN_REGIME = 4
IV_MIN, IV_MAX = 0.005, 3.0
VERSION = "cross_walls v1.1"


# ----------------------------------------------------------------- utilities
def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def read_rows(p):
    with p.open(newline="") as f:
        return list(csv.DictReader(f))


def underlying_future(futs, opt_expiry):
    """[F1] CME FX options exercise into the next QUARTERLY future (Mar/Jun/Sep/Dec),
    never the monthly serial. futs sorted by expiry, each with 'expiry' and 'settle'."""
    q = [r for r in futs if r["expiry"] >= opt_expiry and r["expiry"][5:7] in QUARTERLY]
    if q:
        return q[0]
    later = [r for r in futs if r["expiry"] >= opt_expiry]
    return later[0] if later else (futs[0] if futs else None)


def norm_cdf(x):
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def median(v):
    s = sorted(v)
    n = len(s)
    if n == 0:
        return None
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def mad_sigma(v):
    m = median(v)
    if m is None:
        return None
    return 1.4826 * median([abs(x - m) for x in v])


def ecdf_z(history, x):
    """Causal empirical CDF mapped to [-1, +1]. history includes x itself."""
    s = sorted(history)
    n = len(s)
    if n < 2 or x is None:
        return None
    lo = bisect_left(s, x)
    hi = lo
    while hi < n and s[hi] == x:
        hi += 1
    rank = 0.5 * (lo + hi)                      # mid-rank (0..n)
    return 2.0 * (rank / n) - 1.0


def pct_rank(history, x):
    """Causal percentile (0-100) of x within history (history includes x)."""
    s = sorted(history)
    n = len(s)
    if n < 2 or x is None:
        return None
    lo = bisect_left(s, x)
    hi = lo
    while hi < n and s[hi] == x:
        hi += 1
    return 100.0 * (0.5 * (lo + hi)) / n


# ----------------------------------------------------------------- SOFR
def load_sofr():
    out = {}
    if not SOFR_CSV.exists():
        return out
    for r in read_rows(SOFR_CSV):
        d = (r.get("DATE") or "").strip()
        v = fnum(r.get("CLOSE"))
        if len(d) == 8 and v is not None:
            out[f"{d[:4]}-{d[4:6]}-{d[6:]}"] = v / 100.0
    return out


def sofr_on(sofr, session):
    keys = sorted(sofr)
    i = bisect_left(keys, session)
    if i < len(keys) and keys[i] == session:
        return sofr[session], session
    if i == 0:
        return (sofr[keys[0]], keys[0]) if keys else (0.0, None)
    return sofr[keys[i - 1]], keys[i - 1]


# ----------------------------------------------------------------- Black-76
def b76_price(F, K, T, r, sigma, right):
    if T <= 0 or sigma <= 0:
        intrinsic = max(F - K, 0.0) if right == "C" else max(K - F, 0.0)
        return math.exp(-r * T) * intrinsic
    v = sigma * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * v * v) / v
    d2 = d1 - v
    df = math.exp(-r * T)
    if right == "C":
        return df * (F * norm_cdf(d1) - K * norm_cdf(d2))
    return df * (K * norm_cdf(-d2) - F * norm_cdf(-d1))


def b76_delta(F, K, T, r, sigma, right):
    v = sigma * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * v * v) / v
    df = math.exp(-r * T)
    return df * norm_cdf(d1) if right == "C" else -df * norm_cdf(-d1)


def implied_vol(price, F, K, T, r, right):
    """Bisection on [IV_MIN, IV_MAX]; None when price has no time value."""
    df = math.exp(-r * T)
    intrinsic = df * (max(F - K, 0.0) if right == "C" else max(K - F, 0.0))
    if price <= intrinsic * 1.0000001:
        return None
    lo, hi = IV_MIN, IV_MAX
    if b76_price(F, K, T, r, hi, right) < price:
        return None
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if b76_price(F, K, T, r, mid, right) < price:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-7:
            break
    return 0.5 * (lo + hi)


def interp(xs, ys, x):
    """Linear interpolation on sorted xs; None outside the range."""
    if not xs or x < xs[0] or x > xs[-1]:
        return None
    i = bisect_left(xs, x)
    if i < len(xs) and xs[i] == x:
        return ys[i]
    x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


# ----------------------------------------------------------------- per session
def load_session(day_dir, session):
    """Returns per-ccy raw chain data for the front option expiry (native)."""
    out = {}
    for ccy, (opt_root, fut_root) in CCY.items():
        fo, ff = day_dir / f"{opt_root}.csv", day_dir / f"{fut_root}.csv"
        if not (fo.exists() and ff.exists()):
            continue
        futs = [r for r in read_rows(ff) if r["type"] == "FUT"
                and r["expiry"] >= session and fnum(r["settle"]) is not None]
        futs.sort(key=lambda r: r["expiry"])
        if not futs:
            continue
        near = fnum(futs[0]["settle"])           # nearest future (audit only)
        opts = [r for r in read_rows(fo) if r["type"] == "OPT"]
        expiries = sorted({r["expiry"] for r in opts if r["expiry"] > session})
        if not expiries:
            continue
        front_raw = expiries[0]
        # analysis expiry: first with DTE >= MIN_DTE (front, else next monthly)
        sd = date.fromisoformat(session)
        ok = [e for e in expiries if (date.fromisoformat(e) - sd).days >= MIN_DTE]
        front = ok[0] if ok else front_raw
        # [F1] underlying = next QUARTERLY future on/after the option expiry
        und = underlying_future(futs, front)
        F = fnum(und["settle"])
        und_raw = underlying_future(futs, front_raw)
        ref = fnum(und_raw["settle"])            # §08 reference (same rule, front chain)
        chain, chain_raw = {}, {}
        for r in opts:
            if r["expiry"] == front_raw:
                k0 = fnum(r["strike"])
                if k0 is not None and k0 > 0:
                    e0 = chain_raw.setdefault(k0, {"c": 0.0, "p": 0.0})
                    e0["c" if r["right"] == "C" else "p"] += fnum(r["oi"]) or 0.0
            if r["expiry"] != front:
                continue
            k = fnum(r["strike"])
            if k is None or k <= 0:
                continue
            e = chain.setdefault(k, {"c": 0.0, "p": 0.0, "sc": None, "sp": None})
            oi = fnum(r["oi"]) or 0.0
            st = fnum(r["settle"])
            if r["right"] == "C":
                e["c"] += oi
                e["sc"] = st
            else:
                e["p"] += oi
                e["sp"] = st
        if not chain:
            continue
        out[ccy] = {"ref": ref, "F": F, "F_sym": und["symbol"], "near": near,
                    "front": front, "front_raw": front_raw,
                    "chain": chain, "chain_raw": chain_raw}
    return out


def gate0_metrics(d):
    """pata/band per session (build_options_summary rule) on the §08 FRONT chain."""
    ref, chain = d["ref"], d["chain_raw"]
    n_call = sum(1 for k, e in chain.items() if k >= ref and e["c"] >= MIN_OI)
    n_put = sum(1 for k, e in chain.items() if k <= ref and e["p"] >= MIN_OI)
    band = {k: e for k, e in chain.items() if ref * (1 - BAND_PCT) <= k <= ref * (1 + BAND_PCT)}
    b_c = sum(1 for e in band.values() if e["c"] >= MIN_OI)
    b_p = sum(1 for e in band.values() if e["p"] >= MIN_OI)
    return {"n_call": n_call, "n_put": n_put,
            "pata": n_call >= MIN_STRIKES and n_put >= MIN_STRIKES,
            "band": b_c >= 2 and b_p >= 2}


def analyse(d, session, r):
    """Geometry, skew and diagnostics for one currency on one session."""
    F, chain, front = d["F"], d["chain"], d["front"]
    dte = (date.fromisoformat(front) - date.fromisoformat(session)).days
    T = max(dte, 1) / 365.0
    # --- [F1] put-call-parity forward: C - P = df * (F - K) on strikes near F
    df = math.exp(-r * T)
    est = sorted(k + (e["sc"] - e["sp"]) / df for k, e in chain.items()
                 if e["sc"] and e["sp"] and abs(math.log(k / F)) <= PARITY_BAND)
    f_parity = est[len(est) // 2] if est else None
    f_parity_pct = 100.0 * (f_parity / F - 1.0) if f_parity else None
    f_mismatch = f_parity_pct is not None and abs(f_parity_pct) > PARITY_MAX_PCT
    # --- implied vols on OTM options only (settle must carry time value)
    settles = sorted({v for e in chain.values() for v in (e["sc"], e["sp"]) if v})
    tick = min((b - a for a, b in zip(settles, settles[1:]) if b - a > 0), default=None)
    min_prem = 3 * tick if tick else 0.0
    vols = []                                  # (K, sigma, right)
    for k, e in sorted(chain.items()):
        right = "C" if k >= F else "P"
        st = e["sc"] if right == "C" else e["sp"]
        if st is None or st < min_prem:
            continue
        iv = implied_vol(st, F, k, T, r, right)
        if iv is not None and IV_MIN < iv < IV_MAX - 1e-6:
            vols.append((k, iv, right))
    atm = None
    atm_src = "NA"
    if vols:
        ks = [v[0] for v in vols]
        ivs = [v[1] for v in vols]
        atm = interp(ks, ivs, F)               # two-point seed (v1.0 rule)
        if atm is None:                        # F outside the invertible range
            j = min(range(len(ks)), key=lambda i: abs(ks[i] - F))
            atm = ivs[j]
        atm_src = "2PT"
        # [A1] v1.0.1 robust ATM: least-squares line sigma(m) over the strikes
        # with |m| <= ATM_FIT_X * seed * sqrt(T) (m = ln K/F), >= ATM_FIT_MIN
        # points and both sides of F represented; ATM = fitted value at m = 0.
        # Kills the one-tick noise of the two-point interpolation.
        half = ATM_FIT_X * atm * math.sqrt(T)
        pts = [(math.log(k / F), iv) for k, iv, _ in vols if abs(math.log(k / F)) <= half]
        if (len(pts) >= ATM_FIT_MIN and any(m < 0 for m, _ in pts)
                and any(m > 0 for m, _ in pts)):
            n = len(pts)
            mx = sum(m for m, _ in pts) / n
            my = sum(v for _, v in pts) / n
            sxx = sum((m - mx) ** 2 for m, _ in pts)
            b = sum((m - mx) * (v - my) for m, v in pts) / sxx if sxx > 0 else 0.0
            fit = my - b * mx
            if IV_MIN < fit < IV_MAX:
                atm, atm_src = fit, f"FIT{n}"
    rr25 = None
    s25c = s25p = None
    if atm and len(vols) >= 8:
        calls = [(b76_delta(F, k, T, r, iv, "C"), iv) for k, iv, rt in vols if rt == "C"]
        puts = [(b76_delta(F, k, T, r, iv, "P"), iv) for k, iv, rt in vols if rt == "P"]
        calls.sort()
        puts.sort()
        if len(calls) >= 2:
            s25c = interp([c[0] for c in calls], [c[1] for c in calls], 0.25)
        if len(puts) >= 2:
            s25p = interp([p[0] for p in puts], [p[1] for p in puts], -0.25)
        if s25c is not None and s25p is not None:
            rr25 = 100.0 * (s25c - s25p)          # vol points
    # --- geometry (OI call+put, kernel on implied move)
    sig = atm if atm else 0.10
    denom = sig * math.sqrt(T)
    num = den = 0.0
    mass_num = oi_tot = oi_up = 0.0
    top_k, top_oi = None, 0.0
    for k, e in chain.items():
        a = e["c"] + e["p"]
        if a <= 0:
            continue
        x = math.log(k / F) / denom
        w = a * math.exp(-0.5 * x * x)
        num += w * x
        den += w
        mass_num += a * (k - F) / F
        oi_tot += a
        if k > F:
            oi_up += a
        if a > top_oi:
            top_oi, top_k = a, k
    G = num / den if den > 0 else None
    mass_pct = 100.0 * mass_num / oi_tot if oi_tot > 0 else None
    conc_pct = 100.0 * top_oi / oi_tot if oi_tot > 0 else None
    oi_up_pct = 100.0 * oi_up / oi_tot if oi_tot > 0 else None
    pin = (top_k is not None and atm is not None and
           abs(math.log(top_k / F)) < PIN_SIGMA * denom)
    g0 = gate0_metrics(d)
    cp_native = f'{g0["n_call"]}/{g0["n_put"]}'
    return {"front": front, "front_raw": d["front_raw"], "next": front != d["front_raw"],
            "dte": dte, "F": F, "F_sym": d["F_sym"], "near": d["near"], "ref": d["ref"], "r": r,
            "f_parity": f_parity, "f_parity_pct": f_parity_pct, "f_mismatch": f_mismatch,
            "atm": atm, "atm_src": atm_src, "s25c": s25c, "s25p": s25p, "rr25": rr25, "n_iv": len(vols),
            "G": G, "mass_pct": mass_pct, "conc_pct": conc_pct, "oi_up_pct": oi_up_pct,
            "oi_front": oi_tot, "oi_c": sum(e["c"] for e in chain.values()),
            "oi_p": sum(e["p"] for e in chain.values()),
            "top_k": top_k, "top_oi": top_oi, "pin": pin,
            "cp_native": cp_native, "pata": g0["pata"], "band": g0["band"],
            "walls": sorted(((k, e["c"] + e["p"], e["c"], e["p"]) for k, e in chain.items()),
                            key=lambda t: -t[1])[:3]}


# ----------------------------------------------------------------- main
def main():
    if not CANONICAL.exists():
        sys.exit(f"FATAL: {CANONICAL} not found")
    sessions = sorted(p.name for p in CANONICAL.iterdir() if p.is_dir() and len(p.name) == 10)
    if not sessions:
        sys.exit("FATAL: no canonical sessions")
    sofr = load_sofr()

    # pass 1: raw per-session analysis
    raw = {}                                   # session -> ccy -> analyse()
    for s in sessions:
        day = load_session(CANONICAL / s, s)
        r, _ = sofr_on(sofr, s)
        raw[s] = {c: analyse(d, s, r) for c, d in day.items()}

    # gate 0 verdict (aggregate coverage, as in build_options_summary)
    gate0 = {}
    for c in ORDER:
        obs = [raw[s][c] for s in sessions if c in raw[s]]
        n = len(obs)
        if n == 0:
            gate0[c] = "NA"
            continue
        pa = sum(o["pata"] for o in obs) / n
        pb = sum(o["band"] for o in obs) / n
        gate0[c] = "CLEAN" if (pa >= COVERAGE and pb >= COVERAGE) else "PROXY" if (pa >= 0.5 or pb >= 0.5) else "NA"

    # pass 2: causal ECDF normalisation, scores, regime, crosses
    hist = {c: {"G": [], "RR": [], "DG": []} for c in ORDER}
    disp_hist, gap_hist = [], {x: [] for x in CROSSES}
    rows, per_session = [], {}
    for i, s in enumerate(sessions):
        day = raw[s]
        scores, res = {}, {}
        for c in ORDER:
            a = day.get(c)
            if not a:
                res[c] = None
                continue
            # movement: same front expiry 5 sessions back
            dg = None
            if i >= DG_LAG:
                b = raw[sessions[i - DG_LAG]].get(c)
                if b and b["front"] == a["front"] and a["G"] is not None and b["G"] is not None:
                    dg = a["G"] - b["G"]
            a["dG5"] = dg
            for key, val in (("G", a["G"]), ("RR", a["rr25"]), ("DG", dg)):
                if val is not None:
                    hist[c][key].append(val)
            zg = ecdf_z(hist[c]["G"], a["G"]) if a["G"] is not None else None
            zr = ecdf_z(hist[c]["RR"], a["rr25"]) if a["rr25"] is not None else None
            zd = ecdf_z(hist[c]["DG"], dg) if dg is not None else None
            comps = [z for z in (zg, zr, zd) if z is not None]
            score = median(comps) if comps else None
            a.update({"zG": zg, "zRR": zr, "zDG": zd, "score": score,
                      "n_hist": len(hist[c]["G"]), "gate": gate0[c], "flow": False})
            if i > 0:
                b = raw[sessions[i - 1]].get(c)
                if b and b["front"] == a["front"] and b["oi_front"] > 0:
                    a["doi_pct"] = 100.0 * (a["oi_front"] - b["oi_front"]) / b["oi_front"]
                    a["doi_net"] = ((a["oi_c"] - b["oi_c"]) - (a["oi_p"] - b["oi_p"])) / b["oi_front"]
                    a["flow"] = abs(a["doi_pct"]) > FLOW_PCT
            res[c] = a
            if score is not None and gate0[c] == "CLEAN" and not a["f_mismatch"]:
                scores[c] = score
        # regime (CLEAN only)
        clean_scores = list(scores.values())
        med = median(clean_scores) if clean_scores else None
        usd_factor = -med if med is not None else None
        disp = mad_sigma(clean_scores) if len(clean_scores) >= 2 else None
        if disp is not None:
            disp_hist.append(disp)
        disp_pct = pct_rank(disp_hist, disp) if disp is not None else None
        regime = ("DOLLAR_PURE" if (disp_pct is not None and disp_pct < DOLLAR_PCT
                                    and len(clean_scores) >= MIN_CLEAN_REGIME)
                  else "CROSS" if clean_scores else "NA")
        for c in ORDER:
            a = res[c]
            if a and a.get("score") is not None and med is not None:
                a["residual"] = a["score"] - med
        # crosses
        xs = {}
        for x in CROSSES:
            A, B = x[:3], x[3:]
            a, b = res.get(A), res.get(B)
            entry = {"base": A, "quote": B, "dir": "—", "gap": None, "strength": None,
                     "lead": None, "eligible": False, "flags": []}
            if a and b and a.get("score") is not None and b.get("score") is not None:
                gap = a["score"] - b["score"]
                gap_hist[x].append(abs(gap))
                entry["gap"] = gap
                entry["dir"] = "▲" if gap > 0 else "▼" if gap < 0 else "—"
                entry["strength"] = pct_rank(gap_hist[x], abs(gap))
                ra, rb = a.get("residual"), b.get("residual")
                if ra is not None and rb is not None:
                    entry["lead"] = A if abs(ra) >= abs(rb) else B
                elig = (a["gate"] == "CLEAN" and b["gate"] == "CLEAN"
                        and a["dte"] >= MIN_DTE and b["dte"] >= MIN_DTE
                        and not a["f_mismatch"] and not b["f_mismatch"])
                entry["eligible"] = elig
                if not elig:
                    entry["flags"].append("NO_VOTE")
                if a["f_mismatch"] or b["f_mismatch"]:
                    entry["flags"].append("F_MISMATCH")
                if a["pin"] or b["pin"]:
                    entry["flags"].append("PIN")
                if a.get("flow") or b.get("flow"):
                    entry["flags"].append("FLOW")
                if a["next"] or b["next"]:
                    entry["flags"].append("NEXT")
            else:
                entry["flags"].append("NO_VOTE")
            xs[x] = entry
        per_session[s] = {"ccy": res, "usd_factor": usd_factor, "dispersion": disp,
                          "dispersion_pct": disp_pct, "regime": regime,
                          "n_clean": len(clean_scores), "crosses": xs}
        # csv row
        row = {"session": s, "usd_factor": usd_factor, "dispersion": disp,
               "dispersion_pct": disp_pct, "regime": regime, "n_clean": len(clean_scores)}
        for c in ORDER:
            a = res[c]
            for k in ("front", "dte", "F", "atm", "rr25", "G", "dG5", "zG", "zRR", "zDG",
                      "score", "residual", "gate", "conc_pct", "oi_up_pct", "cp_native",
                      "doi_pct", "doi_net", "pin", "flow", "next", "n_iv", "n_hist",
                      "F_sym", "f_parity_pct", "f_mismatch"):
                row[f"{c}_{k}"] = (a or {}).get(k)
        for x in CROSSES:
            e = xs[x]
            row[f"{x}_gap"] = e["gap"]
            row[f"{x}_dir"] = e["dir"]
            row[f"{x}_strength"] = e["strength"]
            row[f"{x}_eligible"] = int(e["eligible"])
            row[f"{x}_flags"] = "|".join(e["flags"])
        rows.append(row)

    # write canonical csv
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[-1].keys()))
        w.writeheader()
        for row in rows:
            w.writerow({k: ("" if v is None else (round(v, 6) if isinstance(v, float) else v))
                        for k, v in row.items()})

    # write JSON (latest)
    latest = sessions[-1]
    L = per_session[latest]

    def rnd(v, n=4):
        return None if v is None else round(v, n)

    ccy_out = {}
    for c in ORDER:
        a = L["ccy"].get(c)
        if not a:
            ccy_out[c] = {"gate": gate0[c], "status": "no chain"}
            continue
        ccy_out[c] = {
            "gate": a["gate"], "front": a["front"], "front_raw": a["front_raw"],
            "next": a["next"], "dte": a["dte"],
            "F_native": a["F"], "F_sym": a["F_sym"], "ref_native": a["ref"], "near_native": a["near"],
            "f_parity_pct": rnd(a["f_parity_pct"], 3) if a["f_parity_pct"] is not None else None,
            "f_mismatch": a["f_mismatch"], "inv": c in INV,
            "display_ref": (1.0 / a["F"]) if c in INV else a["F"],
            "atm_vol_pct": rnd(100.0 * a["atm"], 2) if a["atm"] else None,
            "atm_src": a.get("atm_src"),
            "sigma25c_pct": rnd(100.0 * a["s25c"], 2) if a["s25c"] else None,
            "sigma25p_pct": rnd(100.0 * a["s25p"], 2) if a["s25p"] else None,
            "rr25": rnd(a["rr25"], 2), "n_iv": a["n_iv"], "sofr": rnd(a["r"], 5),
            "G": rnd(a["G"]), "dG5": rnd(a["dG5"]), "mass_pct": rnd(a["mass_pct"], 2),
            "zG": rnd(a["zG"]), "zRR": rnd(a["zRR"]), "zDG": rnd(a["zDG"]),
            "score": rnd(a["score"]), "residual": rnd(a.get("residual")),
            "n_hist": a["n_hist"], "low_history": a["n_hist"] < LOW_HISTORY,
            "conc_pct": rnd(a["conc_pct"], 1), "oi_up_pct": rnd(a["oi_up_pct"], 1),
            "oi_front": round(a["oi_front"]), "doi_pct": rnd(a.get("doi_pct"), 1),
            "doi_net": rnd(a.get("doi_net"), 4), "cp_native": a["cp_native"],
            "pin": a["pin"], "flow": a.get("flow", False),
            "top_wall_native": a["top_k"],
            "walls_native": [{"k": k, "oi": round(oi), "c": round(cc), "p": round(pp),
                              "dist_pct": rnd(100.0 * (k - a["F"]) / a["F"], 2),
                              "x_sigma": rnd(math.log(k / a["F"]) / ((a["atm"] or 0.10) * math.sqrt(max(a["dte"], 1) / 365.0)), 2),
                              "display_k": (1.0 / k) if c in INV else k,
                              "display_side": ("P" if cc > pp else "C") if c in INV else ("C" if cc > pp else "P")}
                             for k, oi, cc, pp in a["walls"]],
        }
    ranking = sorted([c for c in ORDER if L["ccy"].get(c) and L["ccy"][c].get("score") is not None],
                     key=lambda c: -L["ccy"][c]["score"])
    doc = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version": VERSION, "status": "RESEARCH",
        "gate_progress": f"{len(sessions)}/378",
        "latest_session": latest, "sessions_total": len(sessions),
        "doctrine": "native CME convention · OI has no sign (C/P counts and dOI never vote) · "
                    "score = median{Z(G), Z(RR25), Z(dG5)} on causal own-ECDF · crosses = direction "
                    "and ranking, never levels · eligible only CLEAN x CLEAN and DTE >= 10 · RESEARCH until gate",
        "regime": {"usd_factor": rnd(L["usd_factor"]), "dispersion": rnd(L["dispersion"]),
                   "dispersion_pct": rnd(L["dispersion_pct"], 1), "regime": L["regime"],
                   "n_clean": L["n_clean"]},
        "gate0": gate0,
        "ranking": ranking,
        "ccy": ccy_out,
        "crosses": {x: {**e, "gap": rnd(e["gap"]), "strength": rnd(e["strength"], 1)}
                    for x, e in L["crosses"].items()},
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(doc, indent=1, ensure_ascii=False))
    print(f"OK: {OUT_JSON} · {OUT_CSV} · latest={latest} · sessions={len(sessions)} · regime={L['regime']}")
    print("ranking (strong→weak):", " > ".join(ranking))
    for x in CROSSES:
        e = L["crosses"][x]
        print(f"  {x} {e['dir']} gap={rnd(e['gap'])} strength={rnd(e['strength'],0)} lead={e['lead']} {'|'.join(e['flags'])}")


if __name__ == "__main__":
    main()

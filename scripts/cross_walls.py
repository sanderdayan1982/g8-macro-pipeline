#!/usr/bin/env python3
# =============================================================================
# cross_walls.py — v2.0 · CROSS WALLS: the operator's wall reading, written down
# Section 08b of the G8 Macro Pipeline · ACTA CW-2 (17-sep-2026) · clock at zero
#
# WHAT CHANGED FROM v1.x (why a new acta): v1 scored each currency as the
# median of three own-history ECDF ranks (OI geometry with a Gaussian kernel,
# 25d risk reversal, 5-session change). Two independent audits (Cursor,
# ChatGPT) and a measurement showed the direction logic did not match the
# title: the ECDF turned "farther than the others today" into "rarer than its
# own past", geometry and skew were anti-correlated in AUD/GBP, and the
# kernel down-weighted exactly the far walls the operator reads. v2 formalises
# the manual method instead, and keeps v1's surface pieces as diagnostics.
#
# RULE (pre-registered, frozen before any evaluation):
#   * Native CME convention XXX/USD for every number; display inversion for
#     USD/JPY, USD/CAD, USD/CHF is exact (-d/(1+d)), presentation only.
#   * Analysis chain = first expiry with DTE >= MIN_DTE. Underlying F = settle
#     of the next QUARTERLY future on/after the option expiry, verified by the
#     put-call-parity forward (|F - F_parity| > PARITY_MAX_PCT => F_MISMATCH,
#     no vote; parity unavailable => no vote).
#   * WALLS: the 3 strikes with the largest OI (call+put) among candidates with
#     OI >= MIN_OI and OI >= WALL_SHARE_MIN of the chain's OI. Ties at the cut
#     share the remaining seats (factor r/m). A strike whose OI has not changed
#     for DEAD_SESSIONS consecutive sessions is DEAD and cannot be a wall.
#   * DISTANCE d_k = 100 * (K/F - 1), percent of the futures price (the
#     operator measures on the chart, not in sigma units). + = above F in
#     native terms = favours XXX; - = below F = favours USD.
#   * SIDE SHARE U = OI-weighted share of the walls below F (USD side).
#     VOTE only if max(U, 1-U) >= SIDE_MAJORITY (two thirds). D = OI-weighted
#     mean distance of the walls on the majority side. Otherwise MIXED, D = NA.
#     The decomposition U, V, A-, A+ and d_near (closest wall) are published.
#   * USD BIAS = mean U over the voting CLEAN currencies (+ list of dissenters),
#     labelled by coverage only (n voters): never a self-fulfilling percentile.
#   * CROSS A/B (operator convention, base first): direction = sign(D_A - D_B),
#     strength = |D_A - D_B| in percentage points; its causal percentile is a
#     separate "rarity" figure, never confidence. Eligible only if both legs
#     vote, are CLEAN (causal Gate 0), DTE >= MIN_DTE, no F_MISMATCH.
#   * UNIVERSE frozen: {EUR, GBP, JPY, AUD}. CAD (PROXY) and CHF (NA) are
#     shown, never vote, never enter the USD bias.
#   * DIAGNOSTICS, no vote: D_sigma (same walls, distance in sigma*sqrt(T)),
#     ATM/RR25 (Black-76 in-house), agree_RR (sign D == sign RR25), C3 (share
#     of chain OI captured by the walls), M34 (margin 3rd vs 4th), NEXT, FLOW.
#   * Status RESEARCH until gate_cross_walls.py (CW-2) passes. Direction and
#     ranking only — NEVER levels for crosses.
#
# INPUT : data/options/canonical/YYYY-MM-DD/{6E,6B,6J,6A,6C,6S}.csv (futures)
#         and {EUU,GBU,JPU,ADU,CAU,CHU}.csv (options), data/SOFR.csv
# OUTPUT: data/CROSS_WALLS.json (dashboard §08b) and
#         data/cross_walls/canonical.csv (one row per session, gate record)
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
UNIVERSE = ["EUR", "GBP", "JPY", "AUD"]         # frozen voting universe (acta CW-2)
INV = {"JPY", "CAD", "CHF"}                     # displayed as USD/XXX
CROSSES = ["EURGBP", "EURJPY", "EURAUD", "EURCAD", "EURCHF",
           "GBPJPY", "GBPAUD", "GBPCAD", "GBPCHF",
           "AUDJPY", "CADJPY", "CHFJPY", "AUDCAD", "AUDCHF", "CADCHF"]
# option minimum price increments per root (CME contract specs), for the
# minimum-premium floor of the vol inversion — no longer inferred from data
TICK = {"EUR": 0.00005, "GBP": 0.0001, "JPY": 0.0000005, "AUD": 0.0001,
        "CAD": 0.00005, "CHF": 0.0001}

# Gate 0 (same rule as build_options_summary.py; v2 applies it CAUSALLY)
MIN_OI, MIN_STRIKES, BAND_PCT, COVERAGE = 100, 5, 0.03, 0.80
GATE0_MIN_DAYS = 20     # sessions of coverage needed before a causal verdict
# pre-registered constants (acta CW-2)
MIN_DTE = 10
N_WALLS = 3
WALL_SHARE_MIN = 0.05   # wall must carry >= 5 % of the chain's OI
DEAD_SESSIONS = 10      # OI unchanged for 10 sessions => DEAD, cannot be a wall
SIDE_MAJORITY = 2.0 / 3.0
PIN_PCT = 0.30          # "OI proximity" flag: F within 0.30 % of the largest wall
FLOW_PCT = 15.0         # |dOI chain| > 15 % in one session
ATM_FIT_X, ATM_FIT_MIN = 0.75, 4
PARITY_BAND, PARITY_MAX_PCT = 0.01, 0.10
SOFR_MAX_STALE_BD = 4   # business days; older SOFR => rate flagged stale (vols still computed)
QUARTERLY = ("03", "06", "09", "12")
IV_MIN, IV_MAX = 0.005, 3.0
VERSION = "cross_walls v2.0"
GATE_TARGET_SESSIONS = 383   # 126 rarity warm-up + 252 evaluable + 5 forward


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
    """Causal: the rate of the session or the LAST PRIOR one. Never a future
    rate. Returns (rate, rate_date, business_days_stale); (None, None, None)
    when no prior rate exists."""
    keys = sorted(sofr)
    i = bisect_left(keys, session)
    if i < len(keys) and keys[i] == session:
        return sofr[session], session, 0
    if i == 0:
        return None, None, None
    d0 = date.fromisoformat(keys[i - 1])
    d1 = date.fromisoformat(session)
    bd = sum(1 for n in range(1, (d1 - d0).days + 1)
             if (d0 + __import__("datetime").timedelta(days=n)).weekday() < 5)
    return sofr[keys[i - 1]], keys[i - 1], bd

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




# ----------------------------------------------------------------- surface
def surface(d, ccy, session, r):
    """Parity forward, OTM implied vols, robust ATM and RR25 for one currency.
    Diagnostics only in v2 (nothing here votes) — kept from v1.1 [F1]/[A1]."""
    F, chain, front = d["F"], d["chain"], d["front"]
    dte = (date.fromisoformat(front) - date.fromisoformat(session)).days
    T = max(dte, 1) / 365.0
    df = math.exp(-r * T)
    est = sorted(k + (e["sc"] - e["sp"]) / df for k, e in chain.items()
                 if e["sc"] is not None and e["sp"] is not None and abs(math.log(k / F)) <= PARITY_BAND)
    if est:
        n = len(est)
        f_parity = est[n // 2] if n % 2 else 0.5 * (est[n // 2 - 1] + est[n // 2])
    else:
        f_parity = None
    f_parity_pct = 100.0 * (f_parity / F - 1.0) if f_parity else None
    f_mismatch = f_parity_pct is None or abs(f_parity_pct) > PARITY_MAX_PCT
    min_prem = 3 * TICK[ccy]
    vols = []                                   # (K, sigma, right)
    for k, e in sorted(chain.items()):
        right = "C" if k >= F else "P"
        st = e["sc"] if right == "C" else e["sp"]
        if st is None or st < min_prem:
            continue
        iv = implied_vol(st, F, k, T, r, right)
        if iv is not None and IV_MIN < iv < IV_MAX - 1e-6:
            vols.append((k, iv, right))
    atm, atm_src = None, "NA"
    if vols:
        ks = [v[0] for v in vols]
        ivs = [v[1] for v in vols]
        atm = interp(ks, ivs, F)
        if atm is None:
            j = min(range(len(ks)), key=lambda i: abs(ks[i] - F))
            atm = ivs[j]
        atm_src = "2PT"
        half = ATM_FIT_X * atm * math.sqrt(T)
        pts = [(math.log(k / F), iv) for k, iv, _ in vols if abs(math.log(k / F)) <= half]
        if len(pts) >= ATM_FIT_MIN and any(m < 0 for m, _ in pts) and any(m > 0 for m, _ in pts):
            n = len(pts)
            mx = sum(m for m, _ in pts) / n
            my = sum(v for _, v in pts) / n
            sxx = sum((m - mx) ** 2 for m, _ in pts)
            b = sum((m - mx) * (v - my) for m, v in pts) / sxx if sxx > 0 else 0.0
            fit = my - b * mx
            if IV_MIN < fit < IV_MAX:
                atm, atm_src = fit, f"FIT{n}"
    rr25 = s25c = s25p = None
    if atm and len(vols) >= 8:
        calls = sorted((b76_delta(F, k, T, r, iv, "C"), iv) for k, iv, rt in vols if rt == "C")
        puts = sorted((b76_delta(F, k, T, r, iv, "P"), iv) for k, iv, rt in vols if rt == "P")
        if len(calls) >= 2:
            s25c = interp([c[0] for c in calls], [c[1] for c in calls], 0.25)
        if len(puts) >= 2:
            s25p = interp([p[0] for p in puts], [p[1] for p in puts], -0.25)
        if s25c is not None and s25p is not None:
            rr25 = 100.0 * (s25c - s25p)
    return {"dte": dte, "T": T, "F": F, "f_parity_pct": f_parity_pct, "f_mismatch": f_mismatch,
            "atm": atm, "atm_src": atm_src, "s25c": s25c, "s25p": s25p, "rr25": rr25,
            "n_iv": len(vols), "vols": vols}


# ----------------------------------------------------------------- walls (v2 rule)
def select_walls(chain, dead):
    """Top-N strikes by OI among candidates (OI >= MIN_OI, >= WALL_SHARE_MIN of
    chain OI, not DEAD). Ties at the cut share the remaining seats (factor r/m).
    Returns list of (K, oi, c, p, seat_factor) and the chain OI total."""
    tot = sum(e["c"] + e["p"] for e in chain.values())
    cand = sorted(((e["c"] + e["p"], k, e["c"], e["p"]) for k, e in chain.items()
                   if e["c"] + e["p"] >= MIN_OI and e["c"] + e["p"] >= WALL_SHARE_MIN * tot
                   and k not in dead), reverse=True)
    if not cand:
        return [], tot, None
    cut = cand[min(N_WALLS, len(cand)) - 1][0]
    above = [(k, o, c, p, 1.0) for o, k, c, p in cand if o > cut]
    tied = [(k, o, c, p) for o, k, c, p in cand if o == cut]
    r = N_WALLS - len(above)
    walls = above + [(k, o, c, p, r / len(tied)) for k, o, c, p in tied]
    fourth = cand[N_WALLS][0] if len(cand) > N_WALLS else None
    return walls, tot, fourth


def wall_reading(walls, F, atm, T):
    """The operator's rule on the selected walls: U, V, A-, A+, D (majority side,
    percent of F), MIXED, d_near, D_sigma (diagnostic)."""
    w_tot = sum(o * a for _, o, _, _, a in walls)
    if not walls or w_tot <= 0:
        return None
    rows = []
    for k, o, c, p, a in walls:
        d = 100.0 * (k / F - 1.0)
        x = math.log(k / F) / (atm * math.sqrt(T)) if atm else None
        rows.append({"k": k, "oi": o, "c": c, "p": p, "seat": a, "w": o * a, "d_pct": d, "x_sigma": x})
    below = [w for w in rows if w["k"] < F]
    above = [w for w in rows if w["k"] > F]
    U = sum(w["w"] for w in below) / w_tot
    V = sum(w["w"] for w in above) / w_tot
    A_minus = (sum(w["w"] * abs(w["d_pct"]) for w in below) / sum(w["w"] for w in below)) if below else None
    A_plus = (sum(w["w"] * w["d_pct"] for w in above) / sum(w["w"] for w in above)) if above else None
    if U >= SIDE_MAJORITY:
        side, sel = "USD", below
    elif V >= SIDE_MAJORITY:
        side, sel = "XXX", above
    else:
        side, sel = "MIXED", []
    D = (sum(w["w"] * w["d_pct"] for w in sel) / sum(w["w"] for w in sel)) if sel else None
    D_sigma = None
    if sel and all(w["x_sigma"] is not None for w in sel):
        D_sigma = sum(w["w"] * w["x_sigma"] for w in sel) / sum(w["w"] for w in sel)
    near = min(rows, key=lambda w: abs(w["d_pct"]))
    top = max(rows, key=lambda w: w["w"])
    return {"walls": rows, "U": U, "V": V, "A_minus": A_minus, "A_plus": A_plus,
            "side": side, "D": D, "D_sigma": D_sigma,
            "d_near": near["d_pct"], "k_near": near["k"],
            "pin": abs(top["d_pct"]) < PIN_PCT, "k_top": top["k"]}


def update_dead(hist, d):
    """Append today's OI per (front, K) to hist and return the DEAD set: strikes
    with OI >= MIN_OI unchanged for DEAD_SESSIONS consecutive sessions."""
    dead = set()
    for k, e in d["chain"].items():
        lst = hist.setdefault((d["front"], k), [])
        lst.append(e["c"] + e["p"])
        if (len(lst) >= DEAD_SESSIONS and len(set(lst[-DEAD_SESSIONS:])) == 1
                and lst[-1] >= MIN_OI):
            dead.add(k)
    return dead


def inv_pct(d):
    """Exact percent distance in the inverted quote: K'=1/K, F'=1/F => -d/(1+d)."""
    return None if d is None else -d / (1.0 + d / 100.0)


# ----------------------------------------------------------------- main
def main():
    if not CANONICAL.exists():
        sys.exit(f"FATAL: {CANONICAL} not found")
    sessions = sorted(p.name for p in CANONICAL.iterdir() if p.is_dir() and len(p.name) == 10)
    if not sessions:
        sys.exit("FATAL: no canonical sessions")
    sofr = load_sofr()

    oi_hist = {c: {} for c in ORDER}          # ccy -> (front, K) -> [oi...] for DEAD
    cov = {c: {"n": 0, "pata": 0, "band": 0} for c in ORDER}   # causal Gate 0
    gap_hist = {x: [] for x in CROSSES}
    prev_chain_oi = {}
    rows, per_session = [], {}
    for s in sessions:
        day = load_session(CANONICAL / s, s)
        r, r_date, r_stale = sofr_on(sofr, s)
        res = {}
        for c in ORDER:
            d = day.get(c)
            if not d:
                res[c] = None
                continue
            # causal Gate 0 (coverage up to and including this session)
            g0 = gate0_metrics(d)
            cv = cov[c]
            cv["n"] += 1
            cv["pata"] += g0["pata"]
            cv["band"] += g0["band"]
            if cv["n"] < GATE0_MIN_DAYS:
                gate = "PENDING"
            else:
                pa, pb = cv["pata"] / cv["n"], cv["band"] / cv["n"]
                gate = "CLEAN" if (pa >= COVERAGE and pb >= COVERAGE) else "PROXY" if (pa >= 0.5 or pb >= 0.5) else "NA"
            # surface (diagnostics) — needs a rate
            sf = surface(d, c, s, r if r is not None else 0.0)
            dead = update_dead(oi_hist[c], d)
            walls, chain_oi, fourth = select_walls(d["chain"], dead)
            wr = wall_reading(walls, d["F"], sf["atm"], sf["T"])
            c3 = (sum(o * a for _, o, _, _, a in walls) / chain_oi) if chain_oi > 0 and walls else None
            m34 = None
            if fourth is not None and walls:
                third = min(o for _, o, _, _, _ in walls)
                m34 = (third - fourth) / third if third > 0 else None
            # FLOW on the analysis chain
            key = (c, d["front"])
            flow, doi_pct = False, None
            if key in prev_chain_oi and prev_chain_oi[key] > 0:
                doi_pct = 100.0 * (chain_oi - prev_chain_oi[key]) / prev_chain_oi[key]
                flow = abs(doi_pct) > FLOW_PCT
            prev_chain_oi[key] = chain_oi
            votes = (c in UNIVERSE and gate == "CLEAN" and sf["dte"] >= MIN_DTE
                     and not sf["f_mismatch"] and sf["atm"] is not None
                     and wr is not None and wr["side"] != "MIXED")
            agree = None
            if wr and wr["D"] is not None and sf["rr25"] is not None:
                agree = (wr["D"] > 0) == (sf["rr25"] > 0)
            res[c] = {"gate": gate, "front": d["front"], "front_raw": d["front_raw"],
                      "next": d["front"] != d["front_raw"], "dte": sf["dte"], "F": d["F"],
                      "F_sym": d["F_sym"], "f_parity_pct": sf["f_parity_pct"], "f_mismatch": sf["f_mismatch"],
                      "atm": sf["atm"], "atm_src": sf["atm_src"], "rr25": sf["rr25"], "n_iv": sf["n_iv"],
                      "sofr": r, "sofr_date": r_date, "sofr_stale_bd": r_stale,
                      "chain_oi": chain_oi, "doi_pct": doi_pct, "flow": flow,
                      "c3": c3, "m34": m34, "dead": sorted(dead),
                      "wr": wr, "votes": votes, "agree_rr": agree,
                      "D": wr["D"] if (wr and votes) else None,
                      "D_raw": wr["D"] if wr else None, "side": wr["side"] if wr else "NA"}
        # USD bias over voting CLEAN currencies
        voters = [c for c in UNIVERSE if res.get(c) and res[c]["votes"]]
        usd_share = (sum(res[c]["wr"]["U"] for c in voters) / len(voters)) if voters else None
        dissent = [c for c in voters if res[c]["wr"]["U"] < 0.5]
        # crosses
        xs = {}
        for x in CROSSES:
            A, B = x[:3], x[3:]
            a, b = res.get(A), res.get(B)
            e = {"base": A, "quote": B, "dir": "—", "gap": None, "strength": None,
                 "rarity": None, "lead": None, "eligible": False, "flags": []}
            if a and b and a["D"] is not None and b["D"] is not None:
                gap = a["D"] - b["D"]
                e["gap"] = gap
                e["dir"] = "▲" if gap > 0 else "▼" if gap < 0 else "—"
                e["strength"] = abs(gap)
                gap_hist[x].append(abs(gap))
                e["rarity"] = pct_rank(gap_hist[x], abs(gap))
                e["lead"] = A if abs(a["D"]) >= abs(b["D"]) else B
                e["eligible"] = True
            else:
                e["flags"].append("NO_VOTE")
                for leg in (a, b):
                    if leg and leg["side"] == "MIXED" and "MIXED" not in e["flags"]:
                        e["flags"].append("MIXED")
                    if leg and leg["f_mismatch"] and "F_MISMATCH" not in e["flags"]:
                        e["flags"].append("F_MISMATCH")
            for leg in (a, b):
                if leg and leg["wr"] and leg["wr"]["pin"] and "PIN" not in e["flags"]:
                    e["flags"].append("PIN")
                if leg and leg["flow"] and "FLOW" not in e["flags"]:
                    e["flags"].append("FLOW")
                if leg and leg["next"] and "NEXT" not in e["flags"]:
                    e["flags"].append("NEXT")
            xs[x] = e
        per_session[s] = {"ccy": res, "usd_share": usd_share, "n_voters": len(voters),
                          "dissent": dissent, "crosses": xs}
        row = {"session": s, "usd_share": usd_share, "n_voters": len(voters),
               "dissent": "|".join(dissent)}
        for c in ORDER:
            a = res[c]
            wr = (a or {}).get("wr") or {}
            for k in ("gate", "front", "dte", "F", "F_sym", "f_parity_pct", "f_mismatch",
                      "atm", "rr25", "n_iv", "sofr_stale_bd", "chain_oi", "doi_pct", "flow",
                      "c3", "m34", "votes", "agree_rr", "D", "D_raw", "side", "next"):
                row[f"{c}_{k}"] = (a or {}).get(k)
            for k in ("U", "V", "A_minus", "A_plus", "D_sigma", "d_near", "pin"):
                row[f"{c}_{k}"] = wr.get(k)
            row[f"{c}_walls"] = "|".join(f'{w["k"]}:{int(w["oi"])}:{w["d_pct"]:.3f}' for w in wr.get("walls", []))
            row[f"{c}_dead"] = "|".join(str(k) for k in (a or {}).get("dead", []))
        for x in CROSSES:
            e = xs[x]
            row[f"{x}_gap"] = e["gap"]
            row[f"{x}_dir"] = e["dir"]
            row[f"{x}_strength"] = e["strength"]
            row[f"{x}_rarity"] = e["rarity"]
            row[f"{x}_eligible"] = int(e["eligible"])
            row[f"{x}_flags"] = "|".join(e["flags"])
        rows.append(row)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[-1].keys()))
        w.writeheader()
        for row in rows:
            w.writerow({k: ("" if v is None else (round(v, 6) if isinstance(v, float) else v))
                        for k, v in row.items()})

    # ---- JSON (latest session)
    latest = sessions[-1]
    L = per_session[latest]

    def rnd(v, n=4):
        return None if v is None else round(v, n)

    ccy_out = {}
    for c in ORDER:
        a = L["ccy"].get(c)
        if not a:
            ccy_out[c] = {"gate": "NA", "status": "no chain"}
            continue
        wr = a["wr"] or {}
        inv = c in INV
        walls = []
        for w in wr.get("walls", []):
            walls.append({"k": w["k"], "oi": round(w["oi"]), "c": round(w["c"]), "p": round(w["p"]),
                          "seat": rnd(w["seat"], 3), "d_pct": rnd(w["d_pct"], 3), "x_sigma": rnd(w["x_sigma"], 2),
                          "side_native": "below" if w["k"] < a["F"] else "above",
                          "display_k": (1.0 / w["k"]) if inv else w["k"],
                          "display_d_pct": rnd(inv_pct(w["d_pct"]) if inv else w["d_pct"], 3),
                          "display_side": ("above" if w["k"] < a["F"] else "below") if inv else ("below" if w["k"] < a["F"] else "above"),
                          "usd_side": w["k"] < a["F"]})
        ccy_out[c] = {
            "gate": a["gate"], "in_universe": c in UNIVERSE, "votes": a["votes"],
            "side": a["side"], "front": a["front"], "next": a["next"], "dte": a["dte"],
            "F_native": a["F"], "F_sym": a["F_sym"], "inv": inv,
            "display_ref": (1.0 / a["F"]) if inv else a["F"],
            "f_parity_pct": rnd(a["f_parity_pct"], 3), "f_mismatch": a["f_mismatch"],
            "D_pct": rnd(a["D_raw"], 3), "D_display_pct": rnd(inv_pct(a["D_raw"]) if inv else a["D_raw"], 3),
            "D_sigma": rnd(wr.get("D_sigma"), 2),
            "U": rnd(wr.get("U"), 3), "V": rnd(wr.get("V"), 3),
            "A_minus": rnd(wr.get("A_minus"), 3), "A_plus": rnd(wr.get("A_plus"), 3),
            "d_near_pct": rnd(wr.get("d_near"), 3), "pin": wr.get("pin", False),
            "c3": rnd(a["c3"], 3), "m34": rnd(a["m34"], 3), "dead_strikes": a["dead"],
            "chain_oi": round(a["chain_oi"]), "doi_pct": rnd(a["doi_pct"], 2), "flow": a["flow"],
            "atm_vol_pct": rnd(100.0 * a["atm"], 2) if a["atm"] else None, "atm_src": a["atm_src"],
            "rr25": rnd(a["rr25"], 2), "agree_rr": a["agree_rr"], "n_iv": a["n_iv"],
            "sofr": rnd(a["sofr"], 5), "sofr_date": a["sofr_date"], "sofr_stale_bd": a["sofr_stale_bd"],
            "walls": walls,
        }
    voters = [c for c in UNIVERSE if L["ccy"].get(c) and L["ccy"][c]["votes"]]
    ranking = sorted(voters, key=lambda c: L["ccy"][c]["D"])        # weakest first
    usd_label = ("INSUFICIENTE" if L["n_voters"] < 2 else
                 "USD alcista" if L["usd_share"] >= SIDE_MAJORITY else
                 "USD bajista" if L["usd_share"] <= 1 - SIDE_MAJORITY else "sin mayoría")
    doc = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version": VERSION, "status": "RESEARCH", "acta": "CW-2",
        "gate_progress": f"{len(sessions)}/{GATE_TARGET_SESSIONS}",
        "latest_session": latest, "sessions_total": len(sessions),
        "doctrine": "the operator's wall reading, written down · native CME convention · 3 largest walls "
                    "(>= 5 % of chain OI, not DEAD) · distance in % of the quarterly future · vote only with "
                    ">= 2/3 of wall OI on one side · D = OI-weighted distance of that side · USD bias = mean "
                    "USD-side share of the voters · cross = sign(D_A - D_B) · direction and ranking, never "
                    "levels · universe {EUR,GBP,JPY,AUD} · RESEARCH until gate CW-2",
        "usd": {"share": rnd(L["usd_share"], 3), "label": usd_label, "n_voters": L["n_voters"],
                "dissent": L["dissent"]},
        "ranking_weak_to_strong": ranking,
        "mixed": [c for c in UNIVERSE if L["ccy"].get(c) and L["ccy"][c]["side"] == "MIXED"],
        "ccy": ccy_out,
        "crosses": {x: {**e, "gap": rnd(e["gap"], 3), "strength": rnd(e["strength"], 3),
                        "rarity": rnd(e["rarity"], 1)} for x, e in L["crosses"].items()},
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(doc, indent=1, ensure_ascii=False))
    print(f"OK: {OUT_JSON} · {latest} · {len(sessions)} sessions · USD {usd_label} "
          f"({rnd(L['usd_share'], 2)}, {L['n_voters']} voters) · weak→strong {ranking} · mixed {doc['mixed']}")
    for x, e in L["crosses"].items():
        if e["eligible"]:
            print(f"  {x} {e['dir']} gap={e['gap']:+.2f}pp rarity={e['rarity']} lead={e['lead']} {'|'.join(e['flags'])}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# =============================================================================
# gate_cross_walls.py — v2.0 · Pre-registered gate CW-2 for CROSS WALLS v2
#
# PRE-REGISTRATION (frozen 17-sep-2026, before any evaluation; ACTA_CW2.md):
#   Signal: D_c,t = OI-weighted percent distance of the majority-side walls of
#      currency c on session t (cross_walls.py v2.0, canonical.csv). Known at
#      t+1 morning (settlement + OI publish T+1), so every target starts at the
#      settlement of t+1.
#   H0 (primary, per leg): sign(D_c,t) is associated out-of-sample with the sign
#      of the native log return of XXX/USD futures from t+1 to t+1+5, using the
#      SAME quarterly contract (F_sym of session t) at both ends. Legs: the
#      voting CLEAN currencies of the frozen universe {EUR, GBP, JPY, AUD}.
#      Metric: sign hit rate pooled by DATE (each date contributes the mean hit
#      of its legs), block bootstrap over dates (blocks of 21, 2000 resamples).
#      PASS-H0 iff hit >= 54 % and CI95 lower bound > 50 %.
#   H1 (crosses): gap_t = D_A - D_B of ELIGIBLE crosses vs the cross log return
#      t+1 -> t+1+5 (same contracts). Metric: daily cross-sectional Spearman IC
#      over eligible crosses (>= 3 on the day), mean IC, block bootstrap over
#      dates. Strong-signal diagnostic: hit rate on crosses with rarity >= 66.7
#      (bootstrap over dates). PASS-H1 iff mean IC5 >= 0.05 with CI95 lower
#      bound > 0 and strong hit >= 54 % with CI95 lower bound > 50 %.
#   VETO: mean IC21 (H1) or hit21 (H0) below the no-information level on the
#      subset with 21-session coverage.
#   Zero returns: excluded (neither hit nor miss). Missing contract at either
#      end: excluded.
#   Sample: first WARMUP = 126 sessions excluded (rarity history; kept for
#      comparability with CW-1), then >= 252 evaluable 5D dates. ONE evaluation:
#      the first run with n_eval5 >= 252 writes data/cross_walls/verdict_cw2.json
#      and later runs only re-print it. No repeated looks.
#   Incrementality (needed for FULL PASS): IC of gap residualised on BREADTH and
#      POS-G8 stays > 0. Files (exported from TradingView, one row per date and
#      currency): data/cross_walls/breadth_export.csv and pos_export.csv with
#      columns date,ccy,value. PENDING while absent.
#   Before eligibility the script prints an interim report tagged RESEARCH and
#      REFUSES to emit a verdict.
# USAGE: python3 scripts/gate_cross_walls.py [--warmup 126] [--min-eval 252]
# stdlib only.
# =============================================================================
import csv, json, math, os, random, sys
from pathlib import Path

CANON_CSV = Path(os.environ.get("G8_CW_CSV", "data/cross_walls/canonical.csv"))
OPT_CANON = Path(os.environ.get("G8_OPT_OUT_DIR", "data/options/canonical"))
VERDICT = Path("data/cross_walls/verdict_cw2.json")
BREADTH = Path("data/cross_walls/breadth_export.csv")
POS = Path("data/cross_walls/pos_export.csv")
FUT = {"EUR": "6E", "GBP": "6B", "JPY": "6J", "AUD": "6A", "CAD": "6C", "CHF": "6S"}
UNIVERSE = ["EUR", "GBP", "JPY", "AUD"]
CROSSES = ["EURGBP", "EURJPY", "EURAUD", "GBPJPY", "GBPAUD", "AUDJPY"]   # universe crosses
H_PRIMARY, H_SECONDARY = 5, 21
MIN_CROSSES_DAY = 3
BLOCK, N_BOOT = 21, 2000
IC_PASS, HIT_PASS = 0.05, 0.54
STRONG_PCT = 66.7
random.seed(20260917)


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load_settles(session):
    """symbol -> settle for every currency's futures on one session."""
    out = {}
    d = OPT_CANON / session
    for c, root in FUT.items():
        p = d / f"{root}.csv"
        if not p.exists():
            continue
        with p.open(newline="") as f:
            for r in csv.DictReader(f):
                if r["type"] == "FUT" and fnum(r["settle"]):
                    out[r["symbol"]] = fnum(r["settle"])
    return out


def spearman(x, y):
    n = len(x)
    if n < 3:
        return None

    def ranks(v):
        order = sorted(range(n), key=lambda i: v[i])
        rk = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                rk[order[k]] = 0.5 * (i + j) + 1
            i = j + 1
        return rk
    rx, ry = ranks(x), ranks(y)
    mx, my = sum(rx) / n, sum(ry) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else None


def block_bootstrap_mean(series, block=BLOCK, n_boot=N_BOOT):
    """CI95 of the mean of a DATE-ordered series, resampling blocks of dates."""
    n = len(series)
    if n < block:
        return None
    means = []
    nb = math.ceil(n / block)
    for _ in range(n_boot):
        sample = []
        for _ in range(nb):
            s = random.randrange(0, n - block + 1)
            sample.extend(series[s:s + block])
        sample = sample[:n]
        means.append(sum(sample) / len(sample))
    means.sort()
    return means[int(0.025 * n_boot)], means[int(0.975 * n_boot)]


def load_export(p):
    """date,ccy,value -> {date: {ccy: value}}"""
    if not p.exists():
        return None
    out = {}
    with p.open(newline="") as f:
        for r in csv.DictReader(f):
            v = fnum(r.get("value"))
            if v is not None:
                out.setdefault(r["date"], {})[r["ccy"]] = v
    return out


def residualise(y, x):
    """OLS residual of y on x (same length)."""
    n = len(y)
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    b = sum((a - mx) * (c - my) for a, c in zip(x, y)) / sxx if sxx > 0 else 0.0
    return [c - (my + b * (a - mx)) for a, c in zip(x, y)]


def main(argv):
    warm = int(argv[argv.index("--warmup") + 1]) if "--warmup" in argv else 126
    min_eval = int(argv[argv.index("--min-eval") + 1]) if "--min-eval" in argv else 252
    if not CANON_CSV.exists():
        sys.exit(f"FATAL: {CANON_CSV} missing — run cross_walls.py first")
    rows = list(csv.DictReader(CANON_CSV.open(newline="")))
    if "EUR_D" not in rows[0]:
        sys.exit("FATAL: canonical.csv is not v2 (no *_D columns) — run cross_walls.py v2.0")
    sessions = [r["session"] for r in rows]
    settles = {s: load_settles(s) for s in sessions}
    n = len(rows)
    breadth, pos = load_export(BREADTH), load_export(POS)

    def leg_ret(i, h, c):
        """native log return of currency c's quarterly future from t+1 to t+1+h."""
        if i + 1 + h >= n:
            return None
        sym = rows[i].get(f"{c}_F_sym")
        s0, s1 = settles[sessions[i + 1]].get(sym), settles[sessions[i + 1 + h]].get(sym)
        if not sym or not s0 or not s1 or s0 <= 0 or s1 <= 0:
            return None
        return math.log(s1 / s0)

    # ---------------- H0 per leg
    h0_hits5, h0_hits21, h0_dates = [], [], []
    h0_resid_pairs = []          # (gap-like D, ret) for incrementality on legs
    # ---------------- H1 crosses
    ic5, ic21, strong_hits_by_date, dates_used = [], [], [], []
    inc_pairs = []               # (gap, ret, breadth_gap, pos_gap)
    for i, r in enumerate(rows):
        if i < warm:
            continue
        hits5, hits21 = [], []
        for c in UNIVERSE:
            D = fnum(r.get(f"{c}_D"))
            if D is None or D == 0 or r.get(f"{c}_votes") != "True":
                continue
            f5 = leg_ret(i, H_PRIMARY, c)
            if f5 is None or f5 == 0:
                continue
            hits5.append(1.0 if (D > 0) == (f5 > 0) else 0.0)
            f21 = leg_ret(i, H_SECONDARY, c)
            if f21 is not None and f21 != 0:
                hits21.append(1.0 if (D > 0) == (f21 > 0) else 0.0)
        if hits5:
            h0_hits5.append(sum(hits5) / len(hits5))
            h0_dates.append(r["session"])
        if hits21:
            h0_hits21.append(sum(hits21) / len(hits21))
        xs, y5, y21, strong = [], [], [], []
        for x in CROSSES:
            if r.get(f"{x}_eligible") != "1":
                continue
            gap = fnum(r.get(f"{x}_gap"))
            if gap is None or gap == 0:
                continue
            A, B = x[:3], x[3:]
            ra, rb = leg_ret(i, H_PRIMARY, A), leg_ret(i, H_PRIMARY, B)
            if ra is None or rb is None:
                continue
            f5 = ra - rb
            if f5 == 0:
                continue
            ra21, rb21 = leg_ret(i, H_SECONDARY, A), leg_ret(i, H_SECONDARY, B)
            f21 = (ra21 - rb21) if (ra21 is not None and rb21 is not None) else None
            xs.append(gap)
            y5.append(f5)
            y21.append(f21)
            rar = fnum(r.get(f"{x}_rarity"))
            if rar is not None and rar >= STRONG_PCT:
                strong.append(1.0 if (gap > 0) == (f5 > 0) else 0.0)
            if breadth and pos and r["session"] in breadth and r["session"] in pos:
                bA, bB = breadth[r["session"]].get(A), breadth[r["session"]].get(B)
                pA, pB = pos[r["session"]].get(A), pos[r["session"]].get(B)
                if None not in (bA, bB, pA, pB):
                    inc_pairs.append((gap, f5, bA - bB, pA - pB))
        if len(xs) >= MIN_CROSSES_DAY:
            ic = spearman(xs, y5)
            if ic is not None:
                ic5.append(ic)
                dates_used.append(r["session"])
            if all(v is not None for v in y21) and len(y21) >= MIN_CROSSES_DAY:
                ic = spearman(xs, y21)
                if ic is not None:
                    ic21.append(ic)
            if strong:
                strong_hits_by_date.append(sum(strong) / len(strong))

    n_eval0, n_eval1 = len(h0_hits5), len(ic5)
    eligible = (n >= warm + min_eval + H_PRIMARY + 1) and n_eval0 >= min_eval
    print("=" * 78)
    print(f"CROSS WALLS v2 · GATE CW-2 · sessions={n} · warm-up={warm} · "
          f"H0 evaluable dates (5D)={n_eval0} · H1 evaluable dates={n_eval1} · target {warm + min_eval + H_PRIMARY + 1}")
    print("=" * 78)
    if VERDICT.exists():
        print("VERDICT ALREADY RECORDED (single-look rule):")
        print(VERDICT.read_text())
        return
    status = "ELIGIBLE FOR VERDICT" if eligible else \
        f"RESEARCH · {n}/{warm + min_eval + H_PRIMARY + 1} sessions · {n_eval0}/{min_eval} evaluable dates — no verdict allowed"
    print("STATUS:", status)
    if n_eval0 == 0:
        print("no evaluable dates yet (need warm-up + 6 sessions of forward data)")
        return
    rep = {}
    hr0 = sum(h0_hits5) / n_eval0
    ci0 = block_bootstrap_mean(h0_hits5)
    print(f"H0 hit rate 5D  {100 * hr0:.1f} %  dates={n_eval0}  block-bootstrap CI95 {ci0 if ci0 else 'n/a (< 21 dates)'}")
    rep["h0_hit5"], rep["h0_ci5"], rep["h0_n"] = hr0, ci0, n_eval0
    if h0_hits21:
        hr021 = sum(h0_hits21) / len(h0_hits21)
        print(f"H0 hit rate 21D {100 * hr021:.1f} %  dates={len(h0_hits21)}  (veto if < 50 %)")
        rep["h0_hit21"] = hr021
    if ic5:
        m5 = sum(ic5) / n_eval1
        ci5 = block_bootstrap_mean(ic5)
        print(f"H1 IC_5D  mean {m5:+.4f}  dates={n_eval1}  CI95 {ci5 if ci5 else 'n/a'}")
        rep["h1_ic5"], rep["h1_ci5"], rep["h1_n"] = m5, ci5, n_eval1
        if ic21:
            m21 = sum(ic21) / len(ic21)
            print(f"H1 IC_21D mean {m21:+.4f}  dates={len(ic21)}  (veto if < 0)")
            rep["h1_ic21"] = m21
        if strong_hits_by_date:
            hr = sum(strong_hits_by_date) / len(strong_hits_by_date)
            ci_h = block_bootstrap_mean(strong_hits_by_date)
            print(f"H1 strong hit rate {100 * hr:.1f} %  dates={len(strong_hits_by_date)}  CI95 {ci_h}")
            rep["h1_strong"], rep["h1_strong_ci"] = hr, ci_h
    if inc_pairs and len(inc_pairs) >= 30:
        g = [p[0] for p in inc_pairs]
        rg = residualise(residualise(g, [p[2] for p in inc_pairs]), [p[3] for p in inc_pairs])
        inc_ic = spearman(rg, [p[1] for p in inc_pairs])
        print(f"incrementality: IC of gap residualised on BREADTH+POS = {inc_ic:+.4f} (pooled, n={len(inc_pairs)})")
        rep["incrementality_ic"] = inc_ic
    else:
        print("incrementality vs BREADTH/POS: PENDING (exports absent or < 30 pairs)")
    if not eligible:
        print("\n>> interim numbers only. The pre-registration forbids a verdict before "
              f"{min_eval} evaluable dates. Do not act on them.")
        return
    ok_h0 = hr0 >= HIT_PASS and ci0 and ci0[0] > 0.50
    veto0 = rep.get("h0_hit21") is not None and rep["h0_hit21"] < 0.50
    ok_h1 = (rep.get("h1_ic5") is not None and rep["h1_ic5"] >= IC_PASS and rep.get("h1_ci5") and rep["h1_ci5"][0] > 0
             and rep.get("h1_strong") is not None and rep["h1_strong"] >= HIT_PASS
             and rep.get("h1_strong_ci") and rep["h1_strong_ci"][0] > 0.50)
    veto1 = rep.get("h1_ic21") is not None and rep["h1_ic21"] < 0
    verdict = {"H0": "PASS" if (ok_h0 and not veto0) else "FAIL",
               "H1": "PASS" if (ok_h1 and not veto1) else "FAIL",
               "incrementality": "PASS" if rep.get("incrementality_ic", 0) > 0 and "incrementality_ic" in rep else "PENDING",
               "evaluated_on": sessions[-1], "n_sessions": n, "report": rep}
    if warm != 126 or min_eval != 252:
        print("\nDRY RUN with non-registered parameters — verdict NOT recorded, output is void:",
              json.dumps({k: v for k, v in verdict.items() if k != "report"}))
        return
    VERDICT.parent.mkdir(parents=True, exist_ok=True)
    VERDICT.write_text(json.dumps(verdict, indent=1))
    print("\nVERDICT (recorded once, never re-evaluated):", json.dumps({k: v for k, v in verdict.items() if k != "report"}))


if __name__ == "__main__":
    main(sys.argv[1:])

#!/usr/bin/env python3
# =============================================================================
# gate_cross_walls.py — v1.0 · Pre-registered gate for CROSS WALLS (§08b)
#
# PRE-REGISTRATION (frozen 16-sep-2026, before any evaluation; ACTA_CW1.md):
#   H1 (primary): on session t, sign(S_A - S_B) of an ELIGIBLE cross is
#      associated out-of-sample with the sign of the cross log-return over the
#      next 5 sessions. Secondary: 21 sessions (veto only).
#   Universe: the 15 G8 crosses, a cross counts only when eligible on t
#      (both legs CLEAN and DTE >= 10, from canonical.csv).
#   Forecast: gap_t = S_A - S_B. Target: ln(F_A,t+h / F_A,t) - ln(F_B,t+h / F_B,t)
#      using the SAME futures contract at t and t+h (no roll contamination).
#   Metric: daily cross-sectional Spearman IC between gap_t and the forward
#      return over the eligible crosses (>= 4 needed on the day); mean IC.
#   Dependence: block bootstrap over DATES (blocks of 21 sessions), the whole
#      matrix of one date resampled together (15 crosses of 6 legs are NOT
#      independent observations). 2000 resamples.
#   Strong-signal diagnostic: hit rate of sign on crosses with strength >= 66.7
#      (top tercile of |gap| for that pair, causal percentile).
#   Sample: history for the causal ECDF = 126 sessions (warm-up, excluded);
#      evaluation = the next >= 252 sessions => first verdict at N >= 378.
#   PASS (5D) iff mean IC_5 >= 0.05 AND bootstrap CI95 lower bound > 0 AND
#      strong hit rate >= 54 % with CI95 lower bound > 50 %.
#   VETO iff mean IC_21 < 0 (5D edge that reverses at 21D is not a filter).
#   Incrementality (needed for FULL PASS): IC of gap residualised on BREADTH
#      and POS-G8 stays > 0 — requires the TradingView exports; reported as
#      PENDING when the files are absent.
#   Before N >= 378 the script prints an interim report tagged RESEARCH and
#      REFUSES to emit a verdict. A material change of the score formula
#      restarts the clock (new version, new acta).
#
# USAGE: python3 scripts/gate_cross_walls.py [--warmup 126] [--min-eval 252]
# stdlib only.
# =============================================================================
import csv, math, os, random, sys
from pathlib import Path

CANON_CSV = Path(os.environ.get("G8_CW_CSV", "data/cross_walls/canonical.csv"))
OPT_CANON = Path(os.environ.get("G8_OPT_OUT_DIR", "data/options/canonical"))
FUT = {"EUR": "6E", "GBP": "6B", "JPY": "6J", "AUD": "6A", "CAD": "6C", "CHF": "6S"}
CROSSES = ["EURGBP", "EURJPY", "EURAUD", "EURCAD", "EURCHF", "GBPJPY", "GBPAUD",
           "GBPCAD", "GBPCHF", "AUDJPY", "CADJPY", "CHFJPY", "AUDCAD", "AUDCHF", "CADCHF"]
H_PRIMARY, H_SECONDARY = 5, 21
MIN_CROSSES_DAY = 4
BLOCK, N_BOOT = 21, 2000
IC_PASS, HIT_PASS = 0.05, 0.54
STRONG_PCT = 66.7
random.seed(20260916)


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load_futures(session):
    """expiry -> settle for each currency's futures on one session."""
    out = {}
    d = OPT_CANON / session
    for c, root in FUT.items():
        p = d / f"{root}.csv"
        if not p.exists():
            continue
        m = {}
        with p.open(newline="") as f:
            for r in csv.DictReader(f):
                if r["type"] == "FUT" and fnum(r["settle"]):
                    m[r["expiry"]] = fnum(r["settle"])
        out[c] = m
    return out


def front_contract(fmap, session):
    exps = sorted(e for e in fmap if e >= session)
    return exps[0] if exps else None


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


def main(argv):
    warm = int(argv[argv.index("--warmup") + 1]) if "--warmup" in argv else 126
    min_eval = int(argv[argv.index("--min-eval") + 1]) if "--min-eval" in argv else 252
    if not CANON_CSV.exists():
        sys.exit(f"FATAL: {CANON_CSV} missing — run cross_walls.py first")
    rows = list(csv.DictReader(CANON_CSV.open(newline="")))
    sessions = [r["session"] for r in rows]
    futs = {s: load_futures(s) for s in sessions}
    n = len(rows)

    def fwd_ret(i, h, A, B):
        if i + h >= n:
            return None
        s0, s1 = sessions[i], sessions[i + h]
        out = 0.0
        for c, sign in ((A, 1.0), (B, -1.0)):
            m0, m1 = futs[s0].get(c, {}), futs[s1].get(c, {})
            k = front_contract(m0, s0)
            if not k or k not in m1 or m0[k] <= 0 or m1[k] <= 0:
                return None
            out += sign * math.log(m1[k] / m0[k])
        return out

    ic5, ic21, strong_hits, days_used = [], [], [], []
    for i, r in enumerate(rows):
        if i < warm:
            continue
        xs, y5, y21, strong = [], [], [], []
        for x in CROSSES:
            if r.get(f"{x}_eligible") != "1":
                continue
            gap = fnum(r.get(f"{x}_gap"))
            if gap is None or gap == 0:
                continue
            A, B = x[:3], x[3:]
            f5 = fwd_ret(i, H_PRIMARY, A, B)
            if f5 is None:
                continue
            f21 = fwd_ret(i, H_SECONDARY, A, B)
            xs.append(gap)
            y5.append(f5)
            y21.append(f21)
            st = fnum(r.get(f"{x}_strength"))
            if st is not None and st >= STRONG_PCT:
                strong.append(1.0 if (gap > 0) == (f5 > 0) else 0.0)
        if len(xs) >= MIN_CROSSES_DAY:
            ic = spearman(xs, y5)
            if ic is not None:
                ic5.append(ic)
                days_used.append(r["session"])
            if all(v is not None for v in y21):
                ic = spearman(xs, y21)
                if ic is not None:
                    ic21.append(ic)
            strong_hits.extend(strong)

    n_eval = len(ic5)
    total = n
    print("=" * 78)
    print(f"CROSS WALLS · GATE CW-1 · sessions={total} · warm-up={warm} · evaluable days (5D)={n_eval}")
    print("=" * 78)
    status = "ELIGIBLE FOR VERDICT" if (total >= warm + min_eval and n_eval >= min_eval) else \
        f"RESEARCH · {total}/{warm + min_eval} sessions — no verdict allowed"
    print("STATUS:", status)
    if n_eval == 0:
        print("no evaluable days yet (need warm-up + 5 sessions of forward data)")
        return
    m5 = sum(ic5) / n_eval
    ci5 = block_bootstrap_mean(ic5)
    print(f"IC_5D  mean {m5:+.4f}  n={n_eval}  block-bootstrap CI95 {ci5 if ci5 else 'n/a (< 21 days)'}")
    if ic21:
        m21 = sum(ic21) / len(ic21)
        print(f"IC_21D mean {m21:+.4f}  n={len(ic21)}  (veto if < 0)")
    else:
        m21 = None
    if strong_hits:
        hr = sum(strong_hits) / len(strong_hits)
        ci_h = block_bootstrap_mean(strong_hits, block=min(BLOCK, max(2, len(strong_hits) // 4)))
        print(f"strong hit rate {100 * hr:.1f} %  n={len(strong_hits)}  CI95 {ci_h}")
    else:
        hr, ci_h = None, None
    print("incrementality vs BREADTH/POS: PENDING (needs TradingView exports)")
    if status.startswith("RESEARCH"):
        print("\n>> interim numbers only. The pre-registration forbids a verdict before "
              f"{warm + min_eval} sessions. Do not act on them.")
        return
    ok_ic = m5 >= IC_PASS and ci5 and ci5[0] > 0
    ok_hit = hr is not None and hr >= HIT_PASS and ci_h and ci_h[0] > 0.50
    veto = m21 is not None and m21 < 0
    verdict = "PASS (pending incrementality)" if (ok_ic and ok_hit and not veto) else "FAIL"
    print("\nVERDICT:", verdict, "| IC ok" if ok_ic else "| IC fail", "| hit ok" if ok_hit else "| hit fail",
          "| VETO 21D" if veto else "")


if __name__ == "__main__":
    main(sys.argv[1:])

#!/usr/bin/env python3
# =============================================================================
# wall_behaviour.py — v1.0 · "PIN or THROUGH": what the price did near a wall
# Companion of Cross Walls v2 (acta CW-2 §6). DIAGNOSTIC ONLY — never votes,
# never shown as direction. Registered from day 1 so that a future CW-3 can be
# judged on data that was collected BEFORE anyone looked at CW-2's verdict.
#
# Language rule (frozen, from the 3rd triangulation round):
#   DESTINO  = the price walks towards far walls  -> that is v2's hypothesis,
#              tested by gate_cross_walls.py, NOT here.
#   PIN      = the future stays around the wall   -> what long-gamma hedging
#              would predict (not identified: we only see the price).
#   THROUGH  = the future settles on the other side and stays there.
# This layer measures PIN vs THROUGH once the future is ALREADY near a wall.
# It cannot sign the dealer's gamma, and it never validates DESTINO.
#
# Per (session, currency, wall) it records:
#   proxy 1  near = |d| <= NEAR_PCT; outcome after N sessions (settlement to
#            settlement, never intraday): THROUGH / PIN / AWAY; dte_short flag
#            (DTE < 5, mechanical expiry pin).
#   proxy 2  dOI_c, dOI_p vs the previous session of the same (front, K):
#            "OI rising / falling / flat", never "alive / dead" as a verdict
#            (DEAD is cross_walls' selection filter, restated here as a flag).
#   proxy 3  smile_resid = IV(K) - IV_interp(K) in vol points, where the
#            interpolation uses total variance w = IV^2 T in m = ln(K/F) between
#            the two nearest VALID strikes on each side EXCLUDING K itself (and
#            no extrapolation). NA when a neighbour is missing.
# Proxy 4 (signed GEX by convention) is NOT computed: not identifiable in FX.
#
# INPUT : same canonical options data as cross_walls.py (imports it)
# OUTPUT: data/cross_walls/walls.csv (one row per session x currency x wall)
# stdlib only.
# =============================================================================
import csv, math, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cross_walls as cw   # noqa: E402

OUT = Path(os.environ.get("G8_CW_WALLS", "data/cross_walls/walls.csv"))
NEAR_PCT = 0.5      # proxy 1: "near" when |d| <= 0.5 % of F
N_FWD = 5           # sessions ahead for the outcome
DTE_SHORT = 5       # expiry pin zone


def smile_residual(vols, K, F, T):
    """IV(K) minus IV interpolated in total variance between the nearest valid
    strikes on each side, excluding K. vols = [(K, iv, right)]."""
    m0 = math.log(K / F)
    ivK = next((iv for k, iv, _ in vols if k == K), None)
    if ivK is None:
        return None
    left = [(math.log(k / F), iv) for k, iv, _ in vols if k < K]
    right = [(math.log(k / F), iv) for k, iv, _ in vols if k > K]
    if not left or not right:
        return None
    mL, ivL = max(left)
    mR, ivR = min(right)
    wL, wR = ivL * ivL * T, ivR * ivR * T
    w = wL + (wR - wL) * (m0 - mL) / (mR - mL)
    if w <= 0:
        return None
    return 100.0 * (ivK - math.sqrt(w / T))


def main():
    sessions = sorted(p.name for p in cw.CANONICAL.iterdir() if p.is_dir() and len(p.name) == 10)
    sofr = cw.load_sofr()
    oi_hist = {c: {} for c in cw.ORDER}
    prev = {}                                   # (ccy, front, K) -> (c, p)
    F_by = {}                                   # (session, ccy) -> F
    recs = []
    for s in sessions:
        day = cw.load_session(cw.CANONICAL / s, s)
        r, _, _ = cw.sofr_on(sofr, s)
        for c in cw.ORDER:
            d = day.get(c)
            if not d:
                continue
            sf = cw.surface(d, c, s, r if r is not None else 0.0)
            F_by[(s, c)] = d["F"]
            dead = cw.update_dead(oi_hist[c], d)
            walls, chain_oi, _ = cw.select_walls(d["chain"], set())   # DEAD flagged, not removed here
            for k, o, cc, pp, seat in walls:
                dpct = 100.0 * (k / d["F"] - 1.0)
                pc = prev.get((c, d["front"], k))
                recs.append({"session": s, "ccy": c, "front": d["front"], "dte": sf["dte"],
                             "dte_short": int(sf["dte"] < DTE_SHORT), "K": k, "F": d["F"],
                             "side_native": "below" if k < d["F"] else "above",
                             "oi": int(o), "c": int(cc), "p": int(pp), "seat": round(seat, 3),
                             "d_pct": round(dpct, 4), "dead": int(k in dead),
                             "dOI_c": (int(cc - pc[0]) if pc else None), "dOI_p": (int(pp - pc[1]) if pc else None),
                             "near": int(abs(dpct) <= NEAR_PCT),
                             "smile_resid": (round(x, 3) if (x := smile_residual(sf["vols"], k, d["F"], sf["T"])) is not None else None),
                             "outcome": None, "d_fwd_pct": None})
            for k, e in d["chain"].items():
                prev[(c, d["front"], k)] = (e["c"], e["p"])
    # outcomes: settlement N_FWD sessions later, same currency
    idx = {s: i for i, s in enumerate(sessions)}
    for rec in recs:
        i = idx[rec["session"]]
        if i + N_FWD >= len(sessions):
            continue
        F1 = F_by.get((sessions[i + N_FWD], rec["ccy"]))
        if not F1:
            continue
        d1 = 100.0 * (rec["K"] / F1 - 1.0)
        rec["d_fwd_pct"] = round(d1, 4)
        if rec["near"]:
            crossed = (rec["d_pct"] > 0) != (d1 > 0)
            rec["outcome"] = "THROUGH" if crossed else ("PIN" if abs(d1) <= NEAR_PCT else "AWAY")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(recs[0].keys()))
        w.writeheader()
        for rec in recs:
            w.writerow({k: ("" if v is None else v) for k, v in rec.items()})
    near = [x for x in recs if x["near"] and x["outcome"]]
    tally = {}
    for x in near:
        tally[x["outcome"]] = tally.get(x["outcome"], 0) + 1
    print(f"OK: {OUT} · {len(recs)} wall-days · near-wall episodes with outcome {len(near)} · {tally} "
          f"(diagnostic only; no reading before N per cell)")


if __name__ == "__main__":
    main()

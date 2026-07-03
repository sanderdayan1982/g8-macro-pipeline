#!/usr/bin/env python3
# =============================================================================
# build_options_summary.py — v1.0 · Options Surface -> dashboard JSON
# Reads canonical sessions (data/options/canonical/YYYY-MM-DD/{ROOT}.csv),
# computes per-ccy front-chain metrics per session (same math as
# viability_study v2.2: front = first expiry STRICTLY after session,
# ref = front future settle, MIN_OI=100, MIN_STRIKES=5, band ±3%),
# aggregates Gate 0 percentages, and writes data/OPTIONS_SURFACE.json.
# stdlib only.
# =============================================================================
import csv, json, os, sys
from pathlib import Path
from datetime import datetime, timezone

CANONICAL = Path(os.environ.get("G8_OPT_OUT_DIR", "data/options/canonical"))
OUT_JSON = Path(os.environ.get("G8_OPT_SUMMARY", "data/OPTIONS_SURFACE.json"))
MIN_OI, MIN_STRIKES, BAND_PCT = 100, 5, 0.03
COVERAGE = 0.80
CCY = {"EUR": ("EUU","6E"), "JPY": ("JPU","6J"), "GBP": ("GBU","6B"),
       "AUD": ("ADU","6A"), "CAD": ("CAU","6C"), "CHF": ("CHU","6S")}
N_WALLS = 5

def read_rows(p):
    with p.open() as f:
        return list(csv.DictReader(f))

def fnum(x):
    try: return float(x)
    except (TypeError, ValueError): return None

def session_metrics(day_dir, session):
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
        ref = fnum(futs[0]["settle"])
        opts = [r for r in read_rows(fo) if r["type"] == "OPT"]
        expiries = sorted({r["expiry"] for r in opts if r["expiry"] > session})
        if not expiries:
            continue
        front = expiries[0]
        chain = {}
        for r in opts:
            if r["expiry"] != front: continue
            k = fnum(r["strike"])
            if k is None: continue
            e = chain.setdefault(k, {"c": 0.0, "p": 0.0})
            oi = fnum(r["oi"]) or 0.0
            if r["right"] == "C": e["c"] += oi
            else: e["p"] += oi
        n_call = sum(1 for k, e in chain.items() if k >= ref and e["c"] >= MIN_OI)
        n_put  = sum(1 for k, e in chain.items() if k <= ref and e["p"] >= MIN_OI)
        band = {k: e for k, e in chain.items()
                if ref*(1-BAND_PCT) <= k <= ref*(1+BAND_PCT)}
        b_c = sum(1 for e in band.values() if e["c"] >= MIN_OI)
        b_p = sum(1 for e in band.values() if e["p"] >= MIN_OI)
        walls = sorted(chain.items(), key=lambda kv: -(kv[1]["c"]+kv[1]["p"]))[:N_WALLS]
        out[ccy] = {
            "ref": ref, "front": front,
            "n_call": n_call, "n_put": n_put,
            "pata": n_call >= MIN_STRIKES and n_put >= MIN_STRIKES,
            "band": b_c >= 2 and b_p >= 2,
            "oi_front": round(sum(e["c"]+e["p"] for e in chain.values())),
            "walls": [{"k": k, "oi": round(e["c"]+e["p"]),
                       "c": round(e["c"]), "p": round(e["p"])} for k, e in walls],
        }
    return out

def main():
    if not CANONICAL.exists():
        sys.exit(f"FATAL: {CANONICAL} not found")
    sessions = sorted(p.name for p in CANONICAL.iterdir()
                      if p.is_dir() and len(p.name) == 10)
    if not sessions:
        sys.exit("FATAL: no canonical sessions")
    history = {s: session_metrics(CANONICAL / s, s) for s in sessions}
    gate0 = {}
    for ccy in CCY:
        obs = [h[ccy] for h in history.values() if ccy in h]
        n = len(obs)
        if n == 0:
            gate0[ccy] = {"days": 0, "verdict": "SIN_DATOS"}; continue
        pa = sum(o["pata"] for o in obs) / n
        pb = sum(o["band"] for o in obs) / n
        v = "CLEAN" if (pa >= COVERAGE and pb >= COVERAGE) else \
            "PROXY" if (pa >= 0.5 or pb >= 0.5) else "NA"
        gate0[ccy] = {"days": n, "pata_pct": round(pa*100),
                      "band_pct": round(pb*100), "verdict": v}
    gate0["NZD"] = {"days": 0, "verdict": "NA", "note": "censo VOI: mercado muerto"}
    latest = sessions[-1]
    doc = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Databento GLBX.MDP3 (licensed CME) · collector v1.1.0",
        "semantics": "settle+OI of session T · front = first expiry > session",
        "latest_session": latest,
        "sessions_total": len(sessions),
        "gate0": gate0,
        "latest": history[latest],
        "prev": history[sessions[-2]] if len(sessions) > 1 else {},
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(doc, indent=1))
    print(f"OK: {OUT_JSON} · latest={latest} · sessions={len(sessions)}")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
nzd_tp_synth.py — v1.1 (2026-09-13)
Materialises the NZD 10Y term-premium PROXY that the dashboard already computes in
§01 (AUD-anchored): TP_NZD = TP_AUD + 0.4·(NOM_NZD − NOM_AUD).
Writes data/ACM_G8_NZD.csv with the same schema as the ACM outputs
(DATE,Y10_FIT,RNY10,TP10) so §04 / §00 can show NZD like the other currencies.

Honesty: this is NOT an ACM fit. NZ has no zero-coupon curve with 6–120m tenors
in the pipeline (RBNZ B2 gives 1Y/2Y/5Y/10Y only), so a K-factor ACM would be
fiction. The proxy formula is the frozen one from the dashboard (index.html
proxyTP) — nothing new is calibrated here. Quality tag: SYNTH.

Inputs:  data/ACM_G8_AUD.csv (DATE,Y10_FIT,RNY10,TP10)
         data/RY_G8_AUD.csv  (NOM10 daily; falls back to AUD Y10_FIT)
         data/NZD_BOND_10Y.csv (Date,Value — RBNZ B2 via local fetch)
Output:  data/ACM_G8_NZD.csv  (only dates where both AUD ACM and NZD B2 exist)
Exit 0 always for the workflow; prints a loud line if inputs are missing.

v1.1: FALLBACK ONLY. If data/ACM_G8_NZD.csv already holds a REAL ACM fit (written by
acm_g8.py NZD — no QUALITY column) that is at least as fresh as NZD_BOND_10Y.csv
(≤ 3 rows behind), this script does nothing and says so. The proxy is written only
when the real fit is absent, stale, or itself a SYNTH file.
"""
import csv, os, sys

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
BETA = 0.4                      # frozen — same as index.html proxyTP()


def read(path, datekey, cols):
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8", errors="ignore") as fh:
        rows = [l for l in fh if l.strip() and not l.startswith("#")]
    for r in csv.DictReader(rows):
        d = (r.get(datekey) or "").strip().replace("-", "")[:8]
        if len(d) != 8:
            continue
        try:
            out[d] = [float(r[c]) for c in cols]
        except (KeyError, ValueError, TypeError):
            continue
    return out


def real_acm_is_fresh(nz_dates):
    """True if data/ACM_G8_NZD.csv is a real ACM output (no QUALITY column) and fresh."""
    p = os.path.join(DATA, "ACM_G8_NZD.csv")
    if not os.path.exists(p):
        return False
    with open(p, encoding="utf-8", errors="ignore") as fh:
        head = fh.readline().strip().upper()
        last = None
        for l in fh:
            if l.strip():
                last = l.split(",")[0].strip()
    if "QUALITY" in head or not last:
        return False
    behind = [d for d in nz_dates if d > last]
    return len(behind) <= 3


def main():
    nz_probe = read(os.path.join(DATA, "NZD_BOND_10Y.csv"), "Date", ["Value"])
    if nz_probe and real_acm_is_fresh(sorted(nz_probe)):
        print("[nzd_tp_synth] real ACM_G8_NZD.csv present and fresh (acm_g8.py NZD) — proxy not written")
        return 0
    acm = read(os.path.join(DATA, "ACM_G8_AUD.csv"), "DATE", ["Y10_FIT", "RNY10", "TP10"])
    ry = read(os.path.join(DATA, "RY_G8_AUD.csv"), "DATE", ["NOM10"])
    nz = read(os.path.join(DATA, "NZD_BOND_10Y.csv"), "Date", ["Value"])
    if not acm or not nz:
        print("[nzd_tp_synth] LOUD: missing inputs (ACM_G8_AUD=%d rows, NZD_BOND_10Y=%d rows) — nothing written"
              % (len(acm), len(nz)))
        return 0
    acm_dates = sorted(acm)
    out, ai = [], 0
    for d in sorted(nz):
        # last AUD ACM row on or before d (ffill ≤ 5 rows back handled by ordering)
        while ai + 1 < len(acm_dates) and acm_dates[ai + 1] <= d:
            ai += 1
        if acm_dates[ai] > d:
            continue
        y_aud, rny_aud, tp_aud = acm[acm_dates[ai]]
        nom_aud = ry[d][0] if d in ry else y_aud
        nom_nzd = nz[d][0]
        tp_nzd = tp_aud + BETA * (nom_nzd - nom_aud)
        out.append((d, nom_nzd, nom_nzd - tp_nzd, tp_nzd))
    if not out:
        print("[nzd_tp_synth] LOUD: no overlapping dates — nothing written")
        return 0
    p = os.path.join(DATA, "ACM_G8_NZD.csv")
    with open(p, "w", encoding="utf-8") as fh:
        # No '#' comment line: the §04 loader parses the first line as header.
        # QUALITY column carries the honesty tag; extra columns are ignored by all parsers.
        fh.write("DATE,Y10_FIT,RNY10,TP10,QUALITY\n")
        for d, y, rny, tp in out:
            fh.write("%s,%.4f,%.4f,%.4f,SYNTH_AUD_ANCHOR\n" % (d, y, rny, tp))
    print("[nzd_tp_synth] wrote %s: %d rows, latest %s TP %.4f (SYNTH)" % (p, len(out), out[-1][0], out[-1][3]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

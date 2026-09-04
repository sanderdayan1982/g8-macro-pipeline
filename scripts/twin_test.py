#!/usr/bin/env python3
"""
G8 PORT — twin-test (D9).  Compares the discrete STATE emitted by the Pine script
(TradingView "Export chart data" CSV, never translated column names) against the
Python generator's state history, bar by bar, for 4–6 weeks.

Inputs
  data/twin/<script>_pine.csv      TradingView export.  Columns: time, <ccy>_state ... (as in Pine plot titles)
  data/twin/<script>_py_history.csv appended by the workflow after every run:
                                    data_date, ccy_or_pair, state
Output
  data/twin/<script>_twin.json     {status, started, weeks_elapsed, state_discrepancy_pct, acta, per_key}

Rule: discrepancy > 10 %  ->  status FAILED  ->  acta in gates/actas/  ->  RECALIBRATING.
The generator reads this file; meta.funnel_eligible is true only when status == PASSED
and weeks_elapsed >= 4.
"""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TWIN = ROOT / "data" / "twin"
MIN_WEEKS = 4
MAX_DISCREPANCY_PCT = 10.0


# ── adapters: turn a raw TradingView export into (data_date, key, state_pine) ──
def _generic(pine):
    state_cols = [c for c in pine.columns if c.endswith("_state")]
    long = pine.melt(id_vars=["data_date"], value_vars=state_cols, var_name="key", value_name="state_pine")
    long["key"] = long["key"].str.replace("_state", "", regex=False)
    return long


def _pos_g8(pine):
    """POS-G8 v1.1.3 exports LANES, not states: plot = lane + clip(z * 0.9 / z_cap), z_cap = 3.
    z = (plot - lane) / 0.3 ; state via the frozen thresholds (fx 1.5/2.0, XAU 1.68/2.29, XAG 1.61/2.25)."""
    LANE = {"USD°": 16, "EUR": 14, "GBP": 12, "JPY": 10, "AUD": 8, "NZD": 6, "CAD": 4, "CHF": 2, "XAU": 18, "XAG": 20}
    THR = {"XAU": (1.68, 2.29), "XAG": (1.61, 2.25)}

    def st(z, w, e):
        if pd.isna(z):
            return "NA"
        return "CROWD LONG" if z >= e else "STRETCH+" if z >= w else "CROWD SHORT" if z <= -e else "STRETCH-" if z <= -w else "NEUTRAL"

    recs = []
    for col, lane in LANE.items():
        if col not in pine.columns:
            continue
        key = col.rstrip("°")
        w, e = THR.get(key, (1.5, 2.0))
        z = (pine[col].astype(float) - lane) / 0.3
        recs.append(pd.DataFrame({"data_date": pine["data_date"], "key": key, "state_pine": [st(v, w, e) for v in z]}))
    long = pd.concat(recs, ignore_index=True)
    # A COT report dated Tuesday R is released Friday R+3 and reaches the TradingView mirror
    # the following days; the Pine bar on R+9 (next Thursday) is stable and unambiguous.
    long["report_date"] = long["data_date"] - pd.Timedelta(days=9)
    return long


def _ffva_g8(pine):
    """FFVA-G8 v1.3.2 exports z_<CCY> (ΔOI Z-252 capped ±3). Bucket with z_flow = 0.25."""
    recs = []
    for col in [c for c in pine.columns if c.startswith("z_")]:
        key = col[2:]
        z = pd.to_numeric(pine[col], errors="coerce")
        b = ["NA" if pd.isna(v) else "Z+" if v > 0.25 else "Z-" if v < -0.25 else "Z0" for v in z]
        recs.append(pd.DataFrame({"data_date": pine["data_date"], "key": key, "state_pine": b}))
    return pd.concat(recs, ignore_index=True)


ADAPTERS = {"pos_g8": _pos_g8, "ffva_g8": _ffva_g8}


def main(script):
    script = script.lower()
    if not script.endswith(("_2y", "_10y", "_g8")):
        script += "_g8"          # POS -> pos_g8 (state file stems)
    pine = pd.read_csv(TWIN / ("%s_pine.csv" % script))
    py = pd.read_csv(TWIN / ("%s_py_history.csv" % script), parse_dates=["data_date"], keep_default_na=False)
    pine["data_date"] = pd.to_datetime(pine["time"], unit="s", errors="coerce").fillna(pd.to_datetime(pine["time"], errors="coerce")).dt.normalize()

    long = ADAPTERS[script](pine) if script in ADAPTERS else _generic(pine)
    py = py.rename(columns={"ccy_or_pair": "key", "state": "state_py"})
    if "report_date" in long.columns:
        # keep, per (report_date, key), the Pine bar closest to the R+9 anchor
        long = long.sort_values("data_date")
        py["report_date"] = py["data_date"]
        m = pd.merge_asof(py.sort_values("report_date"), long.drop(columns=["data_date"]).sort_values("report_date"),
                          on="report_date", by="key", direction="nearest", tolerance=pd.Timedelta(days=3))
        m = m.dropna(subset=["state_pine"])
    else:
        m = long.merge(py, on=["data_date", "key"], how="inner")
    if m.empty:
        raise SystemExit("twin-test %s: no overlapping bars — check export and history" % script)

    # Python NA from warm-up (not enough history) is not a logic discrepancy: reported apart.
    warm = m["state_py"].astype(str) == "NA"
    py_na_pct = float(warm.mean() * 100.0)
    c = m[~warm]
    if c.empty:
        raise SystemExit("twin-test %s: all Python bars are NA (warm-up) — nothing to compare yet" % script)
    c = c.assign(mismatch=c["state_pine"].astype(str) != c["state_py"].astype(str))
    disc = float(c["mismatch"].mean() * 100.0)
    per_key = c.groupby("key")["mismatch"].mean().mul(100).round(2).to_dict()

    # D9: the parallel-run clock starts at the FIRST twin-test run, not at the first bar
    prev_p = TWIN / ("%s_twin.json" % script)
    prev = json.loads(prev_p.read_text()) if prev_p.exists() else {}
    started = pd.Timestamp(prev["started"]) if prev.get("started") else pd.Timestamp.now("UTC").normalize().tz_localize(None)
    weeks = float((pd.Timestamp.now("UTC").normalize().tz_localize(None) - started).days / 7.0)
    if disc > MAX_DISCREPANCY_PCT:
        status = "FAILED"
    elif weeks >= MIN_WEEKS:
        status = "PASSED"
    else:
        status = "RUNNING"
    out = {"status": status, "started": started.strftime("%Y-%m-%d"), "weeks_elapsed": round(weeks, 1),
           "state_discrepancy_pct": round(disc, 2), "acta": prev.get("acta"), "per_key": per_key,
           "bars_compared": int(len(c)), "py_warmup_na_pct": round(py_na_pct, 2),
           "compared_from": c["data_date"].min().strftime("%Y-%m-%d"),
           "compared_to": c["data_date"].max().strftime("%Y-%m-%d")}
    prev_p.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    return 1 if status == "FAILED" else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: twin_test.py <SCRIPT>")
    sys.exit(main(sys.argv[1]))

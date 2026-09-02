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


def main(script):
    script = script.lower()
    pine = pd.read_csv(TWIN / ("%s_pine.csv" % script))
    py = pd.read_csv(TWIN / ("%s_py_history.csv" % script), parse_dates=["data_date"])
    pine["data_date"] = pd.to_datetime(pine["time"], unit="s", errors="coerce").fillna(pd.to_datetime(pine["time"], errors="coerce")).dt.normalize()

    state_cols = [c for c in pine.columns if c.endswith("_state")]
    long = pine.melt(id_vars=["data_date"], value_vars=state_cols, var_name="key", value_name="state_pine")
    long["key"] = long["key"].str.replace("_state", "", regex=False)
    py = py.rename(columns={"ccy_or_pair": "key", "state": "state_py"})
    m = long.merge(py, on=["data_date", "key"], how="inner")
    if m.empty:
        raise SystemExit("twin-test %s: no overlapping bars — check export and history" % script)

    m["mismatch"] = m["state_pine"].astype(str) != m["state_py"].astype(str)
    disc = float(m["mismatch"].mean() * 100.0)
    per_key = m.groupby("key")["mismatch"].mean().mul(100).round(2).to_dict()
    started = m["data_date"].min()
    weeks = float((m["data_date"].max() - started).days / 7.0)

    prev_p = TWIN / ("%s_twin.json" % script)
    prev = json.loads(prev_p.read_text()) if prev_p.exists() else {}
    if disc > MAX_DISCREPANCY_PCT:
        status = "FAILED"
    elif weeks >= MIN_WEEKS:
        status = "PASSED"
    else:
        status = "RUNNING"
    out = {"status": status, "started": started.strftime("%Y-%m-%d"), "weeks_elapsed": round(weeks, 1),
           "state_discrepancy_pct": round(disc, 2), "acta": prev.get("acta"), "per_key": per_key,
           "bars_compared": int(len(m))}
    prev_p.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    return 1 if status == "FAILED" else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: twin_test.py <SCRIPT>")
    sys.exit(main(sys.argv[1]))

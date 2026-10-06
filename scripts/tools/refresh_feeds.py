#!/usr/bin/env python3
"""Additional official-feed collection windows; no paid collectors or model fitting."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
GROUPS = {
    "ASIA": [("tona", "fetch_tona.py"), ("aonia", "fetch_aonia.py"),
             ("aud_bills", "fetch_aud_bills.py"), ("jpy_bills", "fetch_jpy_bills.py"), ("jpy_real", "fetch_jpy_real.py")],
    "EU": [("estr", "fetch_estr.py"), ("sonia", "fetch_sonia.py"),
           ("eur_bills", "fetch_eur_bills.py"), ("gbp_bills", "fetch_gbp_bills.py"),
           ("eur_real", "fetch_eur_real.py"), ("real_yields", "real_yields_g8.py", "GBP"),
           ("chf_cpi", "fetch_chf_cpi.py")],              # acta P-10: IPC suizo (BFS publica ~06:30Z)
    "US": [("sofr", "fetch_sofr.py"), ("corra", "fetch_corra.py"),
           ("cad_bills", "fetch_cad_bills.py"), ("floors", "fetch_floor_spreads.py"),
           ("acm_tp", "fetch_acm.py"), ("bis_gb", "fetch_bis_policy.py", "GB"),
           ("bis_jp", "fetch_bis_policy.py", "JP"), ("bis_ch", "fetch_bis_policy.py", "CH"),
           ("bis_au", "fetch_bis_policy.py", "AU"), ("bis_ocr", "fetch_bis_ocr.py")],
    "LATE": [("us_bills", "fetch_us_bills.py"), ("floors", "fetch_floor_spreads.py"),
             ("real_yields", "real_yields_g8.py", "USD", "AUD"),
             # acta P-9: §01/§04 siguen a sus entradas el mismo día (antes esperaban al Daily, que GitHub lanza ~00:35Z).
             # Mismo script y mismos argumentos que el Daily; solo cambia la hora. Después, la estimación AUD (P-8).
             ("acm_g8", "acm_g8.py", "USD", "EUR", "JPY", "GBP", "CAD", "AUD"),
             ("acm_nzd", "acm_g8.py", "NZD"), ("acm_chf", "acm_g8.py", "CHF"),
             ("aud_nowcast", "aud_nowcast.py")],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("group", choices=list(GROUPS) + ["ALL"])
    a = ap.parse_args()
    os.environ.setdefault("G8_JOB_DEADLINE_EPOCH", str(time.time() + 20 * 60))
    os.environ.setdefault("G8_JOB_RESERVE_S", "300")
    os.environ.setdefault("G8_STEP_GUARD", "data")
    os.environ.setdefault("G8_GUARD_SKIP", "options,futures")
    os.environ.setdefault("G8_JOB", "intraday")
    rc = 0
    jobs = GROUPS[a.group] if a.group != "ALL" else list(dict.fromkeys(job for group in GROUPS.values() for job in group))
    guard = str(ROOT / "scripts/tools/g8step.py")
    for name, script, *args in jobs:
        result = subprocess.call([sys.executable, guard, "--name", name, "--cap", "180", "--", sys.executable,
                                  str(ROOT / "scripts" / script)] + args, cwd=ROOT)
        rc = max(rc, bool(result))
    ledger = subprocess.call([sys.executable, guard, "--ledger", "intraday"], cwd=ROOT)
    return max(rc, bool(ledger))


if __name__ == "__main__":
    sys.exit(main())

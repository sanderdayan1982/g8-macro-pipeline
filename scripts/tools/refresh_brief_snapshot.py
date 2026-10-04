#!/usr/bin/env python3
"""Rebuild dashboard snapshot without sending messages or advancing delivery state."""
import copy
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import dashboard_alerts as D


def main():
    state = copy.deepcopy(D.load_state())
    lines = []
    for fn in (D.check_floors,D.check_policy,D.check_tp,D.check_vs_usd,D.check_real_vs_usd,
               D.check_s01b,D.check_metals,D.check_walls,D.check_cot,D.check_factor,D.check_dqm,D.check_policy_coherence):
        fn(state,lines)
    D.build_brief(state,[])
    print('Brief rebuilt; notification delivery state unchanged')


if __name__ == '__main__':
    main()

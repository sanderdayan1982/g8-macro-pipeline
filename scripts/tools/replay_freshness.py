#!/usr/bin/env python3
"""replay_freshness.py — reproduce el motor F3 (y el motor actual) sobre el historial de git, instante a instante.

    python scripts/tools/replay_freshness.py --ref ed9ed64 --start 2026-09-09T00:00:00Z --end 2026-09-24T23:00:00Z \
        [--step-min 60] [--files NZD_BOND_10Y.csv,TONA.csv] [--json salida.json]

Salida: por fichero, las TRANSICIONES de estado (instante UTC, estado, observaciones que faltan) y, por feed del
registro, las del motor actual; más el recuento de casos en que el motor nuevo es más permisivo (ampliaciones).
Solo lee git; no escribe en el repositorio."""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import freshness_report as FRR  # noqa: E402

UTC = timezone.utc


def replay(root, ref, start, end, step_min=60, files=None, with_current=True):
    src = FRR.GitSource(root, ref, start)
    t = start
    trans, cur_trans, amp = {}, {}, []
    last, last_cur = {}, {}
    while t <= end:
        src.at = t
        rep = FRR.build(root, t, src)
        for r in rep["outputs"]:
            if files and r["file"] not in files:
                continue
            key = (r["state"], tuple(r.get("missing", [])))
            if last.get(r["file"]) != key:
                trans.setdefault(r["file"], []).append({"at": t.strftime("%Y-%m-%dT%H:%MZ"), "state": r["state"],
                                                         "missing": r.get("missing", []),
                                                         "flags": r.get("flags", [])})
                last[r["file"]] = key
        if with_current:
            cmp_ = FRR.compare(rep, FRR.current_engine(root, t, src))
            for x in cmp_["rows"]:
                if files and x["file"] not in files:
                    continue
                if last_cur.get(x["feed_id"]) != x["current"]:
                    cur_trans.setdefault(x["feed_id"], []).append({"at": t.strftime("%Y-%m-%dT%H:%MZ"),
                                                                   "state": x["current"]})
                    last_cur[x["feed_id"]] = x["current"]
                if x["kind"] == "AMPLIACION_A_DECIDIR":
                    amp.append(dict(x, at=t.strftime("%Y-%m-%dT%H:%MZ")))
        t += timedelta(minutes=step_min)
    return {"ref": ref, "start": start.strftime("%Y-%m-%dT%H:%MZ"), "end": end.strftime("%Y-%m-%dT%H:%MZ"),
            "step_min": step_min, "transitions": trans, "current_engine_transitions": cur_trans,
            "ampliaciones": amp}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=FRR.ROOT)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--step-min", type=int, default=60)
    ap.add_argument("--files")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    res = replay(a.root, a.ref, FRR._utc(a.start), FRR._utc(a.end), a.step_min,
                 set(a.files.split(",")) if a.files else None)
    txt = json.dumps(res, ensure_ascii=False, indent=1, sort_keys=True)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            fh.write(txt + "\n")
    else:
        print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())

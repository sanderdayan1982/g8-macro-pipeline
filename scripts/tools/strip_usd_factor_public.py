#!/usr/bin/env python3
"""strip_usd_factor_public.py — DATOS-02 C (orden Sander t319u, opción a).

Quita de data/USD_FACTOR.json las correlaciones derivadas de futuros CME/ICE
(diag.twins.futures, con by_ccy) antes de cualquier git add/commit del job.
El bloque se sustituye por {"available": false, "note": "privado / licencia"}.
Nada más cambia: el resto del JSON se reescribe con el mismo formato que
usd_factor.py (json.dumps indent=1, ensure_ascii=False). Idempotente.
usd_factor.py no se toca (hash congelado).

    python3 scripts/tools/strip_usd_factor_public.py [--path data/USD_FACTOR.json]

No imprime valores: solo si cambió o no.
"""
import argparse
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT = os.path.join(ROOT, "data", "USD_FACTOR.json")
PUBLIC_FUTURES = {"available": False, "note": "privado / licencia"}


def strip(data):
    """Devuelve (data, changed). Solo toca diag.twins.futures."""
    diag = data.get("diag") if isinstance(data, dict) else None
    twins = diag.get("twins") if isinstance(diag, dict) else None
    if not isinstance(twins, dict) or "futures" not in twins:
        return data, False
    if twins["futures"] == PUBLIC_FUTURES:
        return data, False
    twins["futures"] = dict(PUBLIC_FUTURES)
    return data, True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--path", default=DEFAULT)
    args = ap.parse_args(argv)
    if not os.path.exists(args.path):
        print("strip_usd_factor_public: no existe %s; nada que hacer" % os.path.relpath(args.path, ROOT))
        return 0
    with open(args.path, encoding="utf-8") as fh:
        data = json.load(fh)
    data, changed = strip(data)
    if changed:
        with open(args.path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=1, ensure_ascii=False))
    print("strip_usd_factor_public: %s" % ("futures twin retirado" if changed else "sin cambios"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

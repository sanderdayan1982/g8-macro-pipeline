#!/usr/bin/env python3
"""
G8 Macro Pipeline — book_risk.py  v1.0  (2026-09-17)
====================================================
Sección §10 · panel LIBRO y tarjeta PRE-TRADE. Capa CONTEXT: identidad
contable + covarianza. Sin voto, sin dirección, sin carry. ACTA_UF1 §C.8-C.9.

Entradas
  data/USD_FACTOR.json          Σ EWMA (diaria), betas, E21, congelados (usd_factor.py)
  data/book.csv                 snapshot de exposiciones lineales FX en nominal USD
                                  id,fecha_snapshot,par,lado,nominal_usd
                                  par = EURJPY o EUR/JPY · lado = +1 largo base / -1 corto
  data/candidate.csv            mismo formato; una fila por operación candidata

Cálculo (q = exposiciones netas de las 7 patas no-USD en USD; Σ diaria; w = 1/7)
  cubos      8 (USD incluido); Σ cubos = 0 por identidad de la representación
  V          = q'Σq          σ = √V
  VaR95 par  = 1.645·σ       VaR95 hist = −cuantil 5 % del P&L hipotético 252 s.
  ES97.5 par = 2.338·σ       (solo canonical/JSON; no es cifra de mesa)
  COLA       |par − hist|/par > 0.30  (convención ACTA_UF1)
  carga      = −q'Σw / (w'Σw)   cuota = (q'Σw)² / (w'Σw · V)   [proyección sobre la cesta]
  Euler      RC_k = q_k'Σq / V  por posición (suman 1; negativas = cobertura)
  estrés     (1) cesta ±1 %: r_i = −β_i·0.01  (2) DOLLAR+: shock = max|f| 2000-2023
             congelado (3) cota σ_max = Σ|q_i|σ_i
  PRE-TRADE  Δcubos · ΔV = 2q_c'Σq + q_c'Σq_c · ΔVaR · ρ = q_c'Σq/√(q_c'Σq_c·V)
             leak β_A−β_B · vol cruce · E21_A−E21_B · Δcuota · P&L escenarios
Snapshots   data/book_snapshots/YYYY-MM-DD.csv (libro del día) y
            data/book_snapshots/forecasts.csv (as_of, σ, VaR par, VaR hist) para
            el backtest de VaR — PENDIENTE hasta ≥ 250 previsiones.
Salida      data/BOOK_RISK.json
"""
from __future__ import annotations

import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

VERSION = "1.0"
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SNAP_DIR = DATA / "book_snapshots"
CCYS = ["EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"]
ALL = ["USD"] + CCYS
Z95, Z975_ES = 1.64485, 2.33780
COLA_THR = 0.30
STALE_SESSIONS = 5
BACKTEST_MIN = 250


def log(m):
    print("book_risk: " + m, flush=True)


def read_positions(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            pair = (row.get("par") or row.get("pair") or "").upper().replace("/", "").strip()
            if len(pair) != 6 or pair[:3] not in ALL or pair[3:] not in ALL or pair[:3] == pair[3:]:
                raise ValueError("par inválido: %r" % row)
            side = float(row.get("lado") or row.get("side") or 0)
            if side not in (1.0, -1.0):
                raise ValueError("lado debe ser +1/-1: %r" % row)
            nom = float(row.get("nominal_usd") or 0)
            if nom <= 0:
                raise ValueError("nominal_usd debe ser > 0: %r" % row)
            out.append({"id": row.get("id") or pair, "date": (row.get("fecha_snapshot") or row.get("date") or "")[:10],
                        "pair": pair, "side": side, "nominal_usd": nom})
    return out


def buckets(pos: list[dict]) -> dict:
    b = {c: 0.0 for c in ALL}
    for p in pos:
        base, quote = p["pair"][:3], p["pair"][3:]
        b[base] += p["side"] * p["nominal_usd"]
        b[quote] -= p["side"] * p["nominal_usd"]
    return b


def qvec(b: dict) -> np.ndarray:
    return np.array([b[c] for c in CCYS])


def qvec_pos(p: dict) -> np.ndarray:
    return qvec(buckets([p]))


def hist_pnl(q: np.ndarray, can: pd.DataFrame) -> np.ndarray:
    R = can[[f"r_{c}" for c in CCYS]].tail(252).values
    return R @ q


def risk_block(q: np.ndarray, S: np.ndarray, w: np.ndarray, can: pd.DataFrame, betas: np.ndarray,
               sd: np.ndarray, shock_frozen: float | None) -> dict:
    V = float(q @ S @ q)
    sig = math.sqrt(V) if V > 0 else 0.0
    vf = float(w @ S @ w)
    qsw = float(q @ S @ w)
    pnl = hist_pnl(q, can)
    var_h = float(-np.quantile(pnl, 0.05)) if len(pnl) >= 100 and V > 0 else None
    var_p = Z95 * sig
    cola = (abs(var_p - var_h) / var_p > COLA_THR) if (var_h is not None and var_p > 0) else None
    out = {"V_daily": V, "sigma_usd": sig, "var95_param": var_p, "var95_hist": var_h,
           "var_diff_usd": (var_p - var_h) if var_h is not None else None, "cola": cola,
           "es975_param": Z975_ES * sig, "n_hist": int(len(pnl)),
           "load_on_basket": (-qsw / vf) if vf > 0 else None,
           "share_on_basket": (qsw * qsw / (vf * V)) if (vf > 0 and V > 0) else None,
           "stress": {"basket_up_1pct": float(-(q * betas).sum() * 0.01),
                      "basket_down_1pct": float((q * betas).sum() * 0.01),
                      "dollar_plus": (float(-(q * betas).sum() * shock_frozen) if shock_frozen else None),
                      "dollar_plus_shock": shock_frozen,
                      "sigma_max_bound": float((np.abs(q) * sd).sum())}}
    return out


def main(argv):
    uf = json.loads((DATA / "USD_FACTOR.json").read_text())
    can = pd.read_csv(DATA / "usd_factor" / "canonical.csv", index_col=0, parse_dates=True)
    S = np.array(uf["sigma"]["daily_cov"], dtype=float)
    w = np.array(uf["sigma"]["w"], dtype=float)
    sd = np.sqrt(np.diag(S))
    betas = np.array([c["beta"] for c in uf["currencies"]], dtype=float)
    E21 = {c["ccy"]: c["E21"] for c in uf["currencies"]}
    frozen = (uf.get("diag") or {}).get("frozen") or {}
    shock = frozen.get("max_abs_f_1d") if frozen.get("written") else None
    as_of = uf["as_of"]
    # PSD check (aceptación)
    eig = np.linalg.eigvalsh(S)
    psd_ok = bool(eig.min() > -1e-14)

    out = {"schema": "BOOK_RISK/1.0", "version": VERSION,
           "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "as_of": as_of, "sigma_psd": psd_ok, "book": None, "candidates": [], "backtest": None,
           "notes": ["libro = snapshot de exposiciones lineales FX en nominal USD actual; no contabilidad ni margen",
                     "Σ cubos = 0 es identidad de la representación equilibrada, no validación de datos",
                     "VaR/ES son previsiones: backtest PENDIENTE hasta ≥ %d snapshots" % BACKTEST_MIN,
                     "estrés = sensibilidades declaradas, no probabilidades; sin umbral de alerta",
                     "sin carry: el tipo oficial no es el carry ejecutable (ACTA_UF1 §C.10)"]}

    try:
        book = read_positions(DATA / "book.csv")
    except Exception as e:
        log("ERROR book.csv: %s" % e); book = None
        out["book"] = {"status": "ERROR", "error": str(e)}
    if book is not None and book:
        b = buckets(book)
        q = qvec(b)
        rb = risk_block(q, S, w, can, betas, sd, shock)
        V = rb["V_daily"]
        euler = []
        for p in book:
            qk = qvec_pos(p)
            euler.append({"id": p["id"], "pair": p["pair"], "side": p["side"], "nominal_usd": p["nominal_usd"],
                          "rc": (float(qk @ S @ q) / V) if V > 0 else None})
        snap_date = max((p["date"] for p in book if p["date"]), default="")
        stale = None
        if snap_date:
            try:
                n_sess = int((can.index > pd.Timestamp(snap_date)).sum())
                stale = n_sess > STALE_SESSIONS
            except Exception:
                stale = None
        out["book"] = {"status": "STALE" if stale else "OK", "snapshot_date": snap_date or None,
                       "n_positions": len(book), "buckets_usd": b, "sum_buckets": float(sum(b.values())),
                       "net_usd": b["USD"], "risk": rb, "euler": euler}
        # snapshots + previsión para backtest
        SNAP_DIR.mkdir(parents=True, exist_ok=True)
        sp = SNAP_DIR / ("%s.csv" % as_of)
        src = (DATA / "book.csv").read_text()
        if not sp.exists() or sp.read_text() != src:
            sp.write_text(src)
        fc = SNAP_DIR / "forecasts.csv"
        rows = []
        if fc.exists():
            rows = list(csv.DictReader(open(fc, newline="")))
        rows = [r for r in rows if r["as_of"] != as_of]
        rows.append({"as_of": as_of, "sigma_usd": "%.2f" % rb["sigma_usd"], "var95_param": "%.2f" % rb["var95_param"],
                     "var95_hist": ("%.2f" % rb["var95_hist"]) if rb["var95_hist"] is not None else "",
                     "n_positions": str(len(book))})
        with open(fc, "w", newline="") as f:
            wr = csv.DictWriter(f, fieldnames=["as_of", "sigma_usd", "var95_param", "var95_hist", "n_positions"])
            wr.writeheader(); wr.writerows(rows)
        out["backtest"] = {"status": "PENDIENTE" if len(rows) < BACKTEST_MIN else "DISPONIBLE",
                           "n_forecasts": len(rows), "min": BACKTEST_MIN}
    elif book is not None:
        out["book"] = {"status": "SIN LIBRO"}

    # candidatos
    try:
        cands = read_positions(DATA / "candidate.csv")
    except Exception as e:
        log("ERROR candidate.csv: %s" % e); cands = []
        out["candidates_error"] = str(e)
    qL = qvec(buckets(book)) if book else np.zeros(len(CCYS))
    VL = float(qL @ S @ qL)
    base_rb = risk_block(qL, S, w, can, betas, sd, shock) if book else None
    for p in cands:
        qc = qvec_pos(p)
        Vc = float(qc @ S @ qc)
        dV = float(2 * qc @ S @ qL + Vc)
        new = risk_block(qL + qc, S, w, can, betas, sd, shock)
        a, bq = p["pair"][:3], p["pair"][3:]
        ia = CCYS.index(a) if a in CCYS else None
        ib = CCYS.index(bq) if bq in CCYS else None
        leak = None; vol = None; dE = None
        ba = betas[ia] if ia is not None else 0.0     # USD: β = 0 por definición del numerario
        bb = betas[ib] if ib is not None else 0.0
        leak = float(ba - bb)
        var_x = (S[ia, ia] if ia is not None else 0) + (S[ib, ib] if ib is not None else 0) - (2 * S[ia, ib] if (ia is not None and ib is not None) else 0)
        vol = float(math.sqrt(max(var_x, 0)) * math.sqrt(252) * 100)
        dE = float((E21.get(a, 0.0) or 0.0) - (E21.get(bq, 0.0) or 0.0))
        out["candidates"].append({
            "id": p["id"], "pair": p["pair"], "side": p["side"], "nominal_usd": p["nominal_usd"],
            "d_buckets_usd": buckets([p]),
            "dV": dV, "d_sigma_usd": new["sigma_usd"] - (base_rb["sigma_usd"] if base_rb else 0.0),
            "d_var95_param": new["var95_param"] - (base_rb["var95_param"] if base_rb else 0.0),
            "rho_book": (float(qc @ S @ qL) / math.sqrt(Vc * VL)) if (Vc > 0 and VL > 0) else None,
            "leak_beta": leak, "cross_vol_ann_pct": vol, "dE21_pct": dE,
            "share_on_basket_after": new["share_on_basket"],
            "d_share_on_basket": (new["share_on_basket"] - base_rb["share_on_basket"]) if (base_rb and base_rb["share_on_basket"] is not None and new["share_on_basket"] is not None) else None,
            "stress_candidate": risk_block(qc, S, w, can, betas, sd, shock)["stress"],
            "vs": "libro" if book else "libro vacío"})
    if not cands and "candidates_error" not in out:
        out["candidates_status"] = "SIN CANDIDATO"

    (DATA / "BOOK_RISK.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    log("as_of %s · libro %s · candidatos %d · PSD %s" % (as_of, (out["book"] or {}).get("status"), len(out["candidates"]), psd_ok))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""
validate_usd_factor.py  v1.0 — pruebas de aceptación de la §10 (ACTA_UF1 §F).
No es un backtest de alpha: son identidades, causalidad y calendario.
Lee data/usd_factor/canonical.csv, data/USD_FACTOR.json, data/BOOK_RISK.json.
Exit 0 = todas pasan; exit 1 = alguna falla (se imprime cuál).
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CCYS = ["EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"]
N = 7
W = np.full(N, 1.0 / N)
LAMBDA = 0.97


def main():
    can = pd.read_csv(DATA / "usd_factor" / "canonical.csv", index_col=0, parse_dates=True)
    uf = json.loads((DATA / "USD_FACTOR.json").read_text())
    res = []

    def check(name, ok, detail=""):
        res.append((name, bool(ok), detail))

    R = can[[f"r_{c}" for c in CCYS]].values
    f = can["f"].values
    # 1. identidad del factor
    check("factor = −media(7 patas)", np.nanmax(np.abs(f + R @ W)) < 1e-9)
    # 2. Σβ = 7 cada día (factor completo, pesos 1/7)
    B = can[[f"beta_{c}" for c in CCYS]].values
    sb = np.nansum(B, axis=1)[1:]
    check("Σβ_i = 7 todos los días", np.nanmax(np.abs(sb - 7.0)) < 1e-7, "max |Σβ−7| = %.2e" % np.nanmax(np.abs(sb - 7.0)))
    # 3. residual = r + β·f (α = 0)
    E = can[[f"eps_{c}" for c in CCYS]].values
    err = np.nanmax(np.abs(E[1:] - (R[1:] + B[1:] * f[1:, None])))
    check("ε = r + β·f exacto", err < 1e-9, "max err %.2e" % err)
    # 4. causalidad: β_t sale de Σ_{t−1} → recomputar la EWMA desde el inicio y comparar en 5 fechas
    S = np.cov(R[:60].T, bias=True)
    T = len(R)
    pick = sorted(set([300, T // 3, T // 2, T - 200, T - 1]))
    ok = True; worst = 0.0
    for t in range(T):
        # al inicio de la iteración t, S = Σ_{t−1} (incluye R[t−1], no R[t])
        if t in pick:
            sw = S @ W; vf = float(W @ S @ W)
            beta_t = sw / vf
            worst = max(worst, float(np.max(np.abs(beta_t - B[t]))))
            ok = ok and worst < 1e-7
        x = R[t][:, None]
        S = LAMBDA * S + (1 - LAMBDA) * (x @ x.T)
    check("β_t usa Σ_{t−1} (causal) en 5 fechas", ok, "max err %.2e" % worst)
    # 5. Σ del JSON = Σ_T recomputada; PSD; reconstrucción espectral
    SJ = np.array(uf["sigma"]["daily_cov"])
    check("Σ del JSON = EWMA recomputada", np.max(np.abs(SJ - S)) < 1e-9, "max err %.2e" % np.max(np.abs(SJ - S)))
    ev, U = np.linalg.eigh(SJ)
    check("Σ semidefinida positiva", ev.min() > -1e-14, "λ_min %.2e" % ev.min())
    check("Σ = UΛU' (reconstrucción)", np.max(np.abs(U @ np.diag(ev) @ U.T - SJ)) < 1e-12)
    # 6. z21 usa los 252 valores ANTERIORES (excluye el actual) — 3 fechas
    ok = True
    for c in ("EUR", "JPY"):
        e21 = can[f"E21_{c}"]
        for t in (T - 1, T - 100, T - 1000):
            w = e21.iloc[t - 252:t].values
            z = (e21.iloc[t] - w.mean()) / w.std(ddof=0)
            ok = ok and abs(z - can[f"z21_{c}"].iloc[t]) < 1e-6
    check("z21 causal (ventana anterior, sin el día)", ok)
    # 7. D con denominador 7 y rango con empates medios; UN-NOMBRE
    E21 = can[[f"E21_{c}" for c in CCYS]]
    D = E21.std(axis=1, ddof=0)
    check("D = sd_7(E21) (denominador 7)", np.nanmax(np.abs(D - can["D"])) < 1e-9)
    rk = can[[f"rank_{c}" for c in CCYS]].dropna()
    check("rangos suman 28 (1..7 con empates medios)", np.allclose(rk.sum(axis=1), 28.0))
    # 8. percentil causal de D (3 fechas)
    ok = True
    for t in (T - 1, T - 50, T - 700):
        w = can["D"].iloc[t - 252:t].values
        p = 100.0 * (w <= can["D"].iloc[t]).sum() / 252
        ok = ok and abs(p - can["D_pct"].iloc[t]) < 1e-6
    check("percentil de D causal", ok)
    # 9. histéresis: ningún cambio de banda sin 3 sesiones previas en la banda nueva
    band = can["band"].values; pct = can["D_pct"].values
    viol = 0
    for t in range(4, T):
        if isinstance(band[t], str) and isinstance(band[t - 1], str) and band[t] != band[t - 1]:
            def raw(p):
                return None if np.isnan(p) else ("COMUN" if p <= 30 else ("DISPERSO" if p >= 70 else "MIXTO"))
            if not all(raw(pct[t - k]) == band[t] for k in range(0, 3)):
                viol += 1
    check("histéresis 3 sesiones respetada", viol == 0, "violaciones %d" % viol)
    # 10. calendario: ningún día con las 7 patas a retorno cero (relleno prohibido)
    zero = int((np.abs(R) < 1e-15).all(axis=1).sum())
    check("sin días rellenados a retorno cero", zero == 0, "días %d" % zero)
    # 11. PC1: ancla de signo (Cov(PC1,f) > 0) y norma 1
    u1 = can[[f"pc1_{c}" for c in CCYS]].iloc[-1].values
    check("PC1 norma 1 y Cov(PC1,f) > 0", abs(np.linalg.norm(u1) - 1) < 1e-9 and float(-(u1 @ SJ @ W)) > 0)
    check("S_pc1 = λ1/traza", abs(ev[-1] / ev.sum() - uf["pca"]["S_pc1"]) < 1e-3)
    # 12. libro: cubos suman 0; V = q'Σq; Euler suma 1; cuota en [0,1]
    brp = DATA / "BOOK_RISK.json"
    if brp.exists():
        br = json.loads(brp.read_text())
        bk = br.get("book") or {}
        if bk.get("status") in ("OK", "STALE"):
            b = bk["buckets_usd"]; q = np.array([b[c] for c in CCYS])
            V = float(q @ SJ @ q)
            check("libro: Σ cubos = 0", abs(bk["sum_buckets"]) < 1e-6)
            check("libro: V = q'Σq", abs(V - bk["risk"]["V_daily"]) / max(V, 1e-9) < 1e-9)
            check("libro: Euler suma 1", abs(sum(e["rc"] for e in bk["euler"]) - 1.0) < 1e-9)
            sh = bk["risk"]["share_on_basket"]
            check("libro: cuota sobre la cesta en [0,1]", sh is None or 0 <= sh <= 1 + 1e-12)
            for c in br.get("candidates") or []:
                qc = np.array([c["d_buckets_usd"][k] for k in CCYS])
                dV = float(2 * qc @ SJ @ q + qc @ SJ @ qc)
                check("candidato %s: ΔV = 2q_c'Σq + q_c'Σq_c" % c["id"], abs(dV - c["dV"]) / max(abs(dV), 1e-9) < 1e-9)
    # 13. sin adjetivos de dirección en las etiquetas del JSON
    txt = json.dumps(uf, ensure_ascii=False).lower()
    bad = [w for w in ("fuerte", "débil", "comprar", "vender", "alcista", "bajista") if w in txt]
    check("sin adjetivos de dirección en USD_FACTOR.json", not bad, ",".join(bad))

    width = max(len(n) for n, _, _ in res)
    fails = 0
    for n, ok, d in res:
        print("%s  %-*s  %s" % ("PASS" if ok else "FAIL", width, n, d))
        fails += (not ok)
    print("%d/%d pruebas superadas" % (len(res) - fails, len(res)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

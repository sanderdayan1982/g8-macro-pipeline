"""B5 — corrección de sesgo de Φ para el ACM de NZD, en SHADOW.

No escribe data/ACM_G8_NZD.csv. La serie publicada sigue siendo la del VAR MCO.

El OLS de un autorregresivo persistente está sesgado hacia cero en muestra corta
(Kendall 1954; el mismo sesgo que Bauer-Rudebusch-Wu corrigen por bootstrap).
Sobre cada autovalor λ de Φ:

    λ' = λ + (1 + 3λ) / N

N es el número de transiciones del VAR (meses − 1). Después se recorta |λ'| ≤ 0.995,
el mismo candado de estacionariedad que ya usa acm_estimate.
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import acm_g8  # noqa: E402

CAP = 0.995
SHADOW_NAME = "ACM_G8_NZD_phi_shadow.json"


def correct_phi(Phi, n_transitions, cap=CAP):
    """Corrección de Kendall sobre los autovalores. Φ real, k×k. N ≥ 1."""
    Phi = np.asarray(Phi, dtype=float)
    n_transitions = int(n_transitions)
    if n_transitions < 1:
        raise ValueError("n_transitions must be positive")
    eigval, eigvec = np.linalg.eig(Phi)
    shifted = eigval + (1.0 + 3.0 * eigval) / n_transitions
    mag = np.abs(shifted)
    scale = np.ones_like(mag)
    hot = mag > cap
    scale[hot] = cap / mag[hot]
    lam = shifted * scale
    out = eigvec @ np.diag(lam) @ np.linalg.inv(eigvec)
    return np.real_if_close(out, tol=1e6).astype(float)


def _yields(params, factors):
    y = acm_g8.affine_yields(params, factors, 120) * 100.0
    rny = acm_g8.affine_yields(params, factors, 120, risk_neutral=True) * 100.0
    return y, rny, y - rny


def measure(data_dir=None):
    """TP10 y RNY10 diarios en memoria: MCO contra Φ corregida. No escribe el CSV publicado."""
    if data_dir is not None:
        acm_g8.DATA_DIR = data_dir
    hist = acm_g8.build_hist_panel("NZD")
    grid_years = acm_g8.GRID_MONTHS / 12.0
    z_hist, _lam = acm_g8.ns_fit_panel(hist, grid_years)
    z_dec = z_hist / 100.0
    z_dec.columns = acm_g8.GRID_MONTHS
    z_m = z_dec.resample("ME").last().dropna()
    base = acm_g8.acm_estimate(z_m)
    alt = acm_g8.acm_estimate(z_m, phi_adjust=correct_phi)
    daily = acm_g8.build_daily_panel("NZD")
    z_d, _ = acm_g8.ns_fit_panel(daily, grid_years)
    z_d = z_d / 100.0
    z_d.columns = acm_g8.GRID_MONTHS
    Fd = acm_g8.factors_from_yields(base, z_d)
    y0, r0, tp0 = _yields(base, Fd)
    y1, r1, tp1 = _yields(alt, Fd)
    e0 = np.linalg.eigvals(base["Phi"])
    e1 = np.linalg.eigvals(alt["Phi"])
    return {
        "schema": "nzd-phi-shadow-1",
        "mode": "SHADOW",
        "method": "kendall-1954-eigenvalues",
        "cap": CAP,
        "n_months": int(len(z_m)),
        "n_transitions": int(len(z_m) - 1),
        "hist_start": str(hist.index[0].date()),
        "hist_end": str(hist.index[-1].date()),
        "daily_end": str(z_d.index[-1].date()),
        "max_abs_eig_ols": float(np.max(np.abs(e0))),
        "max_abs_eig_shadow": float(np.max(np.abs(e1))),
        "last_y10_ols": float(y0[-1]),
        "last_rny_ols": float(r0[-1]),
        "last_tp_ols": float(tp0[-1]),
        "last_y10_shadow": float(y1[-1]),
        "last_rny_shadow": float(r1[-1]),
        "last_tp_shadow": float(tp1[-1]),
        "last_d_tp_bp": float((tp1[-1] - tp0[-1]) * 100.0),
        "last_d_rny_bp": float((r1[-1] - r0[-1]) * 100.0),
        "mean_abs_d_tp_bp": float(np.mean(np.abs(tp1 - tp0)) * 100.0),
        "mean_abs_d_rny_bp": float(np.mean(np.abs(r1 - r0)) * 100.0),
    }


def write_summary(summary, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


if __name__ == "__main__":
    out = measure()
    dest = os.path.join(acm_g8.DATA_DIR, SHADOW_NAME)
    write_summary(out, dest)
    print(json.dumps(out, indent=1, ensure_ascii=False))
    print("[nzd_phi_shadow] wrote", dest)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kalman_core.py — Núcleo Kalman TVP (time-varying parameters) con forgetting factor.
Aislado y testeable. Lo consume metals_fairvalue_g8.py v3.

MODELO (estado-espacio, regresión de coeficientes variables):
  Observación:  y_t = x_t · β_t + ε_t ,   ε_t ~ N(0, R_t)
  Estado:       β_t = β_{t-1} + η_t ,      η_t ~ N(0, Q_t)

donde x_t es el vector fila de regresores (incluida constante) en t, y β_t el vector
de coeficientes time-varying. El "fair value" es ŷ_{t|t-1} = x_t · β_{t|t-1} y el
residuo MDP crudo es ε_t = y_t − ŷ_{t|t-1} (usando el estado PREDICHO, no el filtrado:
β_{t|t-1}, que es el prior antes de asimilar la observación de hoy → cero look-ahead,
recomendación Manus/Perplexity).

RESTRICCIÓN DE Q vía FORGETTING FACTOR δ (Koop-Korobilis):
  En vez de estimar una matriz Q completa (sobreajuste con k≥5 regresores), se infla
  la covarianza del estado por un factor 1/δ en cada paso de predicción:
      P_{t|t-1} = (1/δ) · P_{t-1|t-1}
  Esto equivale a Q_t = (1/δ − 1)·P_{t-1|t-1}: las betas pueden derivar, pero NO más
  rápido de lo que δ permite. δ→1 ≈ betas casi fijas (DOLS); δ pequeño ≈ betas ágiles
  (riesgo de comerse la señal). δ∈[0.97,0.99] es el rango macro-TVP estándar. Un único
  hiperparámetro interpretable, calibrado por walk-forward IC (no por MLE).

R_t (varianza de observación): EWMA de los residuos al cuadrado (volatilidad local del
oro, captura regímenes 2020/2022 sin sobreparametrizar). Piso numérico para estabilidad.

Sin look-ahead: el filtro es estrictamente forward. NUNCA usa smoother para la señal.
"""

import numpy as np


def kalman_tvp_filter(y, X, delta=0.98, r_ewma=0.94, burn=156,
                      p0_scale=10.0, r_floor=1e-6):
    """Filtro de Kalman TVP forward-only con forgetting factor.

    Parámetros
    ----------
    y : ndarray (T,)        observable (log-precio del metal)
    X : ndarray (T, k)      regresores YA con columna de constante incluida
    delta : float           forgetting factor δ∈(0,1]; controla deriva de β
    r_ewma : float          factor EWMA para R_t (vol de observación). None → R fija OLS
    burn : int              nº de obs iniciales de calentamiento (no se emiten señales)
    p0_scale : float        escala de la covarianza inicial del estado (difuso)
    r_floor : float         piso de R_t para estabilidad numérica

    Devuelve dict con arrays alineados a y (longitud T):
      beta_pred  (T,k)  β_{t|t-1}  estado PREDICHO (para señal, sin look-ahead)
      beta_filt  (T,k)  β_{t|t}    estado FILTRADO  (diagnóstico)
      resid      (T,)   ε_t = y_t − x_t·β_{t|t-1}   (MDP crudo)
      fair_pred  (T,)   x_t·β_{t|t-1}               (fair value sin look-ahead)
      R          (T,)   varianza de observación usada
      trace_P    (T,)   traza de P_{t|t-1} (incertidumbre → peso de riesgo)
      pred_var   (T,)   varianza del error de predicción S_t = x P x' + R
      burn       int    eco del burn-in
    """
    y = np.asarray(y, float)
    X = np.asarray(X, float)
    T, k = X.shape

    # --- guardia: ningún NaN/inf puede entrar (rompería el SVD con un error críptico)
    if not np.all(np.isfinite(y)) or not np.all(np.isfinite(X)):
        bad_y = int(np.sum(~np.isfinite(y)))
        bad_X = int(np.sum(~np.isfinite(X)))
        raise ValueError(
            f"kalman_tvp_filter: entrada no finita (y:{bad_y} X:{bad_X} valores NaN/inf). "
            "Limpia el panel antes de filtrar (drop de filas con NaN/inf, log solo de >0).")

    # --- estado inicial: OLS sobre la ventana de burn-in (arranque informado)
    nb = min(max(burn, k + 5), T)
    Xb, yb = X[:nb], y[:nb]
    beta, *_ = np.linalg.lstsq(Xb, yb, rcond=None)
    resid_b = yb - Xb @ beta
    sigma2 = float(resid_b @ resid_b) / max(nb - k, 1)   # varianza residual OLS inicial
    R = max(sigma2, r_floor)
    # covarianza inicial difusa, escalada a la incertidumbre OLS
    XtX_inv = np.linalg.pinv(Xb.T @ Xb)
    P = p0_scale * sigma2 * XtX_inv

    beta_pred = np.full((T, k), np.nan)
    beta_filt = np.full((T, k), np.nan)
    resid     = np.full(T, np.nan)
    fair_pred = np.full(T, np.nan)
    Rser      = np.full(T, np.nan)
    traceP    = np.full(T, np.nan)
    predvar   = np.full(T, np.nan)

    inv_delta = 1.0 / delta

    for t in range(T):
        x = X[t]                                # (k,) vector fila de regresores

        # ---- PREDICCIÓN del estado (random walk + forgetting) ----
        # β_{t|t-1} = β_{t-1|t-1}  (random walk: la media no cambia)
        # P_{t|t-1} = (1/δ) · P_{t-1|t-1}        ← forgetting factor (restringe Q)
        P = inv_delta * P
        beta_pred[t] = beta                     # estado predicho ANTES de ver y_t

        # fair value y residuo con el estado PREDICHO (sin look-ahead)
        yhat = float(x @ beta)
        fair_pred[t] = yhat
        e = y[t] - yhat
        resid[t] = e

        # varianza del error de predicción S_t = x P x' + R
        Px = P @ x
        S = float(x @ Px) + R
        predvar[t] = S
        traceP[t] = float(np.trace(P))
        Rser[t] = R

        # ---- ACTUALIZACIÓN (corrección con la observación de hoy) ----
        K = Px / S                              # ganancia de Kalman (k,)
        beta = beta + K * e                     # β_{t|t}
        P = P - np.outer(K, Px)                 # P_{t|t} = P - K x P
        P = 0.5 * (P + P.T)                     # simetriza (estabilidad numérica)
        beta_filt[t] = beta

        # ---- R_t adaptativa (EWMA de e²): vol de observación local ----
        if r_ewma is not None:
            R = r_ewma * R + (1.0 - r_ewma) * (e * e)
            R = max(R, r_floor)

    return {
        "beta_pred": beta_pred, "beta_filt": beta_filt, "resid": resid,
        "fair_pred": fair_pred, "R": Rser, "trace_P": traceP, "pred_var": predvar,
        "burn": nb, "delta": delta, "k": k,
    }


def ou_half_life(resid):
    """Half-life OU del residuo: Δr_t = a + b·r_{t-1} + e ; HL = −ln2/ln(1+b).
    En nº de períodos (martes → semanas). NA si no es mean-reverting (b>=0)."""
    r = np.asarray(resid, float)
    r = r[~np.isnan(r)]
    if len(r) < 30:
        return np.nan
    r_lag = r[:-1]
    dr = np.diff(r)
    A = np.column_stack([np.ones_like(r_lag), r_lag])
    coef, *_ = np.linalg.lstsq(A, dr, rcond=None)
    b = coef[1]
    if b >= 0 or (1.0 + b) <= 0:
        return np.nan
    return float(-np.log(2.0) / np.log(1.0 + b))


def expanding_zscore(resid, burn, min_obs=52):
    """Z-score con ventana EXPANSIVA hasta t-1 (cero look-ahead): cada z_t usa solo
    media/desv de los residuos [burn .. t-1]. Antes de burn+min_obs → NaN.
    Devuelve ndarray (T,)."""
    r = np.asarray(resid, float)
    T = len(r)
    z = np.full(T, np.nan)
    for t in range(burn + min_obs, T):
        hist = r[burn:t]                        # estricto < t (no incluye hoy)
        hist = hist[~np.isnan(hist)]
        if len(hist) < min_obs:
            continue
        mu = hist.mean(); sd = hist.std(ddof=0)
        if sd > 1e-12:
            z[t] = (r[t] - mu) / sd
    return z


def ljung_box(resid, lags=8):
    """Estadístico Ljung-Box Q y p-valor (χ² con 'lags' gl). Sin dependencias.
    H0: ruido blanco (sin autocorrelación). Para el MDP QUEREMOS rechazar H0
    (p<0.10) → el desorden persiste → la señal NO está vacía."""
    from scipy import stats
    r = np.asarray(resid, float)
    r = r[~np.isnan(r)]
    n = len(r)
    if n < lags + 10:
        return np.nan, np.nan
    r = r - r.mean()
    c0 = float(r @ r) / n
    if c0 <= 0:
        return np.nan, np.nan
    Q = 0.0
    for L in range(1, lags + 1):
        ck = float(r[L:] @ r[:-L]) / n
        rho = ck / c0
        Q += rho * rho / (n - L)
    Q *= n * (n + 2)
    p = 1.0 - stats.chi2.cdf(Q, lags)
    return float(Q), float(p)

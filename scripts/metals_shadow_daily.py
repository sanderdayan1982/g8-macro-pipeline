"""B2 — MDP diario en SHADOW.

El Kalman se sigue estimando solo en la rejilla semanal W-TUE (ancla COT),
forward-filter, sin smoother. Esta proyección no reestima betas ni escribe
MFV_G8_XAU.csv / MFV_G8_XAG.csv / MFV_G8_state.json.

Regla firmada:
- Martes τ: estado predicho β_{τ|τ-1}. El MDP_Z publicado es el semanal, al decimal.
- Miércoles a lunes, hasta 4 días hábiles después de τ: estado filtrado β_{τ|τ}
  propagado con ffill causal (τ ≤ t).
- El Z de esos días sale de una ventana rolling causal de residuos diarios.
  No se divide un residuo diario entre la varianza de innovación semanal.
"""
import numpy as np
import pandas as pd


def _asof(series, days):
    import metals_fairvalue_g8 as M
    return M.asof_weekly(series, days)


def _finish_levels(panel, required):
    panel = panel.replace([np.inf, -np.inf], np.nan)
    return panel.dropna(subset=required)


def xau_daily(price, be, slope, nfa, dxy, real, official, start, end):
    """Mismo ensamblado que el panel semanal, sobre días hábiles (lun–vie)."""
    import metals_fairvalue_g8 as M
    days = pd.bdate_range(start, end)
    if len(days) == 0:
        return None
    panel = pd.concat([
        _asof(price.rename("PX"), days),
        _asof(be.rename("BE"), days),
        _asof(slope.rename("SL"), days),
        _asof(nfa.rename("NFA"), days),
        _asof(dxy.rename("DXY"), days),
        _asof(real.rename("REAL"), days),
    ], axis=1, sort=True).dropna(subset=["PX", "BE", "SL", "NFA", "DXY", "REAL"])
    panel = panel[(panel["PX"] > 0) & (panel["NFA"] > 0)]
    panel["LOGPX"] = np.log(panel["PX"])
    panel["LOGNFA"] = np.log(panel["NFA"])
    brk = (panel.index >= pd.Timestamp(M.RATE_BREAK)).astype(float)
    panel["REAL_BRK"] = panel["REAL"].values * brk
    if official is not None:
        panel = panel.join(_asof(official.rename("OFF"), days))
        panel["LOGOFF"] = np.log(panel["OFF"].clip(lower=1e-9))
    return _finish_levels(panel, ["PX", "BE", "SL", "NFA", "DXY", "REAL", "LOGPX", "LOGNFA", "REAL_BRK"])


def xag_daily(price, xau, real, dxy, copper, hg_coef, start, end):
    """HG_ORTH usa los coeficientes del OLS semanal: no se reestima en el diario."""
    days = pd.bdate_range(start, end)
    if len(days) == 0:
        return None
    cols = [
        _asof(price.rename("PX"), days),
        _asof(xau.rename("XAU"), days),
        _asof(real.rename("REAL"), days),
        _asof(dxy.rename("DXY"), days),
    ]
    has_copper = copper is not None and hg_coef is not None
    if has_copper:
        cols.append(_asof(copper.rename("HG"), days))
    need = ["PX", "XAU", "REAL", "DXY"] + (["HG"] if has_copper else [])
    panel = pd.concat(cols, axis=1, sort=True).dropna(subset=need)
    panel = panel[panel["PX"] > 0]
    panel["LOGPX"] = np.log(panel["PX"])
    panel["LOGXAU"] = np.log(panel["XAU"].clip(lower=1e-9))
    if has_copper:
        lx = panel["LOGXAU"].values
        lh = np.log(panel["HG"].values)
        A = np.column_stack([np.ones_like(lx), lx])
        panel["HG_ORTH"] = lh - A @ np.asarray(hg_coef, float)
    return _finish_levels(panel, ["PX", "LOGPX", "LOGXAU", "REAL", "DXY"] + (["HG_ORTH"] if has_copper else []))


def design_matrix(df, driver_names):
    cols = []
    for name in driver_names:
        if name == "const":
            cols.append(np.ones(len(df)))
        elif name == "be":
            cols.append(df["BE"].to_numpy(float))
        elif name == "slope":
            cols.append(df["SL"].to_numpy(float))
        elif name == "log_nfa":
            cols.append(df["LOGNFA"].to_numpy(float))
        elif name == "real":
            cols.append(df["REAL"].to_numpy(float))
        elif name == "real_brk":
            cols.append(df["REAL_BRK"].to_numpy(float))
        elif name == "official":
            lo = df["LOGOFF"].to_numpy(float)
            cols.append(np.where(np.isnan(lo), np.nanmedian(lo), lo))
        elif name == "log_xau":
            cols.append(df["LOGXAU"].to_numpy(float))
        elif name == "hg_orth":
            cols.append(df["HG_ORTH"].to_numpy(float))
        elif name == "dxy":
            cols.append(df["DXY"].to_numpy(float))
        else:
            raise KeyError(name)
    return np.column_stack(cols)


def business_steps(tau, t):
    """Días hábiles lun–vie estrictamente posteriores a τ y hasta t, incluido t."""
    return len(pd.bdate_range(tau, t)) - 1


def rolling_causal_z(resid, window, min_obs):
    """Z con media y desviación poblacional de los residuos diarios estrictamente anteriores."""
    r = np.asarray(resid, float)
    z = np.full(len(r), np.nan)
    for t in range(len(r)):
        hist = r[max(0, t - window):t]
        hist = hist[np.isfinite(hist)]
        if len(hist) < min_obs:
            continue
        sd = hist.std(ddof=0)
        if sd > 1e-12 and np.isfinite(r[t]):
            z[t] = (r[t] - hist.mean()) / sd
    return z


def project(daily, weekly_index, beta_pred, beta_filt, weekly_z, y, X, z_window):
    """Proyecta betas semanales sobre el panel diario. Devuelve un DataFrame o None."""
    if daily is None or len(daily) == 0:
        return None
    w_index = pd.DatetimeIndex(pd.to_datetime(weekly_index))
    y = np.asarray(y, float)
    X = np.asarray(X, float)
    beta_pred = np.asarray(beta_pred, float)
    beta_filt = np.asarray(beta_filt, float)
    weekly_z = np.asarray(weekly_z, float)
    n = len(daily)
    resid = np.full(n, np.nan)
    z_tue = np.full(n, np.nan)
    kind = np.array([""] * n, dtype=object)
    for i, t in enumerate(daily.index):
        ts = pd.Timestamp(t)
        if ts.weekday() >= 5:
            continue
        j = int(w_index.searchsorted(ts, side="right")) - 1
        if j < 0:
            continue
        steps = business_steps(w_index[j], ts)
        if steps == 0:
            beta, tag = beta_pred[j], "pred"
        elif 1 <= steps <= 4:
            beta, tag = beta_filt[j], "filt"
        else:
            continue
        x = X[i]
        if not (np.isfinite(y[i]) and np.all(np.isfinite(x)) and np.all(np.isfinite(beta))):
            continue
        resid[i] = y[i] - float(x @ beta)
        kind[i] = tag
        if steps == 0 and np.isfinite(weekly_z[j]):
            z_tue[i] = weekly_z[j]
    window = int(z_window) * 5
    min_obs = min(52, int(z_window) // 3) * 5
    z = rolling_causal_z(resid, window, min_obs)
    tue = np.isfinite(z_tue)
    z[tue] = z_tue[tue]
    keep = kind != ""
    if not np.any(keep):
        return None
    out = pd.DataFrame({
        "DATE": daily.index[keep].strftime("%Y%m%d"),
        "PX": np.round(daily["PX"].to_numpy(float)[keep], 4),
        "RESID": np.round(resid[keep], 6),
        "MDP_Z": np.round(z[keep], 4),
        "BETA_STATE": kind[keep],
    })
    return out


def last_meta(frame, weekly_index):
    """mdp_as_of = último día diario; cot_as_of = último martes de la rejilla ≤ ese día."""
    if frame is None or len(frame) == 0:
        return None
    last = frame.iloc[-1]
    mdp_as_of = pd.Timestamp(str(last["DATE"]))
    w = pd.DatetimeIndex(pd.to_datetime(weekly_index))
    j = int(w.searchsorted(mdp_as_of, side="right")) - 1
    cot = w[j] if j >= 0 else None
    z = last["MDP_Z"]
    return {
        "mdp_z": None if pd.isna(z) else float(z),
        "mdp_as_of": mdp_as_of.strftime("%Y-%m-%d"),
        "cot_as_of": None if cot is None else cot.strftime("%Y-%m-%d"),
        "beta_state": str(last["BETA_STATE"]),
        "mode": "SHADOW",
    }

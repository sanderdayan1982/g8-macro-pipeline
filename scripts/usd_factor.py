#!/usr/bin/env python3
"""
G8 Macro Pipeline — usd_factor.py  v1.0.1  (2026-09-17 · twin AFE: índice antes de dropna)
=====================================================
Sección §10 "FACTOR USD · RESIDUALES · MATRIZ" — capa CONTEXT de composición.
Especificación: docs/actas/ACTA_UF1.md (v2 consolidada tras triangulación
Kimi K3 / Cursor / ChatGPT Astra, 17-sep-2026). Sin voto, sin dirección.

Objeto
  Siete patas en convención nativa P_i = dólares por una unidad de i
  (EUR GBP JPY CHF CAD AUD NZD), trianguladas desde los tipos de referencia
  diarios del BCE:  P_EUR = EURUSD ;  P_i = EURUSD / (i por EUR).
  Retornos logarítmicos diarios r_i.  Factor dólar f = −(1/7)·Σ r_i
  (cesta equiponderada congelada; f > 0 = el dólar se fortalece).

Cálculo (todo causal: Σ y β del día t−1 se aplican al retorno de t)
  Σ_t   covarianza EWMA recursiva λ = 0.97, media cero (semivida ≈ 22.8 s.)
  β_i   = −Cov(r_i, f)/Var(f) desde Σ  (factor COMPLETO, α = 0;
          identidad Σβ_i = 7 por construcción; NO es sesgo individual)
  ε_i,t = r_i,t + β_i,t−1 · f_t          residual del día
  E_h   = Σ ε en h ∈ {5, 21, 63}          21 = horizonte de mesa
  z21   = (E21 − media)/desv de los 252 valores ANTERIORES (solapados: z
          descriptivo, no probabilidad gaussiana)
  D_t   = desv. estándar transversal (denominador 7) de los siete E21;
          bandera UN-NOMBRE si max(E21²)/ΣE21² > 0.5
  banda = percentil causal 252 de D_t: ≤30 COMÚN · 30-70 MIXTO · ≥70 DISPERSO
          (convención simétrica pre-registrada, histéresis 3 sesiones)
  PCA   sobre Σ_t: S_t = λ1/traza; cargas PC1 con signo anclado
          (Cov(PC1, f) > 0); bandera ROTACIÓN si cos(u1_t, u1_t−21) < 0.8
  matriz 21 cruces: leak = β_A − β_B · vol = √(Σ_AA+Σ_BB−2Σ_AB) · ρ · E21_A−E21_B

Twins (números, sin pasa/falla)
  · futuros CME (data/futures/canonical, ya en repo): corr 252 de retornos
    diarios por divisa, con y sin días DESFASE_US
  · DTWEXAFEGS (FRED, opcional): corr de retornos semanales
  · β EWMA vs β OLS-252 (α = 0) · cestas equi vs inv-vol · factores LOO

Congelados (data/usd_factor/frozen_2000_2023.json): se calculan UNA vez sobre
  2000-2023 y nunca se recomputan (máximo |f| a 1 sesión, correlaciones típicas).

Salidas
  data/usd_factor/ecb_rates.csv      caché de la fuente (i por EUR, fechas BCE)
  data/usd_factor/canonical.csv      todas las magnitudes diarias (auditoría; texto: git lo delta-comprime)
  data/USD_FACTOR.json               foto del último día para el dashboard
                                      (incluye Σ para book_risk.py)
Uso
  python3 scripts/usd_factor.py                      # BCE (Actions / Mac)
  python3 scripts/usd_factor.py --source-file X.csv --source-label TEST
        X.csv = DATE,EUR,GBP,JPY,CHF,CAD,AUD,NZD ya en convención nativa
Ley 2: cualquier error de fuente termina con exit ≠ 0 (fallo ruidoso). Un
  festivo TARGET (sin dato nuevo) NO es un fallo: exit 0 con nota NO_NEW_DATA.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import warnings

warnings.simplefilter("ignore", category=pd.errors.PerformanceWarning)

VERSION = "1.0.1"
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT_DIR = DATA / "usd_factor"
CCYS = ["EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"]
N = len(CCYS)
W = np.full(N, 1.0 / N)
LAMBDA = 0.97
Z_WIN = 252
HORIZONS = (5, 21, 63)
BAND_LO, BAND_HI = 30.0, 70.0          # convención 30/40/30 (ACTA_UF1)
HYST = 3                               # sesiones
ONE_NAME_SHARE = 0.5
ROTATION_MIN = 0.8
CALIB_START, CALIB_END = "2000-01-03", "2023-12-31"
ECB_URL = ("https://data-api.ecb.europa.eu/service/data/EXR/"
           "D.USD+GBP+JPY+CHF+CAD+AUD+NZD.EUR.SP00.A?format=csvdata&startPeriod=1999-01-04")
FRED_AFE = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTWEXAFEGS"
FUT_ROOT = {"EUR": "6E", "GBP": "6B", "JPY": "6J", "CHF": "6S", "CAD": "6C", "AUD": "6A", "NZD": "6N"}


def log(msg: str) -> None:
    print("usd_factor: " + msg, flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Fuente
# ─────────────────────────────────────────────────────────────────────────────
def fetch_ecb(timeout: int = 90) -> pd.DataFrame:
    """Descarga el histórico completo del BCE (7 divisas por EUR). Devuelve
    DataFrame indexado por fecha con columnas CCYS = unidades de i por EUR."""
    import requests
    r = requests.get(ECB_URL, headers={"Accept": "text/csv", "User-Agent": "g8-macro-pipeline/usd_factor"},
                     timeout=timeout)
    r.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(r.text)))
    if not rows:
        raise RuntimeError("BCE: respuesta vacía")
    rec = {}
    for row in rows:
        cur = row.get("CURRENCY")
        d = row.get("TIME_PERIOD")
        v = row.get("OBS_VALUE")
        if cur not in ("USD", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD") or not d or v in (None, ""):
            continue
        rec.setdefault(d, {})[cur] = float(v)
    df = pd.DataFrame.from_dict(rec, orient="index").sort_index()
    df.index = pd.to_datetime(df.index)
    df = df.reindex(columns=["USD", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"])
    return df


def load_cache() -> pd.DataFrame | None:
    p = OUT_DIR / "ecb_rates.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p, index_col=0, parse_dates=True)
    return df


def drop_carried_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Una fila idéntica a la anterior en TODAS las patas es un arrastre de la
    fuente (no hubo fix), no una observación: se elimina (B.2/B.3)."""
    same = (df == df.shift(1)).all(axis=1)
    same.iloc[0] = False
    return df[~same], int(same.sum())


def native_from_ecb(df: pd.DataFrame) -> pd.DataFrame:
    """i por EUR → dólares por unidad de i. Fechas comunes completas (B.3)."""
    df = df.dropna(how="any").copy()
    df, _ = drop_carried_rows(df)
    eurusd = df["USD"]
    out = pd.DataFrame(index=df.index)
    out["EUR"] = eurusd
    for c in ("GBP", "JPY", "CHF", "CAD", "AUD", "NZD"):
        out[c] = eurusd / df[c]
    return out


def ingestion_checks(raw: pd.DataFrame, native: pd.DataFrame) -> dict:
    """Identidades de ingestión (Astra): pasa/falla porque son aritmética."""
    chk = {}
    chk["dates_sorted_unique"] = bool(raw.index.is_monotonic_increasing and raw.index.is_unique)
    chk["all_positive"] = bool((raw.dropna() > 0).all().all())
    # inversión exacta: P_i × (i por EUR) == EURUSD
    tri = (native[["GBP", "JPY", "CHF", "CAD", "AUD", "NZD"]].values
           * raw.loc[native.index, ["GBP", "JPY", "CHF", "CAD", "AUD", "NZD"]].values
           - native["EUR"].values[:, None])
    chk["triangulation_max_abs_err"] = float(np.nanmax(np.abs(tri)))
    chk["triangulation_ok"] = chk["triangulation_max_abs_err"] < 1e-9
    r = np.log(native).diff().abs()
    jumps = r[(r > 0.10).any(axis=1)]
    chk["jumps_gt_10pct"] = [d.strftime("%Y-%m-%d") for d in jumps.index][-10:]
    chk["carried_rows_dropped"] = drop_carried_rows(raw.dropna(how="any"))[1]
    chk["n_common_dates"] = int(len(native))
    chk["first_date"] = native.index[0].strftime("%Y-%m-%d")
    chk["last_date"] = native.index[-1].strftime("%Y-%m-%d")
    return chk


# ─────────────────────────────────────────────────────────────────────────────
# 2. Calendario DESFASE_US (regla NFP primer viernes + fichero manual)
# ─────────────────────────────────────────────────────────────────────────────
def us_calendar(index: pd.DatetimeIndex) -> pd.Series:
    """True en días con publicación estadounidense posterior a las 14:15 CET.
    Regla: NFP = primer viernes del mes (BLS; excepciones no capturadas).
    Fichero manual data/usd_factor/us_calendar.csv (date,event) para FOMC,
    CPI, PCE — verificado por el operador contra la fuente oficial."""
    flags = pd.Series(False, index=index)
    for d in index:
        if d.weekday() == 4 and d.day <= 7:
            flags[d] = True
    p = OUT_DIR / "us_calendar.csv"
    if p.exists():
        cal = pd.read_csv(p, comment="#")
        for d in pd.to_datetime(cal["date"], errors="coerce").dropna():
            if d in flags.index:
                flags[d] = True
    return flags


# ─────────────────────────────────────────────────────────────────────────────
# 3. Motor
# ─────────────────────────────────────────────────────────────────────────────
def ewma_path(R: np.ndarray, warm: int = 60) -> np.ndarray:
    """Σ_t (T×N×N) recursiva λ, media cero; Σ_t usa retornos hasta t incluido.
    Inicialización: covarianza muestral de las primeras `warm` sesiones."""
    T = R.shape[0]
    S = np.zeros((T, N, N))
    S0 = np.cov(R[:warm].T, bias=True)
    cur = S0.copy()
    for t in range(T):
        x = R[t][:, None]
        cur = LAMBDA * cur + (1.0 - LAMBDA) * (x @ x.T)
        S[t] = cur
    return S


def betas_from_sigma(S: np.ndarray) -> np.ndarray:
    """β_i = −Cov(r_i,f)/Var(f), f = −w'r  ⇒  Cov(r_i,f) = −(Σw)_i."""
    sw = S @ W                 # Cov(r_i, w'r)
    vf = float(W @ S @ W)      # Var(f)
    return sw / vf if vf > 0 else np.full(N, np.nan)


def loo_betas_from_sigma(S: np.ndarray) -> np.ndarray:
    out = np.full(N, np.nan)
    for i in range(N):
        w = np.full(N, 1.0 / (N - 1)); w[i] = 0.0
        vf = float(w @ S @ w)
        out[i] = float((S @ w)[i] / vf) if vf > 0 else np.nan
    return out


def loo_factor_corr_min(S: np.ndarray) -> float:
    """Mínima correlación dos a dos entre los siete factores leave-one-out."""
    ws = []
    for i in range(N):
        w = np.full(N, 1.0 / (N - 1)); w[i] = 0.0
        ws.append(w)
    m = 1.0
    for i in range(N):
        for j in range(i + 1, N):
            c = float(ws[i] @ S @ ws[j]) / math.sqrt(float(ws[i] @ S @ ws[i]) * float(ws[j] @ S @ ws[j]))
            m = min(m, c)
    return m


def pca1(S: np.ndarray):
    vals, vecs = np.linalg.eigh(S)
    order = np.argsort(vals)[::-1]
    vals, vecs = vals[order], vecs[:, order]
    u1 = vecs[:, 0].copy()
    # ancla de signo: Cov(u1'r, f) = −u1'Σw > 0
    if float(-(u1 @ S @ W)) < 0:
        u1 = -u1
    share = float(vals[0] / vals.sum()) if vals.sum() > 0 else np.nan
    gap = float(vals[0] - vals[1]) if len(vals) > 1 else np.nan
    return share, u1, vals, gap


def causal_z(x: pd.Series, win: int = Z_WIN) -> pd.Series:
    prev = x.shift(1)
    m = prev.rolling(win, min_periods=win).mean()
    s = prev.rolling(win, min_periods=win).std(ddof=0)
    z = (x - m) / s
    return z.where(s > 0)


def causal_pct(x: pd.Series, win: int = Z_WIN) -> pd.Series:
    """Percentil (0-100) de x_t frente a sus `win` valores ANTERIORES."""
    out = pd.Series(np.nan, index=x.index)
    vals = x.values
    for t in range(win, len(vals)):
        w = vals[t - win:t]
        if np.isnan(vals[t]) or np.isnan(w).any():
            continue
        out.iloc[t] = 100.0 * float((w <= vals[t]).sum()) / win
    return out


def bands_with_hysteresis(pct: pd.Series) -> pd.Series:
    """COMÚN/MIXTO/DISPERSO con 3 sesiones consecutivas en la banda nueva."""
    def raw(p):
        if np.isnan(p):
            return None
        return "COMUN" if p <= BAND_LO else ("DISPERSO" if p >= BAND_HI else "MIXTO")
    state, cand, run = None, None, 0
    out = []
    for p in pct.values:
        b = raw(p)
        if b is None:
            out.append(state); continue
        if state is None:
            state = b; cand, run = None, 0
        elif b != state:
            if b == cand:
                run += 1
            else:
                cand, run = b, 1
            if run >= HYST:
                state, cand, run = b, None, 0
        else:
            cand, run = None, 0
        out.append(state)
    return pd.Series(out, index=pct.index)


def compute(native: pd.DataFrame, us_flags: pd.Series) -> tuple[pd.DataFrame, dict]:
    P = native[CCYS]
    R = np.log(P).diff().dropna()
    idx = R.index
    Rv = R.values
    T = len(idx)
    f = -(Rv @ W)                                   # factor del día
    S = ewma_path(Rv)                               # Σ_t con t incluido
    # β causal: Σ_{t−1}
    beta = np.full((T, N), np.nan)
    beta_loo = np.full((T, N), np.nan)
    loo_corr = np.full(T, np.nan)
    S1 = np.full(T, np.nan); u1 = np.full((T, N), np.nan); gap = np.full(T, np.nan)
    for t in range(1, T):
        beta[t] = betas_from_sigma(S[t - 1])
        beta_loo[t] = loo_betas_from_sigma(S[t - 1])
        loo_corr[t] = loo_factor_corr_min(S[t - 1])
    for t in range(T):
        S1[t], u1[t], _, gap[t] = pca1(S[t])
    eps = Rv + beta * f[:, None]
    eps[0] = np.nan

    can = pd.DataFrame(index=idx)
    can["f"] = f
    for h in HORIZONS:
        can[f"f_cum{h}"] = pd.Series(f, index=idx).rolling(h).sum()
        can[f"f_z{h}"] = causal_z(can[f"f_cum{h}"])
    can["I_level"] = np.exp(np.cumsum(f))
    # cesta inversa de vol (diagnóstico): pesos ∝ 1/σ_i de Σ_{t−1}, normalizados
    finv = np.full(T, np.nan)
    for t in range(1, T):
        sd = np.sqrt(np.diag(S[t - 1]))
        wv = (1.0 / sd); wv /= wv.sum()
        finv[t] = -float(Rv[t] @ wv)
    can["f_invvol"] = finv
    can["corr63_f_invvol"] = pd.Series(f, index=idx).rolling(63).corr(pd.Series(finv, index=idx))

    for i, c in enumerate(CCYS):
        can[f"r_{c}"] = Rv[:, i]
        can[f"beta_{c}"] = beta[:, i]
        can[f"beta_loo_{c}"] = beta_loo[:, i]
        can[f"dbeta21_{c}"] = pd.Series(beta[:, i], index=idx).diff(21)
        can[f"eps_{c}"] = eps[:, i]
        for h in HORIZONS:
            can[f"E{h}_{c}"] = pd.Series(eps[:, i], index=idx).rolling(h).sum()
        can[f"z21_{c}"] = causal_z(can[f"E21_{c}"])
        # OLS-252 sin intercepto (twin): β = −Σ r_i f / Σ f², ventana hasta t−1
        rf = pd.Series(Rv[:, i] * f, index=idx).shift(1).rolling(Z_WIN).sum()
        ff = pd.Series(f * f, index=idx).shift(1).rolling(Z_WIN).sum()
        can[f"beta_ols252_{c}"] = -rf / ff
        can[f"pc1_{c}"] = u1[:, i]
    E21 = can[[f"E21_{c}" for c in CCYS]]
    rk = E21.rank(axis=1, ascending=False, method="average")
    for c in CCYS:
        can[f"rank_{c}"] = rk[f"E21_{c}"]
    can["D"] = E21.std(axis=1, ddof=0)
    can["D_mad"] = (E21.sub(E21.median(axis=1), axis=0)).abs().median(axis=1)
    sq = E21.pow(2)
    can["one_name_share"] = sq.max(axis=1) / sq.sum(axis=1)
    valid = sq.notna().all(axis=1)
    onc = pd.Series(None, index=idx, dtype=object)
    onc[valid] = sq[valid].idxmax(axis=1).str.replace("E21_", "")
    can["one_name_ccy"] = onc
    can["D_pct"] = causal_pct(can["D"])
    can["band"] = bands_with_hysteresis(can["D_pct"])
    can["S_pc1"] = S1
    can["eig_gap"] = gap
    cosu = np.full(T, np.nan)
    for t in range(21, T):
        a, b = u1[t], u1[t - 21]
        cosu[t] = float(abs(a @ b) / (np.linalg.norm(a) * np.linalg.norm(b)))
    can["pc1_rotation_cos21"] = cosu
    can["loo_corr_min"] = loo_corr
    can["desfase_us"] = us_flags.reindex(idx).fillna(False).astype(bool).values
    return can, {"S": S, "idx": idx, "R": Rv}


# ─────────────────────────────────────────────────────────────────────────────
# 4. Twins
# ─────────────────────────────────────────────────────────────────────────────
def twin_futures(can: pd.DataFrame) -> dict:
    out = {"available": False, "note": "data/futures/canonical ausente"}
    base = DATA / "futures" / "canonical"
    if not base.exists():
        return out
    res = {}
    for c, root in FUT_ROOT.items():
        p = base / f"{root}.csv"
        if not p.exists():
            res[c] = None; continue
        df = pd.read_csv(p)
        df = df[df["oi"] > 0]
        # contrato front por OI en cada sesión; retorno solo si el mismo símbolo
        front = df.sort_values(["session", "oi"], ascending=[True, False]).groupby("session").head(1)
        front = front.set_index(pd.to_datetime(front["session"]))
        same = front["symbol"] == front["symbol"].shift(1)
        rf = np.log(front["settle"]).diff().where(same)
        rb = can[f"r_{c}"]
        j = pd.concat([rb, rf.rename("fut")], axis=1, join="inner").dropna()
        j = j.tail(Z_WIN)
        if len(j) < 60:
            res[c] = {"n": int(len(j)), "corr": None, "corr_ex_us": None}
            continue
        us = can["desfase_us"].reindex(j.index).fillna(False)
        res[c] = {"n": int(len(j)),
                  "corr": round(float(j.iloc[:, 0].corr(j["fut"])), 3),
                  "corr_ex_us": round(float(j[~us].iloc[:, 0].corr(j[~us]["fut"])), 3),
                  "last_session": j.index[-1].strftime("%Y-%m-%d")}
    out = {"available": True, "window": Z_WIN, "by_ccy": res,
           "note": "retornos diarios BCE(14:15 CET) vs settle CME(21:00 CET) — relojes distintos; número, no veredicto"}
    return out


def twin_afe(can: pd.DataFrame, timeout: int = 30) -> dict:
    try:
        import requests
        r = requests.get(FRED_AFE, timeout=timeout)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        df.columns = ["DATE", "AFE"]
        df["AFE"] = pd.to_numeric(df["AFE"], errors="coerce")
        df = df.set_index(pd.to_datetime(df["DATE"]))["AFE"].dropna()     # v1.0.1: índice antes de dropna
        wk_afe = np.log(df).resample("W-FRI").last().diff()
        wk_f = can["f"].resample("W-FRI").sum()
        j = pd.concat([wk_f.rename("f"), wk_afe.rename("afe")], axis=1).dropna().tail(104)
        return {"available": True, "n_weeks": int(len(j)), "corr_weekly": round(float(j["f"].corr(j["afe"])), 3),
                "afe_last": df.index[-1].strftime("%Y-%m-%d"),
                "note": "pesos comerciales de la Fed ≠ cesta equiponderada; divergencia de nivel esperada"}
    except Exception as e:  # opcional
        return {"available": False, "note": "DTWEXAFEGS no accesible: %s" % str(e)[:120]}


# ─────────────────────────────────────────────────────────────────────────────
# 5. Congelados 2000-2023 (una vez)
# ─────────────────────────────────────────────────────────────────────────────
def frozen_numbers(can: pd.DataFrame, source_label: str) -> dict:
    p = OUT_DIR / "frozen_2000_2023.json"
    if p.exists():
        return json.loads(p.read_text())
    w = can.loc[CALIB_START:CALIB_END]
    if len(w) < 5000 or source_label != "ECB":
        log("congelados NO escritos: histórico insuficiente o fuente no primaria (%s, n=%d)" % (source_label, len(w)))
        return {"written": False, "reason": "calibración exige BCE 2000-2023 completo", "source": source_label}
    imax = w["f"].abs().idxmax()
    fz = {"written": True, "source": source_label, "window": [CALIB_START, CALIB_END],
          "computed_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
          "max_abs_f_1d": float(w["f"].abs().max()), "max_abs_f_1d_date": imax.strftime("%Y-%m-%d"),
          "f_1d_std": float(w["f"].std(ddof=0)),
          "median_corr63_f_invvol": float(w["corr63_f_invvol"].median()),
          "median_S_pc1": float(w["S_pc1"].median()),
          "median_D": float(w["D"].median()),
          "band_days": {k: int(v) for k, v in w["band"].value_counts().items()},
          "one_name_freq": float((w["one_name_share"] > ONE_NAME_SHARE).mean()),
          "note": "calculado UNA vez; nunca se recomputa (ACTA_UF1)"}
    p.write_text(json.dumps(fz, indent=2, ensure_ascii=False))
    log("congelados escritos en %s" % p)
    return fz


# ─────────────────────────────────────────────────────────────────────────────
# 6. JSON de la foto
# ─────────────────────────────────────────────────────────────────────────────
def snapshot(can: pd.DataFrame, eng: dict, chk: dict, source_label: str, twins: dict, frozen: dict) -> dict:
    t = len(can) - 1
    last = can.iloc[t]
    d = can.index[t]
    S = eng["S"][t]
    sd = np.sqrt(np.diag(S))
    def fl(x, nd=6):
        return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), nd)
    cur = []
    for i, c in enumerate(CCYS):
        cur.append({"ccy": c, "beta": fl(last[f"beta_{c}"], 3), "dbeta21": fl(last[f"dbeta21_{c}"], 3),
                    "E5": fl(last[f"E5_{c}"] * 100, 3), "E21": fl(last[f"E21_{c}"] * 100, 3), "E63": fl(last[f"E63_{c}"] * 100, 3),
                    "z21": fl(last[f"z21_{c}"], 2), "rank": fl(last[f"rank_{c}"], 1),
                    "beta_ols252": fl(last[f"beta_ols252_{c}"], 3), "beta_loo": fl(last[f"beta_loo_{c}"], 3),
                    "vol_ann_pct": fl(sd[i] * math.sqrt(252) * 100, 2), "pc1": fl(last[f"pc1_{c}"], 3)})
    pairs = {}
    for i, a in enumerate(CCYS):
        for j, b in enumerate(CCYS):
            if i == j:
                continue
            var = S[i, i] + S[j, j] - 2 * S[i, j]
            pairs[a + b] = {"leak": fl(last[f"beta_{a}"] - last[f"beta_{b}"], 3),
                            "vol_ann_pct": fl(math.sqrt(max(var, 0)) * math.sqrt(252) * 100, 2),
                            "rho": fl(S[i, j] / (sd[i] * sd[j]), 3),
                            "dE21": fl((last[f"E21_{a}"] - last[f"E21_{b}"]) * 100, 3)}
    # frescura: días de calendario y sesiones BCE desde el último dato
    today = date.today()
    age_cal = (today - d.date()).days
    return {
        "schema": "USD_FACTOR/1.0", "version": VERSION,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "as_of": d.strftime("%Y-%m-%d"), "source": source_label,
        "source_note": "BCE tipos de referencia (concertación ~14:15 CET, publicación ~16:00 CET), triangulados a USD; no ejecutables",
        "factor": {f"cum{h}": fl(last[f"f_cum{h}"] * 100, 3) for h in HORIZONS} |
                  {f"z{h}": fl(last[f"f_z{h}"], 2) for h in HORIZONS} | {"today": fl(last["f"] * 100, 3)},
        "dispersion": {"D": fl(last["D"] * 100, 3), "D_pct": fl(last["D_pct"], 1), "band": last["band"],
                       "one_name": bool(last["one_name_share"] > ONE_NAME_SHARE),
                       "one_name_ccy": str(last["one_name_ccy"]), "one_name_share": fl(last["one_name_share"], 2),
                       "S_pc1": fl(last["S_pc1"], 3), "band_since": _band_since(can)},
        "currencies": cur, "pairs": pairs,
        "pca": {"S_pc1": fl(last["S_pc1"], 3), "loadings_pc1": {c: fl(last[f"pc1_{c}"], 3) for c in CCYS},
                "rotation_cos21": fl(last["pc1_rotation_cos21"], 3),
                "rotation_flag": bool(last["pc1_rotation_cos21"] < ROTATION_MIN) if np.isfinite(last["pc1_rotation_cos21"]) else None,
                "eig_gap": fl(last["eig_gap"], 8)},
        "sigma": {"ccys": CCYS, "lambda": LAMBDA, "daily_cov": [[fl(S[i, j], 10) for j in range(N)] for i in range(N)],
                  "w": [fl(x, 6) for x in W]},
        "diag": {"fix": "14:15 CET", "calendar": "TARGET (BCE)", "age_calendar_days": age_cal,
                 "desfase_us_today": bool(last["desfase_us"]),
                 "n_sessions": int(len(can)), "first": can.index[0].strftime("%Y-%m-%d"),
                 "ingestion": chk,
                 "loo_corr_min": fl(last["loo_corr_min"], 3),
                 "corr63_f_invvol": fl(last["corr63_f_invvol"], 3),
                 "beta_ewma_vs_ols252": {c: {"ewma": fl(last[f"beta_{c}"], 3), "ols252": fl(last[f"beta_ols252_{c}"], 3)} for c in CCYS},
                 "twins": twins, "frozen": frozen},
        "footer": ["Σβ_i = 7 por construcción (factor completo con pesos 1/7): identidad, no sesgo individual",
                   "los siete residuales NO son siete confirmaciones independientes: promediar cruces devuelve la ordenación de las patas",
                   "z = distancia descriptiva sobre ventanas solapadas, no probabilidad gaussiana",
                   "|β_A − β_B| = carga de retorno por unidad de factor, no riesgo en dólares (eso exige nominal y Σ)",
                   "proyección sobre la cesta definida ≠ componente dólar causal; los componentes no se bautizan",
                   "CONTEXT permanente: no vota, no entra en confluencia ni en ranking jefe"]}


def _band_since(can: pd.DataFrame) -> int:
    b = can["band"].values
    if b[-1] is None:
        return 0
    n = 0
    for x in b[::-1]:
        if x == b[-1]:
            n += 1
        else:
            break
    return n


# ─────────────────────────────────────────────────────────────────────────────
def main(argv: list[str]) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    src_file = None; src_label = "ECB"
    if "--source-file" in argv:
        src_file = Path(argv[argv.index("--source-file") + 1])
    if "--source-label" in argv:
        src_label = argv[argv.index("--source-label") + 1]
    if src_file:
        native = pd.read_csv(src_file, index_col=0, parse_dates=True)[CCYS].dropna().sort_index()
        native, n_carried = drop_carried_rows(native)
        raw = None
        chk = {"note": "fuente no primaria (%s): identidades de triangulación no aplicables" % src_label,
               "n_common_dates": int(len(native)), "first_date": native.index[0].strftime("%Y-%m-%d"),
               "last_date": native.index[-1].strftime("%Y-%m-%d"), "carried_rows_dropped": n_carried}
    else:
        prev = load_cache()
        try:
            raw = fetch_ecb()
        except Exception as e:
            if prev is None:
                log("ERROR fuente BCE: %s" % e); return 2
            log("BCE no accesible (%s); se usa la caché hasta %s" % (str(e)[:100], prev.index[-1].date()))
            raw = prev
        else:
            if prev is not None and raw.index[-1] <= prev.index[-1]:
                log("NO_NEW_DATA: último dato BCE %s (festivo TARGET o pendiente de publicación)" % raw.index[-1].date())
            raw.to_csv(OUT_DIR / "ecb_rates.csv", float_format="%.6f")
        native = native_from_ecb(raw)
        chk = ingestion_checks(raw, native)
        if not (chk["dates_sorted_unique"] and chk["all_positive"] and chk["triangulation_ok"]):
            log("ERROR identidades de ingestión: %s" % chk); return 3
    if len(native) < 400:
        log("ERROR histórico insuficiente (%d)" % len(native)); return 4
    flags = us_calendar(native.index)
    can, eng = compute(native, flags)
    twins = {"futures": twin_futures(can), "afe": twin_afe(can) if not src_file else {"available": False, "note": "omitido en modo fichero"}}
    frozen = frozen_numbers(can, src_label)
    snap = snapshot(can, eng, chk, src_label, twins, frozen)
    can.to_csv(OUT_DIR / "canonical.csv", float_format="%.10g")
    (DATA / "USD_FACTOR.json").write_text(json.dumps(snap, indent=1, ensure_ascii=False))
    log("as_of %s · band %s (p%s) · S_pc1 %s · f z21 %s · escrito USD_FACTOR.json + canonical.csv (%d filas)" %
        (snap["as_of"], snap["dispersion"]["band"], snap["dispersion"]["D_pct"], snap["pca"]["S_pc1"],
         snap["factor"]["z21"], len(can)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

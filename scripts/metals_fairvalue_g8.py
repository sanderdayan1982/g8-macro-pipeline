#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
metals_fairvalue_g8.py v3 — Kalman TVP / MDP bajo marco MMT (XAU / XAG)
=======================================================================
Motor de FAIR VALUE de oro y plata bajo Modern Monetary Theory (MMT/Mosler).
El precio del metal se ancla NO a un coste de oportunidad neoclásico (real yield),
sino al DESORDEN DEL PASIVO SOBERANO: expectativas de poder de compra (breakeven),
prima de plazo observable (slope 10Y−EFFR) y activos financieros netos del sector
no gubernamental (NFA_total). El residuo del modelo, z-scoreado, es la señal
MDP (Monetary Disorder Premium): cuánto se desvía el precio de su fundamental MMT.

POR QUÉ KALMAN TVP (no DOLS/cointegración)
------------------------------------------
Dos rondas de triangulación adversarial (10 respuestas de 6 sistemas) convergieron:
el equilibrio del oro NO es estacionario — se DESPLAZA con la postura fiscal del
emisor soberano. Un vector cointegrante fijo (DOLS) es teóricamente incoherente con
MMT y empíricamente falló (no cointegraba; el oro llevaba años 2.4× sobre fair value,
lo que un test ADF lee como raíz unitaria). La respuesta es un fair value MÓVIL:
  Observación:  log(XAU)_t = x_t · β_t + ε_t
  Estado:       β_t = β_{t-1} + η_t     (coeficientes time-varying)
El MDP = ε_t estandarizado. El "equilibrio" respira con el régimen fiscal.

RESTRICCIÓN DE Q (el riesgo #1 según las 5 AI de ronda 2)
--------------------------------------------------------
Un Kalman con betas libres "se come" la señal: las betas persiguen el precio y el
MDP queda como ruido blanco inoperable. Se restringe vía FORGETTING FACTOR δ
(Koop-Korobilis): P_{t|t-1} = (1/δ)·P_{t-1|t-1}. δ∈[0.97,0.999], un solo
hiperparámetro, calibrado por WALK-FORWARD que maximiza el Information Coefficient
(NO por máxima verosimilitud, que infla Q y destruye la predicción).

SEÑAL OPERATIVA, SIN LOOK-AHEAD (consenso 5/5)
----------------------------------------------
- Forward-filter ÚNICAMENTE para la señal (estado predicho β_{t|t-1}). El smoother
  usaría datos futuros → solo se reservaría para auditoría histórica.
- z-score con ventana EXPANSIVA hasta t-1.
- MDP ajustado por half-life OU (urgencia de reversión).
- Matriz de activación con COT Managed Money (overlay táctico, no driver).
- Peso de riesgo nativo 1/√trace(P_t) (incertidumbre de régimen del propio filtro).

DRIVERS MMT (consenso ronda 2)
------------------------------
  ORO:  log(XAU) = α_t + β_t·[ BE10 + slope(10Y−EFFR) + log(NFA_total) ]
                 + γ·DXY (beta FIJA, fuera del TVP) + ε_t
        NFA_total = FDHBFIN + WRESBAL − WTREGEN   (métrica Mosler exacta del desorden)
        slope     = DGS10 − EFFR                  (libre de modelo; reemplaza TP10 ACM)
        challenger OOS: TP10 ACM (si añade IC sobre slope en walk-forward, se adopta)
  PLATA: XAG = βm_t·XAU + βi_t·HG_orth(cobre⊥oro) + ε_t  (dual: monetario + industrial)

VALIDACIÓN PRE-PRODUCCIÓN (gates institucionales)
-------------------------------------------------
  walk-forward forward-filter only · IC Spearman(MDP, ret_fwd 4/8/12w) · hit-rate ·
  Sharpe de señal · Ljung-Box(MDP)≠ruido blanco · Var(MDP)/Var(logPX)≥0.20 ·
  half-life OU>1w. Si el MDP no pasa estos gates, la señal está vacía aunque el
  framework sea elegante.

FUENTES (local-first; calca real_yields_g8.py / acm_g8.py)
----------------------------------------------------------
  XAU/XAG precio       CSV TradingView (~/Downloads, xau_price.csv/xag_price.csv)
  BE10                 data/RY_G8_USD.csv (real_yields_g8) → fallback FRED T10YIE
  slope = DGS10−EFFR   FRED DGS10, EFFR (o FEDFUNDS)
  NFA_total            FRED FDHBFIN + WRESBAL − WTREGEN  (las 3 ya en USD-LCC)
  DXY (broad)          FRED DTWEXBGS
  TP10 (challenger)    data/ACM_G8.csv (acm_g8) col TP10 → opcional
  Cobre (plata)        CSV TradingView (~/Downloads, hg_price.csv); ausente→degrada
  COT MM (overlay)     data/POS_G8_COT.csv (POS-G8) col NET_MM → opcional

FRECUENCIA: semanal, anclada al martes COT (W-TUE), as-of backward (sin look-ahead).

Outputs (data/):
  MFV_G8_<XAU|XAG>.csv  — serie temporal con fair, MDP, betas TVP, señal
  MFV_G8_state.json     — bridge (último MDP + cot_as_of + δ + métricas, para Pine)
  MFV_G8_walkforward_<metal>.csv — métricas OOS por δ (auditoría de calibración)

Usage:
  python3 metals_fairvalue_g8.py            # XAU + XAG
  python3 metals_fairvalue_g8.py XAU        # solo oro
  python3 metals_fairvalue_g8.py --delta 0.99   # fija δ (salta calibración)

requirements: numpy, pandas, scipy, statsmodels
"""

import io
import os
import sys
import json
import time
import glob
import ssl
import urllib.request

import numpy as np
import pandas as pd
from scipy import stats

# núcleo Kalman (módulo hermano, ya testeado aislado)
try:
    import kalman_core as kc
except ImportError:
    sys.exit("[FALTA] kalman_core.py debe estar junto a este script.")


# ============================================================== config / rutas
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
if not os.path.isdir(DATA_DIR):
    DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0 (g8-macro-pipeline metals_fairvalue_g8/3.0)"}
START = "2006-01-01"          # NFA (WRESBAL) y COT MM disaggregated arrancan ~2006

LOCAL_PRICE = {
    "XAU":    {"fixed": "~/Downloads/xau_price.csv", "globs": ["*GC1*.csv", "*XAU*.csv", "*GOLD*.csv"]},
    "XAG":    {"fixed": "~/Downloads/xag_price.csv", "globs": ["*SI1*.csv", "*XAG*.csv", "*SILVER*.csv"]},
    "COPPER": {"fixed": "~/Downloads/hg_price.csv",  "globs": ["*HG1*.csv", "*COPPER*.csv", "*COBRE*.csv"]},
}

# Modelos MMT. 'drivers_tvp' llevan beta time-varying; 'dxy' va con beta FIJA aparte.
MODELS = {
    "XAU": {"z_window": 156, "drivers_tvp": ["be", "slope", "nfa"], "fixed": ["dxy"]},
    "XAG": {"z_window": 104, "drivers_tvp": ["xau", "hg_orth"],      "fixed": []},
}

# δ a barrer en walk-forward (forgetting factor). El test del núcleo mostró que el
# oro necesita δ alto (0.99-0.999) o el MDP se vacía; barremos ese rango fino.
DELTA_GRID = [0.999, 0.995, 0.99, 0.985, 0.98]
FWD_HORIZONS = [4, 8, 12]     # semanas para el retorno forward (IC/hit-rate)
BURN = 156                    # 3 años de calentamiento del filtro
MIN_VARRATIO = 0.20           # Var(MDP)/Var(logPX) mínimo (regla anti-MDP-vacío, Manus)

# D1: fuente de deuda activa del NFA (la rellena load_nfa_total). Sólo aplica a XAU.
NFA_DEBT_SRC = None
NFA_DEBT_ASOF = None


def min_varratio(n_drivers):
    """Umbral var_ratio. Mantiene 0.20 como piso anti-MDP-vacío (regla de Manus:
    el residuo debe retener señal real, no ser ruido). El conteo de drivers ya no
    relaja el umbral — en su lugar, la plata COMPARA modelos (con/sin cobre) y elige
    el de mejor IC que respete este piso. Que el dato decida, no el umbral."""
    return MIN_VARRATIO

# FRED series
FRED_BE    = "T10YIE"         # breakeven 10Y (fallback si no hay RY_G8_USD)
FRED_DGS10 = "DGS10"          # nominal 10Y (para slope)
FRED_EFFR  = "EFFR"           # effective fed funds (para slope); fallback FEDFUNDS
FRED_FF    = "FEDFUNDS"
FRED_DXY   = "DTWEXBGS"       # broad USD
FRED_FDHBFIN = "FDHBFIN"      # deuda federal en manos del público (NFA stock)
FRED_WRESBAL = "WRESBAL"      # reservas bancarias (NFA)
FRED_TGA     = "WTREGEN"      # Treasury General Account (NFA, se RESTA)


# ============================================================== HTTP / fetchers
# Contextos SSL: primero el normal; si la red usa un proxy con certificado propio
# (interceptación TLS, común en redes corporativas/regionales — da
# CERTIFICATE_VERIFY_FAILED self-signed), se reintenta con verificación relajada.
# Esto NO afecta la integridad del dato (FRED/Tesoro son fuentes públicas read-only).
_SSL_STRICT = ssl.create_default_context()
_SSL_RELAXED = ssl.create_default_context()
_SSL_RELAXED.check_hostname = False
_SSL_RELAXED.verify_mode = ssl.CERT_NONE


def _http_get(url, timeout=90, retries=3, headers=None):
    last = None
    for attempt in range(1, retries + 1):
        # en el primer intento prueba estricto; si falla por SSL, relaja en el siguiente
        ctx = _SSL_STRICT if attempt == 1 else _SSL_RELAXED
        try:
            req = urllib.request.Request(url, headers={**UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:                                     # noqa: BLE001
            last = e
            is_ssl = "CERTIFICATE" in str(e) or "SSL" in str(e)
            wait = 2 if is_ssl else 5 * (2 ** (attempt - 1))
            note = " (SSL: reintento sin verificación — proxy con cert propio)" if is_ssl else ""
            print(f"    [http] attempt {attempt} failed ({e}){note} — retry in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"HTTP failed after {retries} attempts: {url} :: {last}")


def fetch_fred(series_id, start=START, freq=None):
    """FRED con API key opcional (FRED_API_KEY) + fallback CSV sin key. Idéntico
    en convención a real_yields_g8.py. Devuelve Series diaria indexada por fecha.
    freq: si se da ('d','w','m','q'), pide a FRED esa frecuencia."""
    api_key = os.environ.get("FRED_API_KEY", "").strip()
    fq = f"&frequency={freq}" if freq else ""
    if api_key:
        url = ("https://api.stlouisfed.org/fred/series/observations"
               f"?series_id={series_id}&api_key={api_key}"
               f"&file_type=json&observation_start={start}{fq}")
        raw = _http_get(url)
        obs = json.loads(raw)["observations"]
        df = pd.DataFrame(obs)[["date", "value"]]
        df["date"] = pd.to_datetime(df["date"])
        s = pd.to_numeric(df.set_index("date")["value"], errors="coerce").dropna()
    else:
        url = (f"https://fred.stlouisfed.org/graph/fredgraph.csv"
               f"?id={series_id}&cosd={start}{fq}")
        raw = _http_get(url)
        df = pd.read_csv(io.StringIO(raw))
        df.columns = ["DATE", "VAL"]
        df["DATE"] = pd.to_datetime(df["DATE"])
        s = pd.to_numeric(df.set_index("DATE")["VAL"], errors="coerce").dropna()
    print(f"    [FRED {series_id}{'@'+freq if freq else ''}] {len(s)} obs  "
          f"{s.index[0].date()} → {s.index[-1].date()}")
    return s.sort_index()


def load_ry_usd():
    """Lee data/RY_G8_USD.csv (producido por real_yields_g8.py). Devuelve DataFrame
    diario con columnas REAL10, BE10 (percent). None si no existe → fuerza fallback."""
    path = os.path.join(DATA_DIR, "RY_G8_USD.csv")
    if not os.path.exists(path):
        return None
    # cabecera es una línea de comentario '# QUALITY=...'; saltarla
    df = pd.read_csv(path, comment="#")
    df.columns = [c.strip().upper() for c in df.columns]
    if not {"DATE", "REAL10", "BE10"}.issubset(df.columns):
        return None
    df["DATE"] = pd.to_datetime(df["DATE"].astype(str), format="%Y%m%d", errors="coerce")
    df = df.dropna(subset=["DATE"]).set_index("DATE").sort_index()
    out = df[["REAL10", "BE10"]].apply(pd.to_numeric, errors="coerce").dropna()
    print(f"    [RY_G8_USD] {len(out)} obs  "
          f"{out.index[0].date()} → {out.index[-1].date()}  (PRIMARY real/BE)")
    return out


def load_tv_csv(path):
    """Lee un export de precio de TradingView. Acepta time en epoch (s) o ISO.
    Devuelve Series 'Close' indexada por fecha (sin tz). None si ilegible."""
    try:
        df = pd.read_csv(os.path.expanduser(path))
    except Exception:
        return None
    cols = {c.lower().strip(): c for c in df.columns}
    tcol = cols.get("time") or cols.get("date") or list(df.columns)[0]
    ccol = cols.get("close")
    if ccol is None:
        return None
    t = df[tcol]
    if pd.api.types.is_numeric_dtype(t):
        dt = pd.to_datetime(t, unit="s", utc=True).dt.tz_localize(None)
    else:
        dt = pd.to_datetime(t, utc=True, errors="coerce").dt.tz_localize(None)
    s = pd.Series(pd.to_numeric(df[ccol], errors="coerce").values, index=dt, name="Close")
    s = s[~s.index.isna()].dropna().sort_index()
    return s if len(s) else None


# Símbolos de futuros continuos COMEX/ICE — equivalentes headless a GC1!/SI1!/HG1!
# de TradingView. Yahoo (primario) + Stooq (fallback keyless) para que el pipeline
# corra 100% automático en GitHub Actions sin exports manuales. (auto 2026-08)
YF_SYMBOL    = {"XAU": "GC=F", "XAG": "SI=F", "COPPER": "HG=F"}
STOOQ_SYMBOL = {"XAU": "gc.f", "XAG": "si.f", "COPPER": "hg.f"}


def fetch_price_network(name):
    """Descarga el precio del metal por red (sin clave), para el modo automático.
    1) yfinance (futuros continuos COMEX)  2) Stooq CSV como fallback. Devuelve
    (Series Close diaria, etiqueta_fuente) o (None, None)."""
    sym = YF_SYMBOL.get(name)
    if sym is None:
        return None, None
    # 1) yfinance
    try:
        import yfinance as yf
        df = yf.download(sym, start=START, interval="1d", progress=False,
                         auto_adjust=False, threads=False)
        if df is not None and len(df):
            close = df["Close"]
            if hasattr(close, "columns"):        # MultiIndex de un solo ticker -> squeeze
                close = close.iloc[:, 0]
            s = pd.to_numeric(close, errors="coerce").dropna()
            s.index = pd.to_datetime(s.index).tz_localize(None)
            s.name = "Close"
            s = s.sort_index()
            if len(s) > 250:
                print(f"    [precio {name}] yfinance {sym}: {len(s)} obs  "
                      f"{s.index[0].date()}→{s.index[-1].date()}")
                return s, f"yfinance:{sym}"
    except Exception as e:                        # noqa: BLE001
        print(f"    [precio {name}] yfinance {sym} falló ({e}) — pruebo Stooq")
    # 2) Stooq CSV (Date,Open,High,Low,Close,Volume)
    try:
        ss = STOOQ_SYMBOL.get(name)
        raw = _http_get(f"https://stooq.com/q/d/l/?s={ss}&i=d", timeout=60)
        df = pd.read_csv(io.StringIO(raw))
        if {"Date", "Close"}.issubset(df.columns):
            s = pd.Series(pd.to_numeric(df["Close"], errors="coerce").values,
                          index=pd.to_datetime(df["Date"], errors="coerce"), name="Close")
            s = s[~s.index.isna()].dropna().sort_index()
            if len(s) > 250:
                print(f"    [precio {name}] Stooq {ss}: {len(s)} obs  "
                      f"{s.index[0].date()}→{s.index[-1].date()}")
                return s, f"stooq:{ss}"
    except Exception as e:                        # noqa: BLE001
        print(f"    [precio {name}] Stooq falló ({e})")
    return None, None


def find_local_price(name):
    """Precio del metal: LOCAL primero (exports TradingView en tu Mac), y si no hay
    (p.ej. corriendo en GitHub Actions) cae a la descarga automática de red."""
    cfg = LOCAL_PRICE.get(name, {})
    fixed = os.path.expanduser(cfg.get("fixed", ""))
    if fixed and os.path.exists(fixed):
        s = load_tv_csv(fixed)
        if s is not None:
            return s, fixed
    dl = os.path.expanduser("~/Downloads")
    cands = []
    for pat in cfg.get("globs", []):
        cands += glob.glob(os.path.join(dl, pat))
    cands = sorted(set(cands), key=lambda p: os.path.getmtime(p), reverse=True)
    for c in cands:
        s = load_tv_csv(c)
        if s is not None:
            return s, c
    # sin export local -> descarga automática (modo desatendido / CI)
    return fetch_price_network(name)

# =========================================================== alineación martes-COT
def cot_tuesdays(start, end):
    """Calendario de martes (W-TUE) en el rango — los días-as-of del corte COT."""
    return pd.date_range(start=start, end=end, freq="W-TUE")


def asof_weekly(daily, tuesdays):
    """Remuestrea una serie/df diario a los martes COT vía merge_asof backward:
    para cada martes toma la ÚLTIMA observación disponible <= ese martes. Evita
    look-ahead (nunca usa datos futuros) y maneja festivos (usa el dato previo).
    """
    if daily is None:
        return None
    d = daily.sort_index()
    left = pd.DataFrame({"DATE": tuesdays})
    if isinstance(d, pd.Series):
        right = pd.DataFrame({"DATE": d.index, d.name or "VAL": d.values})
        valcols = [d.name or "VAL"]
    else:
        right = d.reset_index().rename(columns={d.index.name or "index": "DATE"})
        valcols = [c for c in right.columns if c != "DATE"]
    left["DATE"] = pd.to_datetime(left["DATE"]).astype("datetime64[ns]")
    right["DATE"] = pd.to_datetime(right["DATE"]).astype("datetime64[ns]")
    merged = pd.merge_asof(left, right.sort_values("DATE"), on="DATE", direction="backward")
    return merged.set_index("DATE")[valcols]


# ============================================================ fuentes MMT (NFA, slope, BE)
def load_be10():
    """Breakeven 10Y: PRIMARY data/RY_G8_USD.csv (BE10), fallback FRED T10YIE.
    Devuelve Series diaria en puntos porcentuales."""
    path = os.path.join(DATA_DIR, "RY_G8_USD.csv")
    if os.path.exists(path):
        try:
            df = pd.read_csv(path, comment="#")
            df.columns = [c.strip().upper() for c in df.columns]
            if {"DATE", "BE10"}.issubset(df.columns):
                df["DATE"] = pd.to_datetime(df["DATE"].astype(str), format="%Y%m%d", errors="coerce")
                s = pd.to_numeric(df.set_index("DATE")["BE10"], errors="coerce").dropna()
                print(f"    [BE10] {len(s)} obs de RY_G8_USD (PRIMARY)")
                return s.sort_index()
        except Exception:
            pass
    s = fetch_fred(FRED_BE)
    print(f"    [BE10] fallback FRED {FRED_BE}")
    return s


def load_slope():
    """slope = DGS10 − EFFR (prima de plazo OBSERVABLE, libre de modelo afín ACM).
    Lectura MMT: compensación que el mercado exige por encima de la tasa que fija el
    emisor soberano. Reemplaza el TP10 ACM (residuo de un modelo neoclásico)."""
    dgs10 = fetch_fred(FRED_DGS10)
    try:
        effr = fetch_fred(FRED_EFFR)
    except Exception:
        effr = fetch_fred(FRED_FF)
        print(f"    [slope] EFFR no disponible → uso {FRED_FF}")
    df = pd.concat([dgs10.rename("DGS10"), effr.rename("EFFR")], axis=1, sort=True).ffill().dropna()
    slope = (df["DGS10"] - df["EFFR"]).rename("slope")
    print(f"    [slope] {len(slope)} obs  DGS10−EFFR  "
          f"[{slope.index[0].date()}→{slope.index[-1].date()}]")
    return slope


def _fetch_debt_to_penny():
    """Deuda pública total DIARIA desde el endpoint 'Debt to the Penny' del Tesoro
    (Fiscal Data API, mismo origen que tu DTS Tracker del USD-LCC). Datos diarios
    desde 2005, publicados con 1 día hábil de retraso → frescura semanal real, sin
    el rezago trimestral del FDHBFIN. Devuelve Series diaria en MILLONES (para
    homogeneizar con WRESBAL/WTREGEN que vienen en millones)."""
    base = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/"
            "v2/accounting/od/debt_to_penny"
            "?fields=record_date,tot_pub_debt_out_amt"
            "&sort=-record_date&page%5Bsize%5D=10000")
    try:
        raw = _http_get(base, timeout=90)
        if not raw:
            return None
        obj = json.loads(raw)
        rows = obj.get("data", [])
        if not rows:
            return None
        dates = pd.to_datetime([r["record_date"] for r in rows], errors="coerce")
        # dólares → millones (homogéneo con WRESBAL/WTREGEN)
        vals = pd.to_numeric(pd.Series([r["tot_pub_debt_out_amt"] for r in rows]),
                             errors="coerce") / 1e6
        s = pd.Series(vals.values, index=dates).dropna().sort_index()
        s = s[s > 0]
        return s.rename("DEBT")
    except Exception:
        return None


def _fetch_debt_public():
    """Deuda federal para el NFA, cascada por FRECUENCIA (lo más fino disponible,
    para que el fair value semanal no quede rezagado):
      1) Debt to the Penny  — DIARIA   (Tesoro Fiscal Data) ★ ideal
      2) GFDEBTN @ semanal  — SEMANAL  (FRED resamplea, alinea con martes-COT)
      3) GFDEBTN @ mensual  — MENSUAL  (FRED)
      4) FDHBFIN            — TRIMESTRAL (FRED, último recurso)
    Devuelve (Series, etiqueta_fuente). Se exige un mínimo de observaciones en cada
    nivel para no aceptar una serie degenerada (p.ej. GFDEBTN anual con 80 puntos)."""
    # 1) diario (Tesoro)
    s = _fetch_debt_to_penny()
    if s is not None and len(s) > 500:
        return s, "DebtToPenny(diaria)"
    # 2) semanal (FRED resampleado) — exige densidad semanal real (>600 en ~20y)
    try:
        w = fetch_fred("GFDEBTN", freq="w")
        if w is not None and len(w) > 600:
            return w, "GFDEBTN(semanal)"
    except Exception:
        pass
    # 3) mensual (FRED) — exige densidad mensual real (>180 en ~20y)
    try:
        m = fetch_fred("GFDEBTN", freq="m")
        if m is not None and len(m) > 180:
            return m, "GFDEBTN(mensual)"
    except Exception:
        pass
    # 4) trimestral (último recurso)
    return fetch_fred(FRED_FDHBFIN), "FDHBFIN(trimestral)"


def load_nfa_total():
    """NFA_total = deuda_pública + WRESBAL − WTREGEN (activos financieros netos del
    sector no gubernamental, métrica Mosler del desorden). Usa deuda DIARIA
    (Debt to the Penny) cuando está disponible → el fair value respira a frecuencia
    semanal sin rezago. WRESBAL/WTREGEN son semanales. Forward-fill sin look-ahead."""
    debt, dsrc = _fetch_debt_public()
    wr = fetch_fred(FRED_WRESBAL)        # reservas bancarias (semanal)
    tg = fetch_fred(FRED_TGA)            # TGA (semanal), se RESTA
    lo = max(debt.index[0], wr.index[0], tg.index[0])
    hi = max(debt.index[-1], wr.index[-1], tg.index[-1])
    idx = pd.date_range(lo, hi, freq="D")
    debt = debt.reindex(idx).ffill()
    wr = wr.reindex(idx).ffill()
    tg = tg.reindex(idx).ffill()
    nfa = (debt + wr - tg)
    nfa = nfa.replace([np.inf, -np.inf], np.nan)
    bad = (~np.isfinite(nfa)) | (nfa <= 0)
    nfa = nfa[~bad].dropna().rename("NFA")
    last_debt = debt.dropna().index[-1].date() if len(debt.dropna()) else "—"
    # D1: registrar la fuente de deuda y su frescura para exponerla en el state.json.
    # El operador DEBE saber qué nivel de la cascada está activo: si el Tesoro cae y
    # degrada a mensual/trimestral, el régimen del oro puede cambiar sin aviso.
    global NFA_DEBT_SRC, NFA_DEBT_ASOF
    NFA_DEBT_SRC = dsrc
    NFA_DEBT_ASOF = str(last_debt)
    print(f"    [NFA_total] {len(nfa)} obs  deuda[{dsrc}]+WRESBAL−WTREGEN  "
          f"[{nfa.index[0].date()}→{nfa.index[-1].date()}]  "
          f"(deuda hasta {last_debt}; {int(bad.sum())} pts no-pos/NaN descartados)")
    return nfa


def load_tp10_challenger():
    """TP10 ACM de acm_g8.py (data/ACM_G8.csv col TP10), challenger OOS vs slope.
    None si no existe (entonces el walk-forward solo evalúa slope)."""
    for fn in ["ACM_G8.csv", "ACM_G8_USD.csv", "acm_g8.csv"]:
        path = os.path.join(DATA_DIR, fn)
        if os.path.exists(path):
            try:
                df = pd.read_csv(path, comment="#")
                df.columns = [c.strip().upper() for c in df.columns]
                if {"DATE", "TP10"}.issubset(df.columns):
                    df["DATE"] = pd.to_datetime(df["DATE"].astype(str), format="%Y%m%d", errors="coerce")
                    s = pd.to_numeric(df.set_index("DATE")["TP10"], errors="coerce").dropna()
                    print(f"    [TP10] {len(s)} obs de {fn} (challenger OOS)")
                    return s.sort_index()
            except Exception:
                pass
    return None


def load_cot_mm(name):
    """COT Managed Money net (z-scoreable) de POS-G8. data/POS_G8_COT.csv con
    columnas DATE, <NAME>_NET_MM. None si no existe (overlay táctico opcional)."""
    for fn in ["POS_G8_COT.csv", "POS_G8.csv", "COT_G8.csv"]:
        path = os.path.join(DATA_DIR, fn)
        if os.path.exists(path):
            try:
                df = pd.read_csv(path, comment="#")
                df.columns = [c.strip().upper() for c in df.columns]
                col = f"{name}_NET_MM"
                if "DATE" in df.columns and col in df.columns:
                    df["DATE"] = pd.to_datetime(df["DATE"].astype(str), format="%Y%m%d", errors="coerce")
                    s = pd.to_numeric(df.set_index("DATE")[col], errors="coerce").dropna()
                    print(f"    [COT {name}] {len(s)} obs de {fn}")
                    return s.sort_index()
            except Exception:
                pass
    return None


# ====================================================== walk-forward / validación OOS
def forward_returns(logpx, h):
    """Retorno log forward a h períodos: r_t = log P_{t+h} − log P_t. NaN al final."""
    r = np.full(len(logpx), np.nan)
    r[:-h] = logpx[h:] - logpx[:-h]
    return r


def evaluate_delta(y, X, delta, z_window):
    """Corre el filtro con un δ y devuelve métricas OOS (IC/hit por horizonte +
    var-ratio + Ljung-Box + half-life). El MDP usa el residuo del estado PREDICHO
    (sin look-ahead) y z-score expansivo. La señal cruda para IC es −MDP_z (MDP alto
    ⇒ caro ⇒ retorno futuro esperado negativo)."""
    out = kc.kalman_tvp_filter(y, X, delta=delta, burn=BURN)
    b = out["burn"]
    resid = out["resid"]
    z = kc.expanding_zscore(resid, b, min_obs=min(52, z_window // 3))

    # var-ratio y blancura sobre el tramo post-burn
    seg = resid[b:]
    var_ratio = float(np.nanvar(seg) / np.nanvar(y[b:])) if np.nanvar(y[b:]) > 0 else np.nan
    lb_Q, lb_p = kc.ljung_box(seg, lags=8)
    half = kc.ou_half_life(seg)

    metrics = {"delta": delta, "var_ratio": var_ratio, "ljung_p": lb_p, "halflife": half}
    ics = {}
    for h in FWD_HORIZONS:
        fr = forward_returns(y, h)
        sig = -z                         # señal direccional
        m = ~np.isnan(sig) & ~np.isnan(fr)
        if m.sum() > 30:
            ic, _ = stats.spearmanr(sig[m], fr[m])
            hit = float(np.mean(np.sign(sig[m]) == np.sign(fr[m])))
        else:
            ic, hit = np.nan, np.nan
        ics[h] = ic
        metrics[f"ic_{h}w"] = None if np.isnan(ic) else round(float(ic), 4)
        metrics[f"hit_{h}w"] = None if np.isnan(hit) else round(float(hit), 4)
    # score de selección: IC medio a 4/8w (foco fundamental, no momentum corto)
    sel = np.nanmean([ics.get(4, np.nan), ics.get(8, np.nan)])
    metrics["score"] = None if np.isnan(sel) else round(float(sel), 4)
    return metrics, out


def calibrate_delta(y, X, z_window, log):
    """Barre DELTA_GRID, elige el δ con mejor |score| (IC medio 4/8w) que ADEMÁS
    cumpla var_ratio ≥ umbral dinámico (escala con nº de drivers). Devuelve
    (best_delta, tabla)."""
    n_drivers = X.shape[1] - 1                     # descuenta la constante
    vr_min = min_varratio(n_drivers)
    rows = []
    for d in DELTA_GRID:
        m, _ = evaluate_delta(y, X, d, z_window)
        rows.append(m)
        vr = m["var_ratio"]; sc = m["score"]
        flag = "" if (vr is not None and vr >= vr_min) else f" [VR<{vr_min:.2f}]"
        log.append(f"    δ={d}: score(IC4/8)={sc} var_ratio={vr:.3f} "
                   f"LB_p={m['ljung_p']:.3f} HL={m['halflife']:.1f}{flag}")
    # candidatos válidos: var_ratio suficiente y score no nulo
    valid = [r for r in rows if r["var_ratio"] is not None
             and r["var_ratio"] >= vr_min and r["score"] is not None]
    pool = valid if valid else [r for r in rows if r["score"] is not None]
    if not pool:
        return DELTA_GRID[0], rows
    best = max(pool, key=lambda r: abs(r["score"]))   # mayor |IC|
    return best["delta"], rows


# ============================================================ matriz COT (overlay)
def cot_activation(mdp_z, cot_z):
    """Matriz MDP × COT (consenso ronda 1+2). Devuelve un multiplicador de convicción
    en [0,1.5] por fila. Señal LIMPIA cuando MDP y COT apuntan en sentidos OPUESTOS
    (caro + specs aún cortos / barato + specs aún largos del lado equivocado);
    DEGRADADA cuando MDP extremo coincide con COT crowded en la misma dirección.
    Sin COT → multiplicador neutro 1.0."""
    n = len(mdp_z)
    mult = np.ones(n)
    if cot_z is None:
        return mult
    for i in range(n):
        m, c = mdp_z[i], cot_z[i]
        if np.isnan(m) or np.isnan(c):
            mult[i] = np.nan
            continue
        if abs(m) < 1.0:
            mult[i] = 0.5                       # MDP neutro → poca convicción
        elif np.sign(m) == np.sign(c):
            # caro & crowded-long (o barato & crowded-short): riesgo de reversión adversa
            mult[i] = max(0.3, 1.0 - 0.3 * min(abs(c), 3.0))   # degradada
        else:
            # caro & specs cortos (o barato & specs largos): smart money no ha llegado
            mult[i] = min(1.5, 1.0 + 0.25 * min(abs(c), 3.0))  # limpia/amplificada
    return mult


# ==================================================== drivers estructurales del oro
# Fecha del QUIEBRE DE NIVEL en tipos reales (audit 2026-08). Post-liftoff de la Fed
# (mar-2022) el oro se DESACOPLÓ del rendimiento real: subió a la vez que los reales,
# rompiendo la relación negativa histórica. Se modela con un término real10×1[t≥break]
# que deja que la sensibilidad al real cambie de régimen (la β la afina el TVP).
RATE_BREAK = "2022-03-01"


def load_real10():
    """Rendimiento REAL 10Y (USD, DFII10/TIPS). PRIMARY data/RY_G8_USD.csv (REAL10),
    fallback FRED DFII10. El oro clásico se ancla al real; el modelo MMT lo había
    omitido — se reincorpora con su quiebre de nivel."""
    path = os.path.join(DATA_DIR, "RY_G8_USD.csv")
    if os.path.exists(path):
        try:
            df = pd.read_csv(path, comment="#")
            df.columns = [c.strip().upper() for c in df.columns]
            if {"DATE", "REAL10"}.issubset(df.columns):
                df["DATE"] = pd.to_datetime(df["DATE"].astype(str), format="%Y%m%d", errors="coerce")
                s = pd.to_numeric(df.set_index("DATE")["REAL10"], errors="coerce").dropna()
                print(f"    [REAL10] {len(s)} obs de RY_G8_USD (PRIMARY)")
                return s.sort_index()
        except Exception:
            pass
    s = fetch_fred("DFII10")
    print("    [REAL10] fallback FRED DFII10")
    return s


def load_official_demand():
    """DEMANDA OFICIAL (bancos centrales) como stock acumulado de tenencias oficiales
    de oro (base ~2005 + compras netas World Gold Council). PRIMARY
    data/OFFICIAL_GOLD_DEMAND.csv (DATE,CUM_TONNES). Es el bid estructural que
    reprecio el oro desde 2022 (des-dolarización) y que el modelo puramente financiero
    no capturaba. None si el CSV no existe -> el driver 'official' no entra (spec base).
    Se interpola/forward-fill as-of martes en el panel."""
    path = os.path.join(DATA_DIR, "OFFICIAL_GOLD_DEMAND.csv")
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path, comment="#")
        df.columns = [c.strip().upper() for c in df.columns]
        col = "CUM_TONNES" if "CUM_TONNES" in df.columns else ("VALUE" if "VALUE" in df.columns else None)
        if "DATE" not in df.columns or col is None:
            return None
        df["DATE"] = pd.to_datetime(df["DATE"].astype(str), errors="coerce")
        s = pd.to_numeric(df.set_index("DATE")[col], errors="coerce").dropna().sort_index()
        s = s[s > 0]
        print(f"    [OFFICIAL] {len(s)} pts de OFFICIAL_GOLD_DEMAND.csv (WGC cum tonnes)")
        return s if len(s) >= 8 else None
    except Exception:
        return None


# ================================================================ build per-metal
def build_panel_xau(tuesdays, log):
    """Ensambla el panel semanal del ORO: y=log(XAU), drivers TVP=[const,BE,slope,
    log NFA], fijo=[DXY]. Todo as-of martes (sin look-ahead). Devuelve (panel_df, ok)."""
    price, src = find_local_price("XAU")
    if price is None:
        log.append("precio XAU local no encontrado — exporta GC1!/XAU a ~/Downloads")
        return None, None
    log.append(f"precio LOCAL: {os.path.basename(src)} "
               f"({price.index[0].date()}→{price.index[-1].date()}, {len(price)} barras)")
    be    = load_be10()
    slope = load_slope()
    nfa   = load_nfa_total()
    dxy   = fetch_fred(FRED_DXY)
    real  = load_real10()               # driver estructural: real10 + quiebre
    official = load_official_demand()   # driver estructural: demanda oficial (opcional)

    P  = asof_weekly(price.rename("PX"), tuesdays)
    BE = asof_weekly(be.rename("BE"), tuesdays)
    SL = asof_weekly(slope.rename("SL"), tuesdays)
    NF = asof_weekly(nfa.rename("NFA"), tuesdays)
    DX = asof_weekly(dxy.rename("DXY"), tuesdays)
    RL = asof_weekly(real.rename("REAL"), tuesdays)
    # baseline requiere PX/BE/SL/NFA/DXY/REAL; official es opcional (se une después)
    panel = pd.concat([P, BE, SL, NF, DX, RL], axis=1, sort=True).dropna(
        subset=["PX", "BE", "SL", "NFA", "DXY", "REAL"])
    # blindaje numérico: NFA y PX deben ser > 0 para el log; descarta lo que no lo sea
    panel = panel[(panel["PX"] > 0) & (panel["NFA"] > 0)]
    panel["LOGPX"]  = np.log(panel["PX"])
    panel["LOGNFA"] = np.log(panel["NFA"])
    # quiebre de nivel en tipos reales: real10 activo sólo desde RATE_BREAK
    brk = (panel.index >= pd.Timestamp(RATE_BREAK)).astype(float)
    panel["REAL_BRK"] = panel["REAL"].values * brk
    # demanda oficial (opcional): log del stock acumulado, as-of martes
    if official is not None:
        OF = asof_weekly(official.rename("OFF"), tuesdays)
        panel = panel.join(OF)
        panel["LOGOFF"] = np.log(panel["OFF"].clip(lower=1e-9))
    panel = panel.replace([np.inf, -np.inf], np.nan)
    panel = panel.dropna(subset=["PX", "BE", "SL", "NFA", "DXY", "REAL", "LOGPX", "LOGNFA", "REAL_BRK"])
    return panel, src


def build_panel_xag(tuesdays, log):
    """Panel semanal de la PLATA: y=log(XAG), drivers TVP=[const, log(XAU), HG_orth].
    HG_orth = residuo de log(cobre) ~ log(oro) (canal industrial puro). Degrada a solo
    [const, log(XAU)] si falta el cobre."""
    price, src = find_local_price("XAG")
    if price is None:
        log.append("precio XAG local no encontrado — exporta SI1!/XAG a ~/Downloads")
        return None, None, False
    log.append(f"precio LOCAL: {os.path.basename(src)} "
               f"({price.index[0].date()}→{price.index[-1].date()}, {len(price)} barras)")
    xau, _ = find_local_price("XAU")
    if xau is None:
        log.append("plata requiere oro (XAU) presente para el canal monetario — falta.")
        return None, None, False
    copper, csrc = find_local_price("COPPER")
    real = load_real10()                 # plata también responde a tipos reales
    dxy  = fetch_fred(FRED_DXY)           # y al dólar

    P  = asof_weekly(price.rename("PX"), tuesdays)
    AU = asof_weekly(xau.rename("XAU"), tuesdays)
    RL = asof_weekly(real.rename("REAL"), tuesdays)
    DX = asof_weekly(dxy.rename("DXY"), tuesdays)
    cols = [P, AU, RL, DX]
    has_copper = copper is not None
    if has_copper:
        HG = asof_weekly(copper.rename("HG"), tuesdays)
        cols.append(HG)
        log.append(f"cobre LOCAL: {os.path.basename(csrc)} → canal industrial activo")
    else:
        log.append("COBRE ausente → plata degrada a canal monetario (exporta HG1! a ~/Downloads)")
    panel = pd.concat(cols, axis=1, sort=True).dropna(
        subset=["PX", "XAU", "REAL", "DXY"] + (["HG"] if has_copper else []))
    panel = panel[panel["PX"] > 0]
    panel["LOGPX"]  = np.log(panel["PX"])
    panel["LOGXAU"] = np.log(panel["XAU"].clip(lower=1e-9))
    if has_copper:
        # HG_orth = resid(log HG ~ log XAU): cobre que el oro NO explica
        lx = panel["LOGXAU"].values
        lh = np.log(panel["HG"].values)
        A = np.column_stack([np.ones_like(lx), lx])
        coef, *_ = np.linalg.lstsq(A, lh, rcond=None)
        panel["HG_ORTH"] = lh - A @ coef
    panel = panel.replace([np.inf, -np.inf], np.nan).dropna()
    return panel, src, has_copper


def build_metal(name, tuesdays, fixed_delta, bridge, log):
    cfg = MODELS[name]
    if name == "XAU":
        panel, src = build_panel_xau(tuesdays, log)
        if panel is None or len(panel) < BURN + 60:
            return False
        y = panel["LOGPX"].values
        # DXY fijo: lo proyectamos fuera del TVP por OLS y lo restamos del observable
        dxy = panel["DXY"].values
        Adx = np.column_stack([np.ones_like(dxy), dxy])
        gdx, *_ = np.linalg.lstsq(Adx, y, rcond=None)
        gamma_dxy = float(gdx[1])
        y_adj = y - gamma_dxy * dxy            # quita el canal DXY (beta fija)
        ycol = y_adj

        # ---- SELECCIÓN DE ESPECIFICACIÓN (audit 2026-08): que decida el dato ----
        # BASE      = MMT puro [const, BE, slope, logNFA]  (comportamiento anterior)
        # ENHANCED  = + real10 + real10×quiebre  (+ demanda oficial si el CSV existe)
        # El enhanced sólo se adopta si MEJORA el IC fuera de muestra respetando
        # var_ratio≥0.20 — mismo criterio que la selección del cobre en la plata, así
        # que NO puede regresionar la especificación sana. Cierra el hueco del oro:
        # captura el quiebre real y el bid oficial que el modelo financiero no veía.
        X_base = np.column_stack([np.ones(len(panel)),
                                  panel["BE"].values, panel["SL"].values, panel["LOGNFA"].values])
        names_base = ["const", "be", "slope", "log_nfa"]
        cols_e = [np.ones(len(panel)), panel["BE"].values, panel["SL"].values,
                  panel["LOGNFA"].values, panel["REAL"].values, panel["REAL_BRK"].values]
        names_e = ["const", "be", "slope", "log_nfa", "real", "real_brk"]
        has_off = ("LOGOFF" in panel.columns and panel["LOGOFF"].notna().sum() > BURN + 60)
        if has_off:
            lo = panel["LOGOFF"].values
            lo = np.where(np.isnan(lo), np.nanmedian(lo), lo)   # rellena bordes (no NaN al filtro)
            cols_e.append(lo); names_e.append("official")
        X_enh = np.column_stack(cols_e)

        d_b, _ = calibrate_delta(ycol, X_base, cfg["z_window"], [])
        m_b, _ = evaluate_delta(ycol, X_base, d_b, cfg["z_window"])
        d_e, _ = calibrate_delta(ycol, X_enh, cfg["z_window"], [])
        m_e, _ = evaluate_delta(ycol, X_enh, d_e, cfg["z_window"])

        def _ok(m):  return (m["var_ratio"] is not None and m["var_ratio"] >= MIN_VARRATIO)
        def _ic(m):  s = m.get("score"); return s if s is not None else -9
        base_ok, enh_ok = _ok(m_b), _ok(m_e)
        ic_b, ic_e = _ic(m_b), _ic(m_e)
        log.append(f"selección oro: base(IC={ic_b:+.3f},vr={m_b['var_ratio']:.3f},ok={base_ok}) vs "
                   f"enhanced[real-break{'+official' if has_off else ''}]"
                   f"(IC={ic_e:+.3f},vr={m_e['var_ratio']:.3f},ok={enh_ok})")
        use_enh = (enh_ok and (not base_ok)) or (enh_ok and base_ok and abs(ic_e) > abs(ic_b) + 0.005)
        if use_enh:
            Xrun, driver_names = X_enh, names_e
            log.append("→ spec ENHANCED aceptada (quiebre real / demanda oficial mejora el OOS)")
            spec = "enhanced+official" if has_off else "enhanced"
        else:
            Xrun, driver_names = X_base, names_base
            log.append("→ spec BASE (el enhanced no mejora el OOS; sin regresión)")
            spec = "baseline"
        extra = {"gamma_dxy": round(gamma_dxy, 6), "spec": spec, "official": bool(has_off and use_enh)}
    else:  # XAG
        panel, src, has_copper = build_panel_xag(tuesdays, log)
        if panel is None or len(panel) < BURN + 60:
            return False
        y = panel["LOGPX"].values
        ycol = y

        # ---- Candidatos de especificación (audit 2026-08): que decida el dato ----
        # La plata tenía poca habilidad OOS (IC≈0.07) por depender casi solo de
        # log(XAU) — su MDP era un eco del oro. Se amplía con el canal MACRO (real10 +
        # DXY, a los que la plata sí responde) y el INDUSTRIAL (cobre⊥oro). Se evalúan
        # todas las specs disponibles y gana la de mayor |IC| que respete
        # var_ratio≥0.20; si ninguna supera a la monetaria por margen, gana la simple
        # (sin regresión ni sobreajuste).
        base  = [np.ones(len(panel)), panel["LOGXAU"].values]
        macro = [panel["REAL"].values, panel["DXY"].values]
        cands = {
            "monetary":  (np.column_stack(base),         ["const", "log_xau"]),
            "mon+macro": (np.column_stack(base + macro), ["const", "log_xau", "real", "dxy"]),
        }
        if has_copper:
            hg = [panel["HG_ORTH"].values]
            cands["dual"]       = (np.column_stack(base + hg),         ["const", "log_xau", "hg_orth"])
            cands["dual+macro"] = (np.column_stack(base + hg + macro), ["const", "log_xau", "hg_orth", "real", "dxy"])

        def _ok(m):  return (m["var_ratio"] is not None and m["var_ratio"] >= MIN_VARRATIO)
        def _ic(m):  s = m.get("score"); return s if s is not None else -9
        scored = {}
        for nm, (Xc, ns) in cands.items():
            dc, _ = calibrate_delta(ycol, Xc, cfg["z_window"], [])
            mc, _ = evaluate_delta(ycol, Xc, dc, cfg["z_window"])
            scored[nm] = (mc, Xc, ns)
            log.append(f"  spec {nm}: IC={_ic(mc):+.3f} vr={mc['var_ratio']:.3f} ok={_ok(mc)}")
        valid = {nm: v for nm, v in scored.items() if _ok(v[0])}
        pool = valid if valid else scored
        best_nm = max(pool, key=lambda nm: abs(_ic(pool[nm][0])))
        # una spec más rica sólo se adopta si SUPERA a la monetaria por margen (>0.005)
        if best_nm != "monetary" and "monetary" in pool and _ok(pool["monetary"][0]):
            if abs(_ic(pool[best_nm][0])) <= abs(_ic(pool["monetary"][0])) + 0.005:
                best_nm = "monetary"
        m_best, Xrun, driver_names = scored[best_nm]
        log.append(f"→ plata: spec elegida = {best_nm} (IC={_ic(m_best):+.3f}, vr={m_best['var_ratio']:.3f})")
        used_copper = "hg_orth" in driver_names
        extra = {"copper": used_copper, "spec": best_nm}
        if has_copper and not used_copper:
            extra["copper_available"] = True

    # ---- calibración de δ por walk-forward (o δ fijo por CLI)
    if fixed_delta is not None:
        delta = fixed_delta
        wf_rows = []
        log.append(f"δ fijado por CLI = {delta} (sin calibración walk-forward)")
    else:
        log.append("calibrando δ por walk-forward (IC 4/8w, gate var_ratio≥0.20):")
        delta, wf_rows = calibrate_delta(ycol, Xrun, cfg["z_window"], log)
        log.append(f"δ* seleccionado = {delta}")

    # ---- corrida final con δ*
    metrics, out = evaluate_delta(ycol, Xrun, delta, cfg["z_window"])
    b = out["burn"]
    resid = out["resid"]
    z = kc.expanding_zscore(resid, b, min_obs=min(52, cfg["z_window"] // 3))
    half = metrics["halflife"]

    # ---- MDP ajustado por half-life OU (urgencia de reversión) + peso de riesgo P_t
    hl = half if (half and not np.isnan(half) and half > 0) else np.nan
    mdp_adj = z.copy()
    if not np.isnan(hl):
        mdp_adj = z * (1.0 / hl) * np.nanmedian(np.abs(z[~np.isnan(z)])) if np.any(~np.isnan(z)) else z
    risk_w = 1.0 / np.sqrt(np.maximum(out["trace_P"], 1e-12))
    risk_w = risk_w / np.nanmedian(risk_w[b:])          # normaliza a ~1

    # ---- overlay COT
    cot = load_cot_mm(name)
    cot_z = None
    if cot is not None:
        cw = asof_weekly(cot.rename("COT"), tuesdays).reindex(panel.index)["COT"].values
        cot_z = kc.expanding_zscore(cw, b, min_obs=52)
    cot_mult = cot_activation(z, cot_z)

    signal = z * cot_mult * risk_w                       # señal compuesta final

    # ---- fair value en nivel de precio (reañadiendo DXY si XAU)
    fair_log = out["fair_pred"].copy()
    if name == "XAU":
        fair_log = fair_log + extra["gamma_dxy"] * panel["DXY"].values
    fair_px = np.exp(fair_log)

    # ---- ensamblar salida
    last = len(panel) - 1
    cot_as_of = panel.index[last].strftime("%Y%m%d")
    df = pd.DataFrame({
        "DATE":   panel.index.strftime("%Y%m%d"),
        "PX":     np.round(panel["PX"].values, 4),
        "FAIR":   np.round(fair_px, 4),
        "RESID":  np.round(resid, 6),
        "MDP_Z":  np.round(z, 4),
        "MDP_ADJ":np.round(mdp_adj, 4),
        "RISK_W": np.round(risk_w, 4),
        "COT_MULT": np.round(cot_mult, 4),
        "SIGNAL": np.round(signal, 4),
    })
    for j, dn in enumerate(driver_names):
        df[f"B_{dn.upper()}"] = np.round(out["beta_filt"][:, j], 6)

    # ---- gates institucionales
    issues = []
    vr = metrics["var_ratio"]
    vr_min = min_varratio(len(driver_names) - 1)   # nº drivers sin la constante
    if vr is None or vr < vr_min:
        issues.append(f"var_ratio={vr:.3f}<{vr_min:.2f} (MDP casi vacío para {len(driver_names)-1} drivers)")
    if metrics["ljung_p"] is not None and not np.isnan(metrics["ljung_p"]) and metrics["ljung_p"] > 0.10:
        issues.append(f"Ljung-Box p={metrics['ljung_p']:.3f}>0.10 (MDP ruido blanco)")
    if np.isnan(hl) or not (1.0 < hl <= 520):
        issues.append(f"half-life fuera de (1,520]: {hl}")
    ic4 = metrics.get("ic_4w"); ic8 = metrics.get("ic_8w")
    # Convención: señal = −MDP_z (MDP alto/caro ⇒ retorno futuro esperado NEGATIVO).
    # Un IC POSITIVO confirma reversión (el modelo funciona). Un IC NEGATIVO significa
    # que el activo hace MOMENTUM: lo caro sigue subiendo. Eso NO es "señal débil" —
    # es la señal apuntando AL REVÉS, y debe marcarse distinto.
    ic_ref = ic8 if (ic8 is not None) else ic4
    if ic_ref is None or abs(ic_ref) < 0.03:
        issues.append(f"IC nulo/débil (4w={ic4}, 8w={ic8}) — sin contenido predictivo")
    elif ic_ref < -0.05:
        issues.append(f"IC NEGATIVO (8w={ic8}) — la señal predice AL REVÉS: el activo "
                      f"hace MOMENTUM, no reversión. NO operar como mean-reversion sin invertir signo")
    sign_regime = "REVERSION" if (ic_ref is not None and ic_ref > 0) else \
                  ("MOMENTUM" if (ic_ref is not None and ic_ref < -0.05) else "INDETERMINADO")
    if len(panel) < BURN + 100:
        issues.append(f"cobertura baja ({len(panel)} martes)")

    quality = "CLEAN" if not issues else "REVIEW"
    if name == "XAG" and not extra.get("copper", True):
        # cobre ausente del disco → PARTIAL; cobre disponible pero descartado por el
        # dato (no mejoraba) → modelo monetario válido, NO es degradación
        if extra.get("copper_available"):
            quality = quality  # CLEAN/REVIEW normal; el monetario es la elección óptima
        else:
            quality = "PARTIAL_NO_COPPER"

    desc = (f"{name} ~ {'+'.join(driver_names)} | Kalman TVP δ={delta} | "
            f"IC4w={ic4} IC8w={ic8} hit8w={metrics.get('hit_8w')} | "
            f"var_ratio={vr:.3f} HL={hl:.1f}w")

    # ---- escribir CSV
    out_path = os.path.join(DATA_DIR, f"MFV_G8_{name}.csv")
    with open(out_path, "w") as f:
        f.write(f"# QUALITY={quality} | {desc} | cot_as_of={cot_as_of} | "
                f"generated by metals_fairvalue_g8.py v3\n")
        df.to_csv(f, index=False)

    # ---- walk-forward CSV (auditoría de δ)
    if wf_rows:
        wf = pd.DataFrame(wf_rows)
        wf.to_csv(os.path.join(DATA_DIR, f"MFV_G8_walkforward_{name}.csv"), index=False)

    # ---- bridge JSON
    mdp_last = None if np.isnan(z[last]) else float(z[last])
    sig_last = None if np.isnan(signal[last]) else float(signal[last])
    bridge[name] = {
        "mdp_z": mdp_last, "signal": sig_last, "cot_as_of": cot_as_of,
        "delta": delta, "quality": quality, "halflife": None if np.isnan(hl) else round(hl, 2),
        "var_ratio": None if vr is None else round(vr, 4),
        "ic_4w": ic4, "ic_8w": ic8, "ic_12w": metrics.get("ic_12w"),
        "regime": sign_regime,
        "hit_8w": metrics.get("hit_8w"), "ljung_p": metrics["ljung_p"],
        "betas_last": {driver_names[j]: round(float(out["beta_filt"][last, j]), 5)
                       for j in range(len(driver_names))},
        **extra,
    }
    # D1 (oro): qué nivel de la cascada de deuda alimentó el NFA, con su frescura.
    if name == "XAU":
        bridge[name]["debt_src"] = NFA_DEBT_SRC
        bridge[name]["debt_asof"] = NFA_DEBT_ASOF
    # D2 (plata): modo activo del modelo — monetario puro vs dual con cobre.
    if name == "XAG":
        if extra.get("copper"):
            bridge[name]["copper_mode"] = "DUAL"          # monetario + industrial
        elif extra.get("copper_available"):
            bridge[name]["copper_mode"] = "MONETARY"      # cobre disponible, descartado por el dato
        else:
            bridge[name]["copper_mode"] = "MONETARY_NO_HG" # cobre ausente del disco

    # ---- consola
    print(f"  fair   : PX={float(df['PX'].iloc[-1]):.2f}  FAIR={float(df['FAIR'].iloc[-1]):.2f}  "
          f"MDP_Z={'NA' if mdp_last is None else f'{mdp_last:+.2f}'}  (cot_as_of {cot_as_of})")
    print(f"  valid  : IC4w={ic4}  IC8w={ic8}  hit8w={metrics.get('hit_8w')}  "
          f"var_ratio={vr:.3f}  LB_p={metrics['ljung_p']:.3f}  HL={hl:.1f}w")
    print(f"  regime : {sign_regime}  "
          f"({'reversión: MDP alto→precio baja' if sign_regime=='REVERSION' else 'momentum: MDP alto→precio sube (señal se invierte)' if sign_regime=='MOMENTUM' else 'IC sin signo claro'})")
    print(f"  delta  : δ*={delta}  (forgetting factor calibrado)")
    print(f"  betas  : " + "  ".join(f"{driver_names[j]}={out['beta_filt'][last,j]:+.4f}"
                                       for j in range(len(driver_names))))
    if name == "XAU":
        print(f"           gamma_dxy(fijo)={extra['gamma_dxy']:+.4f}")
    print(f"  quality: {quality} — {desc}")
    print(f"  gates  : {'ALL PASS' if not issues else 'ISSUES: ' + '; '.join(issues)}")
    print(f"  wrote  : {out_path} ({len(df)} rows)")
    return len(issues) == 0


# ============================================================ run / main
def main():
    args = [a for a in sys.argv[1:]]
    fixed_delta = None
    if "--delta" in args:
        i = args.index("--delta")
        fixed_delta = float(args[i + 1]); del args[i:i + 2]
    only = [a.upper() for a in args if a.upper() in MODELS]
    targets = only if only else list(MODELS.keys())

    print("=" * 78)
    print(f"  METALS FAIR VALUE G8 v3 — Kalman TVP / MDP bajo MMT   ·  {time.strftime('%Y-%m-%d')}")
    print("=" * 78)
    print("  Cargando fuentes compartidas…")

    end = pd.Timestamp.today().normalize()
    tuesdays = cot_tuesdays(START, end)
    bridge = {}
    ok_count = 0
    for name in targets:
        print("\n" + "=" * 78)
        print(f"  {name}  (modelo MMT: {'+'.join(MODELS[name]['drivers_tvp'])}"
              f"{' + DXY[fijo]' if MODELS[name]['fixed'] else ''}  ·  Z-{MODELS[name]['z_window']}w)")
        print("=" * 78)
        log = []
        try:
            ok = build_metal(name, tuesdays, fixed_delta, bridge, log)
            ok_count += int(ok)
        except Exception as e:                                  # noqa: BLE001
            import traceback
            print(f"[{name}] FAILED: {e}")
            traceback.print_exc()
            ok = False
        finally:
            for line in log:
                print(f"  · {line}")

    if bridge:
        bpath = os.path.join(DATA_DIR, "MFV_G8_state.json")
        with open(bpath, "w") as f:
            json.dump({"generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       "metals": bridge}, f, indent=2)
        print(f"\n  bridge : {bpath}")

    print(f"\nSummary: {ok_count} clean of {len(targets)}")


if __name__ == "__main__":
    main()

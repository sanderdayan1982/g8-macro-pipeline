#!/usr/bin/env python3
"""
G8 Macro Pipeline — dashboard_alerts.py  v1.0  (2026-09-12)
=============================================================
Un mensaje de Telegram al día, SOLO si algo cambió en el dashboard.
Lee los ficheros que ya están en el repo (cero descargas, cero coste) y
compara contra su propio estado anterior (data/alerts/state.json).

Doctrina
  · Regla 8: ningún umbral inventado. Los umbrales de nivel son los que el
    dashboard ya pinta (floor badges 2/10 bp, |z|>2, TP/NOM 60%, COT
    thresholds del JSON). Los umbrales de MOVIMIENTO son percentiles rolling
    252 obs de la propia serie (P95 de |Δ|), medidos cada día.
  · Histéresis (Schmitt): un estado entra en P95 y sale en P90; |z| entra en
    2.0 y sale en 1.5. Nunca parpadea, nunca repite "sigue alto".
  · Solo se avisa en el CAMBIO de estado. Sin cambios → no se envía nada.
  · Ley 2 (fallos ruidosos): un fichero ausente/ilegible se reporta como
    línea DQM, jamás se silencia. El notificador nunca rompe el pipeline
    (exit 0 siempre).

Uso
  python3 scripts/dashboard_alerts.py            # normal (envía si hay cambios)
  python3 scripts/dashboard_alerts.py --dry-run  # imprime, no envía, no guarda estado
  python3 scripts/dashboard_alerts.py --baseline # fija el estado actual y envía un resumen
Secrets (env): TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  (los mismos del G8 Port Run)

Secciones cubiertas (todas las divisas con dato en repo)
  §02 floor spreads   USD EUR GBP JPY CAD AUD   badge AMPLE/TIGHT/PRESSURE · |z| 252d
  §03 policy rates    USD(IORB) EUR(DFR) GBP JPY CHF AUD CAD(BoC) NZD(OCR)   cambio de tasa
  §04 term premium    USD EUR JPY GBP CAD AUD   |ΔTP 1d| > P95 · TP/NOM cruza 60 %
  §01 spread vs USD   EUR JPY GBP CAD AUD (NOM10 − NOM10_USD)   |z| 252d · |Δ1w| > P95
  §06/§07 metales     XAU XAG   cambio de régimen · MDP z cruza ±2 · |Δz sem| > P95
  §08 strike walls    EUR JPY GBP AUD CAD CHF   nueva sesión: tarjeta completa + cambios
  §09 COT             7 divisas + USD + XAU/XAG   nuevo reporte: cambios de estado / EXT
  §05 DQM             frescura de los feeds del repo (presupuesto de sources/registry.csv)
"""
import csv
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from notify_telegram import send as tg_send
except Exception:                       # pragma: no cover
    def tg_send(text):
        print("telegram: notify_telegram.py not importable — message:\n" + text)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
STATE_PATH = os.path.join(DATA, "alerts", "state.json")
REGISTRY = os.path.join(ROOT, "sources", "registry.csv")

# ── umbrales de NIVEL: los del dashboard (index.html), no inventados ─────────
FLOOR_TIGHT_BP, FLOOR_PRESS_BP = 2.0, 10.0          # §02 badges
Z_IN, Z_OUT = 2.0, 1.5                              # |z| histéresis
TPNOM_IN, TPNOM_OUT = 0.60, 0.55                    # eje fiscal, elemento 0
MDP_IN, MDP_OUT = 2.0, 1.5                          # §06/07 mdp_z
# ── umbrales de MOVIMIENTO: percentiles rolling, medidos ─────────────────────
WIN = 252
P_IN, P_OUT = 95.0, 90.0
# ── walls ────────────────────────────────────────────────────────────────────
WALL_NEAR_PCT = 0.005                               # spot a <0.5 % de un wall
OI_FRONT_JUMP = 0.15                                # |Δ OI front| > 15 %
INV = {"JPY", "CAD", "CHF"}                         # CME cotiza XXX/USD; tú operas USD/XXX
PAIR = {"EUR": "EUR/USD", "JPY": "USD/JPY", "GBP": "GBP/USD",
        "AUD": "AUD/USD", "CAD": "USD/CAD", "CHF": "USD/CHF"}

TODAY = datetime.now(timezone.utc).date()
NOTES = []            # líneas DQM / problemas de lectura


# ═════════════════════════════════════════════════════════════════════════════
# utilidades
# ═════════════════════════════════════════════════════════════════════════════
def note(msg):
    NOTES.append(msg)


def read_series(name, col="CLOSE", datecol="DATE"):
    """CSV del repo → list[(date, float)] ordenada. Ignora líneas '#'. None si falla."""
    p = os.path.join(DATA, name)
    if not os.path.exists(p):
        note("DQM · %s ausente" % name)
        return None
    out = []
    try:
        with open(p, encoding="utf-8", errors="ignore") as fh:
            rows = [l for l in fh if l.strip() and not l.startswith("#")]
        for r in csv.DictReader(rows):
            d = (r.get(datecol) or "").strip().replace("-", "")[:8]
            v = r.get(col)
            if len(d) != 8 or v in (None, "", "NA", "nan"):
                continue
            try:
                out.append((date(int(d[:4]), int(d[4:6]), int(d[6:8])), float(v)))
            except ValueError:
                continue
    except Exception as e:
        note("DQM · %s ilegible (%s)" % (name, e))
        return None
    out.sort()
    return out or None


def read_json(name):
    p = os.path.join(DATA, name)
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as e:
        note("DQM · %s ilegible (%s)" % (name, e))
        return None


def pct(values, q):
    """Percentil q (0-100) por interpolación lineal sobre la lista (≥30 obs)."""
    v = sorted(x for x in values if x is not None)
    if len(v) < 30:
        return None
    k = (len(v) - 1) * q / 100.0
    f, c = int(k), min(int(k) + 1, len(v) - 1)
    return v[f] + (v[c] - v[f]) * (k - f)


def zscore(series_vals):
    win = series_vals[-WIN:]
    if len(win) < 60:
        return None
    mu = sum(win) / len(win)
    sd = (sum((x - mu) ** 2 for x in win) / len(win)) ** 0.5
    return None if sd < 1e-9 else (win[-1] - mu) / sd


def ffill_join(a, b):
    """Alinea b (forward-fill) sobre la rejilla de fechas de a → list[(d, va, vb)]."""
    out, bi, last = [], 0, None
    for d, va in a:
        while bi < len(b) and b[bi][0] <= d:
            last = b[bi][1]
            bi += 1
        if last is not None:
            out.append((d, va, last))
    return out


def bdays_between(d0, d1):
    n, d = 0, d0
    while d < d1:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def band_z(z, prev):
    """Estado de |z| con histéresis: '' | 'HI' | 'LO'."""
    if z is None:
        return prev or ""
    if prev in ("HI", "LO"):
        return prev if abs(z) >= Z_OUT else ""
    return "HI" if z >= Z_IN else "LO" if z <= -Z_IN else ""


def band_pct(val_abs, hist_abs, prev):
    """Estado 'EXT' si |Δ| supera P95 (sale por debajo de P90)."""
    p_in, p_out = pct(hist_abs, P_IN), pct(hist_abs, P_OUT)
    if p_in is None:
        return prev or "", None
    if prev == "EXT":
        return ("EXT" if val_abs >= p_out else ""), p_in
    return ("EXT" if val_abs >= p_in else ""), p_in


def fmt_sign(x, nd=1, unit=""):
    return ("%+." + str(nd) + "f%s") % (x, unit)


def rank_pct(val, hist):
    v = sorted(x for x in hist if x is not None)
    if not v:
        return None
    return 100.0 * sum(1 for x in v if x <= val) / len(v)


# ═════════════════════════════════════════════════════════════════════════════
# §02 floor spreads
# ═════════════════════════════════════════════════════════════════════════════
FLOORS = [("USD", "SOFR.csv", "FLOOR_USD.csv", "IORB"),
          ("EUR", "ESTR.csv", "FLOOR_EUR.csv", "DFR"),
          ("GBP", "SONIA.csv", "GB_POLICY.csv", "Bank Rate"),
          ("JPY", "TONA.csv", "JP_POLICY.csv", "Policy Rate"),
          ("CAD", "CORRA.csv", "FLOOR_CAD.csv", "BoC Target"),
          ("AUD", "AONIA.csv", "AU_POLICY.csv", "Cash Rate")]


def floor_badge(bp):
    return "PRESSURE" if bp > FLOOR_PRESS_BP else "TIGHT" if bp > FLOOR_TIGHT_BP else "AMPLE"


def check_floors(st, lines):
    s = st.setdefault("floors", {})
    for ccy, rfr, flr, ref in FLOORS:
        a, b = read_series(rfr), read_series(flr)
        if not a or not b:
            continue
        sp = [(d, (va - vb) * 100.0) for d, va, vb in ffill_join(a, b)]
        if not sp:
            continue
        vals = [v for _, v in sp]
        last, z = vals[-1], zscore(vals)
        badge = floor_badge(last)
        prev = s.get(ccy, {})
        zb = band_z(z, prev.get("zband"))
        chg = []
        if prev.get("badge") and prev["badge"] != badge:
            chg.append("%s → %s" % (prev["badge"], badge))
        if prev.get("zband", "") != zb and zb:
            chg.append("z %+.1f (%s)" % (z, "extremo alto" if zb == "HI" else "extremo bajo"))
        elif prev.get("zband") in ("HI", "LO") and not zb:
            chg.append("z %+.1f normaliza" % (z if z is not None else 0.0))
        if chg:
            lines.append("§02 %s %s−%s %+.1f bp · %s" % (ccy, rfr[:-4], ref, last, " · ".join(chg)))
        s[ccy] = {"badge": badge, "zband": zb, "last": round(last, 2), "date": sp[-1][0].isoformat()}


# ═════════════════════════════════════════════════════════════════════════════
# §03 policy rates
# ═════════════════════════════════════════════════════════════════════════════
POLICY = [("USD", "FLOOR_USD.csv", "Fed IORB"), ("EUR", "FLOOR_EUR.csv", "ECB DFR"),
          ("GBP", "GB_POLICY.csv", "BoE Bank Rate"), ("JPY", "JP_POLICY.csv", "BoJ Policy Rate"),
          ("CHF", "CH_POLICY.csv", "SNB Policy Rate"), ("AUD", "AU_POLICY.csv", "RBA Cash Rate"),
          ("CAD", "FLOOR_CAD.csv", "BoC Target"), ("NZD", "NZD_OCR.csv", "RBNZ OCR")]


def check_policy(st, lines):
    s = st.setdefault("policy", {})
    for ccy, f, label in POLICY:
        ser = read_series(f)
        if not ser:
            continue
        last_d, last_v = ser[-1]
        prev = s.get(ccy)
        if prev and abs(prev["v"] - last_v) > 1e-6:
            lines.append("§03 %s %s: %.2f → %.2f (%+.0f bp) · %s"
                         % (ccy, label, prev["v"], last_v, (last_v - prev["v"]) * 100, last_d.isoformat()))
        s[ccy] = {"v": last_v, "date": last_d.isoformat()}


# ═════════════════════════════════════════════════════════════════════════════
# §04 term premium ACM  +  §01 spread vs USD
# ═════════════════════════════════════════════════════════════════════════════
ACM_CCY = ["USD", "EUR", "JPY", "GBP", "CAD", "AUD"]


def check_tp(st, lines):
    s = st.setdefault("tp", {})
    for ccy in ACM_CCY:
        tp = read_series("ACM_G8_%s.csv" % ccy, col="TP10")
        y10 = read_series("ACM_G8_%s.csv" % ccy, col="Y10_FIT")
        if not tp or len(tp) < 40:
            continue
        vals = [v for _, v in tp]
        d1 = [(vals[i] - vals[i - 1]) * 100 for i in range(1, len(vals))]      # bp
        hist = [abs(x) for x in d1[-WIN:]]
        last_d1 = d1[-1]
        prev = s.get(ccy, {})
        band, p95 = band_pct(abs(last_d1), hist, prev.get("band"))
        # TP / NOM (elemento 0 de la firma fiscal)
        ratio = None
        if y10 and abs(y10[-1][1]) > 1e-9:
            ratio = vals[-1] / y10[-1][1]
        fis_prev = prev.get("fiscal", False)
        fis = fis_prev
        if ratio is not None:
            fis = (ratio >= TPNOM_OUT) if fis_prev else (ratio >= TPNOM_IN)
        chg = []
        if band == "EXT" and prev.get("band") != "EXT":
            chg.append("ΔTP 1d %+.0f bp — P%.0f (umbral P95 = %.0f bp)"
                       % (last_d1, rank_pct(abs(last_d1), hist) or 0, p95 or 0))
        if ratio is not None and fis != fis_prev:
            chg.append("TP/NOM %.0f %% %s 60 %% — %s" % (ratio * 100, "cruza ▲" if fis else "vuelve ▼",
                                                          "revisa RTF10/Curva (eje fiscal)" if fis else "prima deja de dominar"))
        if chg:
            lines.append("§04 %s TP %.2f %% · %s" % (ccy, vals[-1], " · ".join(chg)))
        s[ccy] = {"band": band, "fiscal": fis, "tp": round(vals[-1], 4),
                  "ratio": None if ratio is None else round(ratio, 3), "date": tp[-1][0].isoformat()}


def check_vs_usd(st, lines):
    s = st.setdefault("vs_usd", {})
    usd = read_series("RY_G8_USD.csv", col="NOM10")
    if not usd:
        return
    for ccy in ["EUR", "JPY", "GBP", "CAD", "AUD"]:
        nom = read_series("RY_G8_%s.csv" % ccy, col="NOM10")
        if not nom:
            continue
        sp = [(d, va - vb) for d, va, vb in ffill_join(nom, usd)]          # pp, + = rinde más que USD
        if len(sp) < 60:
            continue
        vals = [v for _, v in sp]
        z = zscore(vals)
        d1w = [(vals[i] - vals[i - 5]) * 100 for i in range(5, len(vals))]  # bp/semana
        hist = [abs(x) for x in d1w[-WIN:]]
        prev = s.get(ccy, {})
        zb = band_z(z, prev.get("zband"))
        mb, p95 = band_pct(abs(d1w[-1]), hist, prev.get("mband"))
        chg = []
        if zb and prev.get("zband", "") != zb:
            chg.append("z %+.1f — %s de su año" % (z, "techo" if zb == "HI" else "suelo"))
        elif prev.get("zband") in ("HI", "LO") and not zb:
            chg.append("z %+.1f normaliza" % (z or 0.0))
        if mb == "EXT" and prev.get("mband") != "EXT":
            chg.append("Δ1w %+.0f bp — P%.0f" % (d1w[-1], rank_pct(abs(d1w[-1]), hist) or 0))
        if chg:
            lines.append("§01 %s−USD 10Y %+.0f bp · %s" % (ccy, vals[-1] * 100, " · ".join(chg)))
        s[ccy] = {"zband": zb, "mband": mb, "spread_bp": round(vals[-1] * 100, 1), "date": sp[-1][0].isoformat()}


# ═════════════════════════════════════════════════════════════════════════════
# §06 / §07 metales MDP
# ═════════════════════════════════════════════════════════════════════════════
def check_metals(st, lines):
    s = st.setdefault("metals", {})
    js = read_json("MFV_G8_state.json")
    if not js:
        return
    for m in ("XAU", "XAG"):
        cur = (js.get("metals") or {}).get(m)
        if not cur:
            continue
        ser = read_series("MFV_G8_%s.csv" % m, col="MDP_Z")
        z = cur.get("mdp_z")
        prev = s.get(m, {})
        zb = band_z(z, prev.get("zband"))
        chg = []
        if prev.get("regime") and prev["regime"] != cur.get("regime"):
            chg.append("régimen %s → %s" % (prev["regime"], cur.get("regime")))
        if zb and prev.get("zband", "") != zb:
            chg.append("MDP z %+.2f cruza %s" % (z, "+2 (caro vs ancla NFA)" if zb == "HI" else "−2 (barato vs ancla NFA)"))
        elif prev.get("zband") in ("HI", "LO") and not zb:
            chg.append("MDP z %+.2f normaliza" % (z or 0.0))
        if ser and len(ser) > 40:
            dz = [ser[i][1] - ser[i - 1][1] for i in range(1, len(ser))]
            hist = [abs(x) for x in dz[-WIN:]]
            mb, _ = band_pct(abs(dz[-1]), hist, prev.get("mband"))
            if mb == "EXT" and prev.get("mband") != "EXT" and ser[-1][0].isoformat() != prev.get("date"):
                chg.append("Δz semana %+.2f — P%.0f" % (dz[-1], rank_pct(abs(dz[-1]), hist) or 0))
        else:
            mb = prev.get("mband", "")
        if prev.get("quality") and prev["quality"] != cur.get("quality"):
            chg.append("quality %s → %s" % (prev["quality"], cur.get("quality")))
        if chg:
            px = ser[-1][1] if False else None
            lines.append("§0%s %s MDP z %+.2f · %s · %s" % ("6" if m == "XAU" else "7", m, z or 0.0,
                                                           cur.get("regime"), " · ".join(chg)))
        s[m] = {"regime": cur.get("regime"), "zband": zb, "mband": mb, "quality": cur.get("quality"),
                "z": None if z is None else round(z, 3),
                "date": ser[-1][0].isoformat() if ser else None}


# ═════════════════════════════════════════════════════════════════════════════
# §08 strike walls (CME options) — tarjeta completa en cada sesión nueva
# ═════════════════════════════════════════════════════════════════════════════
def user_lvl(ccy, x):
    return 1.0 / x if ccy in INV else x


def fmt_lvl(ccy, x):
    v = user_lvl(ccy, x)
    return ("%.2f" if ccy == "JPY" else "%.4f") % v


def walls_user(ccy, entry):
    """Convierte los walls a la convención del operador (USD/XXX invertidos, call↔put)."""
    ref = user_lvl(ccy, entry["ref"])
    ws = []
    for w in entry.get("walls", []):
        k = user_lvl(ccy, w["k"])
        c, p = (w["p"], w["c"]) if ccy in INV else (w["c"], w["p"])   # un put CME = call del usuario
        ws.append({"k": k, "oi": w["oi"], "c": c, "p": p})
    ws.sort(key=lambda w: -w["oi"])
    return ref, ws


def describe_walls(ccy, entry):
    ref, ws = walls_user(ccy, entry)
    if not ws:
        return None
    pin = ws[0]
    above = [w for w in ws if w["k"] > ref]
    below = [w for w in ws if w["k"] < ref]
    call_wall = max(above, key=lambda w: w["c"]) if above else None
    put_wall = max(below, key=lambda w: w["p"]) if below else None
    near = min(ws, key=lambda w: abs(w["k"] - ref))
    near_pct = abs(near["k"] - ref) / ref
    return {"ref": ref, "pin": pin, "call": call_wall, "put": put_wall, "near": near,
            "near_pct": near_pct, "oi_front": entry.get("oi_front"), "front": entry.get("front"),
            "pata": entry.get("pata"), "band": entry.get("band")}


def k_str(ccy, w):
    return None if w is None else "%s (%.1fk)" % (("%.2f" if ccy == "JPY" else "%.4f") % w["k"], w["oi"] / 1000.0)


def check_walls(st, lines):
    s = st.setdefault("walls", {})
    js = read_json("OPTIONS_SURFACE.json")
    if not js:
        return
    sess = js.get("latest_session")
    if not sess or sess == s.get("session"):
        return                                              # ninguna sesión nueva → silencio
    latest, prevj = js.get("latest") or {}, js.get("prev") or {}
    gate0 = js.get("gate0") or {}
    lines.append("§08 STRIKE WALLS · sesión %s (front = 1er vencimiento > sesión)" % sess)
    for ccy in ["EUR", "JPY", "GBP", "AUD", "CAD", "CHF"]:
        e = latest.get(ccy)
        if not e or not e.get("walls"):
            lines.append("  %s — sin cadena (%s)" % (PAIR[ccy], (gate0.get(ccy) or {}).get("verdict", "NA")))
            continue
        d = describe_walls(ccy, e)
        pe = s.get(ccy) or {}
        pd_ = describe_walls(ccy, prevj[ccy]) if prevj.get(ccy) and prevj[ccy].get("walls") else None
        parts = ["  %s %s" % (PAIR[ccy], fmt_lvl(ccy, e["ref"]))]
        parts.append("pin %s" % k_str(ccy, d["pin"]))
        parts.append("call wall %s" % (k_str(ccy, d["call"]) or "—"))
        parts.append("put wall %s" % (k_str(ccy, d["put"]) or "—"))
        if d["put"] and d["call"]:
            parts.append("corredor %s–%s" % (("%.2f" if ccy == "JPY" else "%.4f") % d["put"]["k"],
                                             ("%.2f" if ccy == "JPY" else "%.4f") % d["call"]["k"]))
        flags = []
        if d["near_pct"] < WALL_NEAR_PCT:
            flags.append("⚠ spot a %.2f %% del wall %s" % (d["near_pct"] * 100, ("%.2f" if ccy == "JPY" else "%.4f") % d["near"]["k"]))
        pin_prev = pe.get("pin") if pe else (pd_["pin"]["k"] if pd_ else None)
        if pin_prev is not None and abs(pin_prev - d["pin"]["k"]) > 1e-9:
            flags.append("pin migró %s → %s" % (("%.2f" if ccy == "JPY" else "%.4f") % pin_prev,
                                                ("%.2f" if ccy == "JPY" else "%.4f") % d["pin"]["k"]))
        oi_prev = pe.get("oi_front") if pe else (pd_["oi_front"] if pd_ else None)
        if oi_prev and d["oi_front"]:
            j = (d["oi_front"] - oi_prev) / oi_prev
            if abs(j) > OI_FRONT_JUMP:
                flags.append("OI front %+.0f %% (%s)" % (j * 100, "dinero nuevo" if j > 0 else "cierre/roll"))
        front_prev = pe.get("front") if pe else (pd_["front"] if pd_ else None)
        if front_prev and front_prev != d["front"]:
            flags.append("nuevo front %s (mapa reconstruido)" % d["front"])
        if not d["pata"] or not d["band"]:
            flags.append("cadena delgada — lectura de baja confianza")
        lines.append(" · ".join(parts) + ((" · " + " · ".join(flags)) if flags else ""))
        s[ccy] = {"pin": d["pin"]["k"], "oi_front": d["oi_front"], "front": d["front"]}
    s["session"] = sess


# ═════════════════════════════════════════════════════════════════════════════
# §09 COT — solo cuando entra un reporte nuevo
# ═════════════════════════════════════════════════════════════════════════════
def check_cot(st, lines):
    s = st.setdefault("cot", {})
    js = read_json("pos_g8_cot.json")
    if not js:
        return
    rep = js.get("report_date")
    if not rep or rep == s.get("report"):
        return
    items = list(js.get("currencies") or [])
    for extra in ("usd", "metals"):
        v = js.get(extra)
        if isinstance(v, dict) and "ccy" in v:
            items.append(v)
        elif isinstance(v, dict):
            items.extend(x for x in v.values() if isinstance(x, dict) and "ccy" in x)
        elif isinstance(v, list):
            items.extend(x for x in v if isinstance(x, dict) and "ccy" in x)
    out = []
    for c in items:
        k = c.get("ccy")
        prev = (s.get("states") or {}).get(k)
        cur = c.get("state")
        tag = []
        if prev and prev != cur:
            tag.append("%s → %s" % (prev, cur))
        elif cur and cur != "NEUTRAL" and not prev:
            tag.append(cur)
        if c.get("divergence"):
            tag.append("divergencia LF/AM")
        if tag:
            out.append("  %s z %+.2f (%s) · %s" % (k, c.get("lf_z") or 0.0, cur, " · ".join(tag)))
        (s.setdefault("states", {}))[k] = cur
    if out:
        lines.append("§09 COT reporte %s" % rep)
        lines.extend(out)
    else:
        lines.append("§09 COT reporte %s · sin cambios de estado" % rep)
    s["report"] = rep


# ═════════════════════════════════════════════════════════════════════════════
# §05 DQM — frescura contra el presupuesto del registry
# ═════════════════════════════════════════════════════════════════════════════
def check_dqm(st, lines):
    s = st.setdefault("dqm", {})
    if not os.path.exists(REGISTRY):
        note("DQM · sources/registry.csv ausente")
        return
    with open(REGISTRY, encoding="utf-8", errors="ignore") as fh:
        rows = [r for r in csv.DictReader(fh) if (r.get("primary_access") or "").startswith("data/")]
    for r in rows:
        fid, path = r["feed_id"], os.path.join(ROOT, r["primary_access"])
        try:
            budget = float(r.get("max_staleness_bd") or 0)
        except ValueError:
            budget = 0
        if not budget or not os.path.exists(path):
            continue
        last = None
        if path.endswith(".json"):
            js = read_json(os.path.relpath(path, DATA))
            for key in ("latest_session", "report_date", "generated"):
                if js and js.get(key):
                    last = str(js[key])[:10].replace("-", "")
                    break
        else:
            ser = read_series(os.path.relpath(path, DATA))
            if ser:
                last = ser[-1][0].strftime("%Y%m%d")
        if not last or len(last) != 8:
            continue
        ld = date(int(last[:4]), int(last[4:6]), int(last[6:8]))
        bd = bdays_between(ld, TODAY)
        status = "DEAD" if bd > 2.5 * budget else "STALE" if bd > 1.5 * budget else "DEGRADED" if bd > budget else "LIVE"
        prev = s.get(fid)
        if prev and prev != status and (status in ("STALE", "DEAD") or prev in ("STALE", "DEAD")):
            lines.append("§05 %s: %s → %s (%d bd, presupuesto %.0f)" % (fid, prev, status, bd, budget))
        s[fid] = status


# ═════════════════════════════════════════════════════════════════════════════
# main
# ═════════════════════════════════════════════════════════════════════════════
def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_state(st):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    st["_updated_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(st, fh, indent=1, ensure_ascii=False)


def summary_baseline(st):
    """Foto del estado actual (para --baseline o primer arranque)."""
    out = ["ESTADO ACTUAL"]
    f = st.get("floors", {})
    if f:
        out.append("§02 floors: " + " · ".join("%s %+.1f %s" % (k, v["last"], v["badge"][0]) for k, v in f.items()))
    p = st.get("policy", {})
    if p:
        out.append("§03 policy: " + " · ".join("%s %.2f" % (k, v["v"]) for k, v in p.items()))
    t = st.get("tp", {})
    if t:
        out.append("§04 TP/NOM: " + " · ".join("%s %s" % (k, "—" if v["ratio"] is None else "%.0f%%" % (v["ratio"] * 100)) for k, v in t.items()))
    u = st.get("vs_usd", {})
    if u:
        out.append("§01 vs USD 10Y: " + " · ".join("%s %+.0f bp" % (k, v["spread_bp"]) for k, v in u.items()))
    m = st.get("metals", {})
    if m:
        out.append("§06/07: " + " · ".join("%s z %+.2f %s" % (k, v["z"] or 0, v["regime"]) for k, v in m.items()))
    return out


def main(argv):
    dry = "--dry-run" in argv
    baseline = "--baseline" in argv
    st = load_state()
    first = not st
    lines = []
    for fn in (check_floors, check_policy, check_tp, check_vs_usd, check_metals, check_walls, check_cot, check_dqm):
        try:
            fn(st, lines)
        except Exception as e:                         # Ley 2: ruidoso, nunca corrompe
            note("ERROR %s: %s" % (fn.__name__, e))
    if first and not baseline:
        baseline = True                                # primer arranque = baseline automático
    body = []
    if baseline:
        body.append("🟢 dashboard_alerts v1.0 activado — baseline fijado")
        body.extend(summary_baseline(st))
        body.extend(l for l in lines if l.startswith("§08"))   # walls: tarjeta completa siempre
        body.extend(l for l in lines if l.startswith("  ") and any(x in l for x in PAIR.values()))
    else:
        body.extend(lines)
    if NOTES:
        body.append("DQM: " + " | ".join(NOTES[:6]))
    if not body:
        print("dashboard_alerts: sin cambios — nada que enviar")
    else:
        text = "<b>G8 MACRO · %s</b>\n" % TODAY.isoformat() + "\n".join(body)
        text = text[:3900]                              # límite Telegram 4096
        print(text)
        if not dry:
            tg_send(text)
    if not dry:
        save_state(st)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

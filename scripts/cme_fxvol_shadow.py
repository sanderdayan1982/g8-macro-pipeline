#!/usr/bin/env python3
"""FXVOL-SHADOW v1.0 — IV30-G8, RR25, BF25 y OI de futuros (SHADOW).

No es el CVOL oficial. No vota y no cambia situaciones, fichas, tamaños ni stops.
La lógica, las constantes y el orden de las operaciones son los de la referencia
normativa (10-nov-2026). Solo biblioteca estándar. Sin red.

    python scripts/cme_fxvol_shadow.py
    python scripts/cme_fxvol_shadow.py --session YYYY-MM-DD
"""
import csv
import io
import json
import math
import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

N = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
OPT = {
    "EUR": ("EUU", "6E"),
    "JPY": ("JPU", "6J"),
    "GBP": ("GBU", "6B"),
    "AUD": ("ADU", "6A"),
    "CAD": ("CAU", "6C"),
    "CHF": ("CHU", "6S"),
}
MIN_DTE = 7
TARGET_DTE = 30
DELTA_MIN = 0.05
MIN_PTS = 5
HIST_MIN = 60
HIST_MAX = 252
LB_1W = 5
LB_1M = 21
OI_FLAT_1D = 1.0
OI_FLAT_1W = 2.0
ROLL_PRE = 9
ROLL_POST = 3

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JSON_NAME = "CME_FXVOL_shadow.json"
CSV_NAME = os.path.join("options", "fxvol_history.csv")
CSV_COLUMNS = [
    "session", "ccy", "status", "reason", "iv30_atm", "rr25", "bf25", "mode",
    "exp1", "dte1", "exp2", "dte2", "und1", "F1", "n1", "n2",
    "fut_oi_total", "px_contract", "px_settle",
]


def b76(F, K, T, s, cp):
    v = s * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * v * v) / v
    d2 = d1 - v
    return F * N(d1) - K * N(d2) if cp == "C" else K * N(-d2) - F * N(-d1)


def solve(p, F, K, T, cp):
    intr = max(0.0, F - K) if cp == "C" else max(0.0, K - F)
    if p <= intr:
        return None
    lo, hi = 1e-4, 5.0
    if b76(F, K, T, hi, cp) < p:
        return None
    for _ in range(200):
        m = 0.5 * (lo + hi)
        if b76(F, K, T, m, cp) < p:
            lo = m
        else:
            hi = m
    return 0.5 * (lo + hi)


def cdelta(F, K, T, s):
    v = s * math.sqrt(T)
    return N((math.log(F / K) + 0.5 * v * v) / v)


def interp_at(pts, x, key):
    srt = sorted(pts, key=lambda q: q[key])
    for a, b in zip(srt, srt[1:]):
        if (a[key] - x) * (b[key] - x) <= 0:
            return a[1] if b[key] == a[key] else a[1] + (b[1] - a[1]) * (x - a[key]) / (b[key] - a[key])
    return None


def expiry_metrics(rows, F, T):
    pts = []
    for r in rows:
        if not r["settle"]:
            continue
        K = float(r["strike"])
        p = float(r["settle"])
        if p <= 0:
            continue
        if r["right"] == "C" and K < F:
            continue
        if r["right"] == "P" and K >= F:
            continue
        s = solve(p, F, K, T, r["right"])
        if s is None:
            continue
        dc = cdelta(F, K, T, s)
        if dc < DELTA_MIN or dc > 1 - DELTA_MIN:
            continue
        pts.append((K, s, dc))
    if len(pts) < MIN_PTS:
        return None
    atm = interp_at(pts, F, 0)
    c25 = interp_at(pts, 0.25, 2)
    p25 = interp_at(pts, 0.75, 2)
    if None in (atm, c25, p25):
        return None
    return dict(atm=atm, c25=c25, p25=p25, n=len(pts))


def quarterly_after(futs, exp):
    q = [f for f in futs if f["expiry"] > exp and f["expiry"][5:7] in ("03", "06", "09", "12") and f["settle"] and float(f["settle"]) > 0]
    q.sort(key=lambda f: f["expiry"])
    return q[0] if q else None


def third_wed(y, m):
    d = date(y, m, 1)
    d += timedelta(days=(2 - d.weekday()) % 7)
    return d + timedelta(days=14)


def roll_window(sd):
    for y in (sd.year - 1, sd.year, sd.year + 1):
        for m in (3, 6, 9, 12):
            w = third_wed(y, m)
            a = w - timedelta(days=ROLL_PRE)
            b = w + timedelta(days=ROLL_POST)
            if sd <= b:
                return a, b, (a <= sd <= b)


def read(path):
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        return list(csv.DictReader(fh))


def raw_session(root, d, ccy):
    o, f = OPT[ccy]
    sd = date.fromisoformat(d)
    rows = read(f"{root}/{d}/{o}.csv")
    futs = read(f"{root}/{d}/{f}.csv")
    if rows is None or futs is None:
        return {"status": "NA", "reason": "NO_FILE"}
    exps = sorted({r["expiry"] for r in rows if (date.fromisoformat(r["expiry"]) - sd).days >= MIN_DTE})
    legs = []
    for e in exps[:2]:
        u = quarterly_after(futs, e)
        if u is None:
            continue
        T = (date.fromisoformat(e) - sd).days
        F = float(u["settle"])
        m = expiry_metrics([r for r in rows if r["expiry"] == e], F, T / 365.0)
        if m:
            m.update(exp=e, dte=T, F=F, F_txt=u["settle"], und=u["symbol"])
            legs.append(m)
    oi_rows = [x for x in futs if x["oi"]]
    oi = sum(int(x["oi"]) for x in oi_rows) if oi_rows else None
    top = max((x for x in futs if x["oi"] and x["settle"]), key=lambda x: int(x["oi"]), default=None)
    fut = {
        "oi_total": oi,
        "px_contract": top["symbol"] if top else None,
        "px_settle": float(top["settle"]) if top else None,
        "px_settle_txt": top["settle"] if top else None,
        "settles": {x["symbol"]: float(x["settle"]) for x in futs if x["settle"] and float(x["settle"]) > 0},
    }
    if not legs:
        return {"status": "NA", "reason": "NO_VALID_EXPIRY", "fut": fut}
    a = legs[0]
    b = legs[1] if len(legs) > 1 else None
    if b and a["dte"] <= TARGET_DTE <= b["dte"]:
        mode = "INTERP"

        def cm(k):
            w = (a[k] ** 2 * a["dte"] * (b["dte"] - TARGET_DTE) + b[k] ** 2 * b["dte"] * (TARGET_DTE - a["dte"])) / (b["dte"] - a["dte"])
            return math.sqrt(w / TARGET_DTE)
    else:
        mode = "FLAT"
        cm = lambda k: a[k]
    atm, c25, p25 = cm("atm"), cm("c25"), cm("p25")
    return {
        "status": "OK",
        "iv30_atm": atm * 100,
        "rr25": (c25 - p25) * 100,
        "bf25": ((c25 + p25) / 2 - atm) * 100,
        "mode": mode,
        "exp1": a["exp"],
        "dte1": a["dte"],
        "und1": a["und"],
        "F1": a["F"],
        "F1_txt": a["F_txt"],
        "n1": a["n"],
        "exp2": b["exp"] if b else None,
        "dte2": b["dte"] if b else None,
        "n2": b["n"] if b else None,
        "fut": fut,
    }


def pct_rank(series, x):
    s = series[-HIST_MAX:]
    if len(s) < HIST_MIN:
        return None
    return round(100.0 * sum(1 for v in s if v <= x) / len(s), 1)


def iv30_pct252_field(ivs, x):
    """Percentil de 252 observaciones de la IV30. Con menos, NA y los días disponibles.

    FLUJOS-01 Sander 12-nov-2026. La cuenta es la de pct_rank (incluye el valor de hoy,
    redondeo a 1 decimal) con ventana fija de 252. No usa HIST_MIN ni HIST_MAX: no cambia iv_pct.
    """
    n = len(ivs)
    if n < 252:
        return "NA", n
    s = ivs[-252:]
    return round(100.0 * sum(1 for v in s if v <= x) / 252.0, 1), None


def r(x, n):
    return None if x is None else round(x, n)


def quadrant(px, oi, flat):
    if px is None or oi is None:
        return None
    if abs(oi) < flat:
        return "OI_PLANO"
    if px == 0:
        return "PRECIO_PLANO"
    return {
        (True, True): "DINERO_NUEVO_COMPRADOR",
        (True, False): "CIERRE_CORTOS",
        (False, True): "DINERO_NUEVO_VENDEDOR",
        (False, False): "LIQUIDACION_LARGOS",
    }[(px > 0, oi > 0)]


def list_days(root):
    return sorted(x for x in os.listdir(root) if len(x) == 10 and x[:2] == "20")


def build(repo, session=None):
    root = f"{repo}/data/options/canonical"
    days = list_days(root)
    if session:
        days = [d for d in days if d <= session]
    S = days[-1]
    sd = date.fromisoformat(S)
    surf_path = f"{repo}/data/OPTIONS_SURFACE.json"
    if os.path.exists(surf_path):
        with open(surf_path) as fh:
            surf = json.load(fh)
    else:
        surf = {}
    g0 = surf.get("gate0", {})
    ra, rb, ract = roll_window(sd)
    out = {
        "version": "FXVOL-SHADOW v1.0",
        "status": "SHADOW",
        "session": S,
        "prev_session": days[-2] if len(days) > 1 else None,
        "sessions_total": len(days),
        "convention": "native CME (XXX/USD). rr25>0 = calls XXX más caras que puts XXX. Dashboard y ORION invierten SOLO el signo de rr25 para JPY/CAD/CHF (USDJPY, USDCAD, USDCHF). iv30_atm y bf25 no cambian.",
        "method": {
            "model": "Black-76 sobre settlement oficial, sin descuento",
            "underlying": "futuro trimestral siguiente al vencimiento de la opción",
            "min_dte": MIN_DTE,
            "target_dte": TARGET_DTE,
            "delta_min": DELTA_MIN,
            "min_points": MIN_PTS,
            "interp": "varianza total lineal en días naturales entre los dos primeros vencimientos con DTE>=7; si el primero ya supera 30 días → FLAT",
            "pct_window": [HIST_MIN, HIST_MAX],
            "lookback_1w": LB_1W,
            "lookback_1m": LB_1M,
            "oi_flat_pct": {"1d": OI_FLAT_1D, "1w": OI_FLAT_1W},
            "roll_window_days": [-ROLL_PRE, ROLL_POST],
        },
        "fut_roll_window": {"start": ra.isoformat(), "end": rb.isoformat(), "active": ract},
        "cvol_reference": "No es el CVOL oficial (índice de varianza con alas). Contraste 17-ago-2026: iv30_atm entre 3,5 % y 8,1 % por debajo del CVOL en las seis divisas.",
        "currencies": {},
    }
    hist = {c: [raw_session(root, d, c) for d in days] for c in OPT}
    for c in OPT:
        H = hist[c]
        cur = H[-1]
        g = g0.get(c) if isinstance(g0, dict) else None
        if isinstance(g, dict):
            g = g.get("verdict") or g.get("status")
        if cur["status"] != "OK":
            # FLUJOS-01 Sander 12-nov-2026: sin IV30 de la sesión no hay percentil ni ΔRR.
            out["currencies"][c] = {
                "status": "NA", "reason": cur["reason"], "gate0": g,
                "iv30_pct252": "UNAVAILABLE",
                "rr25_d5": "UNAVAILABLE",
                "rv21": "UNAVAILABLE",
                "vrp": "UNAVAILABLE",
                "vol_regime": "UNAVAILABLE",
            }
            continue
        ok = [h for h in H if h["status"] == "OK"]
        ivs = [h["iv30_atm"] for h in ok]
        rrs = [h["rr25"] for h in ok]

        def back(k, n, H=H, cur=cur):
            if len(H) - 1 - n < 0:
                return None
            h = H[-1 - n]
            return None if h["status"] != "OK" else cur[k] - h[k]

        e = {
            "status": "OK",
            "gate0": g,
            "iv30_atm": r(cur["iv30_atm"], 2),
            "rr25": r(cur["rr25"], 2),
            "bf25": r(cur["bf25"], 2),
            "mode": cur["mode"],
            "exp1": cur["exp1"],
            "dte1": cur["dte1"],
            "exp2": cur["exp2"],
            "dte2": cur["dte2"],
            "und1": cur["und1"],
            "F1": cur["F1"],
            "n1": cur["n1"],
            "n2": cur["n2"],
            "d_iv": {"1d": r(back("iv30_atm", 1), 2), "1w": r(back("iv30_atm", LB_1W), 2), "1m": r(back("iv30_atm", LB_1M), 2)},
            "d_rr": {"1d": r(back("rr25", 1), 2), "1w": r(back("rr25", LB_1W), 2), "1m": r(back("rr25", LB_1M), 2)},
            "iv_pct": pct_rank(ivs, cur["iv30_atm"]),
            "rr_pct": pct_rank(rrs, cur["rr25"]),
            "hist_n": min(len(ivs), HIST_MAX),
            "hist_from": days[max(0, len(days) - HIST_MAX)],
        }
        # FLUJOS-01 Sander 12-nov-2026. Presentación, sin voto.
        # rr25_d5: el cambio a 5 sesiones que ya es d_rr["1w"] (LB_1W). No es otra fórmula.
        # rv21 / vrp / vol_regime: UNAVAILABLE. La orden pide RV21 anualizada con los
        # settlements del mismo futuro, pero no fija retorno (log o simple), divisor
        # (N o N−1) ni factor (√252 o √365). No se inventa esa cifra ni el régimen que depende de ella.
        pct, n_iv = iv30_pct252_field(ivs, cur["iv30_atm"])
        e["iv30_pct252"] = pct
        if n_iv is not None:
            e["iv30_pct252_days"] = n_iv
        if e["iv_pct"] is None:
            e["note"] = "HIST_SHORT"
        e["rr25_d5"] = e["d_rr"]["1w"]
        e["rv21"] = "UNAVAILABLE"
        e["vrp"] = "UNAVAILABLE"
        e["vol_regime"] = "UNAVAILABLE"
        fu = cur["fut"]
        f = {"oi_total": fu["oi_total"], "px_contract": fu["px_contract"], "px_settle": fu["px_settle"]}
        for lab, n, flat in (("1d", 1, OI_FLAT_1D), ("1w", LB_1W, OI_FLAT_1W)):
            if len(H) - 1 - n < 0 or "fut" not in H[-1 - n]:
                f[lab] = None
                continue
            pf = H[-1 - n]["fut"]
            oi_p = pf["oi_total"]
            px_p = pf["settles"].get(fu["px_contract"])
            oi_pct = None if (oi_p in (None, 0) or fu["oi_total"] is None) else (fu["oi_total"] / oi_p - 1) * 100
            px_pct = None if px_p is None else (fu["px_settle"] / px_p - 1) * 100
            q = "ROLL" if ract else quadrant(px_pct, oi_pct, flat)
            f[lab] = {"d_oi": None if oi_p is None else fu["oi_total"] - oi_p, "d_oi_pct": r(oi_pct, 2), "px_chg_pct": r(px_pct, 3), "quadrant": q}
        e["fut"] = f
        out["currencies"][c] = e
    out["currencies"]["NZD"] = {
        "status": "NA", "reason": "NO_CME_OPTIONS", "gate0": "NA",
        "iv30_pct252": "UNAVAILABLE",
        "rr25_d5": "UNAVAILABLE",
        "rv21": "UNAVAILABLE",
        "vrp": "UNAVAILABLE",
        "vol_regime": "UNAVAILABLE",
    }
    return out, hist, days


def _blank(x):
    return "" if x is None else str(x)


def _f4(x):
    return "" if x is None else format(x, ".4f")


def history_csv(days, hist):
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for i, session in enumerate(days):
        for ccy in OPT:
            h = hist[ccy][i]
            ok = h.get("status") == "OK"
            fu = h.get("fut") or {}
            writer.writerow([
                session,
                ccy,
                _blank(h.get("status")),
                _blank(h.get("reason")),
                _f4(h.get("iv30_atm")) if ok else "",
                _f4(h.get("rr25")) if ok else "",
                _f4(h.get("bf25")) if ok else "",
                _blank(h.get("mode")) if ok else "",
                _blank(h.get("exp1")) if ok else "",
                _blank(h.get("dte1")) if ok else "",
                _blank(h.get("exp2")) if ok else "",
                _blank(h.get("dte2")) if ok else "",
                _blank(h.get("und1")) if ok else "",
                _blank(h.get("F1_txt")) if ok else "",
                _blank(h.get("n1")) if ok else "",
                _blank(h.get("n2")) if ok else "",
                _blank(fu.get("oi_total")),
                _blank(fu.get("px_contract")),
                _blank(fu.get("px_settle_txt")),
            ])
    return buf.getvalue()


def _atomic(path, text):
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".fxvol-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def all_six_na(out):
    return all(out["currencies"].get(c, {}).get("status") != "OK" for c in OPT)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    session = None
    if "--session" in args:
        i = args.index("--session")
        if i + 1 >= len(args):
            print("falta la fecha de --session", file=sys.stderr)
            return 2
        session = args[i + 1]
    repo = REPO
    root = os.path.join(repo, "data", "options", "canonical")
    if not os.path.isdir(root):
        print("canonical no existe: %s" % root, file=sys.stderr)
        return 1
    days = list_days(root)
    if session is not None and session not in days:
        print("sesión no existe: %s" % session, file=sys.stderr)
        return 1
    if not days:
        print("canonical no tiene sesiones", file=sys.stderr)
        return 1
    out, hist, used = build(repo, session)
    if all_six_na(out):
        print("las seis divisas salen NA; no se escribe nada", file=sys.stderr)
        return 1
    out["generated_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = json.dumps(out, indent=1, ensure_ascii=False) + "\n"
    csv_text = history_csv(used, hist)
    json_path = os.path.join(repo, "data", JSON_NAME)
    csv_path = os.path.join(repo, "data", CSV_NAME)
    # Ambos documentos completos en memoria antes de reemplazar nada.
    _atomic(json_path, payload)
    _atomic(csv_path, csv_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

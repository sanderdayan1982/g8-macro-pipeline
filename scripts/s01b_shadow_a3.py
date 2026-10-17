"""A3 — diagnósticos de contagio de §01-b en SHADOW.

No evalúa el Paso C y no escribe su estado ni su log.
La señal oficial sigue saliendo solo de scripts/s01b.py.

Regla de hierro (enmienda v2 #1): ΔTP22_USD se toma en las mismas fechas
reales de la curva de entrada de la divisa i (acm_input_asof_t0 →
acm_input_asof_t). Una fila con _FFILL tiene fecha de CSV reciente y curva
antigua. Si esas fechas no existen como impresión real en USD, el diagnóstico
sale vacío. NZD queda fuera hasta cerrar B5 (enmienda v2 #2).
"""
import csv
import math
import os
from datetime import date

H = 22
MIN_BETA_STEPS = 60
MIN_PCT = 60
P_LOCAL = 80.0
EXCLUDED = frozenset({"NZD"})
REFERENCE = "USD"
READ_CCY = ("EUR", "GBP", "JPY", "AUD", "CAD", "CHF")
PROBES = {"AUD": "AUD_NOM_2Y.csv", "CAD": "CAD_NOM_2Y.csv"}
FFILL_MARK = "_FFILL"


def is_real_print(quality):
    return not (quality or "").endswith(FFILL_MARK)


_BY_DATE_CACHE = {}


def _by_date(rows):
    key = id(rows)
    hit = _BY_DATE_CACHE.get(key)
    if hit is not None and hit[0] is rows and hit[1] == len(rows) and (not rows or hit[2] == rows[-1]):
        return hit[3]
    out = {}
    for d, tp, quality in rows:
        out[d] = (tp, quality)
    if isinstance(rows, list):
        _BY_DATE_CACHE[key] = (rows, len(rows), rows[-1] if rows else None, out)
    return out


def probe_asof(probe_dates, day):
    """Última fecha de la sonda ≤ day. La sonda es la serie oficial del insumo largo."""
    best = None
    for d in probe_dates or ():
        if d <= day:
            best = d
        else:
            break
    return best


def input_date(rows, row_date, probe=None):
    """Fecha real de la curva que sostiene la fila `row_date`.

    Con sonda (AUD/CAD, la misma que el Paso C): última observación oficial ≤ row_date.
    Sin sonda: la propia fila si no lleva _FFILL; si lo lleva, la impresión real anterior.
    """
    by = _by_date(rows)
    if row_date not in by:
        return None
    if probe is not None:
        return probe_asof(probe, row_date)
    dates = sorted(by)
    i = dates.index(row_date)
    while i >= 0:
        d = dates[i]
        if is_real_print(by[d][1]):
            return d
        i -= 1
    return None


def real_print_tp(rows, day):
    """TP10 de la impresión cuya fecha de fila es exactamente `day` y no es _FFILL."""
    rec = _by_date(rows).get(day)
    if rec is None or not is_real_print(rec[1]):
        return None
    tp = rec[0]
    if tp is None or not math.isfinite(tp):
        return None
    return tp


def real_tp_asof(rows, day, probe=None):
    """TP de la curva real vigente en `day`, no el TP de una fila arrastrada posterior."""
    dates = sorted(d for d, _, _ in rows if d <= day)
    if not dates:
        return None
    src = input_date(rows, dates[-1], probe)
    if src is None:
        return None
    return real_print_tp(rows, src)


def _shared_steps(rows_i, rows_usd, before):
    """Pasos entre impresiones reales comunes, con el extremo final estrictamente anterior a `before`."""
    bi, bu = _by_date(rows_i), _by_date(rows_usd)
    dates = sorted(d for d in set(bi) & set(bu) if d < before and real_print_tp(rows_i, d) is not None
                   and real_print_tp(rows_usd, d) is not None)
    steps = []
    for d0, d1 in zip(dates, dates[1:]):
        dx = (real_print_tp(rows_usd, d1) - real_print_tp(rows_usd, d0)) * 100.0
        dy = (real_print_tp(rows_i, d1) - real_print_tp(rows_i, d0)) * 100.0
        steps.append((d1, dx, dy))
    return steps


def beta_usd(rows_i, rows_usd, before, min_steps=MIN_BETA_STEPS):
    """Pendiente MCO sin constante de ΔTP_i sobre ΔTP_USD, en pb, solo con pasos anteriores a `before`.

    Devuelve (beta o None, nº de pasos). Sin constante porque la fórmula firmada no lleva α.
    """
    steps = _shared_steps(rows_i, rows_usd, before)
    n = len(steps)
    if n < min_steps:
        return None, n
    sxx = sum(dx * dx for _, dx, _ in steps)
    if sxx == 0.0:
        return None, n
    beta = sum(dx * dy for _, dx, dy in steps) / sxx
    if not math.isfinite(beta):
        return None, n
    return beta, n


def dtp22_local(ccy, input_t0, input_t, rows_i, rows_usd, beta):
    """ΔTP22_i − β·ΔTP22_USD, en pb, sobre las dos fechas reales de entrada.

    None si la divisa está excluida, si las fechas no son dos impresiones reales distintas
    en las dos curvas, o si no hay β. Nunca empareja la fila CSV de un día con la curva de otro.
    """
    if ccy == REFERENCE or ccy in EXCLUDED:
        return None
    if input_t0 is None or input_t is None or not (input_t0 < input_t):
        return None
    if beta is None or not math.isfinite(beta):
        return None
    ti0, ti1 = real_print_tp(rows_i, input_t0), real_print_tp(rows_i, input_t)
    tu0, tu1 = real_print_tp(rows_usd, input_t0), real_print_tp(rows_usd, input_t)
    if any(v is None for v in (ti0, ti1, tu0, tu1)):
        return None
    return ((ti1 - ti0) - beta * (tu1 - tu0)) * 100.0


def percentile(sorted_vals, q):
    """Mismo percentil lineal que s01b.pct. `sorted_vals` ya viene ordenado."""
    n = len(sorted_vals)
    if n == 0:
        return None
    k = (n - 1) * q / 100.0
    f = math.floor(k)
    c = min(f + 1, n - 1)
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def percentile_population(history, ccy):
    """Historia propia de la divisa. NZD y USD no entran en ningún percentil de A3."""
    if ccy == REFERENCE or ccy in EXCLUDED:
        return []
    return [v for v in history.get(ccy, ()) if v is not None and math.isfinite(v)]


def contagion_percentile(history, ccy, q=P_LOCAL, min_n=MIN_PCT):
    vals = percentile_population(history, ccy)
    if len(vals) < min_n:
        return None
    return percentile(sorted(vals), q)


def rho_tp_fx_22(tp_real, fx_log):
    """Pearson de (ΔTP_d en pb, Δln FX_d) en la ventana de 22 sesiones.

    `tp_real` tiene 23 puntos: ancla t0 y las 22 sesiones. Cada punto es el TP de la
    curva real vigente, no el TP escrito en una fila _FFILL.
    `fx_log` tiene 22 retornos. Signo de §01-b: XXX/USD, positivo = la divisa se fortalece.
    """
    if len(tp_real) != H + 1 or len(fx_log) != H:
        return None, 0
    if any(v is None or not math.isfinite(v) for v in tp_real):
        return None, 0
    if any(v is None or not math.isfinite(v) for v in fx_log):
        return None, 0
    dtp = [(tp_real[i + 1] - tp_real[i]) * 100.0 for i in range(H)]
    return _pearson(dtp, list(fx_log)), H


def _pearson(xs, ys):
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    sxx = sum(v * v for v in dx)
    syy = sum(v * v for v in dy)
    if sxx == 0.0 or syy == 0.0:
        return None
    return sum(a * b for a, b in zip(dx, dy)) / math.sqrt(sxx * syy)


def _iso(d):
    return d.isoformat() if isinstance(d, date) else None


def shadow_currency(ccy, input_t0, input_t, rows_i, rows_usd, tp_window, fx_window, past_local,
                    asof, min_steps=MIN_BETA_STEPS):
    """Una divisa, una sesión. `past_local` son los dtp22_local estrictamente anteriores a `asof`.

    β se corta en la fecha real de entrada final (si existe): el paso que termina ese día no entra
    en la pendiente con la que se lee ese mismo día.
    """
    if ccy in EXCLUDED:
        return _empty(ccy, "EXCLUIDA_B5")
    if ccy == REFERENCE:
        return _empty(ccy, "REFERENCIA")
    cut = input_t if input_t is not None else asof
    beta, n_beta = beta_usd(rows_i, rows_usd, cut, min_steps=min_steps) if cut is not None else (None, 0)
    local = dtp22_local(ccy, input_t0, input_t, rows_i, rows_usd, beta)
    rho, n_rho = rho_tp_fx_22(tp_window, fx_window)
    if local is not None:
        reason = "OK"
    elif beta is None:
        reason = "SIN_BETA"
    else:
        reason = "FECHAS_NO_CUADRAN"
    return {
        "ccy": ccy,
        "status": reason,
        "mode": "SHADOW",
        "input_t0": _iso(input_t0),
        "input_t": _iso(input_t),
        "beta_usd": beta,
        "n_beta": n_beta,
        "dtp22_local": local,
        "rho_tp_fx_22": rho,
        "n_rho": n_rho,
        "pct_dtp22_local": contagion_percentile({ccy: past_local}, ccy),
        "fx_convention": "XXX/USD",
    }


def _empty(ccy, status):
    return {
        "ccy": ccy, "status": status, "mode": "SHADOW",
        "input_t0": None, "input_t": None, "beta_usd": None, "n_beta": 0,
        "dtp22_local": None, "rho_tp_fx_22": None, "n_rho": 0,
        "pct_dtp22_local": None, "fx_convention": None,
    }


def _load_rows(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8", errors="ignore") as fh:
        rows = [ln for ln in fh if ln.strip() and not ln.startswith("#")]
    for rec in csv.DictReader(rows):
        rec = {(k or "").strip(): (v or "").strip() for k, v in rec.items()}
        raw = rec.get("DATE") or rec.get("Date") or ""
        try:
            if len(raw) == 8 and raw.isdigit():
                d = date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
            else:
                d = date.fromisoformat(raw[:10])
        except ValueError:
            continue
        try:
            tp = float(rec["TP10"])
        except (KeyError, ValueError):
            continue
        if math.isfinite(tp):
            out.append((d, tp, rec.get("QUALITY") or ""))
    out.sort()
    return out


def _load_probe(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8", errors="ignore") as fh:
        rows = [ln for ln in fh if ln.strip() and not ln.startswith("#")]
    for rec in csv.DictReader(rows):
        rec = {(k or "").strip(): (v or "").strip() for k, v in rec.items()}
        raw = rec.get("DATE") or rec.get("Date") or ""
        try:
            d = date(int(raw[:4]), int(raw[4:6]), int(raw[6:8])) if len(raw) == 8 and raw.isdigit() else date.fromisoformat(raw[:10])
        except ValueError:
            continue
        out.append(d)
    return tuple(sorted(set(out)))


def _read_fx(path):
    """canonical.csv → fechas, {ccy: [r diario alineado a esas fechas]}."""
    dates, series = [], {c: [] for c in READ_CCY}
    if not os.path.exists(path):
        return dates, series
    with open(path, encoding="utf-8", errors="ignore") as fh:
        rd = csv.reader(fh)
        head = next(rd)
        idx = {h: i for i, h in enumerate(head)}
        for row in rd:
            raw = row[0].strip()
            try:
                d = date.fromisoformat(raw[:10]) if "-" in raw else date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
            except ValueError:
                continue
            dates.append(d)
            for c in series:
                key = "r_" + c
                try:
                    value = float(row[idx[key]])
                    series[c].append(value if math.isfinite(value) else None)
                except (KeyError, ValueError, IndexError):
                    series[c].append(None)
    return dates, series


def _target_days(start, end):
    import s01b
    days, d = [], start
    from datetime import timedelta
    while d <= end:
        if s01b.is_target_day(d):
            days.append(d)
        d += timedelta(days=1)
    return days


def build_shadow(data_dir, session):
    """Lectura de una sesión. No escribe. NZD excluida. USD solo es la referencia."""
    usd = _load_rows(os.path.join(data_dir, "ACM_G8_USD.csv"))
    fx_dates, fx = _read_fx(os.path.join(data_dir, "usd_factor", "canonical.csv"))
    if not fx_dates:
        return None
    calendar = _target_days(fx_dates[0], session)
    if session not in calendar or calendar.index(session) < H:
        return None
    pos = calendar.index(session)
    window = calendar[pos - H:pos + 1]          # t0 + 22 sesiones
    out = {
        "schema": "s01b-shadow-a3",
        "mode": "SHADOW",
        "t": session.isoformat(),
        "t0": window[0].isoformat(),
        "note": "SHADOW. No modifica señal, umbrales ni log del Paso C. NZD excluida hasta B5.",
        "currencies": {},
    }
    fx_index = {d: i for i, d in enumerate(fx_dates)}
    for ccy in ("USD",) + READ_CCY + ("NZD",):
        if ccy in EXCLUDED or ccy == REFERENCE:
            out["currencies"][ccy] = _empty(ccy, "EXCLUIDA_B5" if ccy in EXCLUDED else "REFERENCIA")
            continue
        rows = _load_rows(os.path.join(data_dir, "ACM_G8_%s.csv" % ccy))
        probe = _load_probe(os.path.join(data_dir, PROBES[ccy])) if ccy in PROBES else None
        ends = (_end_input(rows, window[0], probe), _end_input(rows, window[-1], probe))
        past = []
        for i in range(H, pos):
            e0 = _end_input(rows, calendar[i - H], probe)
            e1 = _end_input(rows, calendar[i], probe)
            cut = e1 if e1 is not None else calendar[i]
            b_then, _ = beta_usd(rows, usd, cut)
            past.append(dtp22_local(ccy, e0, e1, rows, usd, b_then))
        tp_window = [real_tp_asof(rows, d, probe) for d in window]
        fx_window = []
        for d in window[1:]:
            j = fx_index.get(d)
            fx_window.append(fx[ccy][j] if j is not None and j < len(fx[ccy]) else None)
        out["currencies"][ccy] = shadow_currency(
            ccy, ends[0], ends[1], rows, usd, tp_window, fx_window, past, asof=window[-1])
    return out


def _end_input(rows, day, probe):
    have = [d for d, _, _ in rows if d <= day]
    if not have:
        return None
    return input_date(rows, have[-1], probe)


SHADOW_NAME = "S01B_shadow_a3.json"


def last_session(data_dir):
    """Último día TARGET con rendimientos en usd_factor/canonical.csv: la misma sesión que lee §01-b."""
    fx_dates, _ = _read_fx(os.path.join(data_dir, "usd_factor", "canonical.csv"))
    for d in reversed(fx_dates):
        if d in _target_days(d, d):
            return d
    return None


def write_shadow(out, path):
    import json
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


if __name__ == "__main__":
    import sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(root, "data")
    session = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else last_session(data_dir)
    out = build_shadow(data_dir, session) if session is not None else None
    if out is None:
        print("[s01b_shadow_a3] sin lectura para la sesión", session, "— no escribo")
        sys.exit(0)
    dest = os.path.join(data_dir, SHADOW_NAME)
    write_shadow(out, dest)
    print("[s01b_shadow_a3] wrote", dest, "t", out["t"])

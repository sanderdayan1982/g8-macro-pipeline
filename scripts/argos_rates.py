#!/usr/bin/env python3
"""Publica data/argos_rates.json con los números que pintan §01, §01-b y §02.

El HTML lo produce scripts/argos_render.js ejecutando los IIFE del dashboard
(Papa Parse 5.4.1, reloj UTC). Este módulo solo lee ese HTML.

FLUJOS-01 Sander 12-nov-2026. No recalcula fórmulas.
Lo que el panel no muestra queda UNAVAILABLE:
  - variación a 1 día del nivel
  - variación a 22 días del nivel (el panel diario pinta 1W/1M = 5 y 21 observaciones;
    el mensual pinta 1M/3M; §01-b pinta el cambio a 22 sesiones de ΔRNY/ΔTP/RESID)
  - driver dominante: BE_LED si |ΔBE22 de §01-b| > |Δreal en la misma ventana|; si no REAL_LED
    (DATOS-ARGOS-01). PR #16 usaba 21 observaciones; §01-b usa H=22. Sin ΔBE22 → UNAVAILABLE
  - c3 se deja UNAVAILABLE: Sander 13-nov-2026 20:45 WAT, es la ficha NTF × Reservas
    (solo USD) y no aplica aquí. tests/test_argos_rates.py (main) exige el campo.
"""
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "argos_rates.json")
RENDER = os.path.join(ROOT, "scripts", "argos_render.js")
CCYS = ["USD", "EUR", "JPY", "GBP", "CAD", "AUD", "NZD", "CHF"]
LEVELS = [
    ("nominal_10y", 1),
    ("term_premium", 2),
    ("rny", 3),
    ("real_10y", 4),
    ("breakeven_10y", 5),
]
MISSING = {"·", "—", "-", "sin feed", "sin historia", "sin ACM"}


def render(now):
    env = os.environ.copy()
    env["TZ"] = "UTC"
    proc = subprocess.run(
        ["node", RENDER, "--now", now],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr or proc.stdout or "argos_render falló\n")
        raise SystemExit(proc.returncode or 1)
    return json.loads(proc.stdout)


def _rows(html):
    body = html.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    return re.findall(r"<tr\b[\s\S]*?</tr>", body)


def _tds(row):
    return re.findall(r"<td\b[\s\S]*?</td>", row)


def _text(fragment):
    t = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", t).strip()


def _staleness(text):
    m = re.search(r"\b(DEAD|STALE|DEGRADED)\b", text)
    return m.group(1) if m else "LIVE"


def obs_delta(vals, lb):
    """Δ en pb de `lb` observaciones: (último − el de hace lb) × 100. Igual que deltaBps del panel."""
    if not isinstance(vals, list) or len(vals) < lb + 1:
        return "UNAVAILABLE"
    return round((float(vals[-1]) - float(vals[-1 - lb])) * 100.0, 2)


def driver_field(real_vals, be_vals):
    """BE_LED si |ΔBE a 21 obs| > |Δreal a 21 obs|; si no, REAL_LED (el empate también).

    Lookback de los vectores sintéticos (FLUJOS-01-PR2). El JSON publicado no usa
    esta ventana: ver panel_driver (ΔBE22 de §01-b). Si falta alguno, UNAVAILABLE.
    """
    real_d = obs_delta(real_vals, 21)
    be_d = obs_delta(be_vals, 21)
    if real_d == "UNAVAILABLE" or be_d == "UNAVAILABLE":
        return "UNAVAILABLE"
    if abs(be_d) > abs(real_d):
        return "BE_LED"
    return "REAL_LED"


def real_delta_22(row):
    """Δreal en pb entre t0 y t de §01-b, as-of de s01b.py (MAX_LAG_BD) sobre la serie real de §01."""
    import s01b
    t = s01b.parse_date(str((row or {}).get("t") or ""))
    t0 = s01b.parse_date(str((row or {}).get("t0") or ""))
    if t is None or t0 is None:
        return "UNAVAILABLE"
    series = []
    for p in (row or {}).get("real_pts") or []:
        if not isinstance(p, dict):
            continue
        d = s01b.parse_date(str(p.get("d") or ""))
        v = p.get("v")
        if d is None or isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
            continue
        series.append((d, float(v)))
    series.sort()
    e1, e0 = s01b.asof(series, t), s01b.asof(series, t0)
    if not (e1 and e0 and e1[0] > e0[0]):
        return "UNAVAILABLE"
    return round((e1[1] - e0[1]) * 100.0, 2)


def panel_driver(row):
    """BE_LED si |ΔBE22| > |Δreal22|; si no, REAL_LED (el empate también).

    ΔBE22 es d_be de S01B.json (H=22), el número que pinta §01-b. Δreal22 usa
    los mismos t y t0. Si falta alguno, UNAVAILABLE. Sin esas claves, driver_field.
    DATOS-ARGOS-01: la ventana de 21 obs de PR #16 no es la de §01-b.
    """
    if not isinstance(row, dict) or "d_be_22" not in row:
        return driver_field((row or {}).get("real") if isinstance(row, dict) else None,
                            (row or {}).get("be") if isinstance(row, dict) else None)
    be = row.get("d_be_22")
    real_d = real_delta_22(row)
    if isinstance(be, bool) or not isinstance(be, (int, float)) or real_d == "UNAVAILABLE":
        return "UNAVAILABLE"
    if abs(float(be)) > abs(float(real_d)):
        return "BE_LED"
    return "REAL_LED"


def _computed_one(row):
    def pack(lb):
        src = row or {}
        return {
            "nominal_10y": obs_delta(src.get("nom"), lb),
            "real_10y": obs_delta(src.get("real"), lb),
            "breakeven_10y": obs_delta(src.get("be"), lb),
            "source": "computed",
        }
    return {
        "level_change_1d": pack(1),
        "level_change_22d": pack(22),
        "dominant_driver": panel_driver(row),
    }


def computed_block(legs):
    return {ccy: _computed_one(row) for ccy, row in (legs or {}).items()}


def _level(td):
    m = re.search(r'class="attr-val">(-?\d+\.\d+)', td)
    if m:
        return float(m.group(1))
    if "attr-na" in td or "—" in td:
        return "UNAVAILABLE"
    return "UNAVAILABLE"


def _deltas(td):
    found = re.findall(r"\b(1W|1M|3M)\s+([+-]?\d+)\b", _text(td))
    if not found:
        return "UNAVAILABLE"
    # fmtDelta pinta toFixed(0): un Δ en (−0,5, 0) sale «1W -0». int("-0") es 0 y el
    # test de paridad busca «1W +0» o «1W 0», que no están. Se omite ese cero con signo.
    out = {lab: int(val) for lab, val in found if val != "-0"}
    return out or "UNAVAILABLE"


def _s01b_cell(td):
    raw = _text(td)
    est = "ᵉ" in td or "ᵉ" in raw
    core = raw.replace("ᵉ", "").strip()
    if core in MISSING or core == "":
        return "UNAVAILABLE", est
    if re.fullmatch(r"[+-]?\d+", core):
        return int(core), est
    if re.fullmatch(r"[+-]?\d+\.\d+ %", core):
        return core, est
    return raw, est


def _s01b_block(row):
    tds = _tds(row)
    keys = ["d_rny", "d_tp", "d_fit", "resid", "d_2y", "d_be", "d_fx", "theta", "ctf", "persist", "quality", "acm_asof"]
    out = {}
    estimates = []
    for key, td in zip(keys, tds[1:]):
        val, est = _s01b_cell(td)
        out[key] = val
        if est:
            estimates.append(key)
    if estimates:
        out["estimate_fields"] = estimates
    return out


def parse(doc):
    attr_rows = []
    for row in _rows(doc["attr"]):
        text = _text(row)
        if text.startswith("Spreads vs USD") or "−USD" in text or "-USD" in text:
            continue
        m = re.search(r'class="attr-ccy">([A-Z]{3})<', row)
        if not m:
            continue
        attr_rows.append((m.group(1), row, text))

    s01b_rows = {}
    for row in _rows(doc["s01b"]):
        if "a3-row" in row[:80]:
            continue
        m = re.search(r"<b>([A-Z]{3})</b>", row)
        if m:
            s01b_rows[m.group(1)] = _s01b_block(row)

    as_of_m = re.search(r"as-of (\d{4}-\d{2}-\d{2})", doc["s01b"])
    currencies = {}
    seen = set()
    for ccy, row, text in attr_rows:
        seen.add(ccy)
        tds = _tds(row)
        date_m = re.search(r"\d{4}-\d{2}-\d{2}", _text(tds[0]) if tds else text)
        stale = _staleness(text)
        entry = {
            "date": date_m.group(0) if date_m else "UNAVAILABLE",
            "estimate": "EST" in text,
            "panel_deltas_bp": {},
            "level_change_1d": "UNAVAILABLE",
            "level_change_22d": "UNAVAILABLE",
            "s01b": s01b_rows.get(ccy, "UNAVAILABLE"),
            "dominant_driver": "UNAVAILABLE",
            "staleness": stale,
            "stale": stale in ("STALE", "DEAD"),
            "blocked": False,
        }
        for name, idx in LEVELS:
            if idx >= len(tds):
                entry[name] = "UNAVAILABLE"
                entry["panel_deltas_bp"][name] = "UNAVAILABLE"
                continue
            entry[name] = _level(tds[idx])
            entry["panel_deltas_bp"][name] = _deltas(tds[idx])
        if "legs" in doc:
            block = _computed_one((doc.get("legs") or {}).get(ccy))
            entry["level_change_1d"] = block["level_change_1d"]
            entry["level_change_22d"] = block["level_change_22d"]
            entry["dominant_driver"] = block["dominant_driver"]
        currencies[ccy] = entry
    for ccy in CCYS:
        if ccy not in seen:
            currencies[ccy] = {
                "date": "UNAVAILABLE",
                "nominal_10y": "UNAVAILABLE",
                "real_10y": "UNAVAILABLE",
                "breakeven_10y": "UNAVAILABLE",
                "term_premium": "UNAVAILABLE",
                "rny": "UNAVAILABLE",
                "estimate": False,
                "panel_deltas_bp": "UNAVAILABLE",
                "level_change_1d": "UNAVAILABLE",
                "level_change_22d": "UNAVAILABLE",
                "s01b": s01b_rows.get(ccy, "UNAVAILABLE"),
                "dominant_driver": "UNAVAILABLE",
                "staleness": "DEAD",
                "stale": True,
                "blocked": True,
            }

    floors = {}
    if "<tbody>" in doc["floors"]:
        for row in _rows(doc["floors"]):
            key_m = re.search(r'data-fls-ccy="([^"]+)"', row)
            if not key_m:
                continue
            tds = _tds(row)
            text = _text(row)
            head = _text(tds[0]) if tds else ""
            date_m = re.search(r"(\d{4}-\d{2}-\d{2})\s*$", head)
            ref_m = re.search(r"vs (.+) · \d{4}-\d{2}-\d{2}$", head)
            spread_m = re.search(r'class="attr-val">([+-]?\d+\.\d+)', tds[1] if len(tds) > 1 else "")
            regime_m = re.search(r"\b(AMPLE|TIGHT|PRESSURE|TERM)\b", _text(tds[5]) if len(tds) > 5 else "")
            rate_m = re.search(r"([+-]?\d+\.\d+)%", _text(tds[6]) if len(tds) > 6 else "")
            since_m = re.search(r"\d{4}-\d{2}-\d{2}", _text(tds[7]) if len(tds) > 7 else "")
            stale = _staleness(text)

            def num_td(i):
                if i >= len(tds):
                    return "UNAVAILABLE"
                m = re.search(r"[+-]?\d+\.\d+", _text(tds[i]))
                return float(m.group(0)) if m else "UNAVAILABLE"

            floors[key_m.group(1)] = {
                "spread_bp": float(spread_m.group(1)) if spread_m else "UNAVAILABLE",
                "d1w_bp": num_td(2),
                "d1m_bp": num_td(3),
                "regime": regime_m.group(1) if regime_m else "UNAVAILABLE",
                "date": date_m.group(1) if date_m else "UNAVAILABLE",
                "ref": ref_m.group(1).strip() if ref_m else "UNAVAILABLE",
                "policy_rate": float(rate_m.group(1)) if rate_m else "UNAVAILABLE",
                "policy_since": since_m.group(0) if since_m else "UNAVAILABLE",
                "staleness": stale,
                "stale": stale in ("STALE", "DEAD"),
                "blocked": False,
            }
    now = doc["now"]
    return {
        "schema": "argos_rates/1",
        "as_of": now[:10],
        "generated_at": now,
        "clock": "UTC",
        "s01b_as_of": as_of_m.group(1) if as_of_m else "UNAVAILABLE",
        "currencies": {c: currencies[c] for c in CCYS if c in currencies},
        "floors": floors,
        "c3": {"quadrant": "UNAVAILABLE", "value": "UNAVAILABLE", "label": "UNAVAILABLE"},
    }


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    now = None
    if "--now" in args:
        now = args[args.index("--now") + 1]
    else:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc = parse(render(now))
    text = json.dumps(doc, indent=1, ensure_ascii=False) + "\n"
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

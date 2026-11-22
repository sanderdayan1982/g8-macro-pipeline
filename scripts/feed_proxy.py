#!/usr/bin/env python3
"""Proxy de feeds (t323u): regla de feed muerto y sustitutos preaprobados.

Regla (Sander, 22-nov-2026):
  2 días hábiles tarde → aviso;
  3 días hábiles → sustituto preaprobado (status=active), filas PROXY + aviso;
  la fuente primaria vuelve → se retiran las filas PROXY + aviso.

Un dato PROXY no vota. No cambia presupuestos LATE del registry ni metodología.
Solo está activo el sustituto que Sander haya aprobado (hoy: NZD_CASH_ON → NZD_OCR).
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if os.path.join(ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "scripts"))

from g8common import schedule as SC  # noqa: E402

SUB_FILE = "sources/substitutes.csv"
PROXY_HEADER = ["Date", "Value", "Status", "Source", "ProxyDate"]
STATUS_PROXY = "PROXY"
STATUS_PRIMARY = "PRIMARY"


@dataclass
class SubRow:
    feed_id: str
    file: str
    primary_source: str
    substitute_feed: str
    substitute_file: str
    substitute_source: str
    equivalence: str
    approval_status: str  # active | proposed | none | licensed_no_public | excluded | retired | not_ingested
    notes: str
    calendar: str = ""
    expected_lag_bd: int = 0
    frequency: str = ""


@dataclass
class Event:
    key: str
    text: str
    kind: str  # late2 | late3 | proxy | recovered | none_sub


@dataclass
class FeedState:
    feed_id: str
    file: str
    last_official: str | None
    last_any: str | None
    expected_obs: str | None
    late_bd: int
    proxy_dates: list
    approval_status: str
    substitute_feed: str


def _as_date(s):
    if s is None:
        return None
    t = str(s).strip().strip('"').replace("-", "")
    if len(t) != 8 or not t.isdigit():
        return None
    try:
        return datetime.strptime(t, "%Y%m%d").date()
    except ValueError:
        return None


def _iso(d):
    return d.isoformat() if d else ""


def load_substitutes(root=ROOT):
    path = os.path.join(root, SUB_FILE)
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            lag = (r.get("expected_lag_bd") or "").strip()
            out.append(SubRow(
                feed_id=(r.get("feed_id") or "").strip(),
                file=(r.get("file") or "").strip(),
                primary_source=(r.get("primary_source") or "").strip(),
                substitute_feed=(r.get("substitute_feed") or "").strip(),
                substitute_file=(r.get("substitute_file") or "").strip(),
                substitute_source=(r.get("substitute_source") or "").strip(),
                equivalence=(r.get("equivalence") or "").strip(),
                approval_status=(r.get("approval_status") or "none").strip(),
                notes=(r.get("notes") or "").strip(),
                calendar=(r.get("calendar") or "").strip(),
                expected_lag_bd=int(lag) if lag.isdigit() else 0,
                frequency=(r.get("frequency") or "").strip(),
            ))
    return out


def load_registry_meta(root=ROOT):
    """feed_id → (calendar, expected_lag_bd, max_staleness_bd, frequency, status, license)."""
    path = os.path.join(root, "sources", "registry.csv")
    meta = {}
    if not os.path.exists(path):
        return meta
    with open(path, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            lag = (r.get("expected_lag_business_days") or "").strip()
            meta[r["feed_id"]] = {
                "calendar": (r.get("calendar") or "").strip(),
                "expected_lag_bd": int(lag) if lag.isdigit() else 0,
                "max_staleness_bd": (r.get("max_staleness_bd") or "").strip(),
                "frequency": (r.get("frequency") or "").strip(),
                "status": (r.get("status") or "").strip(),
                "license": (r.get("license_class") or "").strip(),
                "primary_source": (r.get("primary_source") or "").strip(),
                "fallback_source": (r.get("fallback_source") or "").strip(),
            }
    return meta


def parse_rows(text):
    """CSV Date/Value (o DATE/CLOSE) → list of dicts with date, value, status, source, proxy_date, raw."""
    if not text:
        return []
    if isinstance(text, bytes):
        text = text.decode("utf-8")
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if not lines:
        return []
    out = []
    for r in csv.DictReader(lines):
        d = _as_date(r.get("Date") or r.get("DATE") or r.get("date"))
        raw_v = r.get("Value")
        if raw_v in (None, ""):
            raw_v = r.get("CLOSE") if r.get("CLOSE") not in (None, "") else r.get("close")
        if d is None or raw_v in (None, "", "NA", "nan", "-", ".."):
            continue
        try:
            float(raw_v)
        except (TypeError, ValueError):
            continue
        st = (r.get("Status") or r.get("STATUS") or "").strip()
        out.append({
            "date": d,
            "value": str(raw_v).strip(),
            "status": st,
            "source": (r.get("Source") or "").strip(),
            "proxy_date": _as_date(r.get("ProxyDate") or r.get("proxy_date")),
            "raw": r,
        })
    out.sort(key=lambda x: x["date"])
    return out


def official_rows(rows):
    return [r for r in rows if r["status"].upper() != STATUS_PROXY]


def proxy_rows(rows):
    return [r for r in rows if r["status"].upper() == STATUS_PROXY]


def official_csv_bytes(text):
    """Vista Date,Value solo de filas no PROXY (para el merge monótono del publicador)."""
    rows = official_rows(parse_rows(text))
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["Date", "Value"])
    for r in rows:
        w.writerow([_iso(r["date"]), r["value"]])
    return buf.getvalue().encode("utf-8")


def iter_bd(cals, cal, start, end):
    d = start
    while d <= end:
        if not cal or SC.is_bd(cals, cal, d):
            yield d
        d += timedelta(days=1)


def prev_bd(cals, cal, d, n=1):
    x = d
    left = n
    # if d itself is not a bd, walk back to a bd first without counting (for lag start)
    guard = 0
    while cal and not SC.is_bd(cals, cal, x) and guard < 40:
        x -= timedelta(days=1)
        guard += 1
    while left > 0 and guard < 80:
        x -= timedelta(days=1)
        guard += 1
        if not cal or SC.is_bd(cals, cal, x):
            left -= 1
    return x


def last_bd_on_or_before(cals, cal, d):
    x = d
    guard = 0
    while cal and not SC.is_bd(cals, cal, x) and guard < 40:
        x -= timedelta(days=1)
        guard += 1
    return x


def late_business_days(cals, cal, last_official, as_of, lag_bd):
    """Días hábiles que ya deberían haberse publicado y no están en la serie oficial."""
    if last_official is None:
        return 0, None
    ref = last_bd_on_or_before(cals, cal, as_of)
    expected = prev_bd(cals, cal, ref, lag_bd) if lag_bd else ref
    if expected <= last_official:
        return 0, expected
    n = sum(1 for _ in iter_bd(cals, cal, last_official + timedelta(days=1), expected))
    return n, expected


def lookup_substitute_value(sub_rows, on_date):
    """Último CLOSE/Value del sustituto en o antes de on_date (forward-fill del dato oficial vigente)."""
    last = None
    for r in sub_rows:
        if r["date"] <= on_date:
            last = r
        else:
            break
    return last


def render_cash_csv(official, proxies):
    """Conserva las filas oficiales tal cual (Date,Value) y añade/actualiza PROXY al final del día."""
    by_date = {r["date"]: r for r in official}
    # official wins
    proxy_keep = [p for p in proxies if p["date"] not in by_date]
    dates = sorted(set(by_date) | {p["date"] for p in proxy_keep})
    lines = [",".join(PROXY_HEADER)]
    for d in dates:
        if d in by_date:
            lines.append("%s,%s,,," % (_iso(d), by_date[d]["value"]))
        else:
            p = next(x for x in proxy_keep if x["date"] == d)
            lines.append("%s,%s,%s,%s,%s" % (
                _iso(d), p["value"], STATUS_PROXY, p["source"], _iso(p["proxy_date"]) if p["proxy_date"] else "",
            ))
    return ("\n".join(lines) + "\n").encode("utf-8")


def inspect_feed(root, sub, cals, as_of, file_text=None, sub_text=None):
    path = os.path.join(root, "data", sub.file) if sub.file else ""
    text = file_text
    if text is None and path and os.path.exists(path):
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    rows = parse_rows(text) if text else []
    off = official_rows(rows)
    last_off = off[-1]["date"] if off else None
    last_any = rows[-1]["date"] if rows else None
    cal = sub.calendar
    late, expected = late_business_days(cals, cal, last_off, as_of, sub.expected_lag_bd)
    return FeedState(
        feed_id=sub.feed_id,
        file=sub.file,
        last_official=_iso(last_off) if last_off else None,
        last_any=_iso(last_any) if last_any else None,
        expected_obs=_iso(expected) if expected else None,
        late_bd=late,
        proxy_dates=[_iso(r["date"]) for r in proxy_rows(rows)],
        approval_status=sub.approval_status,
        substitute_feed=sub.substitute_feed,
    ), rows, off


def apply_one(root, sub, cals, as_of, file_text=None, sub_text=None, force_from=None, official_text=None):
    """Devuelve (new_bytes_or_None, events, state). new_bytes is None si no hay que escribir."""
    st, rows, off = inspect_feed(root, sub, cals, as_of, file_text, sub_text)
    events = []
    if official_text is not None:
        off = official_rows(parse_rows(official_text))
        # fechas que eran PROXY y ahora trae la primaria
        recovered_pre = [p["date"] for p in proxy_rows(rows) if p["date"] in {r["date"] for r in off}]
    else:
        recovered_pre = []
    if sub.approval_status != "active" or not sub.substitute_file:
        if st.late_bd >= 2:
            kind = "late3" if st.late_bd >= 3 else "late2"
            events.append(_event(st, kind, sub))
        return None, events, st

    sub_path = os.path.join(root, "data", sub.substitute_file)
    text = sub_text
    if text is None and os.path.exists(sub_path):
        text = Path(sub_path).read_text(encoding="utf-8", errors="replace")
    sub_rows = parse_rows(text) if text else []

    last_off = off[-1]["date"] if off else None
    fill_to = last_bd_on_or_before(cals, sub.calendar, as_of)
    if fill_to >= as_of:
        # si as_of es hábil, no rellenar ese mismo día (puede no haber cerrado)
        fill_to = prev_bd(cals, sub.calendar, fill_to, 1)

    official_dates = {r["date"] for r in off}
    recovered = list(recovered_pre)
    kept_proxy = []
    for p in proxy_rows(rows):
        if p["date"] in official_dates:
            if p["date"] not in recovered:
                recovered.append(p["date"])
        else:
            kept_proxy.append(p)

    should_fill = st.late_bd >= 3 or bool(kept_proxy) or force_from is not None
    new_proxies = list(kept_proxy)
    start = force_from or (last_off + timedelta(days=1) if last_off else None)
    if should_fill and start and fill_to and start <= fill_to:
        existing_proxy = {p["date"] for p in kept_proxy}
        for d in iter_bd(cals, sub.calendar, start, fill_to):
            if d in official_dates or d in existing_proxy:
                continue
            hit = lookup_substitute_value(sub_rows, d)
            if not hit:
                continue
            new_proxies.append({
                "date": d,
                "value": _fmt_value(hit["value"]),
                "status": STATUS_PROXY,
                "source": sub.substitute_feed or sub.substitute_file,
                "proxy_date": hit["date"],
            })
            existing_proxy.add(d)

    if not new_proxies and not recovered:
        # No reescribir un CSV oficial solo para añadir columnas vacías (el publicador
        # del Mac y sus tests deben seguir en NOOP cuando B2 no trae novedad).
        ev2 = []
        if st.late_bd >= 2 and sub.approval_status == "active":
            ev2.append(_event(st, "late3" if st.late_bd >= 3 else "late2", sub))
        return None, ev2, st

    new_bytes = render_cash_csv(off, new_proxies)
    old_bytes = file_text.encode("utf-8") if isinstance(file_text, str) else file_text
    if old_bytes is None:
        path = os.path.join(root, "data", sub.file)
        old_bytes = Path(path).read_bytes() if os.path.exists(path) else b""

    changed = new_bytes != _norm_nl(old_bytes) and new_bytes != old_bytes
    # also treat equivalent content
    if parse_rows(new_bytes) == rows and not recovered:
        changed = False
        # unless we added dates
        if {r["date"] for r in parse_rows(new_bytes)} != {r["date"] for r in rows}:
            changed = True
    if { (r["date"], r["value"], r["status"]) for r in parse_rows(new_bytes) } == { (r["date"], r["value"], r["status"]) for r in rows }:
        changed = False

    new_st, _, _ = inspect_feed(root, sub, cals, as_of, file_text=new_bytes.decode("utf-8"))
    if recovered:
        events.append(Event(
            key="proxy:recovered:%s" % sub.feed_id,
            kind="recovered",
            text="G8 · %s: volvió la fuente primaria (%s). Se retiran las filas PROXY de %s. El dato oficial vuelve a usarse."
                 % (sub.feed_id, sub.primary_source, ", ".join(_iso(d) for d in recovered[:8]) + ("…" if len(recovered) > 8 else "")),
        ))
    if new_st.late_bd >= 2 or new_proxies:
        if new_proxies and (changed or new_st.proxy_dates):
            first = min(p["date"] for p in new_proxies)
            last = max(p["date"] for p in new_proxies)
            events.append(Event(
                key="proxy:active:%s" % sub.feed_id,
                kind="proxy",
                text="G8 · %s: PROXY activo = %s del %s al %s (fuente/fecha visibles en el CSV). Un dato PROXY no vota. Cuando %s vuelva, recuperación automática."
                     % (sub.feed_id, sub.substitute_feed, _iso(first), _iso(last), sub.primary_source),
            ))
        elif new_st.late_bd >= 3 and sub.approval_status != "active":
            events.append(_event(new_st, "late3", sub))
        elif new_st.late_bd >= 2 and not new_proxies:
            events.append(_event(new_st, "late2", sub))
    return (new_bytes if changed else None), events, new_st


def _fmt_value(v):
    s = str(v).strip()
    try:
        f = float(s)
    except ValueError:
        return s
    # no inflar decimales: 2.7500 → 2.75, 2.8 se queda 2.8
    t = ("%.4f" % f).rstrip("0").rstrip(".")
    return t


def _norm_nl(b):
    if not b:
        return b
    if isinstance(b, str):
        b = b.encode("utf-8")
    return b.replace(b"\r\n", b"\n")


def _event(st, kind, sub):
    if kind == "late2":
        return Event(
            key="proxy:late2:%s" % st.feed_id,
            kind="late2",
            text="G8 · %s: 2 días hábiles tarde (último oficial %s, esperado %s, %d d.h.). Aviso; aún no se activa sustituto."
                 % (st.feed_id, st.last_official, st.expected_obs, st.late_bd),
        )
    if kind == "late3":
        why = "no hay sustituto preaprobado" if st.approval_status != "active" else "sustituto %s" % st.substitute_feed
        return Event(
            key="proxy:late3:%s" % st.feed_id,
            kind="late3",
            text="G8 · %s: 3 días hábiles tarde (último oficial %s, esperado %s, %d d.h.). %s."
                 % (st.feed_id, st.last_official, st.expected_obs, st.late_bd, why),
        )
    return Event(key="proxy:%s:%s" % (kind, st.feed_id), kind=kind, text="G8 · %s: %s" % (st.feed_id, kind))


def watch(root, as_of, apply=False, force_cash_from=None):
    """Revisa todos los feeds de substitutes.csv.

    apply=True escribe solo los que tienen approval_status=active.
    Devuelve (events, written_files, states).
    """
    cals = SC.load_calendars(root)
    events, written, states = [], {}, []
    for sub in load_substitutes(root):
        if not sub.feed_id:
            continue
        if sub.approval_status == "active" and sub.file:
            force = None
            if force_cash_from and sub.feed_id == "NZD_CASH_ON":
                force = force_cash_from
            new_b, ev, st = apply_one(root, sub, cals, as_of, force_from=force)
            events.extend(ev)
            states.append(st)
            if apply and new_b is not None:
                dest = os.path.join(root, "data", sub.file)
                Path(dest).write_bytes(new_b)
                written[sub.file] = new_b
        else:
            if not sub.file or sub.approval_status in ("retired", "not_ingested", "excluded", "licensed_no_public"):
                # no avisar en masa por feeds no ingeridos / licenciados / retirados
                states.append(FeedState(sub.feed_id, sub.file, None, None, None, 0, [], sub.approval_status, sub.substitute_feed))
                continue
            path = os.path.join(root, "data", sub.file)
            if not os.path.exists(path):
                states.append(FeedState(sub.feed_id, sub.file, None, None, None, 0, [], sub.approval_status, sub.substitute_feed))
                continue
            st, _, _ = inspect_feed(root, sub, cals, as_of)
            states.append(st)
            # La regla 2/3 d.h. aplica a feeds diarios ingeridos. Event-driven, monthly,
            # weekly, disabled, retired, excluded o licenciados no disparan aviso masivo
            # (el presupuesto LATE del registry sigue igual; ver acta).
            if st.late_bd >= 2 and sub.frequency == "daily" and sub.approval_status == "none":
                events.append(_event(st, "late3" if st.late_bd >= 3 else "late2", sub))
    return events, written, states


# frequency is on registry, not SubRow — don't use that broken ternary. I'll attach frequency on SubRow in loader.
# Fix watch() non-active branch in a follow-up patch if needed. For non-active daily feeds, 2bd/3bd alerts yes.
# Skip event-driven / monthly / disabled via calendar notes: add frequency to SubRow.

def check_for_ingest_watch(root, now_utc, alerts, info, prev_active=None):
    """Enganche de ingest_watch: solo avisa, no escribe CSV (el publicador del Mac aplica/recupera)."""
    as_of = now_utc.date() if hasattr(now_utc, "date") else now_utc
    if not os.path.exists(os.path.join(root, SUB_FILE)):
        return
    try:
        events, _, states = watch(root, as_of, apply=False)
    except Exception as e:
        alerts["proxy:error"] = "feed_proxy: error al revisar feeds: %s" % e
        return
    active_proxy = [s for s in states if s.proxy_dates]
    late = [s for s in states if s.late_bd >= 2]
    info.append("feed_proxy: %d feeds revisados · %d con PROXY · %d tarde ≥2 d.h."
                % (len(states), len(active_proxy), len(late)))
    # Un aviso por feed (el más grave). AlertBook deduplica por clave.
    by = {}
    for ev in events:
        # prioridad: recovered > proxy/late3 > late2
        rank = {"recovered": 3, "proxy": 2, "late3": 2, "late2": 1}.get(ev.kind, 0)
        prev = by.get(ev.key.rsplit(":", 1)[-1] if ev.key.startswith("proxy:") else ev.key)
        # keep one key per feed
        fid = ev.key.split(":")[-1]
        old = by.get(fid)
        if old is None or rank >= old[0]:
            by[fid] = (rank, ev)
    for fid, (_, ev) in sorted(by.items()):
        alerts[ev.key] = ev.text


def official_dates_from_bytes(data):
    """Fechas oficiales (no PROXY) para health_derivatives.cash()."""
    from g8common.freshness import obs_dates_from_bytes
    rows = parse_rows(data)
    if not rows or not any(r["status"] for r in rows):
        return obs_dates_from_bytes(data)
    return [r["date"] for r in official_rows(rows)]


def all_dates_from_bytes(data):
    rows = parse_rows(data)
    if not rows:
        from g8common.freshness import obs_dates_from_bytes
        return obs_dates_from_bytes(data)
    return [r["date"] for r in rows]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Proxy de feeds G8 (regla de feed muerto, t323u)")
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--as-of", default="", help="YYYY-MM-DD (por defecto hoy, zona local)")
    ap.add_argument("--apply", action="store_true", help="Escribe las filas PROXY de sustitutos active")
    ap.add_argument("--force-from", default="", help="Forzar relleno PROXY de NZD_CASH_ON desde esta fecha (YYYY-MM-DD)")
    ap.add_argument("--notify", action="store_true", help="Enviar eventos por Telegram (notify_telegram.send)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    as_of = date.fromisoformat(a.as_of) if a.as_of else datetime.now().date()
    force = date.fromisoformat(a.force_from) if a.force_from else None
    apply = a.apply and not a.dry_run
    events, written, states = watch(a.root, as_of, apply=apply, force_cash_from=force)
    for s in states:
        if s.late_bd or s.proxy_dates:
            print("  %s  late=%s  official=%s  any=%s  expected=%s  proxy=%d  status=%s"
                  % (s.feed_id, s.late_bd, s.last_official, s.last_any, s.expected_obs, len(s.proxy_dates), s.approval_status))
    print("eventos: %d  escritos: %s" % (len(events), ", ".join(written) or "—"))
    for ev in events:
        print("  [%s] %s" % (ev.kind, ev.text))
        if a.notify and not a.dry_run:
            try:
                import notify_telegram as NT
                NT.send(ev.text)
            except Exception as e:
                print("telegram: %s" % e)
    return 0


if __name__ == "__main__":
    sys.exit(main())

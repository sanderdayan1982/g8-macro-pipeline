#!/usr/bin/env python3
"""Operational health by publication calendar, plus final-session liveness.

No model/threshold changes. Unknown schedules remain UNKNOWN. Registry budgets
remain an additional guard; no holiday in another currency can make a feed fresh.
The report and alert ledger are separate from macro signals and their state.
"""
import argparse
import csv
import html
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import freshness_report as F
import health_evidence as E
from g8common import freshness as FR, schedule as SC, series, notify
from tools.finalize_session import session_due

ROOT = Path(__file__).resolve().parents[1]
BAD = set(FR.UNHEALTHY_STATES) | {"ERROR", "FINAL_MISSING", "MISSING"}
GOOD = {"CURRENT", "NO_PUBLICATION"}


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def business_age(start, end, calendar, cals):
    n = 0
    while start < end:
        start += timedelta(days=1)
        n += int(SC.is_bd(cals, calendar, start))
    return n


def outstanding_fetch_failure(facts, filename):
    """A newer validated publication closes an older attempt, never the reverse."""
    records = list(facts.values())
    for failed in records:
        if failed.get('rc') in (None, 0):
            continue
        failed_at = failed.get('finished_utc') or failed.get('started_utc')
        recovered = False
        for ok in records:
            published_at = ok.get('finished_utc') or ok.get('started_utc')
            output = ok.get('file') or (ok.get('files') or {}).get(filename) or {}
            if (ok.get('rc') == 0 and output.get('status') in ('PUBLISH', 'NOOP')
                    and (output.get('status') == 'NOOP' or output.get('written') is True)
                    and failed_at and published_at and published_at > failed_at):
                recovered = True
        if not recovered:
            return True
    return False


def build(root, now, report=None):
    root = Path(root)
    src = F.TreeSource(str(root))
    report = report if report is not None else F.build(str(root), now, src)
    cals = SC.load_calendars(str(root))
    with (root / "sources/registry.csv").open() as f:
        registry = list(csv.DictReader(f))
    regs = {}
    for r in registry:
        if r["primary_access"].startswith("data/"):
            regs.setdefault(r["primary_access"][5:], []).append(r)
    with (root / "sources/freshness_rules.csv").open() as f:
        cadence = {name: r['frequency'] for r in csv.DictReader(f) for name in r['files'].split(';') if name}
    policy = E.read_json(root / "sources/policy_evidence.json").get("policies", {})
    items = []
    for row in report["outputs"]:
        item = {k: row.get(k) for k in ("file", "state", "have_max", "expected_obs", "calendar", "basis", "reason", "cause")}
        item["flags"] = row.get("flags", [])
        item["status"] = "LATE" if row["state"] in BAD else "CURRENT" if row["state"] in GOOD else "UNKNOWN"
        if row["state"] in ("DUE", "PENDING_TIME_UNKNOWN"):
            item["status"] = "PENDING"
        if row["state"] == "NOT_MONITORED":
            item["status"] = "EXCLUDED"
        # An HTTP success cannot close an outstanding error without a published
        # output; existing ingest_watch also checks these facts independently.
        if outstanding_fetch_failure(row.get("facts", {}).get("by_source") or {}, row['file']):
            item["flags"] = item["flags"] + ["LAST_FETCH_FAILED"]
        for reg in regs.get(row["file"], []):
            # Monthly/manual budgets stay as registered. The calendar is LOCAL.
            d = row.get("have_max")
            if not d and row["file"].endswith(".json"):
                js = read_json(root / "data" / row["file"])
                d = next((str(js[k])[:10] for k in ("latest_session", "report_date", "as_of") if js.get(k)), None)
                if row["file"] == "MFV_G8_state.json" and js.get("metals"):
                    observations = [FR.obs_dates_from_bytes(src.read("MFV_G8_%s.csv" % metal)) for metal in ("XAU", "XAG")]
                    if all(observations):
                        d = min(max(ds) for ds in observations).isoformat()
            if d:
                parsed = FR._parse_date(d)
                budget = float(reg.get("max_staleness_bd") or 0)
                cal = {"EU": "TARGET"}.get(reg["calendar"], reg["calendar"])
                if parsed:
                    age = business_age(parsed, now.date(), cal, cals)
                    item["have_max"] = parsed.isoformat()
                    item["age_business_days"] = age
                    if parsed > now.date():
                        item.update(status="UNKNOWN", reason="Fecha futura: no es una observación corriente")
                    elif budget and age > budget:
                        item.update(status="LATE", reason="Fuera del presupuesto del registro (%s días hábiles %s)" % (budget, cal))
                    elif item["status"] == "EXCLUDED":
                        item.update(status="UNKNOWN", reason="Dentro del presupuesto; calendario exacto de publicación sin validar")
            elif row["file"] not in ("manual/manual_inputs.json",) and reg.get("manual") != "Y":
                item.update(status="MISSING", reason="Sin observación legible para un feed del registro")
        if "LAST_FETCH_FAILED" in item["flags"]:
            item["status"] = "ERROR"
        item['publication_frequency'] = cadence.get(row['file'], 'unknown')
        item['slow_fallback'] = item['publication_frequency'].startswith(('monthly', 'quarterly'))
        if row['file'] == 'ACM_G8_CHF.csv':
            item['frequency_note'] = 'Salida diaria estimada; curva oficial mensual + nominal 10Y diario (NOWCAST)'
        E.refine(root, now, item, policy)
        items.append(item)
    # A recently generated provisional JSON does not prove that CTF was evaluated.
    # 4h operational deadline allows observed scheduler delays; visible separately
    # from the unchanged detector. Before the deadline, require the prior session.
    target = session_due(now - timedelta(hours=4)).isoformat()
    state = read_json(root / "data/s01b/state.json")
    log = read_json(root / "data/s01b/log" / (target + ".json"))
    ok = state.get("t", "") >= target and log.get("t") == target and log.get("final_run") is True
    items.append({"file": "s01b/state.json", "state": "CURRENT" if ok else "FINAL_MISSING",
                  "status": "CURRENT" if ok else "LATE", "have_max": state.get("t"), "expected_obs": target,
                  "reason": "Última evaluación definitiva; PROVISIONAL no cuenta como cierre", "flags": []})
    for item in items:
        if item['file'] == 'S01B.json':
            snapshot = read_json(root / 'data/S01B.json')
            same_session = snapshot.get('as_of') == state.get('t') and snapshot.get('as_of', '') >= target
            if item['status'] != 'ERROR':
                item.update(status='CURRENT' if ok and same_session else 'LATE', expected_obs=target,
                            reason='Fecha del panel contrastada con estado y log definitivos CTF; no certifica por sí sola todas las entradas macro.')
    # The per-section metadata files are checked for presence without pretending
    # that their write timestamp is the observation timestamp.
    for name, key in (("alerts/brief.json", "generated_utc"), ("BOOK_RISK.json", "as_of")):
        d = read_json(root / "data" / name)
        status = "CURRENT" if d.get(key) else "MISSING"
        if key == "generated_utc" and d.get(key):
            try:
                age = (now - datetime.fromisoformat(d[key].replace("Z", "+00:00"))).total_seconds() / 3600
                if age > (78 if now.weekday() in (5, 6, 0) else 30):
                    status = "LATE"
            except ValueError:
                status = "MISSING"
        if name == "BOOK_RISK.json" and d.get(key) != read_json(root / "data/USD_FACTOR.json").get("as_of"):
            status = "LATE"
        items.append({"file": name, "status": status, "state": status, "have_max": d.get(key), "flags": []})
    manual = read_json(root / "data/manual/manual_inputs.json")
    for reg in registry:
        if reg.get("manual") != "Y" or not reg.get("manual_expiry_days"):
            continue
        entry = manual.get(reg["feed_id"], {})
        parsed = FR._parse_date(str(entry.get("date") or ""))
        age = (now.date() - parsed).days if parsed else None
        status = "MISSING" if age is None or entry.get("value") is None else "LATE" if age > float(reg["manual_expiry_days"]) else "UNKNOWN"
        items.append({"file": "manual/" + reg["feed_id"], "status": status, "state": "MANUAL",
                      "have_max": entry.get("date"), "expected_obs": None, "flags": ["MANUAL"], "publication_frequency": "excluded" if entry.get("disabled") else reg.get("frequency", "unknown"), "slow_fallback": False,
                      "reason": "NO DISPONIBLE: fuente trimestral excluida; máximo mensual" if entry.get("disabled") else "Constante manual; caducidad %s días; no es una observación diaria" % reg["manual_expiry_days"]})
    issues = [x for x in items if x["status"] in ("LATE", "ERROR", "MISSING")]
    unknown = [x for x in items if x["status"] == "UNKNOWN"]
    return {"schema": "G8_HEALTH/1", "generated_utc": now.isoformat().replace("+00:00", "Z"),
            "status": "DEGRADED" if issues else "UNVERIFIED" if unknown else "CURRENT",
            "slow_fallbacks": [x["file"] for x in items if x.get("slow_fallback")],
            "source_priority": "daily > weekly > monthly as last resort; quarterly and slower prohibited",
            "needs_repair": bool(issues), "issues": issues, "unknown_count": len(unknown), "feeds": items,
            "note": "Fechas de observación y calendario de cada fuente. UNKNOWN no significa fresco. EST no es dato oficial."}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/_ingest/health.json")
    ap.add_argument("--notify", action="store_true")
    ap.add_argument("--at")
    args = ap.parse_args(argv)
    now = datetime.fromisoformat(args.at.replace("Z", "+00:00")) if args.at else datetime.now(timezone.utc)
    report = build(ROOT, now)
    series.write_atomic(str(ROOT / args.out), (json.dumps(report, ensure_ascii=False, indent=1) + "\n").encode())
    if args.notify:
        book = notify.AlertBook(str(ROOT / "data/_ingest/health_alert_state.json"))
        alerts = {x["file"]: "%s: %s · dato %s · esperado %s" % (x["file"], x["status"], x.get("have_max"), x.get("expected_obs")) for x in report["issues"]}
        plan = book.plan(alerts, now.timestamp())
        sent, pending = set(), []
        # Bounded batches avoid marking truncated, unseen alerts as delivered.
        for i in range(0, len(plan), 8):
            group = plan[i:i+8]
            msg = "<b>G8 · frescura y evaluación</b>\n" + "\n".join(html.escape(kind + ": " + text) for kind, _, text in group)
            delivery = notify.send(msg)
            if delivery == "SENT":
                sent.update(key for _, key, _ in group)
            elif delivery != "DRY":
                pending.append(msg)
        if os.environ.get("G8_NO_SEND") != "1":
            book.commit(alerts, sent, now.timestamp(), pending)
        if pending:
            print("Health alerts not delivered")
            return 1
    print("Health: %s · %d incidencias · %d sin calendario confirmado" % (report["status"], len(report["issues"]), report["unknown_count"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

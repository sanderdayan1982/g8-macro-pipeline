#!/usr/bin/env python3
"""agent_doctor.py — Diagnóstico priorizado (1º Diaria → 2º Semanal → 3º Mensual) y autorrecuperación para el agente G8.

Cumple la instrucción del propietario:
  - Mantener la frescura de datos con prioridad estricta:
      1º DIARIA (daily)
      2º SEMANAL (weekly)
      3º MENSUAL (monthly, únicamente como último recurso; trimestral o más lento PROHIBIDO)
  - Dotar al agente de diagnóstico estructurado antes de actuar (.agent/diagnosis.md / .agent/diagnosis.json),
    autorrecuperación de workflows de datos (.agent/recovery.json) y resumen claro de incidencias por prioridad
    para el reporte de Telegram.

Uso:
  python scripts/tools/agent_doctor.py --out-md .agent/diagnosis.md --out-json .agent/diagnosis.json
  python scripts/tools/agent_doctor.py --auto-recovery .agent/recovery.json
  python scripts/tools/agent_doctor.py --telegram-summary
"""
import argparse
import csv
import datetime as dt
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA = os.path.join(ROOT, "data")
INGEST = os.path.join(DATA, "_ingest")
LATEST_DIR = os.path.join(INGEST, "latest")
SOURCES_REG = os.path.join(ROOT, "sources", "registry.csv")

FREQ_PRIORITY = {
    "daily": 1,
    "event": 1,     # tipos oficiales vigilados a diario + por reunión
    "weekly": 2,
    "monthly": 3,
}

PRIORITY_LABELS = {
    1: "1º DIARIA",
    2: "2º SEMANAL",
    3: "3º MENSUAL (último recurso)",
    4: "FUERA DE TECHO (> mensual · PROHIBIDO)",
}

SHADOW_ARTIFACTS = [
    ("MFV_G8_shadow_state.json", "daily", "§04/§05 Metales B2 diario", "metals_update.yml"),
    ("CB_EVENT_48H_shadow.json", "daily", "§02 Calendario BC 48h C2", "daily_update.yml"),
    ("CME_OPTIONS_C1_shadow.json", "daily", "§06 Opciones CME C1", "cme_options.yml"),
    ("S01B_shadow_a3.json", "daily", "§01-b CTF shadow A3", "daily_update.yml"),
    ("ACM_G8_NZD_phi_shadow.json", "daily", "§01/§03 NZD ACM phi shadow A1", "daily_update.yml"),
    ("CHF_NOWCAST_shadow.json", "daily", "§01/§03 CHF nowcast shadow A2", "daily_update.yml"),
]


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def load_registry(reg_path=SOURCES_REG):
    """Devuelve dict por nombre de fichero CSV/JSON y por feed_id desde sources/registry.csv."""
    by_file = {}
    by_id = {}
    if not os.path.exists(reg_path):
        return by_file, by_id
    with open(reg_path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            fid = (row.get("feed_id") or "").strip()
            rel = (row.get("file") or "").strip()
            base = os.path.basename(rel)
            if fid:
                by_id[fid] = row
            if base:
                by_file[base] = row
    return by_file, by_id


def normalize_freq(freq_str):
    f = str(freq_str or "").strip().lower()
    if f in ("daily", "diaria", "intraday", "business_daily"):
        return "daily"
    if f in ("event", "evento"):
        return "event"
    if f in ("weekly", "semanal"):
        return "weekly"
    if f in ("monthly", "mensual"):
        return "monthly"
    if f in ("quarterly", "trimestral", "annual", "anual"):
        return "prohibited"
    return "daily"


def freq_rank(freq_str):
    nf = normalize_freq(freq_str)
    if nf == "prohibited":
        return 4
    return FREQ_PRIORITY.get(nf, 1)


def _remediation_for_feed(feed_name, causes, fetcher=""):
    fn = str(feed_name or "")
    causes_s = ",".join(causes or [])
    if fn in ("NZD_OCR.csv", "fetch_bis_ocr") or fn.endswith("_POLICY.csv"):
        return {
            "action": "Usar respaldo oficial BIS WS_CBPOL_csv_flat.zip (g8common.bis_bulk con >=20 obs de solapamiento) y relanzar feed_recovery.yml",
            "workflow": "feed_recovery.yml",
            "reason": "%s con %s: refrescar tipos oficiales con respaldo bulk BIS" % (fn, causes_s or "fallo de ingesta"),
        }
    if fn in ("NZD_BOND_10Y.csv", "RY_G8_NZD.csv", "ACM_G8_NZD.csv"):
        return {
            "action": "Ejecutar feed_recovery.yml (fetch_nzd_b2.py --no-hist + real_yields_g8.py NZD) o verificar launchd en el Mac del operador",
            "workflow": "feed_recovery.yml",
            "reason": "%s con %s: recuperar curva NZD B2 / IIB sin histórico" % (fn, causes_s or "atraso"),
        }
    if fn in ("CHF_NOM_10Y.csv", "ACM_G8_CHF.csv", "CHF_CPI_YOY.csv"):
        return {
            "action": "Verificar fetch_chf_snb.py (Mac launchd / RSS SNB) y fetch_chf_cpi.py (BFS mensual); relanzar feed_recovery.yml",
            "workflow": "feed_recovery.yml",
            "reason": "%s con %s: refrescar insumos suizos y snapshots" % (fn, causes_s or "atraso"),
        }
    if fn.startswith("OPTIONS_SURFACE") or "CME_OPTIONS" in fn:
        return {
            "action": "Relanzar workflow cme_options.yml y verificar DATABENTO_API_KEY",
            "workflow": "cme_options.yml",
            "reason": "%s con %s: regenerar superficie de opciones CME" % (fn, causes_s or "atraso"),
        }
    if fn.startswith("MFV_G8_"):
        return {
            "action": "Relanzar workflow metals_update.yml para actualizar estado semanal y shadow diario B2",
            "workflow": "metals_update.yml",
            "reason": "%s con %s: actualizar modelo y shadow de metales" % (fn, causes_s or "atraso"),
        }
    if fn in ("USD_FACTOR.json", "BOOK_RISK.json"):
        return {
            "action": "Relanzar workflow usd_factor.yml tras publicación de tipos BCE",
            "workflow": "usd_factor.yml",
            "reason": "%s con %s: recalcular factor USD y riesgo de libro" % (fn, causes_s or "atraso"),
        }
    return {
        "action": "Revisar descargador %s y relanzar feed_recovery.yml / intraday_fetch.yml" % (fetcher or fn),
        "workflow": "feed_recovery.yml",
        "reason": "%s con %s: refrescar feeds diarios oficiales y snapshots" % (fn, causes_s or "incidencia"),
    }


def collect_diagnosis(data_dir=DATA, reg_path=SOURCES_REG):
    """Recopila el estado completo de salud, frescura, descargadores, artefactos shadow y alertas de workflows."""
    ingest_dir = os.path.join(data_dir, "_ingest")
    latest_dir = os.path.join(ingest_dir, "latest")
    by_file, by_id = load_registry(reg_path)

    health = _load_json(os.path.join(ingest_dir, "health.json")) or {}
    watch = _load_json(os.path.join(ingest_dir, "watch_state.json")) or {}
    brief = _load_json(os.path.join(data_dir, "alerts", "brief.json")) or {}

    incidents = []
    seen_feeds = set()
    seen_jobs = set()

    # 1. Feeds en ERROR / WARNING / UNKNOWN en health.json (issues + feeds)
    issue_files = {x.get("file") for x in (health.get("issues") or [])}
    feed_items = (health.get("issues") or []) + [
        f for f in (health.get("feeds") or [])
        if f.get("status") in ("ERROR", "WARNING", "UNKNOWN") and f.get("file") not in issue_files
    ]
    for f in feed_items:
        status = f.get("status") or ("ERROR" if "LAST_FETCH_FAILED" in (f.get("flags") or []) else "WARNING")
        fname = f.get("file") or f.get("name") or ""
        reg = by_file.get(fname) or by_id.get(f.get("name") or "") or {}
        freq = f.get("publication_frequency") or f.get("freq") or reg.get("frequency") or "daily"
        rank = freq_rank(freq)
        causes = list(f.get("flags") or [])
        if f.get("state") and f.get("state") not in ("CURRENT", "NO_PUBLICATION"):
            causes.append(f["state"])
        fetcher = f.get("fetcher") or reg.get("fetcher") or ""
        rem = _remediation_for_feed(fname, causes, fetcher)
        incidents.append({
            "kind": "FEED_HEALTH",
            "id": fname or f.get("name") or "unknown",
            "section": f.get("section") or reg.get("section") or "—",
            "state": status,
            "frequency": normalize_freq(freq),
            "priority_rank": rank,
            "priority_label": PRIORITY_LABELS.get(rank, PRIORITY_LABELS[1]),
            "last_obs": f.get("have_max") or f.get("last_obs"),
            "delay_bd": f.get("age_business_days") if f.get("age_business_days") is not None else f.get("delay_bd"),
            "budget_bd": f.get("budget_bd"),
            "causes": causes,
            "fetcher": fetcher,
            "detail": f.get("reason") or (f.get("last_fetch") or {}).get("error") or ", ".join(causes),
            "remediation": rem["action"],
            "recovery_workflow": rem["workflow"],
            "recovery_reason": rem["reason"],
        })
        seen_feeds.add(fname)
        if fname == "NZD_OCR.csv":
            seen_jobs.add("fetch_bis_ocr")

    # 2. Últimas ejecuciones de descargadores en data/_ingest/latest/*.json con rc != 0
    if os.path.isdir(latest_dir):
        for fn in sorted(os.listdir(latest_dir)):
            if not fn.endswith(".json"):
                continue
            doc = _load_json(os.path.join(latest_dir, fn)) or {}
            rc = doc.get("rc", 0)
            if rc == 0:
                continue
            job_name = doc.get("job") or fn.replace("actions__", "").replace(".json", "")
            if job_name in seen_jobs:
                continue
            files_field = doc.get("files") or {}
            target_files = [os.path.basename(p) for p in (files_field.keys() if isinstance(files_field, dict) else files_field)]
            primary_file = target_files[0] if target_files else ("NZD_OCR.csv" if job_name == "fetch_bis_ocr" else job_name)
            if primary_file in seen_feeds:
                continue
            reg = by_file.get(primary_file) or {}
            freq = reg.get("frequency") or "daily"
            rank = freq_rank(freq)
            errs = doc.get("errors") or ["rc=%s (failing_since=%s)" % (rc, doc.get("failing_since_utc") or "—")]
            rem = _remediation_for_feed(primary_file, ["LAST_FETCH_FAILED"], job_name)
            incidents.append({
                "kind": "FETCHER_FAILURE",
                "id": primary_file,
                "section": "ingesta",
                "state": "ERROR",
                "frequency": normalize_freq(freq),
                "priority_rank": rank,
                "priority_label": PRIORITY_LABELS.get(rank, PRIORITY_LABELS[1]),
                "last_obs": doc.get("last_ok_utc"),
                "delay_bd": None,
                "budget_bd": None,
                "causes": ["LAST_FETCH_FAILED"],
                "fetcher": job_name,
                "detail": "; ".join(str(x) for x in errs if x)[:240],
                "remediation": rem["action"],
                "recovery_workflow": rem["workflow"],
                "recovery_reason": rem["reason"],
            })
            seen_feeds.add(primary_file)
            seen_jobs.add(job_name)

    # 3. Artefactos shadow (U2: B2, B3, C1, C2, A4)
    for sh_file, sh_freq, sh_label, sh_wf in SHADOW_ARTIFACTS:
        sh_path = os.path.join(data_dir, sh_file)
        ok_sh = os.path.isfile(sh_path) and os.path.getsize(sh_path) > 10
        if ok_sh and sh_file.endswith(".json"):
            ok_sh = _load_json(sh_path) is not None
        rank = freq_rank(sh_freq)
        if not ok_sh:
            incidents.append({
                "kind": "SHADOW_ARTIFACT",
                "id": sh_file,
                "section": sh_label,
                "state": "ERROR",
                "frequency": sh_freq,
                "priority_rank": rank,
                "priority_label": PRIORITY_LABELS.get(rank, PRIORITY_LABELS[1]),
                "last_obs": None,
                "delay_bd": None,
                "budget_bd": None,
                "causes": ["SHADOW_MISSING_OR_CORRUPT"],
                "fetcher": sh_wf,
                "detail": "Artefacto shadow ausente o corrupto",
                "remediation": "Relanzar %s para regenerar %s" % (sh_wf, sh_file),
                "recovery_workflow": sh_wf,
                "recovery_reason": "Regenerar artefacto shadow %s (%s)" % (sh_file, sh_label),
            })

    # 4. Alertas operativas activas en data/_ingest/watch_state.json
    active_alerts = (watch.get("active") or {}) if isinstance(watch, dict) else {}
    for key, info in sorted(active_alerts.items()):
        if not isinstance(info, dict):
            continue
        msg = info.get("text") or info.get("msg") or key
        wf_suggest = None
        prio_freq = "daily"
        if "fetch_bis_ocr" in key:
            wf_suggest = "feed_recovery.yml"
            prio_freq = "event"
        elif "Mac Local Fetch" in key:
            wf_suggest = "feed_recovery.yml"
            prio_freq = "daily"
        elif "CME Options" in key or "cme_options" in key:
            wf_suggest = "cme_options.yml"
            prio_freq = "daily"
        elif "metals" in key.lower():
            wf_suggest = "metals_update.yml"
            prio_freq = "weekly"
        elif "usd_factor" in key.lower() or "USD Factor" in key:
            wf_suggest = "usd_factor.yml"
            prio_freq = "daily"
        elif "daily_update" in key.lower() or "Daily Data Update" in key:
            wf_suggest = "daily_update.yml"
            prio_freq = "daily"
        rank = freq_rank(prio_freq)
        incidents.append({
            "kind": "WATCH_ALERT",
            "id": key,
            "section": "Actions / Vigilancia",
            "state": "WARNING",
            "frequency": normalize_freq(prio_freq),
            "priority_rank": rank,
            "priority_label": PRIORITY_LABELS.get(rank, PRIORITY_LABELS[1]),
            "last_obs": info.get("since"),
            "delay_bd": None,
            "budget_bd": None,
            "causes": ["WATCH_ACTIVE"],
            "fetcher": wf_suggest or "",
            "detail": msg,
            "remediation": (
                "Relanzar %s y verificar que la ejecución completa limpia la alerta en watch_state.json" % wf_suggest
                if wf_suggest else "Revisar logs del workflow en GitHub Actions"
            ),
            "recovery_workflow": wf_suggest,
            "recovery_reason": "Alerta activa en watch_state (%s): %s" % (key, msg[:160]),
        })

    # Ordenar estrictamente por: 1º prioridad de frecuencia (diaria -> semanal -> mensual), 2º severidad (ERROR antes que WARNING)
    sev_order = {"ERROR": 0, "WARNING": 1, "UNKNOWN": 2}
    incidents.sort(key=lambda x: (x["priority_rank"], sev_order.get(x["state"], 3), x["id"]))

    by_prio = {1: 0, 2: 0, 3: 0, 4: 0}
    for inc in incidents:
        r = inc["priority_rank"]
        by_prio[r] = by_prio.get(r, 0) + 1

    return {
        "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "health_summary": health.get("summary") or {},
        "ctf_final": health.get("ctf_final") or {},
        "brief_generated_utc": brief.get("generated_utc"),
        "priority_counts": {
            "daily": by_prio[1],
            "weekly": by_prio[2],
            "monthly": by_prio[3],
            "prohibited": by_prio[4],
        },
        "incidents": incidents,
    }


def build_recovery_requests(diag, max_dispatch=3):
    """Construye hasta max_dispatch peticiones válidas para agent_recovery.py priorizando 1º Diaria -> 2º Semanal -> 3º Mensual."""
    reqs = []
    seen_wf = set()
    for inc in diag.get("incidents") or []:
        wf = inc.get("recovery_workflow")
        if not wf or wf in seen_wf:
            continue
        # Solo proponer relanzamiento automático ante fallos reales de descarga/salud o alertas activas
        if inc.get("state") not in ("ERROR", "WARNING"):
            continue
        reason = inc.get("recovery_reason") or ("%s en estado %s" % (inc.get("id"), inc.get("state")))
        reqs.append({
            "workflow": wf,
            "reason": "[%s] %s" % (inc.get("priority_label", "1º DIARIA"), reason[:240]),
        })
        seen_wf.add(wf)
        if len(reqs) >= max_dispatch:
            break
    return reqs


def format_markdown(diag):
    """Genera el informe Markdown pre-diagnóstico para el agente."""
    lines = [
        "# Pre-diagnóstico automático G8 (`agent_doctor.py`)",
        "",
        "- **Generado (UTC)**: `%s`" % (diag.get("generated_utc") or "—"),
        "- **Jerarquía de frescura obligatoria**: **1º DIARIA** → **2º SEMANAL** → **3º MENSUAL (último recurso; prohibido trimestral o más lento)**",
        "- **Resumen `health.json`**: `%s`" % json.dumps(diag.get("health_summary") or {}, ensure_ascii=False),
        "- **Última sesión definitiva CTF (`§01-b`)**: `%s`" % json.dumps(diag.get("ctf_final") or {}, ensure_ascii=False),
        "",
    ]
    incs = diag.get("incidents") or []
    if not incs:
        lines.append("## Estado: SIN INCIDENCIAS ABIERTAS")
        lines.append("Todos los feeds registrados, artefactos shadow y monitores de workflows están en verde.")
        return "\n".join(lines) + "\n"

    lines.append("## Incidencias abiertas ordenadas por prioridad de frecuencia (%d)" % len(incs))
    lines.append("")
    for idx, inc in enumerate(incs, 1):
        lines.append(
            "%d. **[%s · %s]** `%s` (%s) — obs: `%s` · causas: `%s`"
            % (
                idx,
                inc["priority_label"],
                inc["state"],
                inc["id"],
                inc["section"],
                inc.get("last_obs") or "—",
                ", ".join(inc.get("causes") or []) or "—",
            )
        )
        if inc.get("detail"):
            lines.append("   - Detalle: %s" % inc["detail"])
        if inc.get("remediation"):
            lines.append("   - Acción recomendada: %s" % inc["remediation"])
    lines.append("")
    return "\n".join(lines) + "\n"


def format_telegram_summary(diag):
    """Resumen compacto por prioridad para adjuntar al reporte de Telegram cuando hay incidencias."""
    incs = diag.get("incidents") or []
    if not incs:
        return "✓ Monitor de frescura (1º Diaria / 2º Semanal / 3º Mensual): 0 incidencias abiertas."
    counts = diag.get("priority_counts") or {}
    head = (
        "⚡ Frescura G8 (1º Diaria: %d · 2º Semanal: %d · 3º Mensual: %d):"
        % (counts.get("daily", 0), counts.get("weekly", 0), counts.get("monthly", 0))
    )
    bullets = []
    for inc in incs[:6]:
        bullets.append(
            "• [%s/%s] %s: %s"
            % (inc["priority_label"].split()[0], inc["state"], inc["id"], (inc.get("detail") or "")[:90])
        )
    return head + "\n" + "\n".join(bullets)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=DATA)
    ap.add_argument("--out-md", help="Ruta donde escribir el diagnóstico Markdown (.agent/diagnosis.md)")
    ap.add_argument("--out-json", help="Ruta donde escribir el diagnóstico JSON (.agent/diagnosis.json)")
    ap.add_argument("--auto-recovery", help="Si no existe o está vacío, escribe peticiones de recuperación en la ruta dada")
    ap.add_argument("--telegram-summary", action="store_true", help="Imprime resumen compacto para Telegram")
    args = ap.parse_args(argv)

    diag = collect_diagnosis(data_dir=args.data_dir)

    if args.out_json:
        os.makedirs(os.path.dirname(os.path.abspath(args.out_json)), exist_ok=True)
        with open(args.out_json, "w", encoding="utf-8") as fh:
            json.dump(diag, fh, indent=2, ensure_ascii=False)

    if args.out_md:
        os.makedirs(os.path.dirname(os.path.abspath(args.out_md)), exist_ok=True)
        with open(args.out_md, "w", encoding="utf-8") as fh:
            fh.write(format_markdown(diag))

    if args.auto_recovery:
        rec_path = args.auto_recovery
        existing = _load_json(rec_path)
        if not existing:
            reqs = build_recovery_requests(diag)
            if reqs:
                os.makedirs(os.path.dirname(os.path.abspath(rec_path)), exist_ok=True)
                with open(rec_path, "w", encoding="utf-8") as fh:
                    json.dump(reqs, fh, indent=2, ensure_ascii=False)

    if args.telegram_summary:
        print(format_telegram_summary(diag))
    elif not args.out_md and not args.out_json and not args.auto_recovery:
        print(format_markdown(diag), end="")

    return 0


if __name__ == "__main__":
    sys.exit(main())

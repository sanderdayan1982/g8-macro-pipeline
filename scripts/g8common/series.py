"""series — validación, fusión monótona, cuarentena y escritura atómica de series CSV (stdlib, Python ≥3.9).

Reglas P6 (plan v1.1 §4.6 + condiciones del 24-sep-2026):
  · Una descarga vacía, ilegible, incompleta o más antigua NO sustituye al último fichero válido.
  · Fusión monótona: unión de fechas; ninguna fecha existente se borra; la fecha máxima no retrocede.
  · Revisiones: se detectan TODAS las diferencias de valor (no solo la fecha máxima). Dentro de la
    ventana revisable de la fuente se aceptan si son plausibles; fuera de ella (corrección histórica)
    pasan a confirmación, igual que un salto implausible.
  · Salto implausible (decisión T06): se conserva el último dato válido, el candidato se guarda aparte,
    se avisa y se vuelve a comprobar. Se acepta con trazabilidad si una descarga POSTERIOR e independiente
    lo confirma (mismo valor, separación mínima) o por decisión manual registrada; se retiene si se
    rechaza manualmente. Nunca entra en los cálculos solo con una etiqueta.
  · Escritura atómica: temporal en el mismo directorio + fsync + os.replace.
  · Las líneas se conservan tal cual (texto original): si la fuente trae todas las fechas guardadas, el
    resultado es byte a byte la respuesta formateada (equivalencia con los escritores actuales).
"""
import bisect
import hashlib
import math
import os
from datetime import datetime, timedelta, timezone

# ── estados ───────────────────────────────────────────────────────────────────
PUBLISH = "PUBLISH"                    # hay cambios válidos que publicar
NOOP = "NOOP"                          # la fuente no aporta nada nuevo
INVALID = "INVALID"                    # respuesta inválida: no se toca nada
REGRESSION_BLOCKED = "REGRESSION_BLOCKED"
HELD = "HELD"                          # hay candidatos en confirmación; se publica lo demás (o nada si todo está retenido)

Q_PENDING = "PENDING"
Q_ACCEPTED_CONFIRMED = "ACCEPTED_CONFIRMED"
Q_ACCEPTED_MANUAL = "ACCEPTED_MANUAL"
Q_REJECTED_MANUAL = "REJECTED_MANUAL"
Q_SUPERSEDED = "SUPERSEDED"

COVERAGE_MIN = 0.90       # la respuesta debe traer ≥90 % de las fechas ya guardadas dentro de su propio rango
CONFIRM_GAP_MIN = 30      # separación mínima (min) entre la descarga candidata y la que la confirma
MIN_YEAR, MAX_YEAR = 1900, 2100   # límites de cordura del calendario (no sustituyen al horizonte por serie)


class SeriesError(ValueError):
    pass


def _norm_date(s):
    """'YYYY-MM-DD' o 'YYYYMMDD' → 'YYYYMMDD', solo si es una fecha REAL del calendario (hallazgo #7)."""
    s = s.strip().strip('"')
    d = s.replace("-", "")
    if not (len(d) == 8 and d.isdigit()) or ("-" in s and not (len(s) == 10 and s[4] == "-" and s[7] == "-")):
        raise SeriesError("fecha ilegible: %r" % s[:20])
    try:
        datetime.strptime(d, "%Y%m%d")
    except ValueError:
        raise SeriesError("fecha inexistente: %r" % s[:20])
    if not (MIN_YEAR <= int(d[:4]) <= MAX_YEAR):
        raise SeriesError("fecha fuera del intervalo admitido %d–%d: %r" % (MIN_YEAR, MAX_YEAR, s[:20]))
    return d


def load_horizons(data):
    """sources/date_horizon.csv (bytes o str) → función fname → max_future_days (int) o None.
    Admite patrones con '*' (fnmatch). Un fichero sin fila, o con celda vacía, no tiene horizonte."""
    import csv as _csv
    import fnmatch
    import io
    rules = []
    if data:
        txt = data.decode("utf-8") if isinstance(data, bytes) else data
        for row in _csv.DictReader(io.StringIO(txt)):
            v = (row.get("max_future_days") or "").strip()
            rules.append((row.get("file", "").strip(), int(v) if v else None))

    def horizon(fname):
        for pat, v in rules:
            if pat == fname:
                return v
        for pat, v in rules:
            if "*" in pat and fnmatch.fnmatchcase(fname, pat):
                return v
        return None
    return horizon


def check_horizon(series, today, max_future_days):
    """Coherencia temporal POR SERIE (hallazgo #7). max_future_days: días naturales que la fecha máxima puede
    adelantarse a `today` (fecha UTC del ejecutor, AAAAMMDD). None = sin límite documentado para esa serie
    (no se impone una prohibición genérica de fechas futuras: p. ej. tipos oficiales con fecha efectiva
    anunciada). Devuelve None si es coherente o el motivo del rechazo."""
    if max_future_days is None or series.max_date is None:
        return None
    lim = (datetime.strptime(today, "%Y%m%d") + timedelta(days=int(max_future_days))).strftime("%Y%m%d")
    if series.max_date > lim:
        return "fecha %s posterior al horizonte de la serie (%s + %s días)" % (series.max_date, today, max_future_days)
    return None


class Series(object):
    """CSV de una serie: comentarios iniciales (#), cabecera, filas por fecha (línea original + valor)."""

    def __init__(self, comments, header, rows, eol, value_col, trailing_eol=True):
        self.comments = comments      # lista de líneas '#…'
        self.header = header          # línea de cabecera original
        self.rows = rows              # {YYYYMMDD: (línea_original, valor_float)}
        self.eol = eol
        self.value_col = value_col
        self.trailing_eol = trailing_eol

    @property
    def dates(self):
        return sorted(self.rows)

    @property
    def max_date(self):
        return max(self.rows) if self.rows else None

    def value(self, d):
        return self.rows[d][1]

    def to_bytes(self):
        lines = list(self.comments) + [self.header] + [self.rows[d][0] for d in sorted(self.rows)]
        txt = self.eol.join(lines)
        if self.trailing_eol:
            txt += self.eol
        return txt.encode("utf-8")


VALUE_COLS = ("CLOSE", "Value", "VALUE", "NOM10", "TP10")


def parse(data, value_col=None, expect_header=None):
    """bytes → Series. Lanza SeriesError si vacío, sin cabecera, fechas ilegibles, duplicadas o valores no finitos."""
    if data is None or not data.strip():
        raise SeriesError("respuesta vacía")
    txt = data.decode("utf-8-sig") if isinstance(data, bytes) else data
    first_nl = txt.find("\n")
    eol = "\r\n" if first_nl > 0 and txt[first_nl - 1] == "\r" else "\n"   # fin de línea de la cabecera
    trailing = txt.endswith("\n") or txt.endswith("\r")
    lines = txt.splitlines()                               # tolera finales mixtos sin perder filas
    comments, i = [], 0
    while i < len(lines) and lines[i].startswith("#"):
        comments.append(lines[i])
        i += 1
    if i >= len(lines):
        raise SeriesError("sin cabecera")
    header = lines[i]
    cols = [c.strip() for c in header.split(",")]
    if expect_header is not None and cols != list(expect_header):
        raise SeriesError("cabecera inesperada: %s" % header[:80])
    if value_col is None:
        value_col = next((c for c in VALUE_COLS if c in cols), None)
    if value_col not in cols:
        raise SeriesError("columna de valor %r ausente" % value_col)
    vi = cols.index(value_col)
    rows = {}
    for ln in lines[i + 1:]:
        if not ln.strip():
            continue
        cells = ln.split(",")
        d = _norm_date(cells[0])
        if d in rows:
            raise SeriesError("fecha duplicada %s" % d)
        try:
            v = float(cells[vi])
        except (IndexError, ValueError):
            raise SeriesError("valor ilegible en %s" % d)
        if not math.isfinite(v):
            raise SeriesError("valor no finito en %s" % d)
        rows[d] = (ln, v)
    if not rows:
        raise SeriesError("sin filas de datos")
    return Series(comments, header, rows, eol, value_col, trailing)


def candidate_id(fname, d, v):
    return hashlib.sha1(("%s|%s|%r" % (fname, d, v)).encode()).hexdigest()[:12]


class MergeResult(object):
    def __init__(self):
        self.status = NOOP
        self.detail = ""
        self.series = None            # Series fusionada (a publicar) o None
        self.new = []                 # fechas nuevas aceptadas
        self.revised = []             # fechas revisadas aceptadas
        self.backfill = []            # fechas antiguas añadidas aceptadas
        self.dropped_by_source = 0    # fechas guardadas que la fuente ya no trae (se conservan)
        self.held = []                # [(fecha, valor, motivo, id)]
        self.candidates = []          # registros nuevos/actualizados de cuarentena
        self.src_max = None
        self.repo_max = None
        self.suspicious_new_from = None

    def report(self):
        return {"status": self.status, "detail": self.detail, "src_max": self.src_max, "repo_max": self.repo_max,
                "new": len(self.new), "revised": self.revised[-20:], "backfill": len(self.backfill),
                "dropped_by_source": self.dropped_by_source,
                "held": [{"date": h[0], "value": h[1], "reason": h[2], "id": h[3]} for h in self.held]}


def _prev_value(keys, rows, d):
    """Valor de la observación inmediatamente anterior a d (keys = fechas ordenadas de rows)."""
    i = bisect.bisect_left(keys, d)
    return rows[keys[i - 1]][1] if i > 0 else None


def merge(fname, repo, src, plaus=None, revision_window=None, quarantine=None, run_id="", now_utc=None,
          hold_new_from=None, confirm_gap_min=CONFIRM_GAP_MIN, retain_from=None, download_id=None,
          max_future_days=None):
    """Fusión monótona de src (descarga) sobre repo (lo publicado).

    plaus: dict(min=, max=, max_jump=) — valores del registro (None = sin control).
    revision_window: nº de observaciones finales de repo en las que la fuente puede revisar sin confirmación.
                     None = sin datos sobre la política de revisión → TODA revisión pasa por confirmación.
    quarantine: dict id → registro (se lee y se actualiza en el MergeResult.candidates).
    hold_new_from: fecha (YYYYMMDD) desde la que las fechas NUEVAS se retienen por coherencia de familia.
    retain_from: fecha (YYYYMMDD) de inicio de la ventana de historia que el escritor original conserva
                 (p. ej. hoy − 5 años). Las filas anteriores se descartan como hacía el script original; esto
                 NO depende de lo que devuelva la fuente, así que una respuesta corta no acorta la historia.
    download_id: identidad de la DESCARGA CORRECTA que produjo src (hallazgo #1). Solo una descarga correcta
                 identificable y DISTINTA de la que originó el candidato puede confirmarlo. None = src no procede
                 de una descarga correcta verificada (fetch fallido, relectura del mismo fichero): puede
                 publicar lo que no necesita confirmación, pero nunca confirma nada.
    max_future_days: horizonte temporal de ESTA serie (ver check_horizon). None = sin límite documentado.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    quarantine = quarantine or {}
    r = MergeResult()
    r.src_max = src.max_date
    r.repo_max = repo.max_date if repo else None
    why = check_horizon(src, now_utc.strftime("%Y%m%d"), max_future_days)
    if why:
        r.status, r.detail = INVALID, "incoherencia temporal: " + why
        return r
    if repo is None:
        base_rows = {}
    else:
        base_rows = dict(repo.rows)
        if src.max_date < repo.max_date:
            r.status, r.detail = REGRESSION_BLOCKED, "la descarga termina en %s y lo publicado en %s" % (src.max_date, repo.max_date)
            return r
        lo, hi = src.dates[0], src.max_date
        in_range = [d for d in repo.rows if lo <= d <= hi]
        have = sum(1 for d in in_range if d in src.rows)
        if in_range and have < COVERAGE_MIN * len(in_range):
            r.status = INVALID
            r.detail = "respuesta incompleta: trae %d de %d fechas ya publicadas en su rango %s–%s" % (have, len(in_range), lo, hi)
            return r
        r.dropped_by_source = sum(1 for d in repo.rows if d not in src.rows)
    window = set()
    if repo is not None and revision_window:
        window = set(repo.dates[-int(revision_window):])
    merged = dict(base_rows)
    keys = sorted(merged)
    for d in sorted(src.rows):
        line, v = src.rows[d]
        if d in base_rows:
            if base_rows[d][1] == v:
                continue                                        # mismo valor (formato puede diferir): se conserva
            kind = "revisión" if d in window else "corrección histórica (fuera de la ventana revisable)"
        elif repo is not None and d < repo.max_date:
            kind = "fecha antigua añadida"
        else:
            kind = "nueva"
        reason = None
        if plaus:
            if plaus.get("min") is not None and v < plaus["min"] or plaus.get("max") is not None and v > plaus["max"]:
                reason = "fuera de rango [%s, %s]" % (plaus.get("min"), plaus.get("max"))
            elif plaus.get("max_jump") is not None and repo is not None:
                pv = _prev_value(keys, merged, d)
                if pv is not None and abs(v - pv) > plaus["max_jump"]:
                    reason = "salto %.4f > %.4f frente a la observación anterior" % (abs(v - pv), plaus["max_jump"])
        if kind in ("corrección histórica (fuera de la ventana revisable)", "fecha antigua añadida") and reason is None:
            reason = kind
        if kind == "revisión" and reason is None and not window:
            reason = "revisión sin ventana revisable documentada"
        if reason:
            cid = candidate_id(fname, d, v)
            rec = quarantine.get(cid)
            if rec is not None and (rec.get("status") == Q_SUPERSEDED or
                                    (rec.get("status") == Q_PENDING and not rec.get("first_download") and download_id)):
                # El valor reaparece tras haber sido sustituido por la fuente, o el candidato nació de una lectura
                # sin descarga verificada: se reabre con ESTA descarga como origen y el reloj de confirmación
                # se reinicia (ni se acepta por una confirmación antigua ni se retiene indefinidamente).
                rec = None
            decision = _decide(rec, run_id, now_utc, confirm_gap_min, download_id)
            if decision == "accept":
                rec = dict(rec)
                if rec["status"] == Q_PENDING:
                    rec["status"] = Q_ACCEPTED_CONFIRMED
                    rec["confirmed_by_run"] = run_id
                    rec["confirmed_by_download"] = download_id
                    rec["confirmed_utc"] = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
                r.candidates.append(rec)
            else:
                if rec is None:
                    rec = {"id": cid, "file": fname, "date": d, "value": v, "kind": kind, "reason": reason,
                           "previous_value": base_rows[d][1] if d in base_rows else None,
                           "status": Q_PENDING, "first_run": run_id, "first_download": download_id,
                           "first_seen_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")}
                    r.candidates.append(rec)
                r.held.append((d, v, reason + ("" if rec["status"] == Q_PENDING else " · " + rec["status"]), cid))
                if kind == "nueva" and (r.suspicious_new_from is None or d < r.suspicious_new_from):
                    r.suspicious_new_from = d
                continue
        if kind == "nueva" and hold_new_from and d >= hold_new_from:
            r.held.append((d, v, "retenida por coherencia de familia (candidato desde %s)" % hold_new_from, None))
            continue
        if d not in merged:
            bisect.insort(keys, d)
        merged[d] = (line, v)
        if kind == "nueva":
            r.new.append(d)
        elif kind == "fecha antigua añadida":
            r.backfill.append(d)
        else:
            r.revised.append(d)
    # superseded: candidatos pendientes (o aceptados por confirmación) de este fichero cuyo valor la fuente ya
    # no publica. Un aceptado por confirmación que la fuente sustituye pierde la aceptación: si el valor vuelve,
    # necesita una confirmación NUEVA e independiente. Solo cuenta si src procede de una descarga verificada.
    for cid, rec in quarantine.items():
        if rec.get("file") == fname and rec["date"] in src.rows and src.rows[rec["date"]][1] != rec["value"] and \
                (rec.get("status") == Q_PENDING or (rec.get("status") == Q_ACCEPTED_CONFIRMED and download_id)):
            rec = dict(rec)
            rec["status"] = Q_SUPERSEDED
            rec["superseded_by_run"] = run_id
            r.candidates.append(rec)
    if retain_from:
        for d in [x for x in merged if x < retain_from and x not in src.rows]:
            del merged[d]
    comments = src.comments if src.comments else (repo.comments if repo else [])
    out = Series(comments, src.header, merged, src.eol, src.value_col, src.trailing_eol)
    changed = repo is None or out.to_bytes() != repo.to_bytes()
    r.series = out if changed else None
    if r.held:
        r.status = HELD
        r.detail = "%d valor(es) en confirmación" % len(r.held)
    elif changed:
        r.status = PUBLISH
    else:
        r.status = NOOP
    return r


def _decide(rec, run_id, now_utc, gap_min, download_id=None):
    """accept | hold, según el registro de cuarentena (decisión manual > confirmación independiente).
    Confirmación independiente = descarga correcta identificable, distinta de la de origen, de otra ejecución
    y separada al menos gap_min minutos. Una ejecución con fetch fallido (download_id None) no confirma."""
    if rec is None:
        return "hold"
    st = rec.get("status")
    if st in (Q_ACCEPTED_MANUAL, Q_ACCEPTED_CONFIRMED):
        return "accept"
    if st == Q_REJECTED_MANUAL:
        return "hold"
    if st == Q_PENDING and rec.get("first_run") != run_id and download_id \
            and rec.get("first_download") and rec.get("first_download") != download_id:
        try:
            t0 = datetime.strptime(rec["first_seen_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except Exception:                                   # noqa: BLE001
            return "hold"
        if (now_utc - t0).total_seconds() >= gap_min * 60:
            return "accept"
    return "hold"


def apply_manual_decisions(quarantine, decisions):
    """decisions: lista de {"id", "decision": "accept"|"reject", "by", "reason", "utc"} (ficheros committeados).
    Devuelve el diccionario de cuarentena actualizado (sin mutar el original)."""
    q = {k: dict(v) for k, v in quarantine.items()}
    for dec in decisions:
        rec = q.get(dec.get("id"))
        if rec is None or rec.get("status") not in (Q_PENDING, Q_REJECTED_MANUAL, Q_ACCEPTED_MANUAL):
            continue
        rec["status"] = Q_ACCEPTED_MANUAL if dec.get("decision") == "accept" else Q_REJECTED_MANUAL
        rec["decision"] = {k: dec.get(k) for k in ("by", "reason", "utc")}
    return q


def write_atomic(path, data):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".%s.%d.tmp" % (os.path.basename(path), os.getpid()))
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)

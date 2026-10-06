"""
fetch_chf_cpi.py — Acta P-10 (2026-10-06): inflación interanual oficial de Suiza (IPC/LIK, BFS), mensual.

Uso en el dashboard: Suiza no emite bonos ligados a la inflación, así que no existe breakeven de mercado ni real
observado. Con OK del propietario (6-oct-2026) se muestra el **real ex post** = nominal 10Y (SNB) − inflación interanual
del IPC, etiquetado como tal; la columna BE de CHF enseña «IPC a/a» y NUNCA se presenta como breakeven.

Fuente primaria (verificada 6-oct-2026):
  Oficina Federal de Estadística (BFS/OFS), «LIK/IPC — Indexierungstabelle / Tableau d'indexation», nº de pedido
  cc-e-05.02.08, hoja «Index_m», columna «% m-12» (variación respecto al mismo mes del año anterior, %, 1 decimal).
  El identificador del fichero cambia cada mes; se resuelve el último por nº de pedido en la API de activos:
    https://dam-api.bfs.admin.ch/hub/api/dam/assets?orderNr=cc-e-05.02.08   → asset más reciente (embargo)
    https://dam-api.bfs.admin.ch/hub/api/dam/assets/<id>/master              → xlsx
  Publicación: mensual, a primeros del mes siguiente (septiembre 2026 publicado el 1-oct-2026, 08:30 CEST).
  Contraste: el SNB (data.snb.ch, cubo plkopr, VVP) publica la misma serie tres semanas más tarde (ago-2026 0,807 %).

Salida: data/CHF_CPI_YOY.csv (OHLCV, DATE = último día del mes de referencia, CLOSE = % interanual).
"""
import calendar
import io
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from g8common import ingest as _ingest  # noqa: E402

requests = None   # lo asigna main(): sustituto de requests.get basado en g8http

DAM = "https://dam-api.bfs.admin.ch/hub/api/dam/assets"
ORDER_NR = "cc-e-05.02.08"
SHEET = "Index_m"
YOY_HEADER = "% m-12"
OUTPUT = "CHF_CPI_YOY.csv"
RETAIN_FROM = "20000101"
USER_AGENT = "g8-macro-pipeline/1.0 (https://github.com/sanderdayan1982/g8-macro-pipeline)"


def latest_asset(listing):
    """JSON de la API → (damId, embargo) del asset más reciente con el nº de pedido exacto."""
    best = None
    for a in (listing or {}).get("data") or []:
        if ((a.get("shop") or {}).get("orderNr") or "") != ORDER_NR:
            continue
        dam = (a.get("ids") or {}).get("damId")
        emb = (a.get("bfs") or {}).get("embargo") or ""
        if dam and (best is None or emb > best[1]):
            best = (dam, emb)
    if best is None:
        raise ValueError("BFS DAM: ningún asset con orderNr %s" % ORDER_NR)
    return best


def parse_yoy(xlsx_bytes):
    """xlsx de la BFS → [(yyyymmdd, %)] desde la columna «% m-12» de la hoja Index_m. Formato cambiado → ValueError."""
    import pandas as pd
    try:
        df = pd.read_excel(io.BytesIO(xlsx_bytes), sheet_name=SHEET, header=None)
    except ValueError as e:
        raise ValueError("BFS: hoja %s ausente (%s)" % (SHEET, e))
    hdr_row = None
    for i in range(min(15, len(df))):
        if str(df.iat[i, 0]).strip().startswith("Datum"):
            hdr_row = i
            break
    if hdr_row is None:
        raise ValueError("BFS: fila de cabecera «Datum / Date» no encontrada")
    cols = [str(x).strip() for x in df.iloc[hdr_row]]
    if YOY_HEADER not in cols:
        raise ValueError("BFS: columna «%s» no encontrada (cabecera: %s)" % (YOY_HEADER, cols[-4:]))
    c = cols.index(YOY_HEADER)
    out = []
    for i in range(hdr_row + 1, len(df)):
        d, v = df.iat[i, 0], df.iat[i, c]
        if not hasattr(d, "year"):
            continue
        try:
            x = float(v)
        except (TypeError, ValueError):
            continue
        if x == x:
            out.append(("%04d%02d%02d" % (d.year, d.month, calendar.monthrange(d.year, d.month)[1]), x))
    if len(out) < 120:
        raise ValueError("BFS: solo %d meses con variación interanual" % len(out))
    return out


def render_csv(rows):
    lines = ["DATE,OPEN,HIGH,LOW,CLOSE,VOLUME"]
    for d, v in rows:
        s = "%.2f" % v
        lines.append(",".join([d, s, s, s, s, "0"]))
    return ("\n".join(lines) + "\n").encode("utf-8")


def main():
    global requests
    ctx = _ingest.Ingest("fetch_chf_cpi")
    requests = ctx.requests(provider="bfs")
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        r = requests.get(DAM, params={"orderNr": ORDER_NR}, headers=headers, timeout=60)
        r.raise_for_status()
        dam, emb = latest_asset(json.loads(r.text))
        x = requests.get("%s/%s/master" % (DAM, dam), headers={"User-Agent": USER_AGENT}, timeout=90)
        x.raise_for_status()
        rows = parse_yoy(x.content if hasattr(x, "content") else x.text.encode("latin-1"))
    except Exception as e:                                             # noqa: BLE001
        print("ERROR: IPC suizo (BFS): %s" % e, file=sys.stderr)
        return ctx.finish(1)
    rows = [x for x in rows if x[0] >= RETAIN_FROM]
    print("BFS IPC (asset %s, publicado %s): %d meses · último %s = %.2f %%" % (dam, emb[:10], len(rows), rows[-1][0][:6], rows[-1][1]))
    rep = ctx.publish(OUTPUT, render_csv(rows), retain_from=RETAIN_FROM)
    print("Publicación %s: %s %s" % (OUTPUT, rep.get("status"), rep.get("detail", "")))
    return ctx.finish(0)


if __name__ == "__main__":
    sys.exit(main())

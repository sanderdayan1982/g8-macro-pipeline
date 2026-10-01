#!/bin/zsh
# nzd_local_run.sh — v1.4 (lote 1 fiabilidad, 2026-09-24): fetch RBNZ B2 (NZD) + SNB (CHF) + BoJ (TONA)
# en el Mac (IP residencial) y publicación por familias con push_nzd_to_github.py v2.1.
# v1.3: el publicador recibe los códigos de salida de cada descarga (van al latido y a los avisos) y
#       cualquier fallo del propio publicador (p. ej. Python roto) queda en logs/ALERTAS.log; si ni siquiera
#       se publica el latido, Actions (ingest_watch.py) avisa por ausencia.
# v1.4: cerrojo state/run.lock (compartido con el instalador) y pasa --fetch-started (epoch previo a las descargas) para que el publicador distinga una descarga
#       correcta nueva de una relectura de ficheros antiguos (solo la primera puede confirmar candidatos).
# v1.5 (acta P-5): una ejecución diaria a las 19:30 hora del Mac (com.g8.nzd-b2.plist v1.4) + RunAtLoad. Guarda «ya hecho»:
#       si la ejecución del turno vigente (las 19:30 más recientes) ya terminó bien, no se repite (p. ej. RunAtLoad tras
#       reiniciar). Solo se marca hecho con TODO a 0 (descargas y publicador); si algo falló, la siguiente ocasión reintenta.
#       El plist v1.5 añade un reintento a las 21:00: con el turno ya hecho sale sin hacer nada.
# Lo lanza launchd; también se puede correr a mano (G8_FORCE=1 ignora la guarda).
cd "$(dirname "$0")" || exit 1
mkdir -p data logs state
# v1.4: cerrojo compartido con el instalador (instalar_lote1.py): nunca se ejecuta con el código a medio cambiar.
# Un cerrojo de más de 3 h se considera abandonado (ejecución interrumpida) y se retira.
if ! mkdir state/run.lock 2>/dev/null; then
  if [ -n "$(find state/run.lock -maxdepth 0 -mmin +180 2>/dev/null)" ]; then
    rmdir state/run.lock 2>/dev/null; mkdir state/run.lock 2>/dev/null || exit 0
  else
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') 🟠 ejecución omitida: otra ejecución o una instalación en curso (state/run.lock)" >> logs/ALERTAS.log
    exit 0
  fi
fi
trap 'rmdir state/run.lock 2>/dev/null' EXIT
# v1.5: turno vigente = las 19:30 (hora local) más recientes, en epoch. G8_NOW (epoch) solo para pruebas.
slot=$(/usr/bin/python3 -c 'import os,time,datetime as d
n=float(os.environ.get("G8_NOW") or time.time()); t=d.datetime.fromtimestamp(n)
s=t.replace(hour=19,minute=30,second=0,microsecond=0)
print(int((s if t>=s else s-d.timedelta(days=1)).timestamp()))' 2>/dev/null)
if [ -z "$G8_FORCE" ] && [ -n "$slot" ] && [ "$(cat state/last_ok_slot 2>/dev/null)" = "$slot" ]; then
  echo "$(date -u '+%Y-%m-%dT%H:%MZ') ya hecho: la ejecución del turno $slot terminó bien; no se repite" >> logs/nzd_$(date '+%Y-%m').log
  exit 0
fi
{
  echo "=== $(date -u '+%Y-%m-%dT%H:%MZ') ==="
  t_fetch=$(date +%s)
  /usr/bin/python3 fetch_nzd_b2.py;  rc_nzd=$?
  /usr/bin/python3 fetch_chf_snb.py; rc_chf=$?
  /usr/bin/python3 fetch_tona_mac.py; rc_tona=$?
  /usr/bin/python3 push_nzd_to_github.py --fetch-status "nzd=$rc_nzd,chf=$rc_chf,tona=$rc_tona" --fetch-started "$t_fetch"; rc_push=$?
  echo "exit nzd=$rc_nzd chf=$rc_chf tona=$rc_tona push=$rc_push"
  if [ -n "$slot" ] && [ $rc_nzd -eq 0 ] && [ $rc_chf -eq 0 ] && [ $rc_tona -eq 0 ] && [ $rc_push -eq 0 ]; then
    echo "$slot" > state/last_ok_slot                     # v1.5: turno hecho
  fi
  if [ $rc_push -gt 1 ]; then
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') 🔴 el publicador terminó con código $rc_push (error inesperado): revisar este log" >> logs/ALERTAS.log
  fi
} >> logs/nzd_$(date '+%Y-%m').log 2>&1

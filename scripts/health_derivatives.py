"""Read-only freshness checks for sparse B2, licensed options and weekly metals."""
import hashlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from g8common import schedule as SC
from g8common.freshness import _parse_date, obs_dates_from_bytes
from metals_provenance import Evidence, OUTPUTS


def timestamp(s):
    d=datetime.fromisoformat(s.replace('Z','+00:00'))
    if d.tzinfo is None: raise ValueError('Missing timezone')
    return d


def b2_evidence_path(root):
    """Prefers the VPS publication record; Mac evidence is only a fallback for tests."""
    latest = root/'data/_ingest/latest'
    vps = latest/'vps-prod__nzchf-tona.json'
    if vps.is_file():
        return vps
    return latest/'mac-primary__nzchf-tona.json'


def official_carries(kept_from_previous, official, all_dates):
    """How many conserved dates count as a carry (t352u / 3b).

    Days marked PROXY when the source does not print (e.g. RBNZ cash gaps) are
    not carries. A conserved official value (no PROXY mark) is a carry: it is
    an incidence and must not vote. A documented T+1 lag (e.g. CME OI) is not
    a carry and is not counted here — it is calendar delay, not a conserved row.
    Does not rewrite kept_from_previous; it only interprets the count.
    """
    kept = int(kept_from_previous or 0)
    official = set(official or [])
    all_dates = set(all_dates or [])
    n_proxy = len(all_dates - official)
    return max(0, kept - n_proxy)


def cash(root, now, item, read_json):
    if item['status'] in ('ERROR','LATE'): return
    try:
        report=read_json(b2_evidence_path(root))
        fetch=report['fetch_detail']['nzd']
        publication=report['families']['NZ-B2']
        file=fetch['files']['NZD_CASH_ON.csv']
        accepted=publication['files']['NZD_CASH_ON.csv']
        local=now.astimezone(ZoneInfo('Pacific/Auckland'))
        due=local.replace(hour=15,minute=0,second=0,microsecond=0)
        cals=SC.load_calendars(str(root))
        while due>local or not SC.is_bd(cals,'NZ',due.date()): due-=timedelta(days=1)
        expected_book=due.date()-timedelta(days=1)
        while not SC.is_bd(cals,'NZ',expected_book): expected_book-=timedelta(days=1)
        book_date=_parse_date(fetch['files']['NZD_BOND_10Y.csv']['max_date'])
        finished=timestamp(fetch['finished_utc'])
        raw=(root/'data/NZD_CASH_ON.csv').read_bytes()
        dates=obs_dates_from_bytes(raw)
        official=dates
        try:
            import feed_proxy as FP
            official=FP.official_dates_from_bytes(raw) or dates
            all_dates=FP.all_dates_from_bytes(raw) or dates
        except Exception:
            all_dates=dates
        latest=max(official) if official else None
        carried=official_carries(file.get('kept_from_previous',0), official, all_dates)
        item['verified_download_utc']=fetch['finished_utc']
        # t352u, orden expresa Sander 23-nov: un valor arrastrado SIN marca PROXY
        # cuenta como incidencia y no vota. Los días PROXY en que la fuente no
        # imprime no cuentan como arrastrados. El desfase T+1 documentado no es un arrastre.
        if carried>0:
            item.update(status='LATE',state='CARRIED_OFFICIAL',
                        have_max=((latest or (max(all_dates) if all_dates else None)).isoformat() if (latest or all_dates) else None),
                        reason='Valor arrastrado sin marca PROXY (%d): cuenta como incidencia y no vota. kept_from_previous no se reescribe.' % carried)
            return
        valid=(fetch['rc']==0 and not fetch.get('errors') and file['status']=='WRITTEN'
               and publication['download_kind']=='FRESH'
               and publication['status'] in ('PUBLISH','PUBLISHED','NOOP') and not publication.get('held')
               and accepted['status'] in ('PUBLISH','NOOP') and not accepted.get('held')
               and latest==_parse_date(file['max_date'])==_parse_date(accepted['src_max'])
               and latest<=now.date() and due<=finished<=now
               and book_date is not None and expected_book<=book_date<=now.date())
        if valid:
            has_proxy=bool(all_dates) and max(all_dates)!=latest
            have=max(all_dates) if has_proxy else latest
            if has_proxy:
                item.update(status='CURRENT',state='PROXY',have_max=have.isoformat(),
                            reason='Última observación oficial B2 en %s, contrastada con descarga y publicación. Huecos posteriores cubiertos con PROXY (NZD_OCR); un dato PROXY no vota y no se hace pasar por overnight oficial.' % latest.isoformat())
            else:
                item.update(status='CURRENT',state='VERIFIED_SPARSE',have_max=latest.isoformat(),
                            reason='Última observación disponible en B2 diario, contrastada con descarga y publicación. Los huecos de cash del proveedor se conservan; no se inventan valores diarios.')
        else:
            item.update(status='UNKNOWN',reason='Falta comprobar la última publicación B2 o su recepción íntegra. Una observación cash dispersa no se rellena.')
    except (KeyError,ValueError,TypeError,OSError):
        item.update(status='UNKNOWN',reason='Evidencia de descarga B2 incompleta; no se certifica frescura.')


def weekday(d, step):
    d+=timedelta(days=step)
    while d.weekday()>=5: d+=timedelta(days=step)
    return d


def options(root, now, item, read_json):
    if item['status']=='ERROR': return
    import build_options_summary as B
    doc=read_json(root/'data/OPTIONS_SURFACE.json')
    state=read_json(root/'data/options/state.json')
    try:
        latest=_parse_date(doc['latest_session'])
        if not latest or latest>=now.date(): raise ValueError('Future or unfinished session')
        folder=root/'data/options/canonical'/latest.isoformat()
        expected_files=[folder/(code+'.csv') for pair in B.CCY.values() for code in pair]
        if not all(p.exists() for p in expected_files): raise ValueError('Incomplete canonical session')
        for p in expected_files:
            rows=B.read_rows(p)
            if not rows or any(r.get('session')!=latest.isoformat() for r in rows): raise ValueError('Wrong canonical session')
        if B.session_metrics(folder,latest.isoformat())!=doc.get('latest'):
            raise ValueError('Summary differs from canonical data')
        if state.get('last_success')!=latest.isoformat():
            item.update(status='UNKNOWN',reason='Estado del colector y resumen no coinciden; verificar sesión sin negociación o publicación incompleta.')
            return
        target=weekday(now.date(),-1)
        release=weekday(target,1)
        deadline=datetime.combine(release,datetime.min.time(),timezone.utc)+timedelta(hours=21)
        due=target if now>=deadline else weekday(target,-1)
        item.update(have_max=latest.isoformat(),expected_obs=target.isoformat(),publication_frequency='daily',
                    expected_publication_utc=deadline.isoformat())
        if latest<due:
            item.update(status='LATE',reason='Falta una sesión tras la ventana del colector (último intento 17 UTC + margen operativo 4 h). Revisar disponibilidad del proveedor.')
        elif latest<target:
            item.update(status='PENDING',state='AWAITING_PUBLICATION',reason='Resumen y 12 archivos canónicos coherentes. Próxima sesión pendiente de ventana de publicación: '+deadline.isoformat()+'. El OI del viernes espera al siguiente día laborable; no se compra ni descarga dato adicional.')
        else:
            item.update(status='CURRENT',state='VERIFIED_CANONICAL',reason='Sesión diaria coherente entre estado, 12 archivos canónicos y resumen; calendario operativo del colector, sin certificar calidad de mercado por la sola frescura.')
    except (KeyError,ValueError,TypeError,OSError) as exc:
        item.update(status='ERROR',reason='Inconsistencia en opciones: '+str(exc))


def metals(root, now, item, read_json):
    if item['status']=='ERROR': return
    proof=read_json(root/'data/_ingest/metals_provenance.json')
    ledger=read_json(root/'data/_ingest/latest/actions__job_metals.json')
    try:
        attempted=timestamp(ledger['written_utc'])
        generated=timestamp(proof['generated_utc']) if proof else None
        if (ledger.get('failed') or ledger.get('time_limited') or ledger.get('unrestorable')) and (not generated or attempted>=generated):
            item.update(status='ERROR',reason='Última ejecución de metales falló o agotó el plazo; se conserva la publicación completa anterior.')
            return
    except (KeyError,ValueError,TypeError):
        pass
    if not proof:
        item.update(status='UNKNOWN',reason='Falta manifiesto de entradas originales y huellas de la publicación semanal de metales.')
        return
    try:
        if proof['status']!='VERIFIED': raise ValueError(proof.get('error') or 'Failed publication')
        generated=timestamp(proof['generated_utc'])
        anchor=_parse_date(proof['anchor'])
        # Weekly Saturday job: next Tuesday output due at the end of Saturday UTC.
        sunday=now.date()-timedelta(days=(now.weekday()+1)%7)
        expected=sunday-timedelta(days=5)
        if not anchor or anchor>now.date() or generated>now: raise ValueError('Future evidence')
        if anchor<expected:
            item.update(status='LATE',expected_obs=expected.isoformat(),reason='Falta salida semanal tras terminar la ventana del sábado.')
            return
        evidence=Evidence();evidence.anchor=anchor;evidence.inputs=proof['inputs']
        for metal in ('XAU','XAG'): evidence.validate(metal)
        for name in OUTPUTS:
            if hashlib.sha256((root/'data'/name).read_bytes()).hexdigest()!=proof['outputs'][name]:
                raise ValueError('Output changed without matching evidence: '+name)
        state=read_json(root/'data/MFV_G8_state.json')
        for metal in ('XAU','XAG'):
            dates=obs_dates_from_bytes((root/'data'/('MFV_G8_'+metal+'.csv')).read_bytes())
            if not dates or max(dates)!=anchor or _parse_date(state['metals'][metal]['cot_as_of'])!=anchor:
                raise ValueError('CSV/state weekly anchor mismatch')
        slow=any(v['publication_frequency']=='monthly' for group in proof['inputs'].values() for v in group.values())
        item.update(status='CURRENT',state='VERIFIED_INPUTS',have_max=anchor.isoformat(),expected_obs=expected.isoformat(),
                    publication_frequency='weekly',slow_fallback=slow,input_evidence=proof['inputs'],
                    reason='Salida semanal ligada por huellas a sus entradas originales verificadas; fechas previas al arrastre. Frescura operativa, no validación de señal o rentabilidad.'+(' Driver mensual activo: último recurso.' if slow else ''))
    except (KeyError,ValueError,TypeError,OSError) as exc:
        item.update(status='ERROR',reason='Evidencia de metales inválida: '+str(exc))

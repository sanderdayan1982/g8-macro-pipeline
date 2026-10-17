#!/usr/bin/env python3
"""Alternate calendar days in Malabo, anchored 2026-10-04; continuous across months."""
import argparse
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ANCHOR = date(2026, 10, 4)


def active(day):
    return (day - ANCHOR).days % 2 == 0


def nominal_day(event_at, cron):
    """Latest nominal daily cron at event creation, before runner queue delay.

    A 17:17Z run created at 23:38Z belongs to that UTC day's 18:17 Malabo
    slot, even if its runner starts after local midnight. Only our daily crons
    are accepted; missing/invalid event metadata fails visibly, never skips.
    """
    event = datetime.fromisoformat(event_at.replace('Z', '+00:00'))
    if event.tzinfo is None:
        raise ValueError('event-at must include a timezone')
    minute, hour, dom, month, dow = cron.split()
    if (dom, month, dow) != ('*', '*', '*'):
        raise ValueError('only daily maintenance/watch crons are supported')
    event = event.astimezone(timezone.utc)
    slot = event.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
    if slot > event:
        slot -= timedelta(days=1)
    return slot.astimezone(ZoneInfo('Africa/Malabo')).date()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--event', default='schedule')
    ap.add_argument('--event-at', help='workflow run createdAt (stable across reruns/queueing)')
    ap.add_argument('--cron', help='github.event.schedule, nominal UTC daily cron')
    ap.add_argument('--nominal-date', type=date.fromisoformat, help='explicit nominal date in Malabo')
    args = ap.parse_args()
    if args.nominal_date:
        day = args.nominal_date
    elif args.event_at or args.cron:
        day = nominal_day(args.event_at or '', args.cron or '')
    else:
        day = datetime.now(ZoneInfo('Africa/Malabo')).date()
    run = args.event == 'workflow_dispatch' or active(day)
    print('run=' + str(run).lower())
    print('nominal_date=' + day.isoformat())


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Alternate weekdays (Mon/Wed/Fri) in Malabo; weekends off, always resuming Monday."""
import argparse
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ANCHOR = date(2026, 10, 5)  # Monday anchor
ACTIVE_WEEKDAYS = (0, 2, 4)  # 0=Monday, 2=Wednesday, 4=Friday (weekends 5,6 off)


def active(day):
    return day.weekday() in ACTIVE_WEEKDAYS


def nominal_day(event_at, cron):
    """Latest nominal cron at event creation, before runner queue delay.

    A 17:17Z run created at 23:38Z belongs to that UTC day's 18:17 Malabo
    slot, even if its runner starts after local midnight. Accepts daily ('*')
    or Mon/Wed/Fri ('1,3,5') crons; missing/invalid metadata fails visibly.
    """
    event = datetime.fromisoformat(event_at.replace('Z', '+00:00'))
    if event.tzinfo is None:
        raise ValueError('event-at must include a timezone')
    minute, hour, dom, month, dow = cron.split()
    if (dom, month) != ('*', '*') or dow not in ('*', '1,3,5'):
        raise ValueError('only daily or Mon/Wed/Fri (1,3,5) maintenance/watch crons are supported')
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

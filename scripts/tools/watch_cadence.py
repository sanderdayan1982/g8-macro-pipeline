#!/usr/bin/env python3
"""Alternate calendar days in Malabo, anchored 2026-10-04; continuous across months."""
import argparse
from datetime import date, datetime
from zoneinfo import ZoneInfo

ANCHOR = date(2026, 10, 4)


def active(day):
    return (day - ANCHOR).days % 2 == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--event', default='schedule')
    args = ap.parse_args()
    day = datetime.now(ZoneInfo('Africa/Malabo')).date()
    run = args.event == 'workflow_dispatch' or active(day)
    print('run=' + str(run).lower())


if __name__ == '__main__':
    main()

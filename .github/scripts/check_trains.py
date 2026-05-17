import os
import requests
from datetime import datetime, timezone, timedelta

# ── CONFIG ────────────────────────────────────────────────────────────────────
NS_API_KEY   = os.environ['NS_API_KEY']
NTFY_TOPIC   = os.environ['NTFY_TOPIC']
FROM_STATION = 'Haarlem'
TO_STATION   = 'Utrecht Centraal'
VIA_STATION  = 'Amsterdam Centraal'
DEPART_FROM  = 7
DEPART_TO    = 9
DELAY_THRESH = 5

def get_nl_timezone():
    now_utc = datetime.now(timezone.utc)
    year = now_utc.year
    march31 = datetime(year, 3, 31, 1, 0, tzinfo=timezone.utc)
    dst_start = march31 - timedelta(days=march31.weekday() + 1)
    oct31 = datetime(year, 10, 31, 1, 0, tzinfo=timezone.utc)
    dst_end = oct31 - timedelta(days=oct31.weekday() + 1)
    return timezone(timedelta(hours=2)) if dst_start <= now_utc < dst_end else timezone(timedelta(hours=1))

def parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except Exception:
        return None

def minutes_late(planned, actual):
    p, a = parse_dt(planned), parse_dt(actual)
    if not p or not a:
        return 0
    return max(0, int((a - p).total_seconds() / 60))

def send_ntfy(title, message, priority='default'):
    try:
        resp = requests.post(
            f'https://ntfy.sh/{NTFY_TOPIC}',
            data=f"{title}\n\n{message}".encode('utf-8'),
            headers={
                'Content-Type': 'text/plain; charset=utf-8',
                'X-Priority': priority,
                'X-Title': title,
            },
            timeout=10
        )
        print(f"ntfy: {resp.status_code}")
    except Exception as e:
        print(f"ntfy error: {e}")

def check_leg(leg, NL_TZ):
    """Returns (dep_time_str, status) where status is 'ok', 'cancelled', or 'Xmin delay'"""
    origin      = leg.get('origin', {})
    dep_planned = origin.get('plannedDateTime')
    dep_actual  = origin.get('actualDateTime') or dep_planned
    if not dep_planned:
        return None, None

    dep_nl    = parse_dt(dep_planned).astimezone(NL_TZ)
    dep_time  = dep_nl.strftime('%H:%M')
    cancelled = leg.get('cancelled', False)
    delay     = minutes_late(dep_planned, dep_actual)

    if cancelled:
        return dep_time, 'cancelled'
    elif delay >= DELAY_THRESH:
        return dep_time, f'{delay}min delay'
    else:
        return dep_time, 'ok'

def main():
    NL_TZ  = get_nl_timezone()
    now_nl = datetime.now(NL_TZ)
    offset = int(NL_TZ.utcoffset(None).total_seconds() / 3600)
    print(f"Running at {now_nl.strftime('%H:%M')} NL time (UTC+{offset}), weekday={now_nl.weekday()}")

    if now_nl.weekday() >= 5:
        print("Weekend — skipping")
        return

    if not (6 <= now_nl.hour <= 8):
        print(f"Outside run window ({now_nl.hour}h) — skipping")
        return

    url = 'https://gateway.apiportal.ns.nl/reisinformatie-api/api/v3/trips'
    params = {
        'fromStation': FROM_STATION,
        'toStation':   TO_STATION,
        'dateTime':    now_nl.strftime('%Y-%m-%dT%H:%M:%S'),
        'numTrips':    10,
    }
    headers = {
        'Ocp-Apim-Subscription-Key': NS_API_KEY,
        'Accept': 'application/json',
    }

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        print(f"NS API status: {resp.status_code}")
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"API error: {e}")
        send_ntfy('Trenes - Error', f'No se pudo consultar NS: {e}', priority='3')
        return

    trips = data.get('trips', [])
    print(f"Got {len(trips)} trips")

    # Track problems per leg route
    har_ams_problems = []
    ams_utr_problems = []
    full_trip_ok     = []
    full_trip_bad    = []

    for trip in trips:
        legs = trip.get('legs', [])
        if not legs:
            continue

        # Check departure time of first leg
        first_origin = legs[0].get('origin', {})
        dep_planned  = first_origin.get('plannedDateTime')
        if not dep_planned:
            continue
        dep_nl = parse_dt(dep_planned).astimezone(NL_TZ)
        if not (DEPART_FROM <= dep_nl.hour < DEPART_TO):
            continue

        trip_dep = dep_nl.strftime('%H:%M')
        trip_has_problem = False

        for leg in legs:
            origin_name = leg.get('origin', {}).get('name', '')
            dest_name   = leg.get('destination', {}).get('name', '')
            dep_time, status = check_leg(leg, NL_TZ)
            if dep_time is None:
                continue

            is_har_ams = 'Haarlem' in origin_name and 'Amsterdam' in dest_name
            is_ams_utr = 'Amsterdam' in origin_name and 'Utrecht' in dest_name

            if status != 'ok':
                trip_has_problem = True
                entry = f"{dep_time} ({status})"
                if is_har_ams:
                    har_ams_problems.append(entry)
                elif is_ams_utr:
                    ams_utr_problems.append(entry)

        if trip_has_problem:
            full_trip_bad.append(trip_dep)
        else:
            full_trip_ok.append(trip_dep)

    print(f"HAR-AMS problems: {har_ams_problems}")
    print(f"AMS-UTR problems: {ams_utr_problems}")
    print(f"OK trips: {full_trip_ok} | Bad trips: {full_trip_bad}")

    has_problems = har_ams_problems or ams_utr_problems

    if has_problems:
        lines = []
        if har_ams_problems:
            lines.append("Haarlem -> Amsterdam:")
            for p in har_ams_problems:
                lines.append(f"  {p}")
        if ams_utr_problems:
            lines.append("Amsterdam -> Utrecht:")
            for p in ams_utr_problems:
                lines.append(f"  {p}")
        if full_trip_ok:
            lines.append(f"\nSin problemas: {', '.join(full_trip_ok)}")

        send_ntfy('Trenes - Problemas hoy', '\n'.join(lines), priority='4')
    else:
        print(f"All good: {', '.join(full_trip_ok)} — no alert sent")

if __name__ == '__main__':
    main()

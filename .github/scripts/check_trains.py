import os
import requests
from datetime import datetime, timezone, timedelta

# ── CONFIG ────────────────────────────────────────────────────────────────────
NS_API_KEY   = os.environ['NS_API_KEY']
NTFY_TOPIC   = os.environ['NTFY_TOPIC']
FROM_STATION = 'Haarlem'
TO_STATION   = 'Utrecht Centraal'
DEPART_FROM  = 13
DEPART_TO    = 15
DELAY_THRESH = 1

def get_nl_timezone():
    """Detect CET (UTC+1) vs CEST (UTC+2) automatically."""
    # DST in NL: last Sunday of March → last Sunday of October
    now_utc = datetime.now(timezone.utc)
    year = now_utc.year

    # Last Sunday of March
    march31 = datetime(year, 3, 31, 1, 0, tzinfo=timezone.utc)
    dst_start = march31 - timedelta(days=march31.weekday() + 1)

    # Last Sunday of October
    oct31 = datetime(year, 10, 31, 1, 0, tzinfo=timezone.utc)
    dst_end = oct31 - timedelta(days=oct31.weekday() + 1)

    if dst_start <= now_utc < dst_end:
        return timezone(timedelta(hours=2))  # CEST
    else:
        return timezone(timedelta(hours=1))  # CET

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

def main():
    NL_TZ  = get_nl_timezone()
    now_nl = datetime.now(NL_TZ)
    offset = int(NL_TZ.utcoffset(None).total_seconds() / 3600)
    print(f"Running at {now_nl.strftime('%H:%M')} NL time (UTC+{offset}), weekday={now_nl.weekday()}")

    # Only run on weekdays
 #   if now_nl.weekday() >= 5:
  #      print("Weekend — skipping")
   #     return

    # Only run around 7:00-7:30am NL time (guard against off-schedule runs)
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

    problems, all_ok = [], []

    for trip in trips:
        legs = trip.get('legs', [])
        if not legs:
            continue
        origin      = legs[0].get('origin', {})
        dep_planned = origin.get('plannedDateTime')
        dep_actual  = origin.get('actualDateTime') or dep_planned
        if not dep_planned:
            continue
        dep_nl = parse_dt(dep_planned).astimezone(NL_TZ)
        if not (DEPART_FROM <= dep_nl.hour < DEPART_TO):
            continue

        dep_time  = dep_nl.strftime('%H:%M')
        cancelled = trip.get('cancelled', False) or legs[0].get('cancelled', False)
        delay     = minutes_late(dep_planned, dep_actual)

        if cancelled:
            problems.append(f"CANCELADO: {dep_time}")
        elif delay >= DELAY_THRESH:
            problems.append(f"Retraso {delay}min: {dep_time}")
        else:
            all_ok.append(dep_time)

    print(f"Problems: {problems} | OK: {all_ok}")

    if problems:
        msg = "Haarlem -> Utrecht:\n" + '\n'.join(problems)
        if all_ok:
            msg += f"\n\nSin problemas: {', '.join(all_ok)}"
        send_ntfy('Trenes - Problemas hoy', msg, priority='4')
    else:
        print(f"All good: {', '.join(all_ok)} — no alert sent")

if __name__ == '__main__':
    main()

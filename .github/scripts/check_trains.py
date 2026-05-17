import os
import requests
from datetime import datetime, timezone, timedelta

# ── CONFIG ────────────────────────────────────────────────────────────────────
NS_API_KEY   = os.environ['NS_API_KEY']
NTFY_TOPIC   = os.environ['NTFY_TOPIC']
FROM_UIC     = '8400282'   # Haarlem
TO_UIC       = '8400621'   # Utrecht Centraal
DEPART_FROM  = 7           # Check trains from 7:00
DEPART_TO    = 9           # ...until 9:00
DELAY_THRESH = 5           # minutes delay to trigger alert
NL_TZ        = timezone(timedelta(hours=2))  # CEST (summer), change to +1 in winter

# ── HELPERS ──────────────────────────────────────────────────────────────────
def parse_dt(s):
    if not s:
        return None
    # Handle both +02:00 and Z formats
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except Exception:
        return None

def minutes_late(planned, actual):
    if not planned or not actual:
        return 0
    diff = (parse_dt(actual) - parse_dt(planned)).total_seconds() / 60
    return max(0, int(diff))

def send_ntfy(title, message, priority='default', tags=''):
    requests.post(
        f'https://ntfy.sh/{NTFY_TOPIC}',
        headers={
            'Title':    title,
            'Priority': priority,
            'Tags':     tags,
        },
        data=message.encode('utf-8'),
        timeout=10
    )

# ── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    now_nl = datetime.now(NL_TZ)
    print(f"Running at {now_nl.strftime('%H:%M')} NL time")

    # Only run on weekdays
    if now_nl.weekday() >= 5:
        print("Weekend — skipping")
        return

    # Fetch journeys from NS API
    url = 'https://gateway.apiportal.ns.nl/reisinformatie-api/api/v3/trips'
    params = {
        'fromStation': FROM_UIC,
        'toStation':   TO_UIC,
        'dateTime':    now_nl.strftime('%Y-%m-%dT%H:%M:%S'),
        'numTrips':    8,
    }
    headers = {'Ocp-Apim-Subscription-Key': NS_API_KEY}

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"API error: {e}")
        send_ntfy('⚠️ Trenes — Error', f'No se pudo consultar la API de NS: {e}', priority='low', tags='warning')
        return

    trips = data.get('trips', [])

    # Filter trips departing in our window
    problems = []
    all_ok    = []

    for trip in trips:
        legs = trip.get('legs', [])
        if not legs:
            continue
        first_leg = legs[0]
        dep_planned = first_leg.get('origin', {}).get('plannedDateTime')
        dep_actual  = first_leg.get('origin', {}).get('actualDateTime') or dep_planned

        if not dep_planned:
            continue

        dep_dt = parse_dt(dep_planned)
        if not dep_dt:
            continue

        dep_nl = dep_dt.astimezone(NL_TZ)
        if not (DEPART_FROM <= dep_nl.hour < DEPART_TO):
            continue

        dep_time   = dep_nl.strftime('%H:%M')
        cancelled  = trip.get('cancelled', False) or first_leg.get('cancelled', False)
        delay_mins = minutes_late(dep_planned, dep_actual)

        if cancelled:
            problems.append(f"❌ {dep_time} — CANCELADO")
        elif delay_mins >= DELAY_THRESH:
            problems.append(f"⏱️ {dep_time} — {delay_mins} min de retraso")
        else:
            all_ok.append(dep_time)

    print(f"Problems: {problems}")
    print(f"OK trains: {all_ok}")

    if problems:
        problem_list = '\n'.join(problems)
        ok_list = ', '.join(all_ok) if all_ok else 'ninguno en ventana'
        msg = f"Problemas en tu commute Haarlem→Utrecht:\n\n{problem_list}\n\nSin problemas: {ok_list}"
        send_ntfy(
            '🚆 Problemas en los trenes',
            msg,
            priority='high',
            tags='train,warning'
        )
        print("Alert sent!")
    else:
        ok_str = ', '.join(all_ok) if all_ok else 'sin trenes en ventana'
        print(f"All good: {ok_str} — no alert sent")

if __name__ == '__main__':
    main()

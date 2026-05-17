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
DELAY_THRESH = 0
NL_TZ        = timezone(timedelta(hours=2))  # CEST (summer)

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
    now_nl = datetime.now(NL_TZ)
    print(f"Running at {now_nl.strftime('%H:%M')} NL time, weekday={now_nl.weekday()}")

   # if now_nl.weekday() >= 5:
    #    print("Weekend — skipping")
     #   return

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
        if resp.status_code != 200:
            print(f"Response body: {resp.text[:500]}")
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

#!/usr/bin/env python3
"""
Generate a CSV of last-login time for each member of a Postman team,
including dormant accounts.

Sources:
  - GET /scim/v2/Users  (full roster — requires SCIM API key)
  - GET /audit/logs     (login events — uses regular admin API key)

Usage:
  # put POSTMAN_API_KEY and POSTMAN_SCIM_KEY in a .env file (see .env.example)
  python3 postman_last_login_report.py [--since 2026-03-19] [--out report.csv]

Output columns:
  postman_user_id, scim_id, email, name, active, last_login_utc, last_login_action

Users with no login event in the window get an empty last_login_utc (dormant).
"""

import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from postman_env import load_dotenv

BASE = "https://api.getpostman.com"

LOGIN_ACTIONS = [
    "user.login_password_success",
    "user.login_sso_success",
    "user.login_google_success",
]


class RetentionFloorError(Exception):
    pass


def get(path, api_key, params=None, bearer=False):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    auth_header = {"Authorization": f"Bearer {api_key}"} if bearer else {"X-Api-Key": api_key}
    req = urllib.request.Request(url, headers={**auth_header, "Accept": "application/json"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 2 ** attempt
                print(f"rate limited, sleeping {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            body = e.read().decode(errors="replace")
            if e.code == 400 and "cannot be older than" in body:
                raise RetentionFloorError(body)
            raise SystemExit(f"HTTP {e.code} on {url}: {body}")
    raise SystemExit(f"giving up on {url} after retries")


def list_users(scim_key):
    users = []
    start = 1
    count = 100
    while True:
        page = get("/scim/v2/Users", scim_key, {"startIndex": start, "count": count}, bearer=True)
        resources = page.get("Resources", [])
        users.extend(resources)
        total = page.get("totalResults", 0)
        if start + len(resources) > total or not resources:
            break
        start += len(resources)
    return users


def fetch_login_events(api_key, since=None, until=None, chunk_days=60, max_lookback_days=365 * 5):
    """Walk backward in fixed-size windows until we hit empty chunks or the floor.

    The Postman audit log endpoint times out on unbounded queries against a large
    team, so we explicitly bound every request to a chunk_days window.
    """
    events = []
    today = dt.date.today()
    end_date = dt.date.fromisoformat(until) if until else today
    floor_date = dt.date.fromisoformat(since) if since else today - dt.timedelta(days=max_lookback_days)

    for action in LOGIN_ACTIONS:
        action_events = 0
        action_pages = 0
        consecutive_empty = 0
        window_end = end_date
        hit_floor = False
        while window_end > floor_date and not hit_floor:
            window_start = max(window_end - dt.timedelta(days=chunk_days), floor_date)
            cursor = None
            chunk_events = 0
            while True:
                params = {
                    "action": action,
                    "since": window_start.isoformat(),
                    "until": window_end.isoformat(),
                    "limit": 100,
                    "orderBy": "desc",
                }
                if cursor:
                    params["cursor"] = cursor
                try:
                    page = get("/audit/logs", api_key, params)
                except RetentionFloorError as e:
                    print(f"  hit retention floor: {e}", file=sys.stderr)
                    hit_floor = True
                    break
                trails = page.get("trails", [])
                events.extend(trails)
                chunk_events += len(trails)
                action_pages += 1
                cursor = page.get("nextCursor")
                if not cursor or not trails:
                    break
            action_events += chunk_events
            if chunk_events == 0:
                consecutive_empty += 1
                if consecutive_empty >= 2:
                    break
            else:
                consecutive_empty = 0
            window_end = window_start
        print(f"  {action}: {action_events} events in {action_pages} page(s)", file=sys.stderr)
    return events


def aggregate_last_login(events):
    by_user = {}
    for ev in events:
        user = (ev.get("data") or {}).get("user") or {}
        uid = user.get("id")
        if uid is None:
            continue
        ts = ev.get("timestamp")
        if not ts:
            continue
        cur = by_user.get(uid)
        if cur is None or ts > cur["timestamp"]:
            by_user[uid] = {
                "timestamp": ts,
                "action": ev.get("action"),
                "email": user.get("email"),
            }
    return by_user


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="YYYY-MM-DD lower bound for audit log window")
    ap.add_argument("--until", help="YYYY-MM-DD upper bound for audit log window")
    ap.add_argument("--out", default="postman_last_login.csv")
    args = ap.parse_args()

    load_dotenv()
    api_key = os.environ.get("POSTMAN_API_KEY")
    scim_key = os.environ.get("POSTMAN_SCIM_KEY")
    if not api_key:
        raise SystemExit("set POSTMAN_API_KEY in a .env file (see .env.example) or the environment first")
    if not scim_key:
        raise SystemExit("set POSTMAN_SCIM_KEY in a .env file (see .env.example) or the environment first")

    print("fetching team roster via SCIM...", file=sys.stderr)
    users = list_users(scim_key)
    print(f"  {len(users)} users", file=sys.stderr)

    print("fetching login audit events...", file=sys.stderr)
    events = fetch_login_events(api_key, since=args.since, until=args.until)
    print(f"  {len(events)} login events", file=sys.stderr)

    by_user_id = aggregate_last_login(events)
    by_email = {v["email"].lower(): v for v in by_user_id.values() if v.get("email")}

    with open(args.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["postman_user_id", "scim_id", "email", "name", "active", "last_login_utc", "last_login_action"])
        for u in users:
            ext_id = u.get("externalId")
            try:
                uid = int(ext_id) if ext_id is not None else None
            except (TypeError, ValueError):
                uid = None
            email = u.get("userName") or ""
            np = u.get("name") or {}
            name = f"{np.get('givenName', '')} {np.get('familyName', '')}".strip()

            li = by_user_id.get(uid) if uid is not None else None
            if li is None and email:
                li = by_email.get(email.lower())

            w.writerow([
                uid if uid is not None else "",
                u.get("id", ""),
                email,
                name,
                u.get("active"),
                (li or {}).get("timestamp", ""),
                (li or {}).get("action", ""),
            ])
    print(f"wrote {args.out} ({len(users)} users)", file=sys.stderr)


if __name__ == "__main__":
    main()

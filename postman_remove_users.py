#!/usr/bin/env python3
"""
Remove (deactivate) Postman team members listed in a CSV, by email.

Postman's SCIM API doesn't hard-delete a user on DELETE -- it deactivates
their account and revokes team access. That's what this script drives.

Source:
  - GET   /scim/v2/Users?filter=userName eq "<email>"  (look up SCIM id)
  - PATCH /scim/v2/Users/<id>                           (deactivate: active=false)

Usage:
  # put POSTMAN_SCIM_KEY=PMAK-... in a .env file next to this script (see .env.example)
  python3 postman_remove_users.py --csv postman_dormant_active.csv          # dry run (default)
  python3 postman_remove_users.py --csv postman_dormant_active.csv --execute --yes

Input CSV must have an `email` column (a `name` column is ignored/optional).

Output:
  Prints a per-user result line and writes a JSON log (removal_log_<ts>.json)
  recording exactly what happened, for audit purposes.
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


def get(path, api_key, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    )
    return _send(req)


def deactivate(scim_id, api_key):
    """Postman's SCIM API doesn't implement DELETE for /Users (404s) --
    deactivation is done via PATCH with a SCIM PatchOp body."""
    url = BASE + f"/scim/v2/Users/{scim_id}"
    payload = json.dumps({
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "value": {"active": False}}],
    }).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        method="PATCH",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/scim+json",
        },
    )
    return _send(req)


def _send(req):
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                body = resp.read().decode()
                return resp.status, (json.loads(body) if body else {})
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 2 ** attempt
                print(f"  rate limited, sleeping {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            body = e.read().decode(errors="replace")
            try:
                body = json.loads(body)
            except json.JSONDecodeError:
                pass
            return e.code, body
    raise SystemExit(f"giving up on {req.full_url} after retries")


def load_targets(csv_path):
    targets = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            email = (row.get("email") or "").strip()
            if not email:
                continue
            targets.append({"name": (row.get("name") or "").strip(), "email": email})
    return targets


def find_scim_user(email, scim_key):
    filt = f'userName eq "{email}"'
    status, body = get("/scim/v2/Users", scim_key, {"filter": filt})
    if status != 200:
        return None, status, body
    resources = body.get("Resources", [])
    if not resources:
        return None, status, body
    return resources[0], status, body


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", default="postman_dormant_active.csv", help="CSV with an `email` column")
    ap.add_argument("--execute", action="store_true", help="Actually deactivate users (default: dry run)")
    ap.add_argument("--yes", action="store_true", help="Skip the interactive confirmation prompt")
    ap.add_argument("--log", default=None, help="Path for the JSON audit log (default: removal_log_<ts>.json)")
    args = ap.parse_args()

    load_dotenv()
    scim_key = os.environ.get("POSTMAN_SCIM_KEY")
    if not scim_key:
        raise SystemExit("set POSTMAN_SCIM_KEY in a .env file (see .env.example) or the environment first")

    targets = load_targets(args.csv)
    if not targets:
        raise SystemExit(f"no rows with an email column found in {args.csv}")

    print(f"loaded {len(targets)} user(s) from {args.csv}", file=sys.stderr)

    if args.execute and not args.yes:
        print(f"\nAbout to DEACTIVATE {len(targets)} Postman user(s):", file=sys.stderr)
        for t in targets:
            print(f"  - {t['name'] or '(no name)'} <{t['email']}>", file=sys.stderr)
        reply = input(f"\nType 'yes' to deactivate all {len(targets)} listed users: ")
        if reply.strip().lower() != "yes":
            raise SystemExit("aborted, no changes made")

    mode = "EXECUTE" if args.execute else "DRY RUN"
    print(f"\n=== {mode} ===", file=sys.stderr)

    results = []
    for t in targets:
        email = t["email"]
        user, status, body = find_scim_user(email, scim_key)
        if user is None:
            reason = "not found in SCIM roster" if status == 200 else f"lookup failed (HTTP {status}): {body}"
            print(f"SKIP  {email}: {reason}")
            results.append({**t, "action": "skip", "reason": reason})
            continue

        scim_id = user.get("id")
        already_inactive = user.get("active") is False
        if already_inactive:
            print(f"SKIP  {email}: already inactive")
            results.append({**t, "scim_id": scim_id, "action": "skip", "reason": "already inactive"})
            continue

        if not args.execute:
            print(f"WOULD REMOVE  {email}  (scim_id={scim_id})")
            results.append({**t, "scim_id": scim_id, "action": "dry_run"})
            continue

        del_status, del_body = deactivate(scim_id, scim_key)
        ok = del_status in (200, 204)
        print(f"{'REMOVED' if ok else 'FAILED '} {email}  (scim_id={scim_id}, HTTP {del_status})")
        results.append({
            **t,
            "scim_id": scim_id,
            "action": "removed" if ok else "failed",
            "http_status": del_status,
            "response": del_body if not ok else None,
        })

    log_path = args.log or f"removal_log_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    with open(log_path, "w") as f:
        json.dump({"mode": mode, "csv": args.csv, "results": results}, f, indent=2, default=str)
    print(f"\nwrote {log_path}", file=sys.stderr)

    if not args.execute:
        print("\nDry run only -- re-run with --execute to actually deactivate these users.", file=sys.stderr)


if __name__ == "__main__":
    main()

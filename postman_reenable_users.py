#!/usr/bin/env python3
"""
Re-enable (reactivate) Postman team members -- reverses postman_remove_users.py.

Postman's SCIM API reactivates a user with a PATCH setting active=true, the
mirror image of the deactivate call.

Source:
  - GET   /scim/v2/Users?filter=userName eq "<email>"  (look up SCIM id)
  - PATCH /scim/v2/Users/<id>                           (reactivate: active=true)

Input (pick one):
  --csv       CSV with an `email` column (a `name` column is optional).
  --from-log  A removal_log_<ts>.json written by postman_remove_users.py;
              only the rows whose action was "removed" are reactivated.

Usage:
  # put POSTMAN_SCIM_KEY=PMAK-... in a .env file next to this script (see .env.example)
  python3 postman_reenable_users.py --from-log removal_log_20260928T172635Z.json   # dry run (default)
  python3 postman_reenable_users.py --from-log removal_log_20260928T172635Z.json --execute
  python3 postman_reenable_users.py --csv reactivate.csv --execute --yes

Output:
  Prints a per-user result line and writes a JSON log (reenable_log_<ts>.json)
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


def reactivate(scim_id, api_key):
    """Mirror of postman_remove_users.deactivate -- PATCH active=true."""
    url = BASE + f"/scim/v2/Users/{scim_id}"
    payload = json.dumps({
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "value": {"active": True}}],
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
        except (urllib.error.URLError, TimeoutError) as e:
            # socket timeout / transient network error -- back off and retry
            wait = 2 ** attempt
            print(f"  network error ({e}), retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
            continue
    raise SystemExit(f"giving up on {req.full_url} after retries")


def load_targets_from_csv(csv_path):
    targets = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            email = (row.get("email") or "").strip()
            if not email:
                continue
            targets.append({"name": (row.get("name") or "").strip(), "email": email})
    return targets


def load_targets_from_log(log_path):
    """Read a removal_log JSON and return only the users that were actually
    deactivated (action == "removed"), so we reverse exactly that run."""
    with open(log_path) as f:
        data = json.load(f)
    targets = []
    for r in data.get("results", []):
        if r.get("action") != "removed":
            continue
        email = (r.get("email") or "").strip()
        if not email:
            continue
        targets.append({"name": (r.get("name") or "").strip(), "email": email})
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
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", help="CSV with an `email` column")
    src.add_argument("--from-log", dest="from_log", help="removal_log_<ts>.json to reverse")
    ap.add_argument("--execute", action="store_true", help="Actually reactivate users (default: dry run)")
    ap.add_argument("--yes", action="store_true", help="Skip the interactive confirmation prompt")
    ap.add_argument("--log", default=None, help="Path for the JSON audit log (default: reenable_log_<ts>.json)")
    args = ap.parse_args()

    load_dotenv()
    scim_key = os.environ.get("POSTMAN_SCIM_KEY")
    if not scim_key:
        raise SystemExit("set POSTMAN_SCIM_KEY in a .env file (see .env.example) or the environment first")

    if args.from_log:
        targets = load_targets_from_log(args.from_log)
        source = args.from_log
    else:
        targets = load_targets_from_csv(args.csv)
        source = args.csv
    if not targets:
        raise SystemExit(f"no users to reactivate found in {source}")

    print(f"loaded {len(targets)} user(s) from {source}", file=sys.stderr)

    if args.execute and not args.yes:
        print(f"\nAbout to REACTIVATE {len(targets)} Postman user(s):", file=sys.stderr)
        for t in targets:
            print(f"  - {t['name'] or '(no name)'} <{t['email']}>", file=sys.stderr)
        reply = input(f"\nType 'yes' to reactivate all {len(targets)} listed users: ")
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
        already_active = user.get("active") is True
        if already_active:
            print(f"SKIP  {email}: already active")
            results.append({**t, "scim_id": scim_id, "action": "skip", "reason": "already active"})
            continue

        if not args.execute:
            print(f"WOULD REACTIVATE  {email}  (scim_id={scim_id})")
            results.append({**t, "scim_id": scim_id, "action": "dry_run"})
            continue

        act_status, act_body = reactivate(scim_id, scim_key)
        ok = act_status in (200, 204)
        print(f"{'REACTIVATED' if ok else 'FAILED     '} {email}  (scim_id={scim_id}, HTTP {act_status})")
        results.append({
            **t,
            "scim_id": scim_id,
            "action": "reactivated" if ok else "failed",
            "http_status": act_status,
            "response": act_body if not ok else None,
        })

    log_path = args.log or f"reenable_log_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    with open(log_path, "w") as f:
        json.dump({"mode": mode, "source": source, "results": results}, f, indent=2, default=str)
    print(f"\nwrote {log_path}", file=sys.stderr)

    if not args.execute:
        print("\nDry run only -- re-run with --execute to actually reactivate these users.", file=sys.stderr)


if __name__ == "__main__":
    main()

# postman-utils

Small utilities for administering a [Postman](https://www.postman.com/) team.

## Setup

Both scripts read their API keys from a `.env` file in this directory
(loaded via the shared `postman_env.py` helper -- no third-party dependency).
Create one:

```
POSTMAN_API_KEY=PMAK-...    # team-admin key
POSTMAN_SCIM_KEY=PMAK-...   # SCIM key (Team Settings -> SCIM provisioning)
```

See `.env.example` for the template. `.env` is listed in `.gitignore` and
should never be committed. Values already set in your shell environment take
precedence over `.env`.

## Scripts

### `postman_last_login_report.py`

Generates a CSV of the last-login time for every member of a Postman team,
including dormant accounts that have never logged in within the audit window.

It combines two data sources:

- **`GET /scim/v2/Users`** — the full team roster (requires a SCIM API key).
- **`GET /audit/logs`** — login events (uses a regular team-admin API key).

The audit log endpoint can time out on unbounded queries against a large team,
so the script walks backward through the history in fixed-size time windows and
stops once it reaches the retention floor or a run of empty windows.

#### Requirements

- Python 3.7+ (standard library only — no third-party dependencies).
- A Postman team-admin API key.
- A Postman SCIM API key (Team Settings → SCIM provisioning).

#### Usage

```sh
python3 postman_last_login_report.py [--since 2026-03-19] [--until 2026-06-17] [--out report.csv]
```

| Flag      | Description                                              | Default                      |
| --------- | -------------------------------------------------------- | ---------------------------- |
| `--since` | `YYYY-MM-DD` lower bound for the audit log window        | up to 5 years back           |
| `--until` | `YYYY-MM-DD` upper bound for the audit log window        | today                        |
| `--out`   | Output CSV path                                          | `postman_last_login.csv`     |

#### Output

A CSV with the following columns:

| Column               | Description                                                       |
| -------------------- | ----------------------------------------------------------------- |
| `postman_user_id`    | Postman user ID                                                   |
| `scim_id`            | SCIM resource ID                                                  |
| `email`              | User email                                                        |
| `name`               | Display name                                                      |
| `active`             | Whether the account is active                                     |
| `last_login_utc`     | Timestamp of the most recent login (empty for dormant accounts)   |
| `last_login_action`  | Which login action was last seen (password / SSO / Google)        |

Users with no login event in the window get an empty `last_login_utc`, marking
them as dormant.

### `postman_remove_users.py`

Deactivates a list of Postman team members via the SCIM API, driven by a CSV
of `name,email` rows (e.g. the output of a dormant-user review).

Postman's SCIM `DELETE` doesn't hard-delete an account -- it deactivates the
user and revokes team access, which is what this script does.

#### Requirements

- Python 3.7+ (standard library only).
- A Postman SCIM API key (Team Settings → SCIM provisioning) -- see [Setup](#setup).

#### Usage

```sh
# Dry run first -- prints who *would* be removed, no changes made.
python3 postman_remove_users.py --csv postman_dormant_active.csv

# Actually deactivate. Prompts for interactive confirmation unless --yes.
python3 postman_remove_users.py --csv postman_dormant_active.csv --execute
```

| Flag        | Description                                              | Default              |
| ----------- | --------------------------------------------------------- | -------------------- |
| `--csv`     | Input CSV with `name,email` columns                       | `postman_dormant_active.csv` |
| `--execute` | Actually deactivate users (omit for a dry run)             | off (dry run)         |
| `--yes`     | Skip the interactive confirmation prompt                  | off                    |
| `--log`     | Path for the JSON audit log                                | `removal_log_<timestamp>.json` |

Every run (dry or real) writes a JSON audit log recording what happened to
each row -- removed, failed, skipped (not found / already inactive), or
dry-run.

### `postman_reenable_users.py`

Reactivates Postman team members via the SCIM API -- the reverse of
`postman_remove_users.py`. It can take a plain `name,email` CSV, or (more
conveniently) a `removal_log_<ts>.json` written by the removal script, in
which case only the users that were actually deactivated in that run
(`action == "removed"`) are reactivated.

#### Requirements

- Python 3.7+ (standard library only).
- A Postman SCIM API key (Team Settings → SCIM provisioning) -- see [Setup](#setup).

#### Usage

```sh
# Reverse a specific removal run (dry run first -- no changes made).
python3 postman_reenable_users.py --from-log removal_log_20260928T172635Z.json

# Actually reactivate. Prompts for interactive confirmation unless --yes.
python3 postman_reenable_users.py --from-log removal_log_20260928T172635Z.json --execute

# Or drive it from a CSV of users to reactivate.
python3 postman_reenable_users.py --csv reactivate.csv --execute
```

| Flag         | Description                                              | Default              |
| ------------ | --------------------------------------------------------- | -------------------- |
| `--csv`      | Input CSV with `name,email` columns                       | (one of `--csv`/`--from-log` required) |
| `--from-log` | A `removal_log_<ts>.json` to reverse                      | (one of `--csv`/`--from-log` required) |
| `--execute`  | Actually reactivate users (omit for a dry run)            | off (dry run)         |
| `--yes`      | Skip the interactive confirmation prompt                 | off                    |
| `--log`      | Path for the JSON audit log                               | `reenable_log_<timestamp>.json` |

Every run (dry or real) writes a JSON audit log recording what happened to
each row -- reactivated, failed, skipped (not found / already active), or
dry-run.

## License

[MIT](LICENSE)

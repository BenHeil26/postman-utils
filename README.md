# postman-utils

Small utilities for administering a [Postman](https://www.postman.com/) team.

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
export POSTMAN_API_KEY='PMAK-...'    # team-admin key
export POSTMAN_SCIM_KEY='PMAK-...'   # SCIM key

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

## License

[MIT](LICENSE)

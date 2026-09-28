"""Shared .env loader for the postman-utils scripts."""

import os


def load_dotenv(path=".env"):
    """Minimal .env loader (KEY=VALUE per line). Existing env vars win."""
    if not os.path.isfile(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)

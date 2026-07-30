"""One-off interactive Garmin login that writes a reusable token store.

The MCP server cannot prompt for an MFA code over stdio, so do the login once
here and point ``GARMIN_TOKEN_STORE`` at the resulting directory:

    python -m app.garmin_login
    export GARMIN_TOKEN_STORE=~/.garminconnect

Tokens last for months; rerun this when they expire.
"""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

from garminconnect import Garmin

DEFAULT_TOKEN_STORE = "~/.garminconnect"


def main() -> int:
    token_store = Path(
        os.environ.get("GARMIN_TOKEN_STORE", DEFAULT_TOKEN_STORE)
    ).expanduser()

    email = os.environ.get("GARMIN_EMAIL") or input("Garmin email: ").strip()
    password = os.environ.get("GARMIN_PASSWORD") or getpass.getpass("Garmin password: ")

    client = Garmin(email, password, return_on_mfa=True)
    mfa_status, state = client.login()

    if mfa_status == "needs_mfa":
        code = input("MFA code: ").strip()
        client.resume_login(state, code)

    token_store.mkdir(parents=True, exist_ok=True)
    (token_store / "garmin_tokens.json").write_text(client.client.dumps())

    print(f"Saved Garmin tokens to {token_store}")
    print(f"Now set: export GARMIN_TOKEN_STORE={token_store}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

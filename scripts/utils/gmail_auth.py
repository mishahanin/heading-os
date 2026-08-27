#!/usr/bin/env python3
"""Shared Gmail API authentication.

One definition of the OAuth dance, so a second Gmail caller does not fork a
second copy of the token handling. Consumed by `scripts/gmail-reader.py` and
`scripts/gmail-send.py`.

Credentials live outside the repo, in the engine clone's gitignored
`.sessions/google/`, and are reused, never re-minted here. The `gmail.modify`
scope covers both reading and `drafts.send`, which is why one token serves both
callers.

Usage:
    from scripts.utils.gmail_auth import get_service
    service = get_service()
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.utils.atomic import atomic_write_text  # noqa: E402

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]


def creds_path() -> str:
    return os.getenv(
        "GOOGLE_GMAIL_CREDENTIALS_PATH",
        os.getenv(
            "GOOGLE_CONTACTS_CREDENTIALS_PATH",
            str(PROJECT_ROOT / ".sessions" / "google" / "credentials.json"),
        ),
    )


def token_path() -> str:
    return str(PROJECT_ROOT / ".sessions" / "google" / "gmail_token.json")


def get_service():
    """Return an authorized Gmail API service, refreshing the token if needed."""
    from scripts.utils.optdeps import require

    require("google", extra="ai-extra")
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    token = token_path()
    creds = None
    if os.path.exists(token):
        try:
            creds = Credentials.from_authorized_user_file(token, SCOPES)
        except (ValueError, json.JSONDecodeError) as exc:
            # A token file that exists but cannot be read is the normal outcome of
            # an interrupted write, a revoked grant, or a hand edit: the library
            # raises JSONDecodeError on a truncated file and ValueError on valid
            # JSON missing `refresh_token`. Neither was caught, so `get_service`
            # raised out of a helper whose only caller-side handler
            # (`scripts/gmail-reader.py`) catches FileNotFoundError - the operator
            # got a traceback instead of a re-authorisation.
            #
            # A None here is not a silent default: it falls through to the same
            # consent flow a first run takes, and the reason is stated first.
            print(f"gmail_auth: the saved token at {token} is unusable "
                  f"({type(exc).__name__}: {exc}); re-authorising.", file=sys.stderr)
            creds = None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            secrets = creds_path()
            if not os.path.exists(secrets):
                raise FileNotFoundError(
                    f"Google OAuth client secrets not found at {secrets}. "
                    "Place them there or set GOOGLE_GMAIL_CREDENTIALS_PATH in .env."
                )
            creds = InstalledAppFlow.from_client_secrets_file(secrets, SCOPES).run_local_server(port=0)
        os.makedirs(os.path.dirname(token), mode=0o700, exist_ok=True)
        # tmp + os.replace, not open("w"). The refresh token is the whole Gmail
        # path's credential: a plain open truncates in place, so a crash or a
        # concurrent reader between truncate and write leaves an empty or
        # half-written token, and every caller then re-runs the browser OAuth
        # dance on a headless machine. atomic_write_text sets the mode on the
        # tempfile before the rename, so the file is never briefly world-readable.
        atomic_write_text(Path(token), creds.to_json(), mode=0o600)
    return build("gmail", "v1", credentials=creds)

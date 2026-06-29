#!/usr/bin/env python3
"""Exchange EWS wrapper with polling support.

Wraps exchangelib.Account to provide email arrival detection via
EWS polling. Designed for the Inbox Pulse daemon.

Usage::

    from scripts.inbox_pulse.exchange import EWSConnection

    conn = EWSConnection()           # reads creds from .env
    for event in conn.poll_inbox(since=last_cursor):
        print(event)
    conn.disconnect()
"""

from __future__ import annotations

import os
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING, Iterator
from scripts.utils.workspace import get_default_tz, get_default_tz_name

if TYPE_CHECKING:
    from exchangelib import Account
    from exchangelib.items import Item

__all__ = ["EWSConnection"]

# local timezone resolved per-instance via get_default_tz()


def _now_local_iso() -> str:
    """Return current time in local timezone as ISO-8601 string."""
    return datetime.now(get_default_tz()).isoformat()


class EWSConnection:
    """Wraps exchangelib.Account with polling support for inbound email events."""

    def __init__(
        self,
        account_email: str | None = None,
        password: str | None = None,
        server_url: str | None = None,
    ) -> None:
        """Initialize from explicit args OR from .env.

        Reads EXCHANGE_EMAIL, EXCHANGE_PASSWORD, EXCHANGE_SERVER (and
        optionally EXCHANGE_USERNAME) from the environment.  load_env() is
        expected to have been called by the process entrypoint; callers in
        tests may monkeypatch os.environ directly.

        Connection is lazy -- the actual exchangelib.Account is created on
        the first access of the `account` property.
        """
        self._email: str | None = account_email or os.getenv("EXCHANGE_EMAIL")
        self._password: str | None = password or os.getenv("EXCHANGE_PASSWORD")
        self._server: str | None = server_url or os.getenv("EXCHANGE_SERVER")
        self._account: "Account | None" = None

    # ------------------------------------------------------------------
    # account property (lazy connect)
    # ------------------------------------------------------------------

    @property
    def account(self) -> "Account":
        """Return the underlying exchangelib.Account, connecting on first access."""
        if self._account is None:
            self._account = self._connect()
        return self._account

    def _connect(self) -> "Account":
        """Open the EWS connection and return a new Account."""
        from exchangelib import Account, Configuration, Credentials, DELEGATE

        email = self._email
        password = self._password
        server = self._server
        username = os.getenv("EXCHANGE_USERNAME", email)

        if not all([email, password, server]):
            raise ValueError(
                "Missing Exchange credentials. Set EXCHANGE_EMAIL, EXCHANGE_PASSWORD, "
                "and EXCHANGE_SERVER in .env or pass them explicitly."
            )

        credentials = Credentials(username=username, password=password)
        exchange_config = Configuration(server=server, credentials=credentials)
        return Account(
            primary_smtp_address=email,
            config=exchange_config,
            autodiscover=False,
            access_type=DELEGATE,
        )

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def poll_inbox(
        self,
        since: datetime | None = None,
        folder_name: str = "Inbox",
        max_items: int = 200,
    ) -> Iterator[dict]:
        """Poll the inbox for items received since `since` and yield event dicts.

        Args:
            since: Datetime cutoff. Only items with datetime_received > since yield.
                   If None, yields the most recent max_items (oldest-first).
            folder_name: Folder to poll (default Inbox).
            max_items: Hard cap on items yielded per poll cycle.

        Yields one dict per item, sorted oldest-first (so consumers process in order):
            - event_type: "NewMail" (synthetic -- matches the old streaming event_type)
            - timestamp: ISO-8601 string in local timezone at the moment of poll
            - item_id: EWS item ID
            - parent_folder_id: EWS parent folder ID
            - datetime_received: ISO-8601 of when Exchange received the item (the
              authoritative timestamp for this item, used by caller to update
              its since-cursor for the next poll)
        """
        folder = self._resolve_folder(folder_name)
        if since is not None:
            items = folder.filter(datetime_received__gt=since).order_by("datetime_received")[:max_items]
        else:
            items = folder.all().order_by("-datetime_received")[:max_items]
            items = list(reversed(items))  # oldest-first for processing order

        now_local = _now_local_iso()
        for item in items:
            yield {
                "event_type": "NewMail",
                "timestamp": now_local,
                "item_id": str(item.id) if item.id else "",
                "parent_folder_id": str(item.parent_folder_id) if hasattr(item, "parent_folder_id") and item.parent_folder_id else "",
                "datetime_received": item.datetime_received.isoformat() if item.datetime_received else None,
            }

    # ------------------------------------------------------------------
    # Item access
    # ------------------------------------------------------------------

    def fetch_item(self, item_id: str) -> "Item":
        """Fetch a full Item by EWS ID.

        :param item_id: EWS item ID string.
        :returns: exchangelib Item object.
        :raises exchangelib.errors.DoesNotExist: When no item with that ID
            exists in the inbox. NOTE: items moved to another folder (e.g. by
            Exchange rules) after the event was received will also raise
            DoesNotExist. Callers should catch this as a transient skip,
            NOT a fatal error.
        """
        return self.account.inbox.get(id=item_id)

    # ------------------------------------------------------------------
    # OWA deep-link
    # ------------------------------------------------------------------

    def build_owa_link(self, item_id: str) -> str:
        """Construct an OWA deep-link for an item.

        Uses EXCHANGE_SERVER from the environment to derive the OWA host.
        The item_id is URL-encoded so IDs containing '+', '/', '=' are safe.

        Format::

            https://<owa_host>/owa/?ItemID=<url-encoded-id>&exvsurl=1&viewmodel=ReadMessageItem

        :param item_id: EWS item ID string (may contain URL-unsafe chars).
        :returns: Fully-qualified OWA URL string.
        """
        owa_host = self._server or os.getenv("EXCHANGE_SERVER", "")
        encoded_id = urllib.parse.quote(item_id, safe="")
        return (
            f"https://{owa_host}/owa/"
            f"?ItemID={encoded_id}&exvsurl=1&viewmodel=ReadMessageItem"
        )

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def disconnect(self) -> None:
        """Close the connection and release the account. Idempotent."""
        self._account = None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_folder(self, folder_name: str):
        """Return the exchangelib folder object for the given name."""
        if folder_name.lower() == "inbox":
            return self.account.inbox
        # Extension point for Phase 4+: non-Inbox folders
        return self.account.inbox / folder_name

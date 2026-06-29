"""Rules engine for Inbox Pulse cheap classifier.

Loads config/email-triage-rules.yaml at startup and on file-change.
Provides matching helpers for sender overrides, keyword overrides,
quiet-hours awareness, and breakthrough allowlist lookup.

Glob semantics for sender overrides: fnmatch-style wildcards.
- "newsletter@*"          matches any sender starting with "newsletter@"
- "*@linkedin.com"        matches any sender ending with "@linkedin.com"
- "alice@31c.io" exact match

Sovereignty discipline
----------------------
This module does NOT log email content. It accepts addresses and subjects
as arguments and returns classification labels only. No caller-supplied
string is written to logs inside this module.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timezone, timedelta
from fnmatch import fnmatch
from pathlib import Path
from typing import Optional

import yaml

log = logging.getLogger(__name__)

# Priority order for sender override buckets (highest first).
_SENDER_PRIORITY: list[str] = ["always_critical", "always_important", "always_normal"]

# Map sender bucket names to keyword bucket names so we can share helpers.
_KEYWORD_PRIORITY: list[str] = ["promote_to_critical", "promote_to_important"]

# How many characters of the body preview to search.
_BODY_PREVIEW_CHARS = 500


@dataclass
class RulesEngine:
    """Loads + matches against config/email-triage-rules.yaml.

    Caller passes the YAML path explicitly (for testability). On each
    reload(), the file is re-read; if missing or invalid, the engine
    keeps its last-known-good state and logs a warning.

    Empty-posture fallback
    ----------------------
    If the YAML file is missing OR fails to parse OR is empty/None, the
    engine operates with an empty config dict. Every match helper returns
    None (or False), cost properties return defaults. The daemon keeps
    running without crashing.
    """

    yaml_path: Path
    _config: dict = field(default_factory=dict, repr=False)
    _last_mtime: float = field(default=0.0, repr=False)
    _missing_warned: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        self.reload()

    # ------------------------------------------------------------------
    # Load / reload
    # ------------------------------------------------------------------

    def reload(self) -> bool:
        """Re-read the YAML file. Return True if config changed, False if unchanged.

        On parse error or missing file the engine keeps its prior config and
        logs a warning -- it never raises.
        """
        try:
            mtime = self.yaml_path.stat().st_mtime
        except OSError:
            if self._config:
                # Had config before; keep it. Warn once.
                log.warning("email-triage-rules.yaml not found at %s; keeping prior config", self.yaml_path)
            else:
                log.warning("email-triage-rules.yaml not found at %s; running with empty rules", self.yaml_path)
            return False

        try:
            raw = self.yaml_path.read_text(encoding="utf-8")
            parsed = yaml.safe_load(raw)
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to parse %s: %s; keeping prior config", self.yaml_path, exc)
            return False

        if not parsed or not isinstance(parsed, dict):
            log.warning("%s parsed as empty or non-dict; keeping prior config", self.yaml_path)
            return False

        if parsed == self._config:
            # File changed mtime but content is identical (e.g., touch).
            self._last_mtime = mtime
            return False

        self._config = parsed
        self._last_mtime = mtime
        log.info("email-triage-rules.yaml reloaded from %s", self.yaml_path)
        return True

    def reload_if_changed(self) -> bool:
        """Check mtime; call reload() only if the file changed since last load.

        Returns True if a reload happened AND the config changed.
        Returns False if the file is unchanged or the reload produced no diff.

        If the file has disappeared since the last successful load (OSError on
        stat), logs a one-shot warning so the operator notices, then returns
        False and keeps the prior config. The warning is throttled so a missing
        file doesn't spam the log every poll cycle.
        """
        try:
            mtime = self.yaml_path.stat().st_mtime
        except OSError as exc:
            if not self._missing_warned:
                log.warning(
                    "rules YAML stat() failed (file gone?): %s -- keeping prior config",
                    exc,
                )
                self._missing_warned = True
            return False

        # File came back; reset the missing flag so a future disappearance re-warns
        self._missing_warned = False

        if mtime <= self._last_mtime:
            return False

        return self.reload()

    # ------------------------------------------------------------------
    # Sender matching
    # ------------------------------------------------------------------

    def match_sender(self, email_addr: str) -> Optional[str]:
        """Return 'always_critical' | 'always_important' | 'always_normal' | None.

        Glob match: case-insensitive. First-match-wins in priority order
        (always_critical > always_important > always_normal).
        """
        sender_overrides: dict = self._config.get("sender_overrides", {})
        addr_lower = email_addr.lower()

        for bucket in _SENDER_PRIORITY:
            patterns: list = sender_overrides.get(bucket) or []
            for pattern in patterns:
                if fnmatch(addr_lower, pattern.lower()):
                    return bucket

        return None

    # ------------------------------------------------------------------
    # Keyword matching
    # ------------------------------------------------------------------

    def match_keywords(self, subject: str, body_preview: str = "") -> Optional[str]:
        """Return 'promote_to_critical' | 'promote_to_important' | None.

        Case-insensitive substring match against subject + first 500 chars
        of body_preview. promote_to_critical wins over promote_to_important.
        """
        keyword_overrides: dict = self._config.get("keyword_overrides", {})
        haystack = (subject + " " + body_preview[:_BODY_PREVIEW_CHARS]).lower()

        for bucket in _KEYWORD_PRIORITY:
            keywords: list = keyword_overrides.get(bucket) or []
            for kw in keywords:
                if kw.lower() in haystack:
                    return bucket

        return None

    # ------------------------------------------------------------------
    # Quiet hours
    # ------------------------------------------------------------------

    def is_quiet_hours(self, at: Optional[datetime] = None) -> bool:
        """Return True if `at` (default: now) falls within quiet_hours window.

        Handles wrap-around (start=23:00 end=07:00 means 23:00-23:59 + 00:00-07:00).
        The end boundary is exclusive: exactly 07:00 is NOT quiet.
        """
        quiet: dict = self._config.get("quiet_hours", {})
        if not quiet:
            return False

        tz_name: str = quiet.get("timezone", "UTC")
        start_str: str = quiet.get("start", "")
        end_str: str = quiet.get("end", "")

        if not start_str or not end_str:
            return False

        try:
            import zoneinfo  # stdlib 3.9+
            tz = zoneinfo.ZoneInfo(tz_name)
        except Exception:  # noqa: BLE001
            try:
                import pytz
                tz = pytz.timezone(tz_name)
            except Exception:  # noqa: BLE001
                log.warning("Unknown timezone %r; quiet_hours disabled", tz_name)
                return False

        if at is None:
            at = datetime.now(tz=timezone.utc)

        # Convert to target timezone.
        local_dt = at.astimezone(tz)
        current = local_dt.time().replace(tzinfo=None)

        start_t = _parse_hhmm(start_str)
        end_t = _parse_hhmm(end_str)

        if start_t is None or end_t is None:
            log.warning("Cannot parse quiet_hours start=%r end=%r", start_str, end_str)
            return False

        if start_t <= end_t:
            # Normal window (e.g., 09:00-17:00).
            return start_t <= current < end_t
        else:
            # Wrap-around window (e.g., 23:00-07:00).
            return current >= start_t or current < end_t

    # ------------------------------------------------------------------
    # Breakthrough allowlist
    # ------------------------------------------------------------------

    def is_breakthrough_sender(self, email_addr: str) -> bool:
        """Return True if sender is in breakthrough_allowlist (glob match)."""
        allowlist: list = self._config.get("breakthrough_allowlist") or []
        addr_lower = email_addr.lower()
        for pattern in allowlist:
            if fnmatch(addr_lower, pattern.lower()):
                return True
        return False

    # ------------------------------------------------------------------
    # Cost properties
    # ------------------------------------------------------------------

    @property
    def internal_domains(self) -> list[str]:
        """List of internal email domains for the recipient-aware classifier rule.

        Used by CheapClassifier to determine whether a sender is internal
        (own company) -- internal senders get the Tribe Leadership + To/CC
        short-circuit logic; external senders go through the standard
        7-signal classifier.

        Defaults to [] if not configured (which effectively disables the
        recipient-aware rule).
        """
        return self._config.get("internal_domains", []) or []

    @property
    def cost_ceiling_usd(self) -> float:
        """Monthly Anthropic spend ceiling from config. Default 50.0 if missing."""
        cost: dict = self._config.get("cost_ceiling", {})
        if not isinstance(cost, dict):
            return 50.0
        return float(cost.get("monthly_anthropic_usd", 50.0))

    @property
    def cost_warn_at_percent(self) -> int:
        """Warning threshold as percent of ceiling. Default 80 if missing."""
        cost: dict = self._config.get("cost_ceiling", {})
        if not isinstance(cost, dict):
            return 80
        return int(cost.get("warn_at_percent", 80))


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _parse_hhmm(value: str) -> Optional[time]:
    """Parse "HH:MM" string into a time object. Returns None on failure."""
    try:
        parts = value.strip().split(":")
        return time(int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        return None

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

Tests: tests/test_a_day_that_could_not_be_read_and_was_called_quiet.py,
       tests/test_a_catch_all_rule_the_report_could_not_see.py
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
    # The mtime of the last version of the file that failed to load. A broken
    # file keeps its mtime above _last_mtime (which only advances on success),
    # so reload_if_changed() retries every poll cycle and each retry logged the
    # full warning again -- forever, at the same rate the missing-file case was
    # throttled to avoid. Retrying is right; saying so 2,880 times a day is not.
    # Keyed on mtime, so SAVING the file warns again if it is still broken.
    _bad_load_warned_mtime: float | None = field(default=None, repr=False)
    # Buckets already reported as scalar-instead-of-list, so the warning is
    # emitted once per config rather than once per inbound email. Cleared
    # whenever a new config is accepted, so a re-saved file warns again.
    _scalar_warned: set = field(default_factory=set, repr=False)

    def __post_init__(self) -> None:
        self.reload()

    # ------------------------------------------------------------------
    # Load / reload
    # ------------------------------------------------------------------

    def reload(self) -> bool:
        """Re-read the YAML file. Return True if config changed, False if unchanged.

        On parse error or missing file the engine keeps its prior config and
        logs a warning -- it never raises. The bad-load warning is throttled by
        mtime, for the reason given on `_bad_load_warned_mtime`; the
        missing-file warning is throttled by `_missing_warned`, shared with
        `reload_if_changed` and cleared the moment the file is readable again.
        """
        try:
            mtime = self.yaml_path.stat().st_mtime
        except OSError:
            # "Warn once" was a comment with nothing behind it: no flag was set,
            # so every call warned again. `_missing_warned` already existed for
            # exactly this, used by `reload_if_changed`, which returns before it
            # ever reaches here -- so the throttle the comment promised lived in
            # the one path that could not use it. Sharing the flag gives it one
            # meaning: the operator has already been told the file is gone.
            if not self._missing_warned:
                if self._config:
                    log.warning("email-triage-rules.yaml not found at %s; keeping prior config",
                                self.yaml_path)
                else:
                    log.warning("email-triage-rules.yaml not found at %s; running with empty rules",
                                self.yaml_path)
                self._missing_warned = True
            return False

        # The file is readable again, so a future disappearance warns afresh.
        self._missing_warned = False

        try:
            raw = self.yaml_path.read_text(encoding="utf-8")
            parsed = yaml.safe_load(raw)
        except Exception as exc:  # noqa: BLE001
            if self._bad_load_warned_mtime != mtime:
                log.warning("Failed to parse %s: %s; keeping prior config",
                            self.yaml_path, exc)
                self._bad_load_warned_mtime = mtime
            return False

        if not parsed or not isinstance(parsed, dict):
            if self._bad_load_warned_mtime != mtime:
                log.warning("%s parsed as empty or non-dict; keeping prior config",
                            self.yaml_path)
                self._bad_load_warned_mtime = mtime
            return False

        # A good load clears the throttle, so the next breakage warns at once.
        self._bad_load_warned_mtime = None

        if parsed == self._config:
            # File changed mtime but content is identical (e.g., touch).
            self._last_mtime = mtime
            return False

        self._config = parsed
        self._last_mtime = mtime
        self._scalar_warned.clear()
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
    # Bucket shape
    # ------------------------------------------------------------------

    def _pattern_list(self, raw: object, where: str) -> list:
        """Return `raw` as a list of pattern strings, tolerating a bare scalar.

        `reload()` checks only that the YAML root is a dict, so a bucket may
        hold anything the operator typed. The natural single-entry form is a
        scalar, `always_critical: "*@acme-telecom.example"`, and iterating a
        string yields its CHARACTERS. Measured 2026-08-29 on exactly that
        config: the first character is `*`, `fnmatch(addr, "*")` is true for
        every address, and `match_sender("nobody@unrelated.example")` returned
        "always_critical". The same shape in `breakthrough_allowlist` made every
        sender a breakthrough sender, and a scalar keyword "DUE" degraded to the
        single-character test `"d" in haystack`, which matched "lunch tomorrow".
        The config parses cleanly, so nothing anywhere said a word about it.

        Wrapping the scalar reads it the way the operator meant it, and the
        warning tells them the file wants a list.
        """
        if raw is None:
            return []
        if isinstance(raw, list):
            return raw
        if where not in self._scalar_warned:
            log.warning(
                "%s: %s is a single value, not a list; reading it as a "
                "one-entry list. Write it as a YAML list to silence this.",
                self.yaml_path,
                where,
            )
            self._scalar_warned.add(where)
        return [str(raw)]

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
            patterns = self._pattern_list(
                sender_overrides.get(bucket), f"sender_overrides.{bucket}"
            )
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
            keywords = self._pattern_list(
                keyword_overrides.get(bucket), f"keyword_overrides.{bucket}"
            )
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

        A naive `at` is interpreted as UTC, never as the host's local time.
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
        elif at.tzinfo is None or at.utcoffset() is None:
            # A naive `at` is read as UTC, the same instant the default branch
            # above would have produced. Without this, `astimezone()` assumes
            # the HOST's local timezone: measured 2026-08-29 with
            # quiet_hours 23:00-07:00 UTC and a naive 23:30, the answer was
            # True on a UTC host and False on an Asia/Dubai one, from identical
            # config and identical input. Which machine the daemon runs on is
            # not an input to "is 23:30 inside the quiet window".
            at = at.replace(tzinfo=timezone.utc)

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
        allowlist = self._pattern_list(
            self._config.get("breakthrough_allowlist"), "breakthrough_allowlist"
        )
        addr_lower = email_addr.lower()
        return any(fnmatch(addr_lower, pattern.lower()) for pattern in allowlist)

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
        return _coerce_number(
            cost.get("monthly_anthropic_usd"), 50.0, float,
            "cost_ceiling.monthly_anthropic_usd",
        )

    @property
    def cost_warn_at_percent(self) -> int:
        """Warning threshold as percent of ceiling. Default 80 if missing."""
        cost: dict = self._config.get("cost_ceiling", {})
        if not isinstance(cost, dict):
            return 80
        return _coerce_number(
            cost.get("warn_at_percent"), 80, int, "cost_ceiling.warn_at_percent"
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _coerce_number(raw: object, default, caster, field_name: str):
    """Cast `raw` with `caster`, falling back to `default` instead of raising.

    `dict.get(key, default)` substitutes only when the key is ABSENT, and YAML
    has a second way to say nothing: `warn_at_percent:` with no value parses as
    None. Measured 2026-08-29 on that exact file, `int(None)` raised TypeError
    and `float("fifty")` raised ValueError, both straight out of a property the
    class docstring promises never to crash on a bad config. A parseable file
    took the daemon down.

    None means "not configured" and takes the default silently, matching the
    absent-key case. A value that is present but uncastable is the operator
    getting something wrong, so it is worth a line in the log: their ceiling is
    not the number they wrote.
    """
    if raw is None:
        return default
    try:
        return caster(raw)
    except (TypeError, ValueError):
        log.warning(
            "email-triage-rules.yaml: %s=%r is not a number; using %r",
            field_name, raw, default,
        )
        return default


def _parse_hhmm(value: str) -> Optional[time]:
    """Parse "HH:MM" string into a time object. Returns None on failure."""
    try:
        parts = value.strip().split(":")
        return time(int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        return None

#!/usr/bin/env python3
"""Inbox Pulse shadow-mode observation CLI.

Fetches JSONL classifier logs from the managed service-host VM, aggregates tier
distribution, identifies "known-good but classified LOW" candidates,
produces YAML tuning suggestions, and renders a markdown report.

Usage:
    python scripts/inbox-pulse-report.py
    python scripts/inbox-pulse-report.py --days 7
    python scripts/inbox-pulse-report.py --days 3 --no-open
    python scripts/inbox-pulse-report.py --days 1 --no-open

Tests: tests/test_a_day_that_could_not_be_read_and_was_called_quiet.py,
       tests/test_inbox_pulse_unreachable.py,
       tests/test_a_catch_all_rule_the_report_could_not_see.py,
       tests/test_a_report_column_that_named_a_denominator_it_did_not_use.py

Options:
    --days N     Number of calendar days to include (default 1 - today only).
    --no-open    Skip opening the report in VS Code.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.crm import contact_index_by_email
from scripts.utils.paths import get_workspace_root, load_env
from scripts.utils.workspace import get_data_config_dir, get_outputs_dir, get_default_tz

# ===========================================================================
# Constants
# ===========================================================================

# VM_HOST / VM_STATE_DIR describe the REMOTE managed service-host VM reached over SSH (see
# ssh_read). They are NOT local-machine paths, so they must not be routed
# through Path.home() / get_workspace_root() - doing so would break the live
# SSH data fetch. They are correct literals for the remote host. To stay
# portable across operators and avoid embedding a host-specific literal, the
# real host + path come from env vars (loaded from .env); the defaults below
# are non-revealing placeholders so the engine ships no instance topology.
load_env()
VM_HOST = os.environ.get("INBOX_PULSE_VM_HOST", "root@service-host")
VM_STATE_DIR = os.environ.get(
    "INBOX_PULSE_VM_STATE_DIR", "/path/to/service-host/state/email-triage"
)
VM_STATE_FILE = f"{VM_STATE_DIR}/state.json"
SSH_TIMEOUT = 30

TIER_HIGH = "HIGH_LIKELY"
TIER_MAYBE = "MAYBE"
TIER_LOW = "LOW"

# Suggestion thresholds
SUGGEST_ALWAYS_NORMAL_MIN_ENTRIES = 5
SUGGEST_CRM_KNOWN_LOW_MIN_ENTRIES = 3


# ===========================================================================
# SSH helpers
# ===========================================================================


SSH_TRANSPORT_FAILURE = 255  # ssh(1)'s own exit status when it cannot connect
REMOTE_FILE_ABSENT = 66      # our own sentinel, raised by the remote `test -f`


def ssh_read(remote_path: str) -> str | None:
    """Read a remote file via SSH.

    Returns the text, `""` when the remote file is CONFIRMED absent, or None
    when anything else went wrong. The three are different answers and the
    caller needs them apart: an absent day-log means the daemon wrote nothing,
    and anything else means this report knows nothing about that day.

    Absence is proved by a remote `test -f`, not inferred from a non-zero exit.
    It used to be inferred, and every remote-side failure -- an unreadable
    file, a full disk, a broken login shell -- came back as `""`, which
    `fetch_jsonl_for_date` reads as "genuinely empty day". A day whose log
    existed and could not be read was reported as a quiet inbox, with exit 0
    and no warning: the report's numbers were wrong and its own docstring said
    they were not.

    The probe is ONE argv element on purpose. ssh joins its remaining arguments
    with spaces and hands the result to the remote login shell, so quoting done
    on this side is lost; the string below is already shell-quoted for that
    shell, which is also what makes a path containing a space work.
    """
    quoted = shlex.quote(remote_path)
    probe = f"test -f {quoted} || exit {REMOTE_FILE_ABSENT}; exec cat {quoted}"
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
             VM_HOST, probe],
            capture_output=True,
            text=True,
            timeout=SSH_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode == REMOTE_FILE_ABSENT:
        return ""
    if result.returncode == SSH_TRANSPORT_FAILURE:
        return None
    if result.returncode != 0:
        print(f"{YELLOW}ssh: reading {remote_path} failed with exit "
              f"{result.returncode}: {result.stderr.strip() or '(no stderr)'}"
              f"{RESET}", file=sys.stderr)
        return None
    return result.stdout


def fetch_jsonl_for_date(target_date: date) -> "list[dict[str, Any]] | None":
    """Entries for one day, `[]` for a genuinely empty day, None if unreachable.

    A line that will not parse is SKIPPED and COUNTED, and the count is printed
    to stderr. It used to be skipped in silence, which made every number this
    report prints a lower bound that read as a total: "Total emails classified:
    N" is the headline, the tier splits and the tuning thresholds are all
    derived from the surviving rows, and a dropped row moved every one of them
    with nothing to say so. This report already distinguishes an UNREACHABLE day
    from a quiet one, loudly and in red, for the same reason.

    The wording follows `corpus_bytes` in scripts/census.py, which is the
    in-repo precedent for a count that had to degrade: name the number that
    vanished and say the total below is a lower bound.
    """
    remote_path = f"{VM_STATE_DIR}/log-{target_date.isoformat()}.jsonl"
    raw = ssh_read(remote_path)
    if raw is None:
        return None
    if not raw:
        return []
    entries = []
    unparseable = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            unparseable += 1
    if unparseable:
        print(f"{YELLOW}warning: {unparseable} unparseable line(s) skipped in "
              f"log-{target_date.isoformat()}.jsonl; every count derived from "
              f"that day is a LOWER bound{RESET}", file=sys.stderr)
    return entries


def fetch_state_json() -> dict[str, Any]:
    """Fetch state.json from the VM. Returns empty dict on failure."""
    raw = ssh_read(VM_STATE_FILE)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


# ===========================================================================
# YAML config parsing (local)
# ===========================================================================


def load_yaml_overrides(workspace_root: Path) -> dict[str, set[str]]:
    """Parse email-triage-rules.yaml and return override sets.

    Returns dict with keys:
      "always_critical", "always_important", "always_normal"
    Each value is a set of raw pattern strings (e.g. "*@noreply.com").
    """
    yaml_path = get_data_config_dir() / "email-triage-rules.yaml"  # config-DATA -> data root
    result: dict[str, set[str]] = {
        "always_critical": set(),
        "always_important": set(),
        "always_normal": set(),
    }
    if not yaml_path.exists():
        return result
    text = yaml_path.read_text(encoding="utf-8")
    current_key: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("always_critical:"):
            current_key = "always_critical"
            continue
        if stripped.startswith("always_important:"):
            current_key = "always_important"
            continue
        if stripped.startswith("always_normal:"):
            current_key = "always_normal"
            continue
        if current_key and stripped.startswith("- "):
            val = stripped[2:].strip().strip('"').strip("'")
            result[current_key].add(val)
            continue
        if stripped and not stripped.startswith("#") and ":" in stripped and not stripped.startswith("-"):
            current_key = None
    return result


def _domain_in_yaml(domain: str, yaml_overrides: dict[str, set[str]]) -> bool:
    """Return True if domain matches any pattern in any YAML override list."""
    for patterns in yaml_overrides.values():
        for pat in patterns:
            if _pattern_matches_domain(pat, domain):
                return True
    return False


def _pattern_matches_domain(pattern: str, domain: str) -> bool:
    """Whether a YAML sender pattern covers EVERY sender at `domain`.

    The daemon matches full addresses with `fnmatch` (`RulesEngine.match_sender`),
    so the right side of a pattern is a glob, not a literal. This used to read
    it as a literal and answer with `==` / `endswith`, which made the two
    components disagree about the same YAML file:

      - `*@*` returned False here and matched every address there. A catch-all
        rule therefore left every domain listed under "Unknown domains" with an
        "Add to `always_normal`" suggestion beside it -- for traffic the daemon
        was already classifying under that very rule. The function even carried
        an explicit `if pattern == "*@*": return True`, unreachable behind the
        `startswith("*@")` branch above it, so the intent was never in doubt.
      - `*@*.com` was the same defect one step narrower.

    A pattern whose LEFT side is not `*` covers one sender, never a domain:
    `alice@example.com` says nothing about the other people at example.com, and
    `newsletter@*` covers one local part across every domain. Both stay False,
    which is deliberate (see `_domain_matches_any`).

    KNOWN DIVERGENCE, left as it was: `*@example.com` is treated as covering
    `mail.example.com` here, and the daemon's `fnmatch` does not. That predates
    this fix and is unchanged by it, so a subdomain sender can still be
    suppressed from the unknown-domains section by a rule that will not fire
    on it.
    """
    from fnmatch import fnmatch

    pattern = pattern.lower()
    domain = domain.lower()
    # Bare domain, or an exact pattern that IS the domain.
    if pattern == domain:
        return True
    # Split at the FIRST `@`, because that is where `_domain_of` in the daemon
    # splits an address (`addr.split("@", 1)[1]`). Splitting at the last one
    # instead disagrees with it about any address carrying two, which is what
    # `sender_domain` in the log would then hold.
    #
    # `local != "*"` also rejects a pattern with no `@` at all: `partition`
    # puts the WHOLE pattern in `local` when the separator is absent, so
    # `example.com` fails that test and returns False right here.
    #
    # `not pat_domain` is the explicit refusal for `"*"` and `"*@"`, which are
    # the two patterns that pass the `local != "*"` test above with nothing
    # after the separator. MEASURED with the guard removed: both still answer
    # False, because `fnmatch(domain, "")` is False and so is
    # `fnmatch(domain, "*.")`. So it is a stated refusal rather than the only
    # one, and deleting it changes no answer this function gives today.
    #
    # This comment used to name `rpartition`, which is not the call below it,
    # and to call the second test unreachable, which it is not. Both splitters
    # reject the same inputs here, so nothing ever misbehaved; the note was
    # simply describing different code from the code it sat on.
    local, _, pat_domain = pattern.partition("@")
    if local != "*" or not pat_domain:
        return False
    return fnmatch(domain, pat_domain) or fnmatch(domain, f"*.{pat_domain}")


# ===========================================================================
# CRM contact parsing (local)
# ===========================================================================


def load_known_crm_domains(workspace_root: Path) -> set[str]:
    """Every email domain the CRM knows about.

    Reads through `contact_index_by_email`, the shared CRM reader, so both
    card schemas resolve: an inline `email:` on the card, and the entity form
    (`entity_ref` plus `crm/address-book/<slug>.md::canonical_email`) that
    `/crm` and the migration actually write.

    This function used to scan the frontmatter text for a line beginning
    `email:`, so it was blind to the entity form in exactly the same way
    `inbox_pulse/rules.py` was. That mattered more here than anywhere: this set
    IS the report's safety net. Its "LOW items from known good domains
    (potential false negatives)" section exists to surface a contact the
    classifier under-scored, and a domain missing from this set is instead filed
    under "Unknown domains" with an `Add to always_normal` tuning suggestion
    beside it. So the report would have advised the operator to permanently
    suppress a customer that the classifier had already failed to recognise.
    Two blind readers, one of them the check on the other.

    Measured on the operator's tree 2026-08-29: 89 addresses reachable by the
    old text scan, 148 by the shared reader.

    The earlier fix here is preserved by the shared reader, which parses
    frontmatter properly rather than assuming a three-character fence: a
    contact whose fence carried a trailing space or a tab contributed no domain
    at all, in a report whose whole subject is known-versus-unknown.
    """
    domains: set[str] = set()
    for address in contact_index_by_email():
        if "@" not in address:
            continue
        domain = address.split("@", 1)[1].strip()
        if domain:
            domains.add(domain)
    return domains


# ===========================================================================
# Aggregation
# ===========================================================================


def extract_triggers(entry: dict[str, Any]) -> str:
    """Build a human-readable trigger string from reason_breakdown."""
    breakdown = entry.get("reason_breakdown", {}) or {}
    parts = []
    sender_ov = breakdown.get("sender_override")
    if sender_ov:
        parts.append(f"sender_override={sender_ov}")
    keyword_ov = breakdown.get("keyword_override")
    if keyword_ov:
        parts.append(f"keyword_override={keyword_ov}")
    for key in ("crm_contact", "pipeline", "threads", "calendar", "time_sensitivity"):
        val = breakdown.get(key, 0)
        if val:
            parts.append(f"{key}={val}")
    return ", ".join(parts) if parts else "-"


def _any_breakdown_fired(entry: dict[str, Any]) -> bool:
    """Return True if any reason_breakdown signal is non-zero / non-null."""
    breakdown = entry.get("reason_breakdown", {}) or {}
    for val in breakdown.values():
        if val:
            return True
    return False


def aggregate(
    entries: list[dict[str, Any]],
    today: date,
    days: int,
    all_entries_by_date: dict[date, list[dict[str, Any]]],
    known_crm_domains: set[str],
    yaml_overrides: dict[str, set[str]],
) -> dict[str, Any]:
    """Aggregate entries into report data structure."""
    by_tier: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in entries:
        tier = e.get("tier_guess", TIER_LOW)
        by_tier[tier].append(e)

    total = len(entries)
    high = by_tier[TIER_HIGH]
    maybe = by_tier[TIER_MAYBE]
    low = by_tier[TIER_LOW]

    # "Known good but LOW" - domain is in CRM or YAML always_critical/always_important,
    # but scored LOW. Excludes always_normal (those SHOULD score LOW by design).
    # "All known" for deduplication purposes (suppresses unknown-domain section).
    always_normal_pats = yaml_overrides["always_normal"]
    priority_pats = (
        yaml_overrides["always_critical"] | yaml_overrides["always_important"]
    )

    def _priority_known(d: str) -> bool:
        return d in known_crm_domains or _domain_matches_any(d, priority_pats)

    def _known(d: str) -> bool:
        return d in known_crm_domains or _domain_in_yaml(d, yaml_overrides)

    known_good_low: dict[str, dict[str, Any]] = {}
    for e in low:
        domain = e.get("sender_domain", "").lower()
        # Only flag if domain is "priority known" (CRM or critical/important overrides)
        # Skip if it's in always_normal (expected to be LOW).
        if domain and _priority_known(domain) and not _domain_matches_any(
                domain, always_normal_pats):
            if domain not in known_good_low:
                known_good_low[domain] = {"count": 0, "last_ts": ""}
            known_good_low[domain]["count"] += 1
            ts = e.get("ts", "")
            if ts > known_good_low[domain]["last_ts"]:
                known_good_low[domain]["last_ts"] = ts

    # Unknown domains (not in CRM, not in YAML at all)
    all_domains_in_window: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "tiers": defaultdict(int)}
    )
    for e in entries:
        domain = e.get("sender_domain", "").lower()
        if not domain:
            continue
        if not _known(domain):
            all_domains_in_window[domain]["count"] += 1
            tier = e.get("tier_guess", TIER_LOW)
            all_domains_in_window[domain]["tiers"][tier] += 1

    # Top 10 unknown by count
    top_unknown = sorted(
        all_domains_in_window.items(),
        key=lambda kv: kv[1]["count"],
        reverse=True,
    )[:10]

    # YAML tuning suggestions
    suggestions = _compute_suggestions(
        low=low,
        known_crm_domains=known_crm_domains,
        yaml_overrides=yaml_overrides,
    )

    # 7-day distribution (for trend section)
    daily_dist = _compute_daily_distribution(all_entries_by_date, today)

    return {
        "total": total,
        "high": high,
        "maybe": maybe,
        "low": low,
        "known_good_low": known_good_low,
        "top_unknown": top_unknown,
        "suggestions": suggestions,
        "daily_dist": daily_dist,
    }


def _domain_matches_any(domain: str, patterns: "set[str]") -> bool:
    """True when `domain` is covered at DOMAIN level by any of `patterns`.

    One matcher, `_pattern_matches_domain`, decides this everywhere. It
    replaces `_yaml_domain_set`, which built a set of "domains" by splitting
    each pattern at its `@` and keeping the right side, and so disagreed with
    the matcher about the same YAML file. `alice@example.com` became the domain
    `example.com`, which made every OTHER sender at example.com count as
    YAML-covered: they were listed as false-negative candidates and suppressed
    from the unknown-domains section on the strength of a rule naming one
    person. `newsletter@*` contributed the literal domain `"*"`.

    A left-side pattern (`newsletter@*`) still does not make its senders'
    domains known, and that is deliberate rather than an oversight: it covers
    one local part across every domain and says nothing about the other senders
    at any of them. Calling those domains covered would hide real unknown
    traffic, and the report's suggestion to add `*@domain` is a WIDENING of
    that rule, not a duplicate of it.
    """
    return any(_pattern_matches_domain(pat, domain) for pat in patterns)


def _compute_suggestions(
    low: list[dict[str, Any]],
    known_crm_domains: set[str],
    yaml_overrides: dict[str, set[str]],
) -> list[str]:
    """Produce YAML tuning suggestion strings."""
    # Count LOW entries per domain, track breakdown signal
    domain_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "any_signal": False}
    )
    for e in low:
        domain = e.get("sender_domain", "").lower()
        if not domain:
            continue
        domain_stats[domain]["count"] += 1
        if _any_breakdown_fired(e):
            domain_stats[domain]["any_signal"] = True

    suggestions = []
    for domain, stats in domain_stats.items():
        count = stats["count"]
        any_signal = stats["any_signal"]
        in_yaml = _domain_in_yaml(domain, yaml_overrides)
        in_crm = domain in known_crm_domains

        # Skip if already in any YAML override list
        if in_yaml:
            continue

        # Suggest always_normal: high volume LOW with zero breakdown signal
        if count >= SUGGEST_ALWAYS_NORMAL_MIN_ENTRIES and not any_signal and not in_crm:
            suggestions.append(
                f"**Add to `always_normal`:** `*@{domain}` - "
                f"{count} LOW items, weight always 0 - looks like newsletter or automated traffic."
            )

        # Suggest checking CRM miss: known via CRM but classified LOW
        if count >= SUGGEST_CRM_KNOWN_LOW_MIN_ENTRIES and in_crm and not any_signal:
            suggestions.append(
                f"**Promote to `always_important`:** `*@{domain}` - "
                f"{count} LOW items but domain appears in CRM - check if classifier missed a signal."
            )

    return suggestions


def _compute_daily_distribution(
    all_entries_by_date: dict[date, list[dict[str, Any]]],
    today: date,
) -> dict[str, Any]:
    """Compute today + the mean over past days that carried mail.

    The divisor is the count of NON-EMPTY past days, and that is deliberate:
    a day this report holds nothing for is absent data, not a measured zero
    (`fetch_jsonl_for_date` answers `[]` both for a log file that is missing
    and for one that exists and is empty, so the two cannot be told apart
    here). Averaging silent days in would halve every reported figure after
    one quiet weekend and the operator would read that as a drop in volume.
    `test_a_silent_day_inside_a_working_window_does_not_enter_the_divisor` and
    its counter-test `test_a_day_with_fewer_entries_does_enter_the_divisor`
    pin both directions.

    What follows from that, and what the report used to get wrong: the mean is
    then per ACTIVE day, never per calendar day, so a column headed "7-day avg"
    was naming a denominator it did not use. `days_in_avg` is returned so the
    caller can state the real one instead of implying seven.
    """
    today_entries = all_entries_by_date.get(today, [])
    today_counts = {TIER_HIGH: 0, TIER_MAYBE: 0, TIER_LOW: 0}
    for e in today_entries:
        tier = e.get("tier_guess", TIER_LOW)
        if tier in today_counts:
            today_counts[tier] += 1

    # 7-day avg (from all available data, max 7 days)
    past_7 = [
        all_entries_by_date.get(today - timedelta(days=i), [])
        for i in range(1, 8)
    ]
    past_7_non_empty = [d for d in past_7 if d]
    if past_7_non_empty:
        avg_counts = {}
        for tier in (TIER_HIGH, TIER_MAYBE, TIER_LOW):
            totals = [
                sum(1 for e in day_entries if e.get("tier_guess") == tier)
                for day_entries in past_7_non_empty
            ]
            avg_counts[tier] = sum(totals) / len(past_7_non_empty)
    else:
        avg_counts = None

    def trend(today_val: int, avg_val: float | None) -> str:
        if avg_val is None:
            return "-"
        if avg_val == 0:
            return "up" if today_val > 0 else "="
        if today_val > 1.5 * avg_val:
            return "up"
        if today_val < 0.5 * avg_val:
            return "dn"
        return "="

    result = {
        "today": today_counts,
        "avg": avg_counts,
        "days_in_avg": len(past_7_non_empty),
        "trend": {
            tier: trend(today_counts[tier], avg_counts[tier] if avg_counts else None)
            for tier in (TIER_HIGH, TIER_MAYBE, TIER_LOW)
        },
        "has_7day": avg_counts is not None,
    }
    return result


# ===========================================================================
# Report rendering
# ===========================================================================


def _fmt_time(ts: str) -> str:
    """Extract HH:MM from an ISO timestamp string."""
    if not ts:
        return "-"
    # "2026-05-28T23:35:01.138011+04:00" -> "23:35"
    try:
        time_part = ts.split("T", 1)[1][:5]
        return time_part
    except (IndexError, AttributeError):
        return ts[:5] if len(ts) >= 5 else ts


def _pct(n: int, total: int) -> str:
    if total == 0:
        return "0%"
    return f"{100 * n / total:.0f}%"


def _tier_table_rows(entries: list[dict[str, Any]], max_rows: int = 50) -> str:
    """Render markdown table rows for HIGH_LIKELY or MAYBE tiers."""
    if not entries:
        return ""
    # One sort, weight descending then timestamp descending. There were two:
    # the first negated the weight and then also had to be undone, so its
    # result was overwritten by the second on the very next statement without
    # ever being read, and the comment between them ("sort again properly")
    # described the overwrite rather than removing it. Dead as of 2026-09-01 --
    # MEASURED by deleting it, with every test over this function green -- and
    # it cost a full extra sort of the entry list on every tier table.
    sorted_entries = sorted(
        entries,
        key=lambda e: (e.get("weight", 0), e.get("ts", "") or ""),
        reverse=True,
    )
    rows = []
    for e in sorted_entries[:max_rows]:
        ts = _fmt_time(e.get("ts", ""))
        domain = e.get("sender_domain", "-")
        weight = e.get("weight", "-")
        triggers = extract_triggers(e)
        rows.append(f"| {ts} | {domain} | {weight} | {triggers} |")
    # The cap is fine; the silence was not. The section headings above carry no
    # count, so a table cut at 50 read as the complete set: on a `--days 7` run,
    # which this script's own docstring advertises, the rows past the cap simply
    # were not there and nothing said a word. The note is a table row so it
    # cannot be lost when the block is pasted somewhere else, and it names the
    # sort so a reader knows WHICH rows went: the lowest-weighted ones.
    dropped = len(sorted_entries) - len(rows)
    if dropped:
        rows.append(f"| ... | _{dropped} more row(s) below the top {max_rows} "
                    f"by weight are not shown_ | | |")
    return "\n".join(rows)


def render_report(
    agg: dict[str, Any],
    today: date,
    days: int,
    window_start: date,
    state_json: dict[str, Any],
    entries_total_in_window: int,
    unreachable: "list[date] | None" = None,
) -> str:
    """Render the full markdown report.

    `unreachable` is the in-window days whose log could not be read. It has to
    reach the FILE, not only stderr and the exit code: this markdown is written
    to `outputs/operations/inbox-pulse/`, opened in VS Code and archived, and
    without it the artifact stated "Total emails classified: N" and, when today
    was the unread day, "No emails classified today." A later reader, or a
    scheduler that files the report without propagating the exit code, was
    handed exactly the quiet-inbox misreading the `ssh_read` redesign exists to
    prevent.
    """
    unreachable = sorted(unreachable or [])
    high = agg["high"]
    maybe = agg["maybe"]
    low = agg["low"]
    total = agg["total"]
    suggestions = agg["suggestions"]
    known_good_low = agg["known_good_low"]
    top_unknown = agg["top_unknown"]
    daily_dist = agg["daily_dist"]

    today_dist = daily_dist["today"]
    avg_dist = daily_dist.get("avg")
    trend = daily_dist["trend"]
    has_7day = daily_dist["has_7day"]

    heartbeat = state_json.get("last_heartbeat", "unknown")
    daemon_pid = state_json.get("daemon_pid", "unknown")

    # --- "At a glance" uses today's numbers only
    today_total = sum(today_dist.values())
    high_today = today_dist[TIER_HIGH]
    maybe_today = today_dist[TIER_MAYBE]
    low_today = today_dist[TIER_LOW]

    window_label = (
        f"last {days} day{'s' if days != 1 else ''} ({window_start} to {today})"
    )

    lines = []

    # Header
    lines.append(f"# Inbox Pulse shadow report -- {today}")
    lines.append(f"Window: {window_label}")
    lines.append(f"Total emails classified: {total}")
    if unreachable:
        lines.append("")
        lines.append(
            f"**Incomplete coverage: {len(unreachable)} day(s) in this window "
            f"could not be read from the classifier host "
            f"({', '.join(d.isoformat() for d in unreachable)}). The total "
            f"above EXCLUDES them, so it is a lower bound. This is not a quiet "
            f"inbox.**"
        )
    lines.append("")

    # At a glance
    lines.append("## At a glance -- today")
    lines.append("")
    if today in unreachable:
        # Never "No emails classified today" for a day nobody could read.
        lines.append(
            "Today's log could not be read from the classifier host, so the "
            "numbers below are unknown rather than zero."
        )
    elif today_total == 0:
        lines.append("No emails classified today.")
    else:
        lines.append(
            f"HIGH_LIKELY: {high_today} ({_pct(high_today, today_total)})"
        )
        lines.append(
            f"MAYBE: {maybe_today} ({_pct(maybe_today, today_total)})"
        )
        lines.append(
            f"LOW: {low_today} ({_pct(low_today, today_total)})"
        )
    lines.append("")
    lines.append(f"Heartbeat: {heartbeat}")
    lines.append(f"Daemon PID: {daemon_pid}")
    lines.append("")

    # HIGH_LIKELY
    lines.append(f"## HIGH_LIKELY items (window: {window_label})")
    lines.append("")
    if not high:
        lines.append("No HIGH_LIKELY items in window.")
    else:
        lines.append("| Time | Sender domain | Weight | Triggers |")
        lines.append("|---|---|---|---|")
        lines.append(_tier_table_rows(high))
    lines.append("")

    # MAYBE
    lines.append(f"## MAYBE items (window: {window_label})")
    lines.append("")
    if not maybe:
        lines.append("No MAYBE items in window.")
    else:
        lines.append("| Time | Sender domain | Weight | Triggers |")
        lines.append("|---|---|---|---|")
        lines.append(_tier_table_rows(maybe))
    lines.append("")

    # Known good but LOW
    lines.append(f"## LOW items from known good domains (potential false negatives)")
    lines.append("")
    lines.append(
        "Known good = domain appears in CRM contact files or in "
        "`config/email-triage-rules.yaml` sender_overrides."
    )
    lines.append("")
    if not known_good_low:
        lines.append("No known-good domains classified LOW in this window.")
    else:
        lines.append("| Sender domain | Count | Last seen |")
        lines.append("|---|---|---|")
        for domain, info in sorted(
            known_good_low.items(), key=lambda kv: kv[1]["count"], reverse=True
        ):
            last_seen = _fmt_time(info["last_ts"])
            lines.append(f"| {domain} | {info['count']} | {last_seen} |")
    lines.append("")

    # YAML tuning suggestions
    lines.append("## YAML tuning suggestions")
    lines.append("")
    lines.append("Based on patterns in the window:")
    lines.append("")
    if not suggestions:
        lines.append(
            "No suggestions today -- classifier appears well-tuned for current patterns."
        )
    else:
        for s in suggestions:
            lines.append(f"- {s}")
    lines.append("")

    # Unknown domains
    lines.append("## Unknown domains (not in CRM, not in YAML)")
    lines.append("")
    lines.append(
        "Top 10 unfamiliar domains in the window. These are candidates to classify:"
    )
    lines.append("")
    if not top_unknown:
        lines.append("No unknown domains in this window.")
    else:
        lines.append("| Sender domain | Count | Top tier_guess |")
        lines.append("|---|---|---|")
        for domain, info in top_unknown:
            count = info["count"]
            tiers_dict = info["tiers"]
            top_tier = max(tiers_dict, key=lambda t: tiers_dict[t]) if tiers_dict else "-"
            lines.append(f"| {domain} | {count} | {top_tier} |")
    lines.append("")

    # Raw distribution
    lines.append("## Raw distribution")
    lines.append("")
    trend_symbols = {"up": "up", "dn": "dn", "=": "~", "-": "-"}
    if has_7day and avg_dist:
        # The column said "7-day avg" and the divisor is the number of past
        # days that CARRIED MAIL, which is at most seven and routinely fewer.
        # `_compute_daily_distribution` excludes silent days on purpose (a day
        # with no log is absent data, not a measured zero), so the arithmetic
        # is right and the heading was the part that lied: six quiet days plus
        # one busy one reported that busy day's count as the "7-day avg". Name
        # the real denominator instead of implying a calendar week.
        days_in_avg = daily_dist.get("days_in_avg", 0)
        lines.append("| Tier | Today | Avg per active day | Trend |")
        lines.append("|---|---|---|---|")
        for tier in (TIER_HIGH, TIER_MAYBE, TIER_LOW):
            t = today_dist[tier]
            a = avg_dist[tier]
            tr = trend_symbols.get(trend[tier], "-")
            lines.append(f"| {tier} | {t} | {a:.1f} | {tr} |")
        lines.append("")
        lines.append(
            f"_Averaged over the {days_in_avg} of the past 7 day(s) that "
            f"carried mail. Days the daemon logged nothing for are left out "
            f"of the divisor, so this is a per-active-day mean and not a "
            f"per-calendar-day one._"
        )
    else:
        lines.append("| Tier | Today |")
        lines.append("|---|---|")
        for tier in (TIER_HIGH, TIER_MAYBE, TIER_LOW):
            lines.append(f"| {tier} | {today_dist[tier]} |")
        if not has_7day:
            lines.append("")
            lines.append(
                "_7-day averages not available: the past 7 days' logs were "
                "empty or could not be read. `--days 7` does not change this -- "
                "those days are fetched for the average whatever `--days` says._"
            )
    lines.append("")

    # How to act
    lines.append("## How to act on this")
    lines.append("")
    lines.append("1. Edit `config/email-triage-rules.yaml` (locally, in ceo-main)")
    lines.append(
        "2. Run `python scripts/publish-service.py --push` to ship the change"
    )
    lines.append(
        "3. VM picks up rules within 30 seconds (auto-reload via mtime check, no restart needed)"
    )
    lines.append("4. Re-run this report tomorrow to verify the change took effect.")
    lines.append("")

    return "\n".join(lines)


# ===========================================================================
# Main
# ===========================================================================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inbox Pulse shadow-mode observation CLI."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=1,
        metavar="N",
        help="Number of days to include (default 1 - today only).",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Skip opening the report in VS Code.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    days = max(1, args.days)
    today = datetime.now(get_default_tz()).date()
    window_start = today - timedelta(days=days - 1)

    workspace_root = get_workspace_root()

    print(f"{CYAN}{BOLD}Inbox Pulse shadow report{RESET}")
    print(f"{GRAY}Fetching {days} day(s) of JSONL from {VM_HOST}...{RESET}")

    # Fetch all entries
    all_entries_by_date: dict[date, list[dict[str, Any]]] = {}
    all_entries: list[dict[str, Any]] = []
    # Two lists, because they mean different things to the reader. A day
    # inside --days was meant to be counted and is missing from the totals; a
    # day fetched only to compute the 7-day average never entered the totals at
    # all. One list said "The counts above EXCLUDE those days" about both,
    # which is false for the second kind and turned a transient SSH blip six
    # days back into a red coverage warning about a window it was never in.
    unreachable: list[date] = []
    unreachable_avg_only: list[date] = []
    for i in range(days):
        target = today - timedelta(days=i)
        print(f"  {GRAY}SSH cat log-{target}.jsonl ...{RESET}", end=" ", flush=True)
        day_entries = fetch_jsonl_for_date(target)
        if day_entries is None:
            unreachable.append(target)
            all_entries_by_date[target] = []
            print(f"{RED}UNREACHABLE{RESET}")
            continue
        all_entries_by_date[target] = day_entries
        all_entries.extend(day_entries)
        print(f"{GREEN}{len(day_entries)} entries{RESET}")

    # Fetch whatever the 7-day average still needs. `_compute_daily_distribution`
    # averages today-1 through today-7 inclusive, so the range ends at 8, not 7.
    # It ended at 7, so today-7 was never fetched: the lookup returned `[]` and
    # the non-empty filter dropped it without a word. The column labelled
    # "7-day avg" was a 6-day average, and `--days 7` did not rescue it -- the
    # old `if days < 7` guard skipped this loop entirely at exactly that value.
    # The range is empty once `--days` already covers the whole span.
    for i in range(days, 8):
        target = today - timedelta(days=i)
        # Dead as written -- this range begins exactly where the window loop
        # ended, so it can never revisit a fetched day. Left in place (it
        # predates this fix) rather than removed, and named so the next reader
        # does not mistake it for a live guard.
        if target not in all_entries_by_date:
            day_entries = fetch_jsonl_for_date(target)
            if day_entries is None:
                unreachable_avg_only.append(target)
                day_entries = []
            all_entries_by_date[target] = day_entries

    # Fetch state.json
    print(f"  {GRAY}SSH cat state.json ...{RESET}", end=" ", flush=True)
    state_json = fetch_state_json()
    print(f"{GREEN}ok{RESET}" if state_json else f"{YELLOW}not found{RESET}")

    # Load local context
    print(f"{GRAY}Loading local YAML + CRM...{RESET}")
    yaml_overrides = load_yaml_overrides(workspace_root)
    known_crm_domains = load_known_crm_domains(workspace_root)
    print(
        f"  {GRAY}YAML overrides: {sum(len(v) for v in yaml_overrides.values())} patterns. "
        f"CRM domains: {len(known_crm_domains)}{RESET}"
    )

    # Aggregate
    agg = aggregate(
        entries=all_entries,
        today=today,
        days=days,
        all_entries_by_date=all_entries_by_date,
        known_crm_domains=known_crm_domains,
        yaml_overrides=yaml_overrides,
    )

    # Render
    report_md = render_report(
        agg=agg,
        today=today,
        days=days,
        window_start=window_start,
        state_json=state_json,
        entries_total_in_window=len(all_entries),
        unreachable=unreachable,
    )

    # Write output
    out_dir = get_outputs_dir() / "operations" / "inbox-pulse"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{today.isoformat()}_shadow-report.md"
    out_path.write_text(report_md, encoding="utf-8")
    print(f"{GREEN}Report written:{RESET} {out_path}")

    # Hidden character scan
    scan_result = subprocess.run(
        [sys.executable, str(workspace_root / "scripts" / "sanitize-text.py"),
         str(out_path), "--scan"],
        capture_output=True,
        text=True,
    )
    scan_out = scan_result.stdout.strip()
    scan_lower = scan_out.lower()
    if scan_result.returncode != 0 or (
        "hidden" in scan_lower and "no hidden" not in scan_lower and "0 hidden" not in scan_lower
    ):
        print(f"{YELLOW}Hidden char scan: {scan_out}{RESET}")
    else:
        print(f"{GREEN}Hidden char scan: clean{RESET}")

    # VS Code open
    if not args.no_open:
        try:
            subprocess.run(["code", str(out_path)], check=False, timeout=5)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass  # graceful degradation

    # Terminal summary
    high_count = len(agg["high"])
    maybe_count = len(agg["maybe"])
    low_count = len(agg["low"])
    total = agg["total"]
    suggestion_count = len(agg["suggestions"])

    print()
    print(f"{BOLD}Inbox Pulse shadow report -- {today}{RESET}")
    print(
        f"Window: {days} day{'s' if days != 1 else ''}  "
        f"{GRAY}.{RESET}  {total} emails  "
        f"{GRAY}.{RESET}  {RED}{high_count} HIGH_LIKELY{RESET}  "
        f"{GRAY}.{RESET}  {YELLOW}{maybe_count} MAYBE{RESET}  "
        f"{GRAY}.{RESET}  {low_count} LOW"
    )
    print(f"Tuning suggestions: {suggestion_count}")
    print(f"Report: {out_path}")

    if unreachable_avg_only:
        # Not an error: these days were never in the window, so no total is
        # missing anything. The trend section is what degrades.
        print(f"{YELLOW}{len(unreachable_avg_only)} day(s) outside the "
              f"--days window could not be read from {VM_HOST}: "
              f"{', '.join(d.isoformat() for d in sorted(unreachable_avg_only))}. "
              f"The 7-day average is computed over fewer days.{RESET}",
              file=sys.stderr)

    if unreachable:
        # Say what the report does NOT cover, and exit non-zero so a scheduler
        # cannot read a broken data path as a quiet inbox.
        print(f"{RED}{len(unreachable)} day(s) could not be read from {VM_HOST}: "
              f"{', '.join(d.isoformat() for d in sorted(unreachable))}.{RESET}",
              file=sys.stderr)
        print(f"{RED}The counts above EXCLUDE those days. This is not a quiet "
              f"inbox -- the host was unreachable.{RESET}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

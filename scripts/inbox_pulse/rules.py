"""Cheap classifier -- runs on every inbound email BEFORE any LLM call.

Aggregates signal weights from explicit YAML overrides, CRM contacts,
pipeline state, active threads, calendar, and time-sensitivity regex.
Returns a tier guess (HIGH_LIKELY / MAYBE / LOW) + breakdown of what
contributed. Phase 3 (Haiku) only runs on HIGH_LIKELY + MAYBE; LOW is
silently dropped to NORMAL tier.

Sovereignty discipline: this module reads emails but NEVER logs body,
subject text, or full sender address. Log entries (added by daemon, not
here) get sender_domain + subject_length only. This module's return dict
carries no email content -- just tier_guess + weight + reason_breakdown
strings.

Signals weighted:
- sender_override (always_critical -> HIGH_LIKELY immediately, +inf effective)
- sender_override (always_important -> contributes weight 3, no short-circuit)
- sender_override (always_normal -> LOW immediately, -inf effective)
- keyword_override (promote_to_critical -> HIGH_LIKELY immediately)
- keyword_override (promote_to_important -> +3)
- CRM contact with matching email -> +1 baseline
- CRM contact with relationship in {tribe, customer, investor, prospect} -> +2 additional
- pipeline.md mentions sender domain -> +2
- threads/business/*.md mentions sender (recent 30d) -> +1
- calendar event with sender (+-7d) -> +1
- time-sensitivity regex match -> +1

Aggregate:
- weight >= 4         -> HIGH_LIKELY
- weight 2 or 3       -> MAYBE
- weight 0 or 1       -> LOW
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Optional

import yaml

if TYPE_CHECKING:
    from scripts.inbox_pulse.overrides import RulesEngine


# Time-sensitivity regex -- matches urgent language in subject/body
_TIME_SENSITIVITY_RE = re.compile(
    r"\b(urgent|asap|eod|today|tomorrow|deadline|by\s+\w+day|by\s+end\s+of)\b",
    re.IGNORECASE,
)

# Time window for "recent" thread mentions
_RECENT_THREAD_DAYS = 30
# Time window for calendar events on either side of "now"
_CALENDAR_WINDOW_DAYS = 7

# Body preview cap -- limit how many chars we scan for keywords/time-sensitivity
_BODY_PREVIEW_CHARS = 500

# CRM relationship types that add the extra +2 bonus
_HIGH_VALUE_RELATIONSHIPS = frozenset({
    "tribe",
    "tribe-leadership",
    "customer",
    "investor",
    "investor-active",
    "investor-declined",
    "prospect",
    "prospect-partner",
})


def _short_circuit(tier: str, weight: int, marker: str) -> dict:
    """Build a short-circuit classification result dict.

    Used by the recipient-aware rule block in CheapClassifier.classify() to
    return early without running the 7-signal classifier.

    Args:
        tier:    "HIGH_LIKELY" | "MAYBE" | "LOW"
        weight:  Effective weight to report (99 for always-important, 0 for always-normal).
        marker:  Identifies which short-circuit branch fired, stored in
                 reason_breakdown["sender_override"]. Valid markers:
                   tl_to_important            -- internal TL sender + CEO in To
                   internal_nonlead_to_normal -- internal non-leadership sender + CEO in To
                   internal_cc_normal         -- internal sender (any role) + CEO in CC only
    """
    return {
        "tier_guess": tier,
        "weight": weight,
        "reason_breakdown": {
            "sender_override": marker,
            "keyword_override": None,
            "crm_contact": 0,
            "pipeline": 0,
            "threads": 0,
            "calendar": 0,
            "time_sensitivity": 0,
        },
    }


class CheapClassifier:
    """No-LLM classifier. Aggregates signals into a tier_guess + breakdown.

    Caller passes:
      rules:          RulesEngine instance (already loaded YAML)
      workspace_root: Path to the engine workspace root (config/ etc.)
      account:        Optional exchangelib Account for calendar lookups.
                      If None, calendar signal is skipped (returns 0).
      data_root:      Optional Path to the DATA root (crm/, threads/,
                      context/). HEADING OS engine/data split: CRM contacts,
                      pipeline, and threads are DATA, so they resolve under
                      ``data_root``. Defaults to ``workspace_root`` when not
                      supplied (identical on transitional ceo-main).

    All file system reads are read-only. CRM/pipeline/threads files are
    parsed lightly (regex + simple frontmatter lookup; no full markdown parse).
    """

    def __init__(
        self,
        rules: "RulesEngine",
        workspace_root: Path,
        account: Optional[Any] = None,
        my_email: Optional[str] = None,
        data_root: Optional[Path] = None,
    ) -> None:
        self.rules = rules
        self.workspace_root = workspace_root
        self.data_root = data_root if data_root is not None else workspace_root
        self.account = account
        self.my_email = my_email

    def classify(
        self,
        sender_email: str,
        subject: str,
        body_preview: str = "",
        now: Optional[datetime] = None,
        recipients_to: Optional[List[str]] = None,
        recipients_cc: Optional[List[str]] = None,
    ) -> dict:
        """Classify an inbound email into HIGH_LIKELY / MAYBE / LOW.

        Args:
            sender_email:   Full email address of sender (used internally for
                            lookup; NOT logged anywhere by this method).
            subject:        Email subject line (used for keyword + time-sensitivity).
            body_preview:   First ~500 chars of body (used for time-sensitivity).
                            Caller should truncate; this method enforces the cap.
            now:            Override "now" for testability. Defaults to datetime.now(UTC).
            recipients_to:  List of To recipient addresses (transient -- NOT stored).
                            Used by the Tribe-Leadership+To/CC rule only.
            recipients_cc:  List of Cc recipient addresses (transient -- NOT stored).
                            Used by the Tribe-Leadership+To/CC rule only.

        Returns:
            {
                "tier_guess": "HIGH_LIKELY" | "MAYBE" | "LOW",
                "weight": int,
                "reason_breakdown": {
                    "sender_override": "always_critical" | "always_important" | "always_normal" | None,
                    "keyword_override": "promote_to_critical" | "promote_to_important" | None,
                    "crm_contact": int,         # 0/1/3 (1 for match, +2 for tribe/cust/inv/prospect)
                    "pipeline": int,            # 0 or 2
                    "threads": int,             # 0 or 1
                    "calendar": int,            # 0 or 1
                    "time_sensitivity": int,    # 0 or 1
                },
            }
        """
        if now is None:
            now = datetime.now(timezone.utc)

        body_preview = body_preview[:_BODY_PREVIEW_CHARS] if body_preview else ""

        # 0. Recipient-aware rule (CEO directive 2026-05-29, extended from bbdfde5).
        # Applies ONLY when sender is internal (sender's domain in rules.internal_domains).
        # External senders bypass this entire block -- they use the 7-signal classifier.
        #
        # Internal sender + CEO in To  + Tribe Leadership   -> always_important (HIGH_LIKELY)
        # Internal sender + CEO in To  + NOT leadership     -> always_normal (LOW)
        # Internal sender + CEO in CC (and not To)          -> always_normal (LOW, role-independent)
        # Internal sender + CEO in neither (BCC/forward)    -> fall through
        #
        # Degrades gracefully (falls through) when my_email is unset, no recipient lists
        # are provided, internal_domains is empty, or sender domain is not internal.
        if self.my_email and (recipients_to or recipients_cc):
            sender_domain = sender_email.split("@")[-1].lower() if "@" in sender_email else ""
            internal_domains_lower = [d.lower() for d in self.rules.internal_domains]
            is_internal = bool(sender_domain and sender_domain in internal_domains_lower)

            if is_internal:
                my_email_lower = self.my_email.lower()
                in_to = any(addr.lower() == my_email_lower for addr in (recipients_to or []))
                in_cc = any(addr.lower() == my_email_lower for addr in (recipients_cc or []))

                if in_to:
                    relationship = self._lookup_relationship_type(sender_email)
                    is_tl = bool(relationship and "tribe-leadership" in relationship.lower())
                    if is_tl:
                        return _short_circuit("HIGH_LIKELY", 99, "tl_to_important")
                    else:
                        return _short_circuit("LOW", 0, "internal_nonlead_to_normal")

                if in_cc:
                    return _short_circuit("LOW", 0, "internal_cc_normal")
        # else: not internal OR sender domain is empty OR my_email unset
        #        OR neither in_to nor in_cc -> fall through to existing classifier

        breakdown: dict = {
            "sender_override": None,
            "keyword_override": None,
            "crm_contact": 0,
            "pipeline": 0,
            "threads": 0,
            "calendar": 0,
            "time_sensitivity": 0,
        }

        # 1. Sender overrides take absolute precedence
        sender_match = self.rules.match_sender(sender_email)
        breakdown["sender_override"] = sender_match
        if sender_match == "always_critical":
            return {"tier_guess": "HIGH_LIKELY", "weight": 99, "reason_breakdown": breakdown}
        if sender_match == "always_normal":
            return {"tier_guess": "LOW", "weight": 0, "reason_breakdown": breakdown}
        # always_important does NOT short-circuit -- it acts as a weight (3)

        # 2. Keyword overrides -- promote_to_critical short-circuits to HIGH_LIKELY
        keyword_match = self.rules.match_keywords(subject, body_preview)
        breakdown["keyword_override"] = keyword_match
        if keyword_match == "promote_to_critical":
            return {"tier_guess": "HIGH_LIKELY", "weight": 99, "reason_breakdown": breakdown}

        weight = 0

        # sender_override = always_important adds weight 3
        if sender_match == "always_important":
            weight += 3

        # keyword_override = promote_to_important adds weight 3
        if keyword_match == "promote_to_important":
            weight += 3

        # 3. CRM contact lookup
        crm_score = self._score_crm_contact(sender_email)
        breakdown["crm_contact"] = crm_score
        weight += crm_score

        # 4. Pipeline.md domain mention
        pipeline_score = self._score_pipeline(sender_email)
        breakdown["pipeline"] = pipeline_score
        weight += pipeline_score

        # 5. Active threads
        threads_score = self._score_threads(sender_email, now)
        breakdown["threads"] = threads_score
        weight += threads_score

        # 6. Calendar (only if account provided)
        if self.account is not None:
            calendar_score = self._score_calendar(sender_email, now)
            breakdown["calendar"] = calendar_score
            weight += calendar_score

        # 7. Time-sensitivity regex
        if _TIME_SENSITIVITY_RE.search(subject) or (
            body_preview and _TIME_SENSITIVITY_RE.search(body_preview)
        ):
            breakdown["time_sensitivity"] = 1
            weight += 1

        # Aggregate to tier
        if weight >= 4:
            tier = "HIGH_LIKELY"
        elif weight >= 2:
            tier = "MAYBE"
        else:
            tier = "LOW"

        return {
            "tier_guess": tier,
            "weight": weight,
            "reason_breakdown": breakdown,
        }

    # ------------------------------------------------------------------
    # Per-signal helpers
    # ------------------------------------------------------------------

    def _lookup_relationship_type(self, sender_email: str) -> Optional[str]:
        """Return the relationship_type from the first CRM contact matching sender_email.

        Walks crm/contacts/*.md and parses YAML frontmatter. Match is case-insensitive.
        Returns None if no contact file matches or the field is absent.

        Kept cheap: same light frontmatter walk as _score_crm_contact; no caching needed
        because this short-circuits before the rest of classify() runs.
        """
        contacts_dir = self.data_root / "crm" / "contacts"
        if not contacts_dir.is_dir():
            return None

        addr_lower = sender_email.lower()

        for md_file in contacts_dir.glob("*.md"):
            frontmatter = _extract_frontmatter(md_file)
            if frontmatter is None:
                continue

            contact_email = frontmatter.get("email", "")
            if not contact_email:
                continue

            if str(contact_email).lower() == addr_lower:
                relationship = frontmatter.get("relationship_type", "")
                return str(relationship) if relationship else None

        return None

    def _score_crm_contact(self, sender_email: str) -> int:
        """Walk crm/contacts/*.md, look for matching email in frontmatter.

        Returns 0 if no contact file matches.
        Returns 1 if a contact file's email field matches (case-insensitive).
        Returns 3 if matched AND contact's relationship_type is in the
            high-value set (tribe, tribe-leadership, customer, investor,
            investor-active, prospect, prospect-partner, etc.).
        """
        contacts_dir = self.data_root / "crm" / "contacts"
        if not contacts_dir.is_dir():
            return 0

        addr_lower = sender_email.lower()

        for md_file in contacts_dir.glob("*.md"):
            frontmatter = _extract_frontmatter(md_file)
            if frontmatter is None:
                continue

            contact_email = frontmatter.get("email", "")
            if not contact_email:
                continue

            if str(contact_email).lower() == addr_lower:
                relationship = str(frontmatter.get("relationship_type", "")).lower()
                if relationship in _HIGH_VALUE_RELATIONSHIPS:
                    return 3  # 1 baseline + 2 bonus
                return 1

        return 0

    def _score_pipeline(self, sender_email: str) -> int:
        """Check context/pipeline.md for sender's domain mention.

        Returns 2 if found, 0 if not. File-not-found returns 0.
        """
        pipeline_path = self.data_root / "context" / "pipeline.md"
        if not pipeline_path.is_file():
            return 0

        domain = sender_email.split("@")[-1].lower()
        text = pipeline_path.read_text(encoding="utf-8", errors="replace").lower()
        if re.search(r"\b" + re.escape(domain) + r"\b", text):
            return 2
        return 0

    def _score_threads(self, sender_email: str, now: datetime) -> int:
        """Scan threads/business/*.md frontmatter for active threads mentioning sender.

        Returns 1 if any thread file mentions sender's email/domain AND its
        last_touched field is within the last _RECENT_THREAD_DAYS days.
        Returns 0 otherwise.

        Frontmatter has 'last_touched: YYYY-MM-DD'.
        """
        threads_dir = self.data_root / "threads" / "business"
        if not threads_dir.is_dir():
            return 0

        addr_lower = sender_email.lower()
        domain = sender_email.split("@")[-1].lower()
        cutoff = now - timedelta(days=_RECENT_THREAD_DAYS)

        for md_file in threads_dir.glob("*.md"):
            frontmatter = _extract_frontmatter(md_file)
            if frontmatter is None:
                continue

            last_touched_raw = frontmatter.get("last_touched", "")
            if not last_touched_raw:
                continue

            last_touched = _parse_date(str(last_touched_raw))
            if last_touched is None:
                continue

            if last_touched < cutoff:
                continue

            # Check if sender email or domain appears anywhere in the file body
            try:
                body = md_file.read_text(encoding="utf-8", errors="replace").lower()
            except OSError:
                continue

            if addr_lower in body or re.search(r"\b" + re.escape(domain) + r"\b", body):
                return 1

        return 0

    def _score_calendar(self, sender_email: str, now: datetime) -> int:
        """Look for calendar events with sender as attendee +-_CALENDAR_WINDOW_DAYS.

        Requires self.account (exchangelib Account). Returns 0 if account is None
        OR if no events match. Returns 1 if at least one event matches.

        Defensive: catch any exception (calendar API issues) and return 0 silently
        -- calendar is enrichment, not blocking.
        """
        try:
            start = now - timedelta(days=_CALENDAR_WINDOW_DAYS)
            end = now + timedelta(days=_CALENDAR_WINDOW_DAYS)
            sender_lower = sender_email.lower()

            for event in self.account.calendar.view(start=start, end=end):
                attendees = (event.required_attendees or []) + (
                    event.optional_attendees or []
                )
                for att in attendees:
                    addr = (
                        (att.mailbox.email_address or "").lower()
                        if att.mailbox
                        else ""
                    )
                    if addr == sender_lower:
                        return 1
            return 0
        except Exception:  # noqa: BLE001
            return 0


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _extract_frontmatter(md_file: Path) -> Optional[dict]:
    """Parse YAML frontmatter from a markdown file.

    Returns the parsed dict, or None if the file has no frontmatter,
    fails to parse, or cannot be read.
    """
    try:
        text = md_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    if not text.startswith("---"):
        return None

    # Find the closing ---
    end = text.find("\n---", 3)
    if end == -1:
        return None

    raw_frontmatter = text[3:end].strip()
    try:
        parsed = yaml.safe_load(raw_frontmatter)
    except yaml.YAMLError:
        return None

    if not isinstance(parsed, dict):
        return None

    return parsed


def _parse_date(value: str) -> Optional[datetime]:
    """Parse a YYYY-MM-DD string into a UTC-aware datetime at midnight.

    Returns None if parsing fails.
    """
    try:
        date_str = str(value).strip().strip("'\"")
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None

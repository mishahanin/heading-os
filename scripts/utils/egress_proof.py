#!/usr/bin/env python3
"""Whether an assembled payload has earned the right to leave this machine.

`is_sensitive()` is fail-closed and right to be: unset means sensitive, so a
forgotten variable can never ship prompts to a third party. It is also a PROXY. It
answers a question about the session, not about the bytes, and it answers the same
way every night. Measured 2026-08-03: `scripts/router-accuracy-nightly.py` has
never run once on any host because of it, while its sibling daemon has fired
nightly and skipped for 74 days with every health surface reporting healthy.

This module answers the narrower question the proxy stands in for: does THIS
payload carry anything private. It does not replace the flag and cannot; it gives
one caller a way to EARN an exemption by presenting evidence, per payload, every
time. `sensitive.py` is untouched and its seven consumers see no change.

The argument, stated exactly as narrowly as it holds:

    the payload was scanned by the same real-entity detector that decides whether
    a file may become PUBLIC on GitHub, which is a strictly higher bar than "may
    be read by a judge model", and every source it was built from is committed, so
    it has already passed that wall and has not changed since.

What it does NOT claim: that the payload is provably free of every private string.
The denylist is precision-over-recall by design, so a novel private string typed
into a source for the first time is invisible to it. What bounds that is the
commit wall, not this scan, which is why an uncommitted source is refused rather
than scanned.

Three states, and the middle one is the posture. A denylist that could not be
built, or that built empty, proves nothing, and a proof that cannot be TAKEN must
refuse rather than permit. Fail closed against haste, open against a broken
environment, the same shape the lifecycle's evidence and attestation gates already
carry.

Consumed by: scripts/router-accuracy-nightly.py.
"""

from __future__ import annotations

from typing import Sequence

EGRESS_CLEAR = "egress_clear"
EGRESS_BLOCKED = "egress_blocked"
EGRESS_UNVERIFIABLE = "egress_unverifiable"

# `Denylist.scan_text` skips any line carrying this marker. That is correct for a
# commit gate a human annotated deliberately on a file under review. It is not
# correct here: honouring it would let one comment in one tracked engine file
# exempt a line from every future scan, silently and permanently, and nobody
# reviews an annotation for what it does to a nightly job a year later. So at this
# layer the marker's PRESENCE is a reason to refuse, never a reason to skip a line.
_SUPPRESSION_MARKER = "content-guard: ok"


def egress_state(payload: str, denylist,
                 dirty_sources: Sequence[str] | None = None) -> tuple[str, str]:
    """Classify `payload` against `denylist`. Returns (state, reason).

    `denylist` is a `content_denylist.Denylist`, built by the real builder from
    the private overlay. It is passed in rather than built here so the caller owns
    the overlay lookup and this stays pure over its inputs.

    Order matters and is deliberate: the cheap structural refusals come before the
    scan, so a payload that cannot be judged is never reported on the strength of
    a scan that was meaningless.
    """
    if dirty_sources:
        named = ", ".join(sorted(dirty_sources)[:3])
        more = "" if len(dirty_sources) <= 3 else f" (+{len(dirty_sources) - 3} more)"
        return EGRESS_UNVERIFIABLE, (
            f"a payload source has uncommitted changes ({named}{more}), so it has "
            f"not passed the content wall that decides whether it may become public; "
            f"commit it, or run this where the tree is clean"
        )

    if getattr(denylist, "degraded", False):
        return EGRESS_UNVERIFIABLE, (
            "the real-entity denylist could not be built (the private overlay is "
            "absent or unreadable), so nothing can be proved about this payload"
        )

    if not getattr(denylist, "tokens", None):
        return EGRESS_UNVERIFIABLE, (
            "the real-entity denylist built empty, so a clean scan would mean only "
            "that the detector can see nothing"
        )

    if _SUPPRESSION_MARKER in payload:
        return EGRESS_UNVERIFIABLE, (
            f"the payload carries a `{_SUPPRESSION_MARKER}` suppression marker, "
            f"which silences the scan for that line; a marker written for a commit "
            f"review cannot be trusted to govern an unattended send"
        )

    hits = denylist.scan_text(payload)
    if hits:
        # The categories, never the tokens. A refusal that quotes what it caught
        # writes the private value into a log, a journal and a scrollback, which
        # is the leak it just refused.
        categories = sorted({category for _line, _text, category in hits})
        return EGRESS_BLOCKED, (
            f"the payload carries {len(hits)} real-entity match(es) in "
            f"{', '.join(categories)}; the value is withheld on purpose"
        )

    return EGRESS_CLEAR, ""

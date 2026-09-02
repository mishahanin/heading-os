"""recipient.py - is this address one an outbound send may actually go to?

The Action Queue lets a drafting skill deposit a card before a real recipient is
known. `/queue-draft` does exactly that on purpose: its documented fallback for a
missing recipient is a reserved placeholder address, and the human is meant to
correct it before approving. Nothing checked that the correction had happened, so
an untouched placeholder reached `send_card` and the transport tried to mail it.

This module answers one question, and only that question: would sending to this
string be a mistake a person can see from the outside? It is a PLAUSIBILITY
check, not deliverability and not an RFC 5322 parser. The addresses it refuses
are the ones that are wrong on their face:

- a reserved DOCUMENTATION domain (RFC 2606 section 3: `example.com`,
  `example.net`, `example.org`, and anything under them), which is where every
  placeholder in this repo and in most drafting tooling lands;
- a well-known stand-in local part (`someone@`, `recipient@`, `changeme@`);
- a string that is not shaped like an address at all.

Deliberately NOT refused, and this one is a decision rather than an oversight:
the reserved TESTING TLDs, `.invalid` and `.test` (RFC 2606 section 2, RFC
6761). Nothing at those can be delivered either, so refusing them would read as
the stricter and therefore better rule. It is the worse one here. This repo's
test corpus addresses its send fixtures at `.test` ON PURPOSE, and
`tests/test_send_body_never_reaches_argv.py` records why: verifying a send guard
by removing it once put three real messages on the wire, and the reserved TLD is
the reason none of them could reach a person. A guard that refused `.test` would
push every future send fixture toward a plausible-looking invented domain, which
is a domain somebody may own. Trading a live safety convention for a tighter
rule against a failure mode nothing in the tree produces is the wrong trade.

Also not refused: an address with a plus tag, or one at an unusual TLD. Those
are real addresses somewhere, and a validator that guesses at deliverability
starts refusing mail the operator intended to send. Refusing too much is a
failure here too - it turns the guard into something the next person routes
around.

Consumed by:
  - scripts/action-queue-execute.py (send_card, the one send choke point)
  - scripts/action-queue.py (cmd_edit, so a correction is checked when typed)
"""
from __future__ import annotations

import re

# Shaped like an address: one `@`, no whitespace, no address-list or
# display-name punctuation, and a dotted domain ending in letters. Angle
# brackets are refused rather than unwrapped: `<a@b.com>` in a card's `to` field
# means a template was never filled, not that a display name needs stripping.
_ADDR = re.compile(r"^[^@\s<>,;:\"\\]+@[^@\s<>,;:\"\\]+\.[A-Za-z]{2,}$")

# RFC 2606 section 3 reserved documentation domains, plus the `.example` TLD
# from RFC 6761. These are the addresses that templates and drafting tools emit
# when nobody has supplied a real one. `.invalid` and `.test` are deliberately
# absent; the module docstring says why.
_RESERVED_DOMAINS = frozenset({"example.com", "example.net", "example.org"})
_RESERVED_TLDS = frozenset({"example"})

# Local parts that ship inside templates and drafting tools. A real person can
# hold one of these on a real domain, so this list alone never refuses: it is
# read only after the domain has already passed.
_PLACEHOLDER_LOCALS = frozenset({
    "someone", "somebody", "recipient", "addressee", "you", "youremail",
    "name", "firstname", "firstname.lastname", "first.last", "fullname",
    "todo", "tbd", "changeme", "placeholder", "example", "sample", "dummy",
})


def refusal_reason(to: str | None) -> str | None:
    """Return why ``to`` must not be sent to, or None when it is plausible.

    The return value is the operator-facing half of a refusal message, so it
    names the fault rather than restating the address.
    """
    addr = (to or "").strip()
    if not addr:
        return "no recipient on the card"
    if not _ADDR.match(addr):
        return f"{addr!r} is not shaped like an email address"
    domain = addr.rsplit("@", 1)[1].lower()
    tld = domain.rsplit(".", 1)[-1]
    if domain in _RESERVED_DOMAINS or any(
            domain.endswith("." + d) for d in _RESERVED_DOMAINS):
        return (f"{domain} is a reserved documentation domain (RFC 2606) - "
                f"the card still carries a placeholder recipient")
    if tld in _RESERVED_TLDS:
        return (f".{tld} is the reserved documentation TLD (RFC 6761) - "
                f"the card still carries a placeholder recipient")
    local = addr.rsplit("@", 1)[0].lower()
    if local in _PLACEHOLDER_LOCALS:
        return (f"{local!r} is a template stand-in, not a person - "
                f"the card still carries a placeholder recipient")
    return None


def is_sendable(to: str | None) -> bool:
    """True when ``refusal_reason`` finds nothing wrong with ``to``."""
    return refusal_reason(to) is None

"""The GAL writer and the GAL reader must agree, on any deployment.

Found by the 2026-08-23 engine audit.

`scripts/gal-export.py` writes `outputs/_sync/gal-<domain>.json` and defaulted
`--domain` to one company's domain. `scripts/bootcamp-roster.py` then read
`gal-31c.io.json` -- the domain written out as a literal, in the engine, in a
module whose own banner says "Event-specific values ... are instance DATA" and
which routes every other such value through `resolve_config_with_example`.

On any other deployment the two disagree: the export writes `gal-acme.com.json`
and `build_roster()` opens `gal-31c.io.json`, which is a `FileNotFoundError` at
best and a stale previous tenant's address book at worst. It is not a secret
leak -- `31c.io` is published identity -- it is the engine/data contamination
the rest of that file works to prevent.

The domain is instance identity, like the slug and the GitHub org, so it now
resolves the same way: `operator_email_domain()` beside them in
`scripts/utils/operator_identity.py`, with the org-chart config's `gal_domain`
taking precedence for the roster because a company's GAL domain and its
operator's email domain are not always the same one. Measured here: the
operator email is on one domain and the GAL is on another, so a
config-before-identity order was required rather than tidy.

Scope, corrected 2026-09-01. This file used to end "the sweep below is
deliberately wider than the two files: this class of defect is written wherever
someone had a value to hand." It is not wider. `WATCHED` names exactly the two
files the incident was found in, and it always has.

The claim was checked rather than repaired, because a blanket widening does not
work: over the 386 tracked `scripts/**/*.py` files there are 76 distinct
`word.tld` tokens and 261 occurrences, and almost all of them are third-party
service addresses (`github.com`, `googleapis.com`, `anthropic.com`) that belong
in engine code. `_DOMAINISH` over that corpus is 260 findings of noise around
one real class.

What the wider sweep WOULD have caught, measured the same day and still open:

  scripts/email-intelligence.py:92   INTERNAL_DOMAIN = "31c.io"
  scripts/utils/crm.py:561           '"@31c.io" in email.lower()'
  scripts/crm-health.py:82           the warning line that reports it

Each is a tenant mail domain deciding BEHAVIOUR, not branding: on any other
deployment `is_internal` classifies nothing and the tribe-email warning never
fires. They are not fixed here, and the reason is not scope: the right value is
the instance's CORPORATE mail domain, which is not the same field as
`operator_email_domain()` (measured on this machine: the operator email is on
one domain and the corporate mail on another, the same split that made
`_gal_domain()` put the org chart's `gal_domain` ahead of the operator email).
Closing them needs a new instance-config key and the operator's word on where it
lives, which is a decision, not a repair. Reported up rather than silently left.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# --- the resolver exists and is honest ---------------------------------------

def test_the_domain_resolver_reads_the_operator_email():
    from scripts.utils import operator_identity as OI
    got = OI.operator_email_domain()
    email = (OI.get_operator().get("email") or "")
    if "@" in email:
        assert got == email.split("@", 1)[1].lower()
    else:
        assert got == "", got


def test_the_resolver_is_empty_rather_than_wrong_when_unconfigured(monkeypatch):
    from scripts.utils import operator_identity as OI
    monkeypatch.setattr(OI, "get_operator", lambda: {"email": ""})
    assert OI.operator_email_domain() == ""


# --- neither script carries a tenant domain ----------------------------------

# Any `word.tld` ANYWHERE in a code line, not only as a whole quoted value.
# The first cut anchored on quotes and missed `"gal-31c.io.json"` -- the exact
# literal this guard exists for -- because the domain was embedded in a longer
# string. A detector that misses the original defect proves nothing.
_DOMAINISH = re.compile(r"\b([a-z0-9][a-z0-9-]*\.(?:io|com|net|org|ai|dev))\b")
_ALLOWED = {
    "example.com", "example.org", "example.net", "example.io",
    # Public project identity, not a tenant filter: these name where the engine
    # itself lives, and they are the same on every deployment.
    "mishahanin.github.io", "github.io", "pypi.org", "docs.astral.sh",
}

# Per file, not over the union. The old floor was `inspected >= 400` across
# both, and `scripts/bootcamp-roster.py` alone contributes 496 non-comment
# lines, so `scripts/gal-export.py` could shrink to nothing and the gate would
# still report green over a file it had stopped reading. Counts measured
# 2026-09-01 (gal-export 228, bootcamp-roster 496); the floors sit well under
# them so retiring a chunk of either script does not fail this test.
WATCHED = {
    "scripts/gal-export.py": 120,
    "scripts/bootcamp-roster.py": 300,
}


def domain_hits(line: str) -> list[str]:
    """The tenant-domain tokens in ONE line of code, or [].

    Extracted 2026-09-01 so the detector has a true negative. The rule lived
    inline in the loop below, so nothing anywhere asserted that it FIRES: a
    broken `_DOMAINISH`, an over-wide `_ALLOWED`, or a `core` fold that swallowed
    the hit would each have left this file green while checking nothing. The
    docstring says the first cut missed `"gal-31c.io.json"`, which is exactly the
    failure a detector with no negative case ships silently.

    A comment line is skipped by the caller, not here: prose may name the old
    default, and the caller is what knows whether it is reading code or prose.
    """
    out = []
    for hit in _DOMAINISH.findall(line):
        # `gal-example.com` is the placeholder wearing a filename prefix;
        # `gal-31c.io` is the defect. Strip a leading `something-` and judge
        # what is left.
        core = hit.rsplit("-", 1)[-1]
        if core not in _ALLOWED and hit not in _ALLOWED:
            out.append(hit)
    return out


# The literal from the incident, plus the near-misses that must NOT fire.
# `31c.io` is published project identity, so it is not a secret; what makes it a
# defect here is being COMPILED IN as an instance value.
_FIRES = [
    'GAL = "gal-31c.io.json"',                       # the original defect
    'ap.add_argument("--domain", default="31c.io")',  # the sibling default
    'INTERNAL_DOMAIN = "acme-tenant.io"',
    'if email.endswith("@some-company.com"):',
    "url = 'https://tenant.example-corp.net/gal'",
]
_QUIET = [
    'GAL = f"gal-{domain}.json"',                    # resolved, not compiled in
    'GAL = "gal-example.com.json"',                  # the shipped placeholder
    'd = _org_data().get("gal_domain") or operator_email_domain()',
    'DOCS = "https://mishahanin.github.io/heading-os/"',
    'INDEX = "https://pypi.org/simple"',
]


def test_the_detector_fires_on_the_literal_it_was_written_for():
    for line in _FIRES:
        assert domain_hits(line), line


def test_the_detector_leaves_placeholders_and_project_identity_alone():
    for line in _QUIET:
        assert domain_hits(line) == [], line


def test_the_sweep_has_something_to_read():
    for rel in WATCHED:
        assert (ROOT / rel).is_file(), rel


@pytest.mark.parametrize("rel,floor", sorted(WATCHED.items()))
def test_the_sweep_still_reads_each_watched_file(rel, floor):
    """If the `#` skip ever drifts true for every line, or a file empties,
    nothing is scanned, `bad` is empty, and the assertion below passes while
    guarding nothing. One floor per file, so neither can hide behind the other.
    """
    inspected = sum(
        1 for line in (ROOT / rel).read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )
    assert inspected >= floor, f"only {inspected} code lines scanned in {rel}"


def test_no_watched_script_hardcodes_a_tenant_domain():
    bad = []
    for rel in WATCHED:
        for n, line in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue                     # prose may name the old default
            bad += [f"{rel}:{n}: {hit!r}" for hit in domain_hits(line)]
    assert not bad, (
        "a tenant domain is compiled into engine code; it belongs in "
        "operator.yaml or the instance config:\n  " + "\n  ".join(bad)
    )


def test_the_shipped_example_config_is_generic():
    d = json.loads((ROOT / "scripts" / "bootcamp-org-chart.example.json")
                   .read_text(encoding="utf-8"))
    assert d.get("gal_domain") in _ALLOWED, d.get("gal_domain")


# --- writer and reader name the same file ------------------------------------

def test_the_roster_reads_the_file_the_export_would_write():
    roster = _load("scripts/bootcamp-roster.py", "bootcamp_roster_domain")
    domain = roster._gal_domain()
    assert domain, "the roster resolved an empty domain"
    assert roster.gal_json().name == f"gal-{domain}.json", roster.gal_json()
    # And that is the shape gal-export builds, read out of its own source.
    export_src = (ROOT / "scripts" / "gal-export.py").read_text(encoding="utf-8")
    assert 'f"gal-{args.domain}.json"' in export_src, (
        "gal-export no longer names its output gal-<domain>.json; the two "
        "sides can drift again"
    )


def test_the_export_refuses_rather_than_sweeping_everything():
    """An empty filter would enumerate the entire address book."""
    src = (ROOT / "scripts" / "gal-export.py").read_text(encoding="utf-8")
    assert "if not args.domain:" in src
    assert "ap.error(" in src

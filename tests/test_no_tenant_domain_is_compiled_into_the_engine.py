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

What the wider sweep WOULD have caught, measured the same day:

  scripts/email-intelligence.py:92   INTERNAL_DOMAIN = "31c.io"
  scripts/utils/crm.py:561           '"@31c.io" in email.lower()'
  scripts/crm-health.py:82           the warning line that reports it

Each is a tenant mail domain deciding BEHAVIOUR, not branding: on any other
deployment `is_internal` classified nothing and the tribe-email warning never
fired. They were left open that day, and the reason was not scope: the right
value is the instance's CORPORATE mail domain, which is not the same field as
`operator_email_domain()` (measured on this machine: the operator email is on
one domain and the corporate mail on another, the same split that made
`_gal_domain()` put the org chart's `gal_domain` ahead of the operator email).
Closing them needed a new instance-config key and the operator's word on where
it lives, which is a decision, not a repair.

CLOSED 2026-09-01. The operator settled it: a fifth field on the same identity
seam, `corporate_email_domain` in `scripts/utils/operator_identity.py`, env
override `HEADING_OS_OPERATOR_CORPORATE_EMAIL_DOMAIN`, value form the BARE
domain because two of the three sites prepend the `@` themselves. The engine
ships `corporate_email_domain: ""` in `scripts/operator.example.yaml` and the
real value lives only in the private data overlay. All three files join
`WATCHED` below, so a re-introduction fails here.

They are named one at a time, never by a glob over `scripts/**` -- the
measurement four paragraphs up is why. Adding them cost one widening of
`_ALLOWED`: `scripts/email-intelligence.py` ships `"*@expensify.com"` and
`"*@linkedin.com"` in `DEFAULT_IGNORE_PATTERNS`, which are third-party senders
every deployment wants filtered, exactly like the `github.com` class already
listed there. One further hit was a real defect and was fixed rather than
allowed: the Exchange-unreachable `hint` string named the operator's own mail
HOST, so it now names the failure without the hostname.

The behavioural coverage the three sites never had is in
`tests/test_a_corporate_domain_that_only_one_deployment_could_match.py`. This
file only proves no tenant domain is written back in; that one proves the
resolved domain still decides what it used to decide, and that an unconfigured
clone warns about nobody rather than everybody.
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
    # Third-party senders every deployment filters, shipped in
    # `DEFAULT_IGNORE_PATTERNS`. Same class as `github.com` above: a service
    # address, not a tenant. Added 2026-09-01 with email-intelligence.py.
    "expensify.com", "linkedin.com",
}

# The `email:` key of a SKILL.md frontmatter block, and only there.
# `.claude/rules/development-standards.md` REQUIRES `metadata.email` of every
# skill, so all three watched markdown skills carry the author address. It is
# upstream authorship, identical on every deployment, and it decides nothing.
#
# Skipping that ONE key is narrower than putting its domain in `_ALLOWED`, which
# would also blind the five watched scripts -- and an author address compiled
# into `scripts/email-intelligence.py` as a mail filter is the precise defect
# this file exists for. It is narrower than skipping the frontmatter block too:
# `description` lives there, and `email-intel`'s description is one of the four
# sites the 2026-09-01 skills sweep had to clean.
_FRONTMATTER_AUTHOR_EMAIL = re.compile(r"^\s*email:\s*\S+@\S+\s*$")


def scannable_lines(rel: str, text: str):
    """The (lineno, line) pairs this sweep judges in one file.

    Python: `#` opens a comment, and prose there may legitimately name the old
    default, so a `#` line is skipped.

    Markdown: `#` opens a HEADING, not a comment. The same skip would drop
    section titles for no reason and would exempt no prose at all, so every line
    is judged except the frontmatter author address above. These are instruction
    files a model executes: a paragraph naming a tenant domain IS the defect,
    not a note about one. A future mention of the old rule gets reworded rather
    than exempted.
    """
    markdown = rel.endswith(".md")
    in_frontmatter = False
    for n, line in enumerate(text.splitlines(), 1):
        if markdown:
            if line.rstrip() == "---" and (n == 1 or in_frontmatter):
                in_frontmatter = n == 1
                continue
            if in_frontmatter and _FRONTMATTER_AUTHOR_EMAIL.match(line):
                continue
        elif line.lstrip().startswith("#"):
            continue
        yield n, line


# Per file, not over the union. The old floor was `inspected >= 400` across
# both, and `scripts/bootcamp-roster.py` alone contributes 496 non-comment
# lines, so `scripts/gal-export.py` could shrink to nothing and the gate would
# still report green over a file it had stopped reading. Counts measured
# 2026-09-01 (gal-export 228, bootcamp-roster 496, email-intelligence 1560,
# crm 905, crm-health 371); the floors sit well under them so retiring a chunk
# of any of these scripts does not fail this test.
#
# The last three joined on 2026-09-01 when `corporate_email_domain` closed
# them; see the module docstring. Named individually and deliberately: a glob
# over `scripts/**` is 260 findings of third-party noise.
#
# The five `.claude/skills/` entries joined on 2026-09-01, after the same class
# of defect was found there and fixed: `/request-skill` mailed every deployment's
# skill requests to ONE tenant's role address, and `/email-intel` and `/crm`
# stated the internal-versus-external and tribe-mailbox rules as a literal that
# only one deployment could ever match -- while the code behind them had already
# moved to `corporate_email_domain()`.
#
# Named one at a time here for the same reason the scripts are. Measured
# 2026-09-01 over all 418 tracked files under `.claude/skills/`: 309 findings
# across 135 files, 97 distinct tokens, almost all of them OSINT source sites
# and third-party APIs a skill legitimately names (`github.com`, `youtube.com`,
# `opensanctions.org`). That is worse than the 260 that ruled out a
# `scripts/**` glob, so the same answer holds: name the files, never the tree.
#
# Cost of admitting these five: exactly one exemption, `_FRONTMATTER_AUTHOR_EMAIL`
# above, for the three findings the required `metadata.email` key contributes.
# The two reference files contribute none.
WATCHED = {
    "scripts/gal-export.py": 120,
    "scripts/bootcamp-roster.py": 300,
    "scripts/email-intelligence.py": 1200,
    "scripts/utils/crm.py": 700,
    "scripts/crm-health.py": 280,
    ".claude/skills/request-skill/SKILL.md": 70,
    ".claude/skills/email-intel/SKILL.md": 200,
    ".claude/skills/email-intel/references/digest-format.md": 110,
    ".claude/skills/crm/SKILL.md": 70,
    ".claude/skills/crm/references/actions.md": 80,
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


_SKILL_MD = (
    "---\n"
    "name: email-intel\n"
    "description: Scans the mailbox at ceo@tenant-corp.io via EWS\n"
    "metadata:\n"
    "  email: author@upstream-project.dev\n"
    "---\n"
    "# Heading naming acme-tenant.com\n"
    "- All participants @acme-tenant.com -> INTERNAL\n"
)


def test_the_frontmatter_exemption_covers_the_author_key_and_nothing_else():
    """The `email:` key is exempt; `description:` in the same block is not.

    `email-intel`'s frontmatter description carried a tenant mailbox until
    2026-09-01, so exempting the whole block would have skipped the defect.
    """
    seen = dict(scannable_lines("a/SKILL.md", _SKILL_MD))
    assert 5 not in seen, "the frontmatter author email should be exempt"
    assert 3 in seen, "the frontmatter description must still be scanned"
    hits = [h for line in seen.values() for h in domain_hits(line)]
    assert hits == ["tenant-corp.io", "acme-tenant.com", "acme-tenant.com"], hits


def test_a_markdown_heading_is_scanned_rather_than_skipped_as_a_comment():
    """`#` is a comment in Python and a heading in markdown.

    Reusing the Python skip on a `.md` file would drop every section title,
    which exempts no prose and hides any domain written into a heading.
    """
    assert [n for n, _ in scannable_lines("a/SKILL.md", _SKILL_MD) if n == 7]
    assert not [n for n, _ in scannable_lines("a/x.py", "# INTERNAL = 'acme.io'\n")]


def test_the_sweep_has_something_to_read():
    for rel in WATCHED:
        assert (ROOT / rel).is_file(), rel


@pytest.mark.parametrize("rel,floor", sorted(WATCHED.items()))
def test_the_sweep_still_reads_each_watched_file(rel, floor):
    """If the `#` skip ever drifts true for every line, or a file empties,
    nothing is scanned, `bad` is empty, and the assertion below passes while
    guarding nothing. One floor per file, so neither can hide behind the other.
    """
    text = (ROOT / rel).read_text(encoding="utf-8")
    inspected = sum(1 for _ in scannable_lines(rel, text))
    assert inspected >= floor, f"only {inspected} code lines scanned in {rel}"


def test_no_watched_script_hardcodes_a_tenant_domain():
    bad = []
    for rel in WATCHED:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for n, line in scannable_lines(rel, text):
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

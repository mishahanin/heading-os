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

The sweep below is deliberately wider than the two files: this class of defect
is written wherever someone had a value to hand.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

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

WATCHED = [
    "scripts/gal-export.py",
    "scripts/bootcamp-roster.py",
]


def test_the_sweep_has_something_to_read():
    for rel in WATCHED:
        assert (ROOT / rel).is_file(), rel


def test_no_watched_script_hardcodes_a_tenant_domain():
    bad = []
    inspected = 0
    for rel in WATCHED:
        for n, line in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue                     # prose may name the old default
            inspected += 1
            for hit in _DOMAINISH.findall(line):
                # `gal-example.com` is the placeholder wearing a filename
                # prefix; `gal-31c.io` is the defect. Strip a leading
                # `something-` and judge what is left.
                core = hit.rsplit("-", 1)[-1]
                if core not in _ALLOWED and hit not in _ALLOWED:
                    bad.append(f"{rel}:{n}: {hit!r}")
    # 627 code lines survived the comment skip on 2026-08-26; floor well under
    # that so retiring a chunk of either script does not fail this test. If the
    # `line.lstrip().startswith("#")` guard ever drifts true for every line
    # (or WATCHED empties), nothing is scanned, `bad` is empty, and the domain
    # assertion below passes while guarding nothing.
    assert inspected >= 400, f"only {inspected} code lines scanned"
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
    domain = roster._GAL_DOMAIN
    assert domain, "the roster resolved an empty domain"
    assert roster.GAL_JSON.name == f"gal-{domain}.json", roster.GAL_JSON
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

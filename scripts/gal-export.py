#!/usr/bin/env python3
"""Enumerate the Exchange Global Address List for one domain's addresses.

Uses exchangelib's protocol.resolve_names() with a-z prefix sweep, then
filters to a target domain. Returns full contact data (job title,
department, phone) when available.

Usage:
    python scripts/gal-export.py
    python scripts/gal-export.py --domain example.com \\
        --out outputs/_sync/gal-example.com.json

Tests: tests/test_a_sweep_that_reported_the_letters_it_never_read.py
"""

from __future__ import annotations

import argparse
import json
import os
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from scripts.utils.venv_guard import ensure_venv  # noqa: E402

ensure_venv()
from scripts.utils.operator_identity import operator_email_domain  # noqa: E402
from scripts.utils.workspace import get_outputs_dir, get_workspace_root, load_env

# exchangelib names are bound lazily (F-2.1: import stays pure).
Account = Configuration = Credentials = DELEGATE = Version = Build = None


def _in_domain(email: str, domain: str) -> bool:
    """True when `email`'s domain part IS `domain`, not merely contains it.

    Substring matching accepts a lookalike: for a domain `acme.example`, both
    `alice@notacme.example` and `bob@acme.example.evil.test` contain it and
    neither belongs to the tenant.
    """
    local, _, host = (email or "").rpartition("@")
    return bool(local) and host.lower() == (domain or "").lower().lstrip("@")


def _ensure_exchangelib():
    global Account, Configuration, Credentials, DELEGATE, Version, Build
    if Account is not None:
        return
    from scripts.utils.optdeps import require
    require("exchangelib", extra="email")
    from exchangelib import Account, Configuration, Credentials, DELEGATE, Version, Build

WORKSPACE_ROOT = get_workspace_root()
ENV_FILE = WORKSPACE_ROOT / ".env"


def load_config() -> dict:
    if not ENV_FILE.exists():
        print(f"[ERROR] .env file not found at: {ENV_FILE}")
        sys.exit(1)
    load_env(WORKSPACE_ROOT)
    cfg = {
        "EXCHANGE_EMAIL": os.getenv("EXCHANGE_EMAIL"),
        "EXCHANGE_PASSWORD": os.getenv("EXCHANGE_PASSWORD"),
        "EXCHANGE_SERVER": os.getenv("EXCHANGE_SERVER"),
    }
    for k, v in cfg.items():
        if not v:
            print(f"[ERROR] Missing {k} in .env")
            sys.exit(1)
    cfg["EXCHANGE_USERNAME"] = os.getenv("EXCHANGE_USERNAME", cfg["EXCHANGE_EMAIL"])
    return cfg


def connect(cfg: dict) -> Account:
    _ensure_exchangelib()
    creds = Credentials(username=cfg["EXCHANGE_USERNAME"], password=cfg["EXCHANGE_PASSWORD"])
    # Exchange 2019 build hint - exchangelib's resolve_names() requires an explicit
    # version_hint on the protocol; without it, ResolveNames raises NoneType.api_version
    version = Version(Build(15, 2, 1748, 37))
    config = Configuration(server=cfg["EXCHANGE_SERVER"], credentials=creds, version=version)
    return Account(
        primary_smtp_address=cfg["EXCHANGE_EMAIL"],
        config=config,
        autodiscover=False,
        access_type=DELEGATE,
    )


def extract_record(item, contact=None) -> dict:
    """Pull useful fields from a Mailbox + optional Contact."""
    rec = {
        "name": getattr(item, "name", None),
        "email": getattr(item, "email_address", None),
        "mailbox_type": getattr(item, "mailbox_type", None),
    }
    if contact is not None:
        rec["display_name"] = getattr(contact, "display_name", None)
        rec["given_name"] = getattr(contact, "given_name", None)
        rec["surname"] = getattr(contact, "surname", None)
        rec["job_title"] = getattr(contact, "job_title", None)
        rec["department"] = getattr(contact, "department", None)
        rec["company_name"] = getattr(contact, "company_name", None)
        rec["office_location"] = getattr(contact, "office_location", None)
        # Phones
        try:
            phones = getattr(contact, "phone_numbers", None) or []
            rec["phones"] = [
                {"label": getattr(p, "label", None), "phone_number": getattr(p, "phone_number", None)}
                for p in phones
            ]
        except Exception:
            rec["phones"] = []
        # Manager and direct reports
        rec["manager"] = getattr(contact, "manager_mailbox", None)
        rec["physical_addresses"] = []
    return rec


def sweep_gal(account: Account, domain: str) -> tuple[list[dict], list[str]]:
    """Sweep the GAL with a-z + 0-9 prefixes, dedupe by email, filter by domain.

    Returns (records, failed_queries). The failures are RETURNED, not just
    printed: a prefix that raised contributes no addresses, and the caller's
    closing "[OK] N unique entries" line read as a completed sweep whether one
    query had failed or thirty. The per-query WARN scrolls off the top of a
    36-line sweep, and the JSON it writes is what downstream tooling then treats
    as the address book.
    """
    seen: dict[str, dict] = {}
    failed: list[str] = []
    queries = list(string.ascii_lowercase) + list(string.digits)
    # Prefixes that surface admin and shared mailboxes the a-z sweep can miss.
    # Two were the tenant's own name and `@<tenant domain>`, written in as
    # literals; both are derived from `domain` now, so the sweep is as thorough
    # on any deployment as it was on the one it was written for.
    label = domain.split(".", 1)[0]
    extra = [label, "info", "sales", "support", "admin", "hr", "finance",
             "noreply", f"@{domain}"]
    queries.extend(dict.fromkeys(extra))       # de-dupe, order preserved

    print(f"[INFO] Sweeping GAL with {len(queries)} prefix queries (filter: @{domain})...")

    for q in queries:
        try:
            results = account.protocol.resolve_names(
                [q],
                return_full_contact_data=True,
                search_scope="ActiveDirectory",
            )
        except Exception as e:
            print(f"  [WARN] query={q!r}: {e}")
            failed.append(str(q))
            continue

        if not results:
            continue

        for item in results:
            # exchangelib returns Mailbox or (Mailbox, Contact) depending on flag
            contact = None
            mailbox = item
            if isinstance(item, tuple) and len(item) == 2:
                mailbox, contact = item
            # Skip Exception items
            if isinstance(item, Exception):
                continue
            email = getattr(mailbox, "email_address", None)
            # Exact domain match, not a substring search. `domain in email`
            # accepted a lookalike domain and a suffixed one, so an export that
            # says it filtered to one tenant could carry identities from
            # another. See `_in_domain` for the shapes.
            if not email or not _in_domain(email, domain):
                continue
            email_key = email.lower()
            if email_key in seen:
                # Merge: prefer record with job_title/department populated
                existing = seen[email_key]
                new_rec = extract_record(mailbox, contact)
                for k in ("job_title", "department", "company_name", "office_location", "given_name", "surname", "display_name"):
                    if not existing.get(k) and new_rec.get(k):
                        existing[k] = new_rec[k]
                if not existing.get("phones") and new_rec.get("phones"):
                    existing["phones"] = new_rec["phones"]
                continue
            seen[email_key] = extract_record(mailbox, contact)

        print(f"  query={q!r:>14}: {len(results):3d} results | total_unique={len(seen)}")

    return list(seen.values()), failed


def main():
    ap = argparse.ArgumentParser(description="Enumerate Exchange GAL by domain")
    ap.add_argument(
        "--domain", default=operator_email_domain() or None,
        help=("Domain filter. Defaults to the domain of the operator email in "
              "operator.yaml; required when that is unset. It used to default to "
              "one company's domain, compiled into the engine."))
    ap.add_argument(
        "--out",
        default=None,
        help="Output JSON path (default: outputs/_sync/gal-<domain>.json)",
    )
    args = ap.parse_args()
    if not args.domain:
        ap.error("no --domain given and operator.yaml carries no email to derive "
                 "one from. Pass --domain explicitly; an empty filter would sweep "
                 "the whole address book.")

    out_path = (
        Path(args.out)
        if args.out
        else get_outputs_dir() / "_sync" / f"gal-{args.domain}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cfg = load_config()
    account = connect(cfg)
    print(f"[OK] Connected as {cfg['EXCHANGE_EMAIL']}")

    records, failed = sweep_gal(account, args.domain)
    records.sort(key=lambda r: (r.get("display_name") or r.get("name") or "").lower())

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False, default=str)

    # The verdict says which sweep produced this file. A partial sweep and a
    # complete one had identical closing lines, so an export missing whole
    # letters of the alphabet was indistinguishable from a full one.
    verdict = "[OK]" if not failed else "[PARTIAL]"
    print(f"\n{verdict} {len(records)} unique @{args.domain} entries -> {out_path}")
    if failed:
        print(f"     {len(failed)} of the prefix queries FAILED and contributed "
              f"nothing: {', '.join(repr(q) for q in failed)}")
        print(f"     This export is incomplete. Re-run before treating it as the "
              f"address book.")
    # Quick summary
    with_title = sum(1 for r in records if r.get("job_title"))
    print(f"     {with_title}/{len(records)} have job_title populated")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

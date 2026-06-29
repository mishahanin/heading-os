#!/usr/bin/env python3
"""Enumerate the Exchange Global Address List for all @31c.io addresses.

Uses exchangelib's protocol.resolve_names() with a-z prefix sweep, then
filters to a target domain. Returns full contact data (job title,
department, phone) when available.

Usage:
    python scripts/gal-export.py
    python scripts/gal-export.py --domain 31c.io --out outputs/_sync/gal-31c.json
"""

from __future__ import annotations

import argparse
import json
import os
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from scripts.utils.venv import ensure_venv  # noqa: E402

ensure_venv()
from exchangelib import Account, Configuration, Credentials, DELEGATE, Version, Build
from exchangelib.protocol import BaseProtocol

from scripts.utils.workspace import get_outputs_dir, get_workspace_root, load_env

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


def sweep_gal(account: Account, domain: str) -> list[dict]:
    """Sweep the GAL with a-z + 0-9 prefixes, dedupe by email, filter by domain."""
    seen: dict[str, dict] = {}
    queries = list(string.ascii_lowercase) + list(string.digits)
    # Also try common 31c-specific prefixes that might surface admin/shared
    extra = ["31c", "info", "sales", "support", "admin", "hr", "finance", "noreply", "@31c.io"]
    queries.extend(extra)

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
            if not email or domain.lower() not in email.lower():
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

    return list(seen.values())


def main():
    ap = argparse.ArgumentParser(description="Enumerate Exchange GAL by domain")
    ap.add_argument("--domain", default="31c.io", help="Domain filter (default: 31c.io)")
    ap.add_argument(
        "--out",
        default=None,
        help="Output JSON path (default: outputs/_sync/gal-<domain>.json)",
    )
    args = ap.parse_args()

    out_path = (
        Path(args.out)
        if args.out
        else get_outputs_dir() / "_sync" / f"gal-{args.domain}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cfg = load_config()
    account = connect(cfg)
    print(f"[OK] Connected as {cfg['EXCHANGE_EMAIL']}")

    records = sweep_gal(account, args.domain)
    records.sort(key=lambda r: (r.get("display_name") or r.get("name") or "").lower())

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False, default=str)

    print(f"\n[OK] {len(records)} unique @{args.domain} entries -> {out_path}")
    # Quick summary
    with_title = sum(1 for r in records if r.get("job_title"))
    print(f"     {with_title}/{len(records)} have job_title populated")


if __name__ == "__main__":
    main()

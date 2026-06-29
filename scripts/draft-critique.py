#!/usr/bin/env python3
"""Read-only console critic for a queued outbound draft (R5b, console-first rule).

Prints an advisory critique of an email draft - either an Action Queue card
(resolved over the bridge loopback, like ``action-queue.py``) or an ad-hoc draft
from a file. This is the terminal path for the R5b pre-approval critique: it
works with the browser closed, and the ``--body-file`` form works even with the
daemon down. It is strictly READ-ONLY - it never stamps the queue, never
approves, never sends. (The daemon's critique sweep is what stamps a card's
``critique`` field; this CLI just shows a critique on demand.)

Usage:
    python scripts/draft-critique.py <id-or-prefix>          # critique a queued card
    python scripts/draft-critique.py --body-file draft.txt [--subject S] [--to ADDR]
    python scripts/draft-critique.py <id> --json             # machine-readable output

Exit codes: 0 critique produced, 1 usage error or no critique produced
(model unavailable / missing API key / empty body), 2 daemon not reachable.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import draft_critique
from scripts.utils.colors import BOLD, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.workspace import get_workspace_root

_RISK_COLOR = {"high": RED, "medium": YELLOW, "low": GREEN}


def _read_state(root: Path, name: str) -> str | None:
    p = root / ".daemon-state" / name
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _die_no_daemon() -> None:
    print(f"{RED}bridge daemon not reachable{RESET} - is it running? "
          f"(for an ad-hoc draft use --body-file, which needs no daemon)", file=sys.stderr)
    sys.exit(2)


def _fetch_card(root: Path, prefix: str) -> dict:
    token = _read_state(root, "token")
    port = _read_state(root, "port")
    if not token or not port:
        _die_no_daemon()
    url = f"http://127.0.0.1:{port}/action-queue"
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:  # subclass of URLError - catch first
        print(f"{RED}HTTP {e.code}{RESET}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError:
        _die_no_daemon()
    items = data.get("items", [])
    exact = [c for c in items if c.get("id") == prefix]
    matches = exact or [c for c in items if str(c.get("id", "")).startswith(prefix)]
    if not matches:
        print(f"{RED}no active card matches '{prefix}'{RESET}", file=sys.stderr)
        sys.exit(1)
    if len(matches) > 1:
        print(f"{RED}ambiguous prefix '{prefix}' ({len(matches)} matches){RESET}", file=sys.stderr)
        sys.exit(1)
    return matches[0]


def _print_human(card_id: str | None, result: dict) -> None:
    risk = result.get("risk", "medium")
    rc = _RISK_COLOR.get(risk, YELLOW)
    head = f"{BOLD}critique{RESET}"
    if card_id:
        head += f" {GRAY}{card_id[:8]}{RESET}"
    print(f"{head}  risk={rc}{risk}{RESET}  {GRAY}model={result.get('model', '?')}{RESET}")
    summary = result.get("summary", "")
    if summary:
        print(f"  {summary}")
    flags = result.get("flags", [])
    if flags:
        for f in flags:
            print(f"  {rc}-{RESET} {f}")
    else:
        print(f"  {GRAY}(no flags raised){RESET}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only advisory critique of an outbound draft.")
    ap.add_argument("id", nargs="?", help="Action Queue card id or unique prefix")
    ap.add_argument("--body-file", help="critique an ad-hoc draft from this file (no daemon needed)")
    ap.add_argument("--subject", default=None, help="subject for --body-file mode")
    ap.add_argument("--to", default=None, help="recipient address for --body-file mode")
    ap.add_argument("--model", default=None, help="model alias/id override (default haiku-class)")
    ap.add_argument("--json", action="store_true", help="emit the critique as JSON")
    args = ap.parse_args()

    if not args.id and not args.body_file:
        print(f"{RED}pass a card id-or-prefix, or --body-file{RESET}", file=sys.stderr)
        return 1
    if args.id and args.body_file:
        print(f"{RED}pass either a card id OR --body-file, not both{RESET}", file=sys.stderr)
        return 1

    card_id = None
    if args.body_file:
        try:
            body = Path(args.body_file).read_text(encoding="utf-8")
        except OSError as e:
            print(f"{RED}cannot read --body-file{RESET}: {e}", file=sys.stderr)
            return 1
        subject, recipient = args.subject, args.to
    else:
        root = get_workspace_root()
        card = _fetch_card(root, args.id)
        card_id = card.get("id")
        subject = card.get("subject")
        body = card.get("draft_body")
        recipient = card.get("to")  # recipient lives in the card's `to` field

    result = draft_critique.critique_draft(subject, body, recipient, model=args.model)
    if result is None:
        print(f"{YELLOW}no critique produced{RESET} - model unavailable, missing API key, "
              f"or empty draft body.", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        _print_human(card_id, result)
    return 0


if __name__ == "__main__":
    sys.exit(main())

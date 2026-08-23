"""No platform settings file may hand out another user's agent state.

`.claude/settings.local.windows.json` carried `Read(//c/Users/*/.claude/**)` in
its allow list. Three of the four provisioned executives run Windows, and that
one line granted, for EVERY user account on the machine:

  - `.credentials.json`, the live OAuth token (present and 0600 on this host,
    so the OS protects it from other users but not from an agent running as
    that user);
  - `projects/**/*.jsonl`, the complete transcript of every session, which is
    where the private data actually lives;
  - `.claude.json` and `settings*.json`, which can carry MCP server configs and
    their tokens.

The deny list beside it covered `.env`, `.sessions/**`, `*.pem` and `*.key`.
None of those match any of the above. The Linux and macOS files granted only
`claude-workspaces/**`, so Windows was the lone outlier granting strictly more.

Removed rather than patched: an allowlist over an entire agent-state directory
cannot be made safe by adding denies, because the deny list is then a blocklist
and a blocklist of "everything sensitive under ~/.claude" is never finished.
Removing an allow degrades the path to ASK, not to blocked, so nothing breaks
that a single prompt does not resolve.

The two denies added at the same time are defence in depth for the same class:
`.credentials.json` anywhere, and `git push --force-with-lease`, which rewrites
remote history exactly as `--force` does and was not covered by the existing
`--force` / `-f` denies.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLATFORMS = ("windows", "linux", "macos")


def _perms(platform: str) -> dict:
    path = ROOT / ".claude" / f"settings.local.{platform}.json"
    return json.loads(path.read_text(encoding="utf-8"))["permissions"]


@pytest.mark.parametrize("platform", PLATFORMS)
def test_the_file_parses(platform):
    _perms(platform)


# --- nothing may grant a home-directory .claude tree --------------------------

HOME_CLAUDE = re.compile(r"\((?:~|//[a-z]/Users/[^/]+|/home/[^/]+|/Users/[^/]+)/\.claude/")


@pytest.mark.parametrize("platform", PLATFORMS)
def test_no_allow_rule_reaches_a_home_claude_directory(platform):
    offenders = [rule for rule in _perms(platform).get("allow", [])
                 if HOME_CLAUDE.search(rule)]
    assert not offenders, (
        f"{platform} grants access to a home .claude tree: {offenders}. That "
        "directory holds .credentials.json and the full session transcripts. "
        "Drop the rule; an absent allow means ASK, not blocked."
    )


@pytest.mark.parametrize("platform", PLATFORMS)
def test_no_allow_rule_is_rooted_at_the_filesystem_root(platform):
    """`Edit(/.claude/skills/crm/**)` looks like a per-skill grant and is dead:
    the leading slash roots it at `/`, which no workspace-relative tool path
    reaches. Sixteen of these sat in the linux file, all shadowed by the real
    `Edit(.claude/skills/**)` beside them, so anyone reading the list believed
    in an access control that was not operating."""
    offenders = [rule for rule in _perms(platform).get("allow", [])
                 if re.search(r"\(/\.(claude|github)/", rule)]
    assert not offenders, (
        f"{platform} has allow rules rooted at the filesystem root: {offenders}. "
        "They can never match, and they misrepresent what is granted."
    )


@pytest.mark.parametrize("platform", PLATFORMS)
def test_no_allow_rule_wildcards_over_user_accounts(platform):
    """`//c/Users/*/...` and `/home/*/...` are only safe when the tail is the
    workspace, which is shared ground. Anything else crosses an account line."""
    offenders = []
    for rule in _perms(platform).get("allow", []):
        for marker in ("Users/*/", "home/*/"):
            if marker in rule and "claude-workspaces" not in rule:
                offenders.append(rule)
    assert not offenders, (
        f"{platform} wildcards over user accounts outside the workspace: {offenders}"
    )


# --- the denies that back it up -----------------------------------------------

@pytest.mark.parametrize("platform", PLATFORMS)
def test_the_oauth_credential_file_is_denied(platform):
    deny = _perms(platform).get("deny", [])
    assert "Read(**/.credentials.json)" in deny, (
        f"{platform} does not deny reading .credentials.json. Note the leading "
        "dot: this is Claude Code's OAuth token, not the Google "
        "`credentials.json` under .sessions/, which the .sessions/** deny "
        "already covers."
    )


@pytest.mark.parametrize("platform", PLATFORMS)
def test_history_rewriting_pushes_are_denied_including_the_lease_variant(platform):
    deny = _perms(platform).get("deny", [])
    for rule in ("Bash(git push --force:*)",
                 "Bash(git push -f:*)",
                 "Bash(git push --force-with-lease:*)"):
        assert rule in deny, (
            f"{platform} is missing {rule}. Denying --force while allowing "
            "--force-with-lease defeats the guardrail with one flag."
        )


@pytest.mark.parametrize("platform", PLATFORMS)
def test_the_commit_hook_bypass_stays_denied(platform):
    """Named in .claude/rules/security.md as never-do. Pinned here so a settings
    edit cannot quietly re-enable it."""
    deny = _perms(platform).get("deny", [])
    assert "Bash(git commit --no-verify:*)" in deny
    assert "Bash(git commit -n:*)" in deny


# --- the three platforms must not drift apart on security rules ---------------

def test_every_platform_carries_the_same_deny_list():
    """The Windows outlier is exactly how this defect survived: one file was
    more permissive than its siblings and nothing compared them."""
    lists = {p: sorted(_perms(p).get("deny", [])) for p in PLATFORMS}
    baseline = lists["linux"]
    for platform, rules in lists.items():
        assert rules == baseline, (
            f"{platform} deny list differs from linux.\n"
            f"  only in {platform}: {sorted(set(rules) - set(baseline))}\n"
            f"  missing from {platform}: {sorted(set(baseline) - set(rules))}"
        )

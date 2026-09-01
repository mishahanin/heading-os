"""The propose tier's brain deny bound Edit and left Write unnamed.

`build_skill_command` appended one `--disallowedTools` pattern for the Odin
brain directory, built by `_abs_pattern`, which hardcoded the string "Edit".
A Claude Code permission pattern binds exactly the tool it names -- the same
`code.claude.com/docs/en/permissions.md` page `_abs_pattern`'s own docstring
cites for the `//` anchor. So the mechanism covered one of the two tools that
can write a file, while the mode spec it enforces
(`.claude/skills/odin/references/mode-catalog.md`, `--propose` mode) states that
`knowledge/odin-brain/` "is never written in this mode, regardless of
confidence".

Measured before the fix:
  build_skill_command("odin", ["reflect", "--propose"], tier="propose")
  -> --disallowedTools contained Edit(//<data_root>/knowledge/odin-brain/**)
     and no pattern of any other tool for that path.

The gap is latent today, because the propose tier grants no Write. That is the
reason to close it rather than to shrug: the deny is the layer that has to hold
when a future tier gains a Write grant, and a deny that silently covers one tool
of two is the "looks like a control" failure this file's own DEFAULT_BUDGET_USD
comment describes.

Reads are deliberately still permitted. `reflect` reviews the brain; denying
reads would break the one skill on this tier. The deny protects write-integrity,
not confidentiality, and this file asserts that distinction so a later reading of
"sensitive brain directory" does not turn it into a read deny.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.heading_cli import (  # noqa: E402
    ODIN_BRAIN_DENY_REL,
    PROPOSE_WRITE_REL,
    SEND_DENY,
    _abs_pattern,
    build_skill_command,
)
from scripts.utils.paths import get_data_root  # noqa: E402

WRITE_TOOLS = ("Edit", "Write")


def _values_after(argv: list[str], flag: str) -> list[str]:
    """Every value between `flag` and the next `--option`."""
    out: list[str] = []
    seen = False
    for token in argv:
        if token == flag:
            seen = True
            continue
        if seen:
            if token.startswith("--"):
                break
            out.append(token)
    return out


def _brain_prefix() -> str:
    return str((get_data_root() / ODIN_BRAIN_DENY_REL).resolve()).lstrip("/")


def test_every_write_tool_is_denied_on_the_brain_directory() -> None:
    cmd = build_skill_command("odin", ["reflect", "--propose"], tier="propose")
    disallowed = _values_after(cmd, "--disallowedTools")
    assert disallowed, "the propose tier must emit a deny list"
    prefix = _brain_prefix()

    for tool in WRITE_TOOLS:
        expected = f"{tool}(//{prefix}/**)"
        assert expected in disallowed, (
            f"{tool} can write a file and is not denied on the brain directory; "
            f"deny list was {disallowed!r}"
        )


def _tools_granted_on(argv: list[str], directory: str) -> set[str]:
    """Every tool name carrying a path-scoped grant over `directory`.

    Derived from the command the tier actually emits, NOT from `WRITE_TOOLS`.
    `PROPOSE_WRITE_REL` is by definition the one directory this tier may WRITE,
    so a tool named in a pattern over it is a tool this tier can write with.
    """
    prefix = str((get_data_root() / directory).resolve()).lstrip("/")
    needle = f"(//{prefix}/**)"
    return {a[: -len(needle)] for a in _values_after(argv, "--allowedTools")
            if a.endswith(needle)}


def test_every_write_tool_THIS_TIER_GRANTS_is_denied_on_the_brain(tmp_path: Path) -> None:
    """The anti-decay half, and the one `WRITE_TOOLS` cannot supply.

    `WRITE_TOOLS` is a hand-maintained tuple inside this file, so it is the
    thing that falls behind - the same one-of-N shape the file is named for,
    reproduced in its own constant. Measured 2026-09-01: granting a THIRD
    write tool on this tier (`NotebookEdit(//<proposals>/**)`) with no matching
    brain deny left `test_every_write_tool_is_denied_on_the_brain_directory`
    GREEN. The only test that reddened was the grant-scope one below, whose
    message is about the grant list; an author who widened that assertion to
    admit the new tool - the natural next step - would have shipped a brain
    deny covering two of three write tools and no test would have said so.

    This derives the obligation from the emitted command instead: whatever set
    of tools the tier grants over its write directory, every one of them is
    denied on the brain directory. A new write tool cannot be granted without
    its deny.
    """
    cmd = build_skill_command("odin", ["reflect", "--propose"], tier="propose")
    granted = _tools_granted_on(cmd, PROPOSE_WRITE_REL)
    # Floor, so the check can never be green over an empty derived set: the
    # tier is known to grant both of these, and set-equality here is what makes
    # a SILENTLY DROPPED grant fail too, in the other direction.
    assert granted == set(WRITE_TOOLS), (
        "the tools this tier grants over its write directory changed; if that is "
        f"deliberate, WRITE_TOOLS must change with it. granted={sorted(granted)!r}"
    )
    disallowed = _values_after(cmd, "--disallowedTools")
    prefix = _brain_prefix()
    missing = [t for t in sorted(granted) if f"{t}(//{prefix}/**)" not in disallowed]
    assert not missing, (
        f"this tier grants {sorted(granted)!r} over {PROPOSE_WRITE_REL} but the "
        f"brain deny does not name {missing!r}; a permission pattern binds exactly "
        f"the tool it names, so the brain is writable by those tools"
    )


def test_the_brain_directory_is_not_read_denied() -> None:
    """reflect reviews the brain. A read deny here would break the mode."""
    cmd = build_skill_command("odin", ["reflect", "--propose"], tier="propose")
    disallowed = _values_after(cmd, "--disallowedTools")
    prefix = _brain_prefix()

    assert f"Read(//{prefix}/**)" not in disallowed
    assert "Read" in _values_after(cmd, "--allowedTools")


def test_abs_pattern_binds_the_tool_it_is_given(tmp_path: Path) -> None:
    """The parameter is the fix; a hardcoded tool name is what caused the gap."""
    assert _abs_pattern(tmp_path, "brain") == f"Edit(//{str(tmp_path).lstrip('/')}/brain/**)"
    assert (_abs_pattern(tmp_path, "brain", tool="Write")
            == f"Write(//{str(tmp_path).lstrip('/')}/brain/**)")
    # The stray-third-slash bug the docstring records must stay fixed for every tool.
    for tool in WRITE_TOOLS + ("Read",):
        pattern = _abs_pattern(tmp_path, "brain", tool=tool)
        assert pattern.startswith(f"{tool}(//")
        assert not pattern.startswith(f"{tool}(///")


def test_the_write_grant_is_still_scoped_to_the_proposals_directory() -> None:
    """Both directions: widening the deny must not have widened the grant."""
    cmd = build_skill_command("odin", ["reflect", "--propose"], tier="propose")
    allowed = _values_after(cmd, "--allowedTools")
    assert allowed, "the propose tier must emit an allow list"

    assert "Write" not in allowed, "a bare Write grant would escape every path scope"
    assert "Edit" not in allowed, "a bare Edit grant would escape every path scope"
    proposals = str((get_data_root() / PROPOSE_WRITE_REL).resolve()).lstrip("/")
    scoped = sorted(a for a in allowed if proposals in a)
    # Both write tools, on that one directory and nowhere else. `Edit` alone
    # could not CREATE the dated file the mode is specified to append to -
    # measured 2026-08-30, the harness Edit tool refuses a path that does not
    # exist - so the first `--propose` of any day had nothing to write into.
    assert scoped == [f"Edit(//{proposals}/**)", f"Write(//{proposals}/**)"]


def test_the_send_transports_are_still_denied_on_this_tier() -> None:
    """The deny list grew; nothing that was in it may have been displaced."""
    cmd = build_skill_command("odin", ["reflect", "--propose"], tier="propose")
    disallowed = _values_after(cmd, "--disallowedTools")
    assert SEND_DENY, "the send denylist must be non-empty"
    for entry in SEND_DENY:
        assert entry in disallowed, f"{entry} fell out of the propose tier deny list"


def test_lower_tiers_gain_no_brain_patterns() -> None:
    """The brain patterns belong to the propose tier alone."""
    prefix = _brain_prefix()
    for tier in ("read-only", "draft"):
        cmd = build_skill_command("state-check", [], tier=tier)
        joined = " ".join(cmd)
        assert prefix not in joined, f"the {tier} tier should not mention the brain path"


def test_repeated_calls_do_not_grow_the_shared_constants() -> None:
    """The deny list is built from a copy; two appends per call would compound."""
    before = list(SEND_DENY)
    first = _values_after(
        build_skill_command("odin", ["reflect", "--propose"], tier="propose"),
        "--disallowedTools")
    second = _values_after(
        build_skill_command("odin", ["reflect", "--propose"], tier="propose"),
        "--disallowedTools")

    assert first == second
    assert list(SEND_DENY) == before

"""Frozen contract: the placeholder exclusion is whole-value, not prefix.

The defect this contract is written against, measured on 2026-07-31 across
5087 tracked files and 1327251 lines in both repositories:

    (?!(?i:your[-_]|changeme|example|placeholder|redacted|dummy|xxx|<))

is a PREFIX test. A value that merely BEGINS with a placeholder marker is
excluded whole, however long and however high-entropy its tail. Every one of
the seven word alternatives is defeated the same way, not just the one recorded
in the carried-forward note. A real password beginning "xxx" therefore passes
the commit-time hook, the blocking PreToolUse gate, AND the authoritative
push-time content scan, which is the one wall this workspace calls unbypassable.

The contract below fixes the required behaviour: the exclusion may fire only
when the value TAKEN WHOLE has placeholder shape. Every credential-shaped
sample here is assembled by runtime concatenation so this file never carries a
matching literal into the tracked tree, and so it cannot block its own push.

Authoring rule (pre-impl Phase 5): imports live inside test bodies, because the
implementation does not exist when this contract is frozen.
"""
import contextlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]

# Assembled, never written whole. Two tails, because the symbol-free one is the
# harder case: a narrowing that keys on punctuation alone still admits an
# alphanumeric password, and plenty of real passwords carry no symbol.
_KEY = "EXCHANGE_" + "PASSWORD"
_TAILS = {
    "with-symbol": "A9f" + "!q2K" + "zLm3" + "Rt7v",
    "symbol-free": "A9f" + "q2Kz" + "Lm3R" + "t7vB",
}

# The prefixes as the shipped exclusion spells them. "your" carries its
# separator because the current alternative is `your[-_]`.
MARKERS = ["your-", "your_", "changeme", "example", "placeholder", "redacted", "dummy", "xxx"]

BYPASSES = [(m, t) for m in MARKERS for t in sorted(_TAILS)]

# Placeholder values this workspace has already committed to leaving unflagged:
# the seven parametrized in tests/security/test_SEC_004_credential_patterns.py,
# plus the lines that live in the tracked corpus today. Flagging any of them
# would block the push of a legitimate file, so they are the contract's control.
REAL_PLACEHOLDERS = [
    "EXCHANGE_" + "PASSWORD=your-email-password",
    "EXCHANGE_" + "PASSWORD=your_password",
    "EXCHANGE_" + "PASSWORD=changeme123",
    "EXCHANGE_" + "PASSWORD=<your-password>",
    "EXCHANGE_" + "PASSWORD=ExampleValue",
    "EXCHANGE_" + "PASSWORD=placeholder-secret",
    "EXCHANGE_" + "PASSWORD=redacted-value",
    "EXCHANGE_" + "PASSWORD=your_exchange_password_here",
    "EXCHANGE_" + "PASSWORD=xxxxxxxx",
]


def _bypass(marker: str, tail_name: str) -> str:
    """A high-entropy value wearing a placeholder marker as a hat."""
    return f"{_KEY}={marker}{_TAILS[tail_name]}"


def _load_embedded_gate():
    """Load .claude/hooks/_dispatch.py for its embedded pattern copy.

    SystemExit is suppressed deliberately and narrowly: a hook module whose
    import parses argv would otherwise take the whole test session down. The
    same guard, for the same reason, is in _load_scanner_module in
    tests/security/test_SEC_004_credential_patterns.py.
    """
    path = _ROOT / ".claude" / "hooks" / "_dispatch.py"
    spec = importlib.util.spec_from_file_location("_contract_dispatch", str(path))
    mod = importlib.util.module_from_spec(spec)
    with contextlib.suppress(SystemExit):
        spec.loader.exec_module(mod)
    return mod


def _flagged_by(patterns, sample: str) -> bool:
    return any(rx.search(sample) for rx, _desc in patterns)


@pytest.mark.parametrize("marker,tail", BYPASSES)
def test_a_marker_prefixed_high_entropy_value_is_flagged(marker, tail):
    """Every word alternative, not only xxx, must stop hiding a real value."""
    from scripts.utils.secret_patterns import SECRET_PATTERNS

    assert _flagged_by(SECRET_PATTERNS, _bypass(marker, tail)), (
        f"a {tail} high-entropy value prefixed {marker!r} slipped past the scanner patterns"
    )


@pytest.mark.parametrize("marker,tail", BYPASSES)
def test_the_blocking_gate_copy_flags_the_same_bypass(marker, tail):
    """_dispatch.py keeps its own copy; the hole must close there too."""
    gate = _load_embedded_gate()

    assert _flagged_by(gate.SECRET_PATTERNS, _bypass(marker, tail)), (
        f"the PreToolUse gate still admits a {tail} value prefixed {marker!r}"
    )


@pytest.mark.parametrize("line", REAL_PLACEHOLDERS)
def test_a_whole_value_placeholder_is_still_excluded(line):
    """The control. Narrowing must not start flagging the real corpus."""
    from scripts.utils.secret_patterns import SECRET_PATTERNS

    assert not _flagged_by(SECRET_PATTERNS, line), (
        f"a legitimate placeholder became a finding and would block a push: {line!r}"
    )


def test_the_push_time_wall_refuses_a_file_carrying_the_bypass(tmp_path):
    """End to end through the authoritative wall, not just the pattern list."""
    victim = tmp_path / "leaked.env"
    victim.write_text(_bypass("xxx", "symbol-free") + "\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "secret-scanner.py"), str(victim)],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0, (
        "the push-time content scan accepted a file carrying a real credential "
        f"behind a placeholder prefix; stdout={result.stdout!r}"
    )


def test_the_scanner_still_accepts_a_file_of_only_placeholders(tmp_path):
    """The same wall, from the other side: no new false positive."""
    sample = tmp_path / "template.env"
    sample.write_text("\n".join(REAL_PLACEHOLDERS) + "\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "secret-scanner.py"), str(sample)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        "the wall began refusing a file of legitimate placeholders; "
        f"stdout={result.stdout!r}"
    )

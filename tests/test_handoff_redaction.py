"""The redactor, and the hook that must never write an unscannable handoff.

Every credential-shaped sample here is assembled at runtime. None is written
whole into this file: it is tracked, the engine repository is public, and the
prevent-secrets hook refuses the write. That refusal is correct and the
assembly is the workspace convention, not a workaround.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))


def _connection_string() -> str:
    """A URL carrying a userinfo pair. The literal that started this slice."""
    return "https://" + "x-access-token" + ":" + "not-a-real-token-value" + "@" + "github.com/owner/repo.git"


def _api_key() -> str:
    return "sk-ant-" + ("A" * 24)


def test_a_credential_shaped_span_is_replaced_by_a_named_marker():
    from scripts.utils.secret_patterns import redact

    out = redact("the remote was " + _connection_string() + " at the time")

    assert "not-a-real-token-value" not in out
    assert "[REDACTED: connection string with inline credentials]" in out


def test_only_the_span_is_replaced_and_the_prose_survives():
    """A handoff gutted by redaction fails at its only job, which is to let the
    next session resume."""
    from scripts.utils.secret_patterns import redact

    out = redact("the remote was " + _connection_string() + " at the time")

    assert out.startswith("the remote was ")
    assert out.endswith(" at the time")
    assert "not-a-real-token-value" not in out


def test_every_pattern_family_is_redacted_not_only_the_one_that_bit_us():
    from scripts.utils.secret_patterns import redact

    out = redact("key " + _api_key() + " end")
    assert _api_key() not in out
    assert "[REDACTED: Anthropic API key]" in out


def test_text_carrying_no_secret_is_returned_unchanged():
    """Byte-identical, not merely equivalent. A redactor that reflows every
    handoff it touches is a redactor nobody will trust."""
    from scripts.utils.secret_patterns import redact

    original = "# Handoff\n\nNothing secret here.\n\n  indented\ttabbed\n"
    assert redact(original) == original


# The eleven line-break code points at issue: the three universal-newline
# forms readlines() honours, plus the eight str.splitlines() over-splits on
# and must therefore NOT be treated as a break here.
_LINE_BREAK_CODE_POINTS = [
    "\r", "\r\n", "\n",                     # universal newlines (readlines())
    "\x0b", "\x0c",                         # vertical tab, form feed
    "\x1c", "\x1d", "\x1e",                 # ASCII file/group/record separators
    "\x85",                                 # NEL
    "\u2028", "\u2029",                     # Unicode LINE/PARAGRAPH SEPARATOR
]


@pytest.mark.parametrize("code_point", _LINE_BREAK_CODE_POINTS)
def test_the_round_trip_is_byte_identical_across_every_line_break_code_point(code_point):
    """No secret anywhere, so the only question is whether redact() reproduces
    its input exactly regardless of which line-break code point it carries."""
    from scripts.utils.secret_patterns import redact

    original = "before" + code_point + "after" + code_point + "tail"
    assert redact(original) == original


def _allowlist_case_token_then_cr_then_secret() -> str:
    """The reviewer's exact repro: token on one universal-newline segment, a
    lone "\\r" boundary, then the secret on the next segment."""
    from scripts.utils.secret_patterns import ALLOWLIST_TOKEN

    return ("Reviewed the scanner. Lines carrying " + ALLOWLIST_TOKEN + " are skipped."
            + "\r" + "The remote was " + _connection_string() + " at the time.\n")


def _allowlist_case_secret_then_cr_then_token() -> str:
    """Same repro, reversed order."""
    from scripts.utils.secret_patterns import ALLOWLIST_TOKEN

    return ("The remote was " + _connection_string() + " at the time."
            + "\r" + "Reviewed the scanner. Lines carrying " + ALLOWLIST_TOKEN
            + " are skipped.\n")


def _allowlist_case_crlf_control() -> str:
    """Control: a real "\\r\\n" break between token and secret. `str.split("\\n")`
    already separates these onto two elements before any `\\r`-aware fix, so this
    case must pass both before and after the fix."""
    from scripts.utils.secret_patterns import ALLOWLIST_TOKEN

    return ("Reviewed the scanner. Lines carrying " + ALLOWLIST_TOKEN + " are skipped.\r\n"
            + "The remote was " + _connection_string() + " at the time.\n")


def _allowlist_case_plain_single_line_control() -> str:
    """Control: token and secret share one real line, exactly as the scanner
    sees it. Must pass both before and after the fix."""
    from scripts.utils.secret_patterns import ALLOWLIST_TOKEN

    return "sample " + _connection_string() + "  # " + ALLOWLIST_TOKEN


@pytest.mark.parametrize("make_text", [
    _allowlist_case_token_then_cr_then_secret,
    _allowlist_case_secret_then_cr_then_token,
    _allowlist_case_crlf_control,
    _allowlist_case_plain_single_line_control,
], ids=[
    "lone_cr_token_then_secret",
    "lone_cr_secret_then_token",
    "crlf_control",
    "plain_single_line_control",
])
def test_the_allowlist_token_suppresses_redaction_where_it_suppresses_scanning(
        make_text, tmp_path):
    """The allowlist decision must match the REAL scanner's line boundaries,
    not redact()'s own idea of a line. A lone "\\r" ends a line for scan_file's
    readlines() (universal newlines) but was, before this fix, invisible to
    redact()'s str.split("\\n"), so an allowlist token on one side of a lone
    "\\r" wrongly suppressed redaction of a secret on the other side.

    Asserted through the real scanner subprocess, not through redact()'s own
    notion of what it did: an identity function would satisfy a bare
    `redact(x) == x` assertion here and hide exactly this bug.
    """
    from scripts.utils.secret_patterns import redact

    text = make_text()
    target = tmp_path / "handoff.md"
    target.write_text(redact(text), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(ENGINE / "scripts" / "secret-scanner.py"), str(target)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout


def test_the_markdown_password_pattern_eats_its_whole_line():
    """The documented exception to "the span, never the line", pinned.

    Pattern 15 ends in a greedy run to end of line, so a line opening with a
    bolded Password label loses its prose too. That is the correct trade: text
    after that label is exactly where a password sits, and narrowing the pattern
    to spare prose would weaken detection.

    It is pinned rather than left implicit because this is the shape a handoff
    summarising a credentials discussion actually produces, and a future author
    who narrows the pattern must change this assertion deliberately instead of
    finding out from a handoff that quietly lost a paragraph.
    """
    from scripts.utils.secret_patterns import redact

    label = "**" + "Password:" + "**"
    out = redact(label + " we discussed the quarterly rotation policy\ntail line\n")

    assert out.startswith("[REDACTED: Plaintext password in markdown]")
    assert "quarterly rotation policy" not in out
    assert "tail line" in out          # the damage stops at the line boundary


def test_a_line_with_two_secrets_loses_both():
    from scripts.utils.secret_patterns import redact

    out = redact(_api_key() + " and " + _connection_string())
    assert _api_key() not in out
    assert "not-a-real-token-value" not in out


def test_the_output_of_the_redactor_passes_the_scanner(tmp_path):
    """The property that actually matters, asserted through the REAL scanner
    rather than through the redactor's own idea of what a secret is."""
    from scripts.utils.secret_patterns import redact

    target = tmp_path / "handoff.md"
    target.write_text(redact("remote " + _connection_string() + "\nkey " + _api_key() + "\n"),
                      encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(ENGINE / "scripts" / "secret-scanner.py"), str(target)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout


def test_no_redaction_marker_reintroduces_a_prefilter_needle():
    """Pins the data property that `iter_patterns` in `redact` relies on for
    correctness rather than proving it structurally.

    `iter_patterns(line)` closes over the string it was called with, but
    `redact`'s loop rebinds that same name as it substitutes matches in. If
    some future pattern's own redaction marker (`REDACTED: {description}`)
    ever contained a `REQUIRED_SUBSTRING` needle, a match on pattern A could
    inject that needle into the text pattern B's prefilter already decided
    against on the ORIGINAL string, silently reviving a pattern the prefilter
    had ruled out for this line. This test catches that the day a new needle
    or a new description makes it true, not after.
    """
    from scripts.utils.secret_patterns import (
        REDACTION_TEMPLATE, REQUIRED_SUBSTRING, SECRET_PATTERNS)

    descriptions = [description for _pattern, description in SECRET_PATTERNS]
    for needle in REQUIRED_SUBSTRING.values():
        for description in descriptions:
            marker = REDACTION_TEMPLATE.format(description=description)
            assert needle not in marker, (
                f"prefilter needle {needle!r} appears in the redaction marker "
                f"for {description!r}: {marker!r}")


# ============================================================
# The hook: the composition, and the promise that it never loses a handoff
# ============================================================

def _load_hook_sandboxed(tmp_path, monkeypatch):
    """Load checkpoint-save.py with BOTH of its write targets redirected.

    Two targets, and missing either one damages the live workspace:

      - HANDOFF_DIR resolves through get_outputs_dir() -> get_data_root(), which
        reads HEADING_OS_DATA at call time and computes HANDOFF_DIR at module
        exec. So the env var must be set BEFORE exec_module, not after.
      - STATE_PATH does NOT go through the data root. It is
        WORKSPACE/.claude/state/checkpoint-state.json, an ENGINE path, and
        main() writes it unconditionally at the end. A test that forgets this
        overwrites the live session's checkpoint state.

    The assertion below is not decoration. If the redirect ever silently fails,
    the next line would write into the operator's real handoff archive.
    """
    import importlib.util

    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    spec = importlib.util.spec_from_file_location(
        "checkpoint_save_under_test", ENGINE / ".claude" / "hooks" / "checkpoint-save.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setattr(module, "STATE_PATH", tmp_path / "checkpoint-state.json")

    # Operand order is not style: ruff's SIM300 flags the natural spelling here
    # as a Yoda condition, and lint-ratchet turns that into a blocked commit.
    # Measured at the pre-impl gate, on this exact line.
    assert tmp_path in module.HANDOFF_DIR.parents or tmp_path == module.HANDOFF_DIR, (
        f"sandbox escaped: HANDOFF_DIR is {module.HANDOFF_DIR}, not under {tmp_path}. "
        f"HEADING_OS_DATA only reaches HANDOFF_DIR through get_outputs_dir(), which "
        f"honours it ONLY when is_ceo_workspace() is true (scripts/utils/workspace.py:272); "
        f"on a non-CEO clone it resolves through get_personal_root() instead, which need "
        f"not sit under the data root. If you are running on such a clone, this failure is "
        f"the sandbox refusing to write outside tmp_path, which is correct: fix the "
        f"harness, never the assertion.")
    return module


def _feed(module, monkeypatch, summary: str):
    """Drive the hook the way Claude Code does: one JSON payload on stdin."""
    import io
    import json

    payload = {"session_id": "s", "trigger": "manual",
               "compact_summary": summary, "transcript_path": "/dev/null"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    module.main()
    return module


def _run_hook(tmp_path, monkeypatch, summary: str):
    return _feed(_load_hook_sandboxed(tmp_path, monkeypatch), monkeypatch, summary)


def test_the_hook_writes_an_archive_the_scanner_accepts(tmp_path, monkeypatch):
    """SC-1. The end-to-end span: a poisoned summary in, a clean file out."""
    module = _run_hook(tmp_path, monkeypatch,
                       "the remote was " + _connection_string() + " at the time")

    written = sorted(p for p in module.HANDOFF_DIR.rglob("*.md"))
    assert written, "the hook wrote no handoff at all"

    result = subprocess.run(
        [sys.executable, str(ENGINE / "scripts" / "secret-scanner.py"),
         *[str(p) for p in written]],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout


def test_both_summary_carrying_files_are_redacted(tmp_path, monkeypatch):
    """The dated archive AND .latest/summary.md carry the summary. Redacting one
    and not the other would leave the wall blocked by the other."""
    module = _run_hook(tmp_path, monkeypatch,
                       "remote " + _connection_string() + " here")

    bodies = [p.read_text(encoding="utf-8") for p in module.HANDOFF_DIR.rglob("*.md")]
    assert len(bodies) >= 2
    for body in bodies:
        assert "not-a-real-token-value" not in body


def test_the_surrounding_summary_still_reaches_the_archive(tmp_path, monkeypatch):
    """Redaction must not cost the handoff its usefulness."""
    module = _run_hook(tmp_path, monkeypatch,
                       "Task 3 is done. remote " + _connection_string() + " . Next: Task 4.")

    body = next(iter(module.HANDOFF_DIR.glob("*.md"))).read_text(encoding="utf-8")
    assert "Task 3 is done." in body
    assert "Next: Task 4." in body


def test_a_redactor_that_raises_quarantines_rather_than_losing_the_handoff(
        tmp_path, monkeypatch, capsys):
    """SC-3. The hook runs AFTER the context is gone, so a handoff it fails to
    write cannot be regenerated by anyone."""
    module = _load_hook_sandboxed(tmp_path, monkeypatch)

    def _boom(_text):
        raise RuntimeError("redactor exploded")

    monkeypatch.setattr(module, "redact", _boom)
    _feed(module, monkeypatch, "plain summary, no secret")

    quarantined = list(module.QUARANTINE_DIR.rglob("*.md"))
    assert quarantined, "the handoff was lost when the redactor raised"
    assert "plain summary, no secret" in quarantined[0].read_text(encoding="utf-8")
    assert "redactor exploded" in capsys.readouterr().err


def test_a_quarantined_summary_never_reaches_the_tracked_pointer(
        tmp_path, monkeypatch):
    """The half that makes quarantine better than a raw write.

    Writing the unredacted summary where it normally goes would resurrect the
    original incident: the wall refuses and the backup is blocked, now rarely
    and undiagnosed. The pointer must name the quarantine WITHOUT reproducing
    the text that could not be redacted.
    """
    module = _load_hook_sandboxed(tmp_path, monkeypatch)

    def _boom(_text):
        raise RuntimeError("redactor exploded")

    monkeypatch.setattr(module, "redact", _boom)
    _feed(module, monkeypatch, "SENSITIVE-MARKER-" + "abc123")

    pointer = (module.LATEST_DIR / "summary.md").read_text(encoding="utf-8")
    assert "SENSITIVE-MARKER-" + "abc123" not in pointer
    assert "QUARANTINED" in pointer
    assert str(module.QUARANTINE_DIR) in pointer

    # And nothing outside the quarantine carries it either.
    outside = [p for p in module.HANDOFF_DIR.rglob("*")
               if p.is_file() and module.QUARANTINE_DIR not in p.parents]
    for path in outside:
        assert "SENSITIVE-MARKER-" + "abc123" not in path.read_text(encoding="utf-8")


# ============================================================
# The alarm state: every channel around the quarantine must tell the truth
# ============================================================

def _raise_exploded(_text):
    raise RuntimeError("redactor exploded")


def _quarantine_run(tmp_path, monkeypatch, summary: str, redactor):
    """Drive the hook down the quarantine branch with a chosen broken redactor."""
    module = _load_hook_sandboxed(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "redact", redactor)
    _feed(module, monkeypatch, summary)
    return module


def _outside_quarantine(module):
    return [p for p in module.HANDOFF_DIR.rglob("*")
            if p.is_file() and module.QUARANTINE_DIR not in p.parents]


def _system_message(capsys) -> tuple[str, str]:
    """The hook's one stdout line, decoded, plus stderr."""
    import json

    captured = capsys.readouterr()
    return json.loads(captured.out.strip().splitlines()[-1])["systemMessage"], captured.err


def test_the_tracked_pointer_never_carries_the_exception_message(
        tmp_path, monkeypatch, capsys):
    """An exception MESSAGE is a channel that can carry the summary text.

    The pointer at .latest/summary.md is tracked, and the whole point of the
    quarantine is that nothing outside it reproduces the text that could not be
    redacted. Interpolating `{exc}` there hands the text straight back into the
    tracked tree by a side door.

    Not reachable through today's redact(), whose every failure mode is a pure
    str.split / pattern.sub with a fixed replacement template. It is reachable
    the moment redact() is swapped or broken, which is the exact premise the
    guarded import above exists on, and neither existing quarantine test can see
    it because both raise RuntimeError("redactor exploded"), a message that
    structurally cannot carry the summary.
    """
    marker = "LEAKY-MARKER-" + "xyz789"

    def _boom_carrying_the_text(text):
        raise ValueError("failed on input: " + text)

    module = _quarantine_run(
        tmp_path, monkeypatch, marker + " and some ordinary prose",
        _boom_carrying_the_text)

    for path in _outside_quarantine(module):
        assert marker not in path.read_text(encoding="utf-8"), (
            f"{path} carries the summary text via the exception message")

    quarantined = [p for p in module.QUARANTINE_DIR.rglob("*") if p.is_file()]
    assert quarantined, "the handoff was lost"
    assert any(marker in p.read_text(encoding="utf-8") for p in quarantined)

    _, err = _system_message(capsys)
    assert marker in err, "the full exception must still reach stderr, which is not tracked"


def test_the_quarantine_system_message_does_not_claim_a_save(
        tmp_path, monkeypatch, capsys):
    """Channel (a). The systemMessage is the ONE channel the operator and the
    assistant actually see, and on the alarm path it reported a save and named a
    file that was never written."""
    module = _quarantine_run(tmp_path, monkeypatch, "plain summary",
                             _raise_exploded)

    message, _ = _system_message(capsys)
    assert "Saved handoff" not in message, f"the alarm path claims a save: {message}"
    assert "QUARANTIN" in message.upper(), f"the alarm is not named: {message}"

    quarantined = next(p for p in module.QUARANTINE_DIR.rglob("*") if p.is_file())
    rel = quarantined.relative_to(tmp_path).as_posix()
    assert rel in message, f"the quarantine is not named: {message}"

    # The dated archive genuinely does not exist, so no channel may name it.
    assert not list(module.HANDOFF_DIR.glob("*.md"))


def _handoff_refs(text: str) -> list[str]:
    """Every handoff-archive path the artifact names, @-prefixed or bare."""
    import re

    return [m.rstrip(".,;:)").lstrip("@")
            for m in re.findall(r"@?outputs/operations/handoff-archive/\S+", text)]


def test_the_quarantine_continuation_prompt_names_no_file_that_was_never_written(
        tmp_path, monkeypatch):
    """Channel (b). The prompt told the next session to read a dated archive that
    the quarantine branch never wrote."""
    module = _quarantine_run(tmp_path, monkeypatch, "plain summary", _raise_exploded)

    prompt = (module.LATEST_DIR / "prompt.md").read_text(encoding="utf-8")
    refs = _handoff_refs(prompt)
    assert refs, f"the prompt points the next session at nothing:\n{prompt}"
    for ref in refs:
        assert (tmp_path / ref).exists(), f"dangling reference {ref!r} in:\n{prompt}"

    # And it must say plainly what state this is.
    assert "QUARANTIN" in prompt.upper()
    assert "UNREDACTED" in prompt.upper()


def test_the_quarantined_body_names_no_file_that_was_never_written(
        tmp_path, monkeypatch):
    """The same defect one level in, where only a human looks.

    The body is built once and written down one of two branches, so its
    continuation section named the dated archive on BOTH. On the quarantine
    branch that file does not exist, and the reader being misdirected is a
    person recovering an unredacted handoff by hand, with no tooling to catch
    the dangling reference for them.
    """
    module = _quarantine_run(tmp_path, monkeypatch, "plain summary", _raise_exploded)

    quarantined = [p for p in module.QUARANTINE_DIR.rglob("*.md") if p.is_file()]
    assert len(quarantined) == 1, f"expected one quarantined file, got {quarantined}"
    body = quarantined[0].read_text(encoding="utf-8")

    refs = _handoff_refs(body)
    assert refs, f"the quarantined body points at nothing:\n{body}"
    for ref in refs:
        assert (tmp_path / ref).exists(), f"dangling reference {ref!r} in:\n{body}"

    assert "QUARANTIN" in body.upper()
    assert "UNREDACTED" in body.upper()


def test_the_normal_body_still_names_its_own_archive(tmp_path, monkeypatch):
    """The control. The conditional above must not blank the success branch."""
    module = _run_hook(tmp_path, monkeypatch, "plain summary with nothing secret in it")

    archived = [p for p in module.HANDOFF_DIR.glob("*.md") if p.is_file()]
    assert len(archived) == 1, f"expected one archived file, got {archived}"
    body = archived[0].read_text(encoding="utf-8")

    refs = _handoff_refs(body)
    assert refs, f"the archived body points at nothing:\n{body}"
    for ref in refs:
        assert (tmp_path / ref).exists(), f"dangling reference {ref!r} in:\n{body}"
    assert archived[0].name in body, "the body does not name its own archive file"


def test_the_quarantine_state_entry_records_no_dangling_path(tmp_path, monkeypatch):
    """Channel (c). checkpoint-state.json recorded the same nonexistent path."""
    import json

    module = _quarantine_run(tmp_path, monkeypatch, "plain summary", _raise_exploded)

    cs = json.loads(module.STATE_PATH.read_text(encoding="utf-8"))
    recorded = cs.get("last_compact_summary_path")
    if recorded is not None:
        assert (tmp_path / recorded).exists(), (
            f"state records a path that was never written: {recorded!r}")


def test_the_quarantine_pointer_renders_in_the_next_signal(tmp_path, monkeypatch):
    """MINOR 3. read_handoff() parses Source / Generated / ## Objective /
    ## Next steps, and the quarantine pointer carried none of them, so /next
    printed the handoff header with nothing under it - the loudest surface the
    operator has, rendering the alarm as blank."""
    import importlib.util

    _quarantine_run(tmp_path, monkeypatch, "plain summary", _raise_exploded)

    spec = importlib.util.spec_from_file_location(
        "next_signal_under_test", ENGINE / "scripts" / "next-signal.py")
    next_signal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(next_signal)

    handoff = next_signal.read_handoff()
    assert handoff is not None
    assert ".quarantine/" in handoff["source"], (
        f"the pointer does not name the quarantine as its source: {handoff['source']!r}")
    assert handoff["objective"], "the alarm renders blank under the handoff header"

    rendered = next_signal.render_text({"handoff": handoff})
    assert "QUARANTIN" in rendered.upper()


def test_a_redactor_that_returns_a_non_string_quarantines_too(
        tmp_path, monkeypatch, capsys):
    """MINOR 5. The guarded import covers the RAISING failure only.

    A redact() that returns None never raises, so the archive was written with
    the literal string "None" as its whole Summary section: the handoff
    destroyed, zero stderr, and a systemMessage reporting success.
    """
    module = _quarantine_run(tmp_path, monkeypatch, "the real summary body",
                             lambda _text: None)

    quarantined = [p for p in module.QUARANTINE_DIR.rglob("*") if p.is_file()]
    assert quarantined, "a non-raising broken redactor destroyed the handoff"
    body = quarantined[0].read_text(encoding="utf-8")
    assert "the real summary body" in body
    assert "\nNone\n" not in body

    message, err = _system_message(capsys)
    assert "Saved handoff" not in message
    assert "TypeError" in err

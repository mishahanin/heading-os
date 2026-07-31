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
    """Token on one universal-newline segment, a lone "\\r" boundary, then the
    secret on the next segment."""
    from scripts.utils.secret_patterns import ALLOWLIST_TOKEN

    return ("Reviewed the scanner. Lines carrying " + ALLOWLIST_TOKEN + " are skipped."
            + "\r" + "The remote was " + _connection_string() + " at the time.\n")


def _allowlist_case_secret_then_cr_then_token() -> str:
    """Same shape, reversed order."""
    from scripts.utils.secret_patterns import ALLOWLIST_TOKEN

    return ("The remote was " + _connection_string() + " at the time."
            + "\r" + "Reviewed the scanner. Lines carrying " + ALLOWLIST_TOKEN
            + " are skipped.\n")


def _allowlist_case_crlf_control() -> str:
    """A real "\\r\\n" break between token and secret."""
    from scripts.utils.secret_patterns import ALLOWLIST_TOKEN

    return ("Reviewed the scanner. Lines carrying " + ALLOWLIST_TOKEN + " are skipped.\r\n"
            + "The remote was " + _connection_string() + " at the time.\n")


def _allowlist_case_plain_single_line_control() -> str:
    """Token and secret share one real line, exactly as the scanner sees it."""
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
def test_the_allowlist_token_does_not_suppress_redaction(make_text, tmp_path):
    """The redactor redacts regardless of the allowlist marker, and the scanner
    still accepts what it produced.

    REPLACES test_the_allowlist_token_suppresses_redaction_where_it_suppresses_scanning,
    which asserted the opposite. That behaviour mirrored scan_file so the
    redactor and the wall would agree line for line, and the mirroring was the
    stated reason the invariant held by construction. It is wrong here. The
    allowlist marker is a human-authored, reviewed annotation in a SOURCE file.
    A compact summary is machine-generated and reviewed by nobody, and the
    marker reaches one by accident, most often in a session ABOUT the secret
    scanner, which is the exact session shape that produced the original
    incident. So a generated summary that happens to mention the marker no
    longer carries a live credential past both the redactor and the wall.

    Dropping the check only ever redacts MORE, so the invariant is strictly
    stronger, not weaker: whatever the scanner would flag is a subset of what
    the redactor now removes. Asserted through the real scanner subprocess
    rather than through redact()'s own idea of what it did.
    """
    from scripts.utils.secret_patterns import redact

    text = make_text()
    out = redact(text)
    assert "not-a-real-token-value" not in out, (
        "the allowlist marker still suppresses redaction")

    target = tmp_path / "handoff.md"
    target.write_text(out, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(ENGINE / "scripts" / "secret-scanner.py"), str(target)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout


def test_prose_mentioning_the_allowlist_marker_beside_a_key_still_loses_the_key(
        tmp_path):
    """The reviewer's exact shape: one segment carrying BOTH an ordinary
    sentence about the allowlist convention and a live credential.

    Measured before the fix: redact returned the input unchanged and the wall
    accepted it, so the credential reached a tracked file and left the machine.
    """
    from scripts.utils.secret_patterns import ALLOWLIST_TOKEN, redact

    text = ("we discussed the " + ALLOWLIST_TOKEN + " convention and the key "
            + _api_key())
    out = redact(text)

    assert _api_key() not in out
    assert "[REDACTED: Anthropic API key]" in out
    assert "we discussed the " in out, "the prose did not survive"

    target = tmp_path / "handoff.md"
    target.write_text(out, encoding="utf-8")
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


# ============================================================
# Nesting: redacting the inner span must not disarm the outer pattern
# ============================================================

def _nested_key_in_userinfo() -> tuple[str, str]:
    """The reviewer's exact F5 shape, plus the password that used to survive it.

    An API key sitting where a connection string's username goes. Replacing the
    inner span first breaks the connection-string pattern's character-class run,
    because the marker carries a space and a bracket, and the outer pattern then
    matches nothing at all - so the password walked straight through both the
    redactor and the wall.
    """
    userinfo_tail = "s3cret" + "pw" + "9142"
    text = ("https://" + _api_key() + ":" + userinfo_tail + "@" + "db.example.com/main")
    return text, userinfo_tail


def test_a_credential_nested_inside_another_does_not_shield_the_outer_one(tmp_path):
    from scripts.utils.secret_patterns import redact

    text, userinfo_tail = _nested_key_in_userinfo()
    out = redact(text)

    assert _api_key() not in out
    assert userinfo_tail not in out, "the outer credential survived the inner redaction"

    target = tmp_path / "handoff.md"
    target.write_text(out, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ENGINE / "scripts" / "secret-scanner.py"), str(target)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout


# Planted secret material, kept distinct per role so an assertion names exactly
# which one survived. Split literals: the file is tracked and public.
_SAMPLE_USERINFO_TAIL = "s3cr" + "etpw" + "0001"
_OUTER_USERINFO_TAIL = "s3cr" + "etpw" + "0002"


def _credential_samples() -> dict:
    """One sample per pattern family, assembled at runtime and never written
    whole, so the prevent-secrets hook has nothing to refuse."""
    alnum = "AB3c" * 6
    return {
        "anthropic": "sk-" + "ant-" + alnum,
        "perplexity": "pplx-" + alnum,
        "replicate": "r8" + "_" + alnum,
        "firecrawl": "fc-" + alnum,
        "context7": "ctx7" + "sk-" + alnum,
        "github_pat": "ghp" + "_" + alnum,
        "github_oauth": "gho" + "_" + alnum,
        "aws": "AKIA" + "AB3C" * 4,
        "slack_bot": "xoxb-" + "12345" + "-" + alnum,
        "slack_user": "xoxp-" + "12345" + "-" + alnum,
        "google_oauth": "ya29" + "." + (alnum * 3)[:56],
        "jwt": "eyJ" + alnum[:14] + "." + alnum[:14] + "." + alnum[:14],
        "pem": "-----BEGIN " + "PRIVATE KEY" + "-----",
        "connection": ("https://" + "carrier" + ":" + _SAMPLE_USERINFO_TAIL
                       + "@" + "db.example.com/main"),
    }


def _containers() -> dict:
    """The four patterns that can lexically ENCLOSE another credential.

    Some pairings are lexically impossible (a PEM header cannot sit in a URL's
    userinfo, which forbids spaces). They are generated anyway: an impossible
    nesting still produces a real inner match, and the property under test is
    that nothing the pattern vocabulary covers in the INPUT survives, never
    that a given pairing forms a valid outer match.
    """
    return {
        "conn_user": lambda inner: (
            "https://" + inner + ":" + _OUTER_USERINFO_TAIL + "@" + "db.example.com/main"),
        "conn_password": lambda inner: (
            "https://" + "carrier" + ":" + inner + "@" + "db.example.com/main"),
        "markdown_password": lambda inner: "**" + "Password:" + "**" + " " + inner,
        "env_password": lambda inner: "EXCHANGE_" + "PASSWORD" + "=" + inner,
    }


def _wrap_variants(core: str, token: str) -> list:
    """The shapes a generated handoff actually produces around a credential."""
    return [
        core,
        "Task 3 is done. The remote was " + core,
        core + " and then Task 4 continues.",
        "Task 3 is done. The remote was " + core + " and then Task 4 continues.",
        "we discussed the " + token + " convention and " + core,
        "line one\nTask 3 used " + core + "\nline three\n",
        "carriage\rTask 3 used " + core + "\rtail\n",
    ]


def _nested_corpus(padding_rounds: int = 0, seed: int = 20260731) -> list:
    """Differential-fuzz corpus of (text, planted-needles) pairs.

    Credential families nested inside one another in BOTH orders, each wrapped
    in the prose shapes a generated handoff produces. Deterministic by default;
    `padding_rounds` adds seeded random filler around every case, which is how
    the larger ad-hoc run was driven.
    """
    import random

    from scripts.utils.secret_patterns import ALLOWLIST_TOKEN

    samples = _credential_samples()
    containers = _containers()

    cores = [(value, (value,)) for value in samples.values()]
    for name, build in containers.items():
        extra = (_OUTER_USERINFO_TAIL,) if name == "conn_user" else ()
        for value in samples.values():
            cores.append((build(value), (value, *extra)))
    # Both orders: every ordered pair of containers, so A(B(x)) and B(A(x)) are
    # both present.
    for outer_name, outer in containers.items():
        for inner_name, inner in containers.items():
            extra = tuple(
                _OUTER_USERINFO_TAIL for name in (outer_name, inner_name)
                if name == "conn_user")[:1]
            for key in ("anthropic", "jwt", "connection"):
                cores.append((outer(inner(samples[key])), (samples[key], *extra)))

    corpus = []
    for core, needles in cores:
        needle_set = tuple(dict.fromkeys(
            (*needles, _SAMPLE_USERINFO_TAIL) if _SAMPLE_USERINFO_TAIL in core else needles))
        for text in _wrap_variants(core, ALLOWLIST_TOKEN):
            corpus.append((text, needle_set))

    if padding_rounds:
        rng = random.Random(seed)  # noqa: S311 - seeded fuzz filler, not crypto
        alphabet = "abcdefghijklmnopqrstuvwxyz0123456789 .,:/@=*-_\r\n"
        padded = []
        for text, needle_set in corpus:
            for _ in range(padding_rounds):
                head = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 24)))
                tail = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 24)))
                padded.append((head + text + tail, needle_set))
        corpus.extend(padded)
    return corpus


def _scanner_lines(text: str) -> list:
    """The line split scan_file gets for free from readlines(): the three
    universal-newline forms, and nothing else."""
    import re

    return re.split(r"\r\n|\r|\n", text)


def _needle_coverage(text: str, needle: str):
    """How many occurrences of needle the pattern vocabulary puts INSIDE a match,
    how many it leaves outside, and what the first covering pattern was.

    Computed line by line, exactly as the wall computes it, and per OCCURRENCE
    rather than per string. A needle no pattern reached was never a credential
    the wall would have flagged, so the redactor owes nothing for that
    occurrence - and the degenerate nestings in this corpus repeat one literal
    both inside a match and outside it, so counting the string once would
    demand the removal of material the wall never objected to.
    """
    from scripts.utils.secret_patterns import SECRET_PATTERNS

    covered = 0
    uncovered = 0
    first = None
    for line in _scanner_lines(text):
        spans = []
        for pattern, description in SECRET_PATTERNS:
            for match in pattern.finditer(line):
                spans.append((match.start(), match.end(), description))
        start = line.find(needle)
        while start != -1:
            end = start + len(needle)
            enclosing = next(
                (d for s, e, d in spans if s <= start and end <= e), None)
            if enclosing is None:
                uncovered += 1
            else:
                covered += 1
                first = first or enclosing
            start = line.find(needle, start + 1)
    return covered, uncovered, first


def test_the_nested_corpus_loses_every_credential_the_wall_would_have_flagged():
    """The differential fuzz, committed rather than run once and reported.

    The assertion is deliberately NOT "the scanner is quiet on the output". The
    scanner is quiet on the broken output too, which is the whole shape of F5:
    redacting the inner span breaks the outer pattern's character-class run, so
    the wall stops seeing the password it used to refuse and accepts it. A fuzz
    that only re-ran the scanner measured zero survivors against the defect.

    The property that catches it: for every planted needle the pattern
    vocabulary COVERS in the input, no byte of that needle survives the
    redaction. Coverage is computed line by line, the way scan_file computes it,
    so a needle no pattern reached is correctly exempt.
    """
    from scripts.utils.secret_patterns import redact

    corpus = _nested_corpus()
    assert len(corpus) >= 500, f"corpus shrank to {len(corpus)} cases"

    survivors = []
    for text, needles in corpus:
        out = redact(text)
        for needle in needles:
            covered, uncovered, description = _needle_coverage(text, needle)
            if covered and out.count(needle) > uncovered:
                survivors.append((description, text, out))

    assert not survivors, (
        f"{len(survivors)} of {len(corpus)} cases kept covered credential material; "
        f"first: pattern={survivors[0][0]!r}\n  in : {survivors[0][1]!r}\n"
        f"  out: {survivors[0][2]!r}")


def test_the_nested_corpus_leaves_the_scanner_nothing_to_find(tmp_path):
    """The same corpus through the REAL scanner, one subprocess for all of it.

    Necessary but not sufficient (see the test above for why), and kept because
    it is the property the wall actually enforces: the count of files the
    scanner objects to must be zero.
    """
    from scripts.utils.secret_patterns import redact

    corpus = _nested_corpus()
    paths = []
    for index, (text, _needles) in enumerate(corpus):
        path = tmp_path / f"case-{index:05d}.md"
        path.write_text(redact(text), encoding="utf-8")
        paths.append(str(path))

    result = subprocess.run(
        [sys.executable, str(ENGINE / "scripts" / "secret-scanner.py"), *paths],
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


def _feed_payload(module, monkeypatch, payload: dict):
    """Drive the hook the way Claude Code does: one JSON payload on stdin."""
    import io
    import json

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    module.main()
    return module


def _feed(module, monkeypatch, summary: str):
    return _feed_payload(module, monkeypatch, {
        "session_id": "s", "trigger": "manual",
        "compact_summary": summary, "transcript_path": "/dev/null"})


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
# The other three payload fields, which also reach tracked files
# ============================================================

def _slug_surviving_key() -> str:
    """A credential made only of characters safe_slug keeps verbatim.

    session_id is slugged into the FILENAME, and that filename is then quoted
    back into the body, both pointers and the state entry. A key of alphanumerics
    and hyphens passes safe_slug untouched, so the credential rides the filename
    into four tracked artifacts.
    """
    return "sk-" + "ant-" + ("B" * 25)


_FIELD_POISON = {
    # Reaches the body and both pointers (each carries "Trigger: compact / ...").
    "trigger": _connection_string,
    # Reaches the filename, and through it the body, both pointers and the state.
    "session_id": _slug_surviving_key,
    # Reaches the body.
    "transcript_path": _connection_string,
}


@pytest.mark.parametrize("field", sorted(_FIELD_POISON))
def test_every_payload_field_is_redacted_not_only_the_summary(
        field, tmp_path, monkeypatch):
    """redact() was applied to compact_summary alone. The other three fields go
    into the same tracked files verbatim, so any one of them blocks the wall and
    with it the backup of BOTH repositories - the exact incident this slice
    exists to remove, reached by a different door.
    """
    module = _load_hook_sandboxed(tmp_path, monkeypatch)
    poison = _FIELD_POISON[field]()

    payload = {"session_id": "s", "trigger": "manual",
               "compact_summary": "plain summary, nothing secret",
               "transcript_path": "/dev/null"}
    payload[field] = poison
    _feed_payload(module, monkeypatch, payload)

    files = [p for p in module.HANDOFF_DIR.rglob("*") if p.is_file()]
    assert files, "the hook wrote nothing"

    for path in files:
        assert poison not in path.read_text(encoding="utf-8"), f"{path} carries it"
        assert poison not in path.name, f"the filename carries it: {path.name}"

    result = subprocess.run(
        [sys.executable, str(ENGINE / "scripts" / "secret-scanner.py"),
         *[str(p) for p in files]],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout


def test_a_redacted_session_id_still_produces_a_sane_filename(tmp_path, monkeypatch):
    """The marker carries a colon, a space and brackets, and it lands in the
    slug. The name must stay something a shell and a human can both handle."""
    import re

    module = _load_hook_sandboxed(tmp_path, monkeypatch)
    payload = {"session_id": _slug_surviving_key(), "trigger": "manual",
               "compact_summary": "plain summary", "transcript_path": ""}
    _feed_payload(module, monkeypatch, payload)

    archived = [p for p in module.HANDOFF_DIR.glob("*.md") if p.is_file()]
    assert len(archived) == 1, f"expected one archived file, got {archived}"
    assert re.fullmatch(r"[A-Za-z0-9._-]+", archived[0].name), (
        f"the redaction marker leaked into the filename: {archived[0].name!r}")


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


def test_the_success_pointer_renders_in_the_next_signal(tmp_path, monkeypatch):
    """The common case, which was blank while the rare alarm case was rich.

    The quarantine pointer got the parseable shape first, so after that fix
    /next rendered the FAILURE path better than the SUCCESS path. Measured
    against the live archive: the 20 newest handoffs all parsed to an empty
    objective and zero next steps, so /next has printed its handoff header over
    nothing after every successful compact since the archive began.
    """
    import importlib.util

    _run_hook(tmp_path, monkeypatch, "plain summary with nothing secret in it")

    spec = importlib.util.spec_from_file_location(
        "next_signal_success", ENGINE / "scripts" / "next-signal.py")
    next_signal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(next_signal)

    handoff = next_signal.read_handoff()
    assert handoff is not None
    assert handoff["source"], "the pointer names no source"
    assert ".quarantine/" not in handoff["source"], "this is the success path"
    assert handoff["objective"], "the handoff header renders over nothing"
    assert handoff["next_steps"], "no next step was parsed"
    assert handoff["source"] in " ".join(handoff["next_steps"]), (
        "the steps do not point at the archive that was actually written")

    rendered = next_signal.render_text({"handoff": handoff})
    assert handoff["objective"] in rendered


def test_a_summary_carrying_its_own_headings_cannot_hijack_the_next_signal(
        tmp_path, monkeypatch):
    """The pointer's fields must not be reachable from the model's own prose.

    Displacement was already impossible: read_handoff() keeps the FIRST
    objective and ignores Source/Generated below the first heading. APPENDING
    was not. A compact summary that happens to contain `## Next steps`, which
    this workspace's summaries routinely do, added its bullets to the pointer's
    own list, so /next rendered the previous session's steps as if they were
    this handoff's. `## Summary` is the last field-bearing heading the hook
    writes, so the parser stops there.
    """
    import importlib.util

    _run_hook(tmp_path, monkeypatch,
              "prose\n\n## Objective\n\nHIJACKED OBJECTIVE\n\n"
              "## Next steps\n\n- HIJACKED STEP ONE\n- HIJACKED STEP TWO\n\n"
              "Source: hijacked-source\n")

    spec = importlib.util.spec_from_file_location(
        "next_signal_hostile", ENGINE / "scripts" / "next-signal.py")
    next_signal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(next_signal)

    handoff = next_signal.read_handoff()
    assert "HIJACK" not in handoff["objective"], "the body displaced the objective"
    assert "hijacked-source" not in handoff["source"], "the body hijacked the source"
    joined = " ".join(handoff["next_steps"])
    assert "HIJACK" not in joined, f"the body appended its own steps: {handoff['next_steps']!r}"


def test_the_success_pointer_still_carries_the_summary_text(tmp_path, monkeypatch):
    """The control. Adding headings must not push the summary out of the file
    that checkpoint-inject.py reads, or the next session gets a shape and no
    content."""
    module = _run_hook(tmp_path, monkeypatch, "a distinctive line of prose")

    pointer = (module.LATEST_DIR / "summary.md").read_text(encoding="utf-8")
    assert "a distinctive line of prose" in pointer


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


# ============================================================
# A body that cannot be written at all: three defects, not one
# ============================================================

_LOSS_MARKER = "LOST-RUN-" + "marker42"
_PREVIOUS_MARKER = "PREVIOUS-RUN-" + "marker17"


def _block_the_quarantine(module):
    """The reviewer's repro: .quarantine is a regular FILE, so mkdir fails.

    Measured before the fix: return code 0, systemMessage "write failed", one
    file on disk (the blocker itself), and the summary text nowhere. The handoff
    was gone, the loudest channel did not say so, and because both pointer
    writes sat inside the same try after the body write, the PREVIOUS compact's
    .latest/summary.md survived untouched - so the next session resumed from a
    stale handoff with nothing marking it stale.
    """
    module.QUARANTINE_DIR.parent.mkdir(parents=True, exist_ok=True)
    module.QUARANTINE_DIR.write_text("a regular file, so mkdir fails\n",
                                     encoding="utf-8")


def _all_written(module):
    return [p for p in module.HANDOFF_DIR.rglob("*") if p.is_file()]


def test_a_body_that_cannot_be_written_says_LOST_not_write_failed(
        tmp_path, monkeypatch, capsys):
    """Defect one. The handoff is unrecoverable - this hook runs after the
    session context is discarded - and the one channel the operator sees called
    it a write failure, which reads like a retryable hiccup."""
    module = _load_hook_sandboxed(tmp_path, monkeypatch)
    _block_the_quarantine(module)
    monkeypatch.setattr(module, "redact", _raise_exploded)
    _feed(module, monkeypatch, _LOSS_MARKER + " and some ordinary prose")

    message, err = _system_message(capsys)
    assert "LOST" in message.upper(), f"the loss is not named: {message}"
    assert "Saved handoff" not in message
    assert _LOSS_MARKER not in message
    assert "OSError" in err or "FileExistsError" in err, err


def test_a_lost_body_still_leaves_a_pointer_that_is_not_the_previous_one(
        tmp_path, monkeypatch):
    """Defect two. Both pointer writes were collateral of the body write, so a
    failed body left the PREVIOUS compact's pointer in place and the next
    session resumed from it as though it were current."""
    first = _load_hook_sandboxed(tmp_path, monkeypatch)
    _feed(first, monkeypatch, _PREVIOUS_MARKER + " is the older handoff")
    pointer_path = first.LATEST_DIR / "summary.md"
    assert _PREVIOUS_MARKER in pointer_path.read_text(encoding="utf-8")

    second = _load_hook_sandboxed(tmp_path, monkeypatch)
    _block_the_quarantine(second)
    monkeypatch.setattr(second, "redact", _raise_exploded)
    _feed(second, monkeypatch, _LOSS_MARKER + " and some ordinary prose")

    pointer = pointer_path.read_text(encoding="utf-8")
    assert _PREVIOUS_MARKER not in pointer, "the stale pointer survived the loss"
    assert "LOST" in pointer.upper(), f"the pointer does not say what happened:\n{pointer}"

    prompt = (second.LATEST_DIR / "prompt.md").read_text(encoding="utf-8")
    assert "LOST" in prompt.upper(), f"the prompt does not say what happened:\n{prompt}"
    for ref in _handoff_refs(prompt):
        assert (tmp_path / ref).exists(), f"dangling reference {ref!r} in:\n{prompt}"


def test_a_lost_body_leaks_the_summary_nowhere(tmp_path, monkeypatch, capsys):
    """Defect three's floor. The text could not be redacted, so no tracked file
    may reproduce it - and on this path there is not even a quarantine to hold
    it."""
    module = _load_hook_sandboxed(tmp_path, monkeypatch)
    _block_the_quarantine(module)
    monkeypatch.setattr(module, "redact", _raise_exploded)
    _feed(module, monkeypatch, _LOSS_MARKER + " and some ordinary prose")

    for path in _all_written(module):
        assert _LOSS_MARKER not in path.read_text(encoding="utf-8"), f"{path} leaked it"
    assert _LOSS_MARKER not in module.STATE_PATH.read_text(encoding="utf-8")

    message, _err = _system_message(capsys)
    assert _LOSS_MARKER not in message


def test_a_failed_archive_write_on_the_success_branch_is_reported_as_lost_too(
        tmp_path, monkeypatch, capsys):
    """The same loss reached by the other branch: redaction succeeded, the
    archive write did not. Nothing about the reporting should differ."""
    module = _load_hook_sandboxed(tmp_path, monkeypatch)
    real_write = module.write_text_atomic

    def _refuse_the_body(path, content):
        if path.suffix == ".md" and path.parent == module.HANDOFF_DIR:
            raise OSError("no space left on device")
        return real_write(path, content)

    monkeypatch.setattr(module, "write_text_atomic", _refuse_the_body)
    _feed(module, monkeypatch, _LOSS_MARKER + " and some ordinary prose")

    message, err = _system_message(capsys)
    assert "LOST" in message.upper(), f"the loss is not named: {message}"
    assert "no space left on device" in err

    pointer = (module.LATEST_DIR / "summary.md").read_text(encoding="utf-8")
    assert "LOST" in pointer.upper()
    assert _LOSS_MARKER not in pointer

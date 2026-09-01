"""Two functions that looked covered and were not, held here by their real behaviour.

Story one: a resolver seven test files patched out of existence.

`scripts/utils/operator_identity._resolve_file` walks three file tiers to decide
which `operator.yaml` an instance runs on: the data overlay, then an engine-local
`config/operator.yaml` for a clone with no overlay, then the shipped
`scripts/operator.example.yaml`. Seven test files reach the module
(`tests/test_operator_seam.py`, `tests/bridge/test_sources_contacts.py`,
`tests/bridge/test_a_launcher_must_not_report_a_window_that_never_opened.py`,
`tests/test_eight_counters_that_reported_a_slice_as_the_whole.py`,
`tests/test_no_stdlib_shadowing.py`,
`tests/test_no_tenant_domain_is_compiled_into_the_engine.py`,
`tests/test_tz_resolver_invocation.py`), and 517 of their tests pass. Not one of
them ran the resolver. Every test in the dedicated seam file opens with

    monkeypatch.setattr(operator_identity, "_resolve_file", lambda: (f, True))

which replaces the whole function with a constant, so the tier the function
exists to implement was never executed. Measured 2026-08-29 in this worktree:
turning `return engine_local, True` into `return engine_local, False` (the
engine-local tier stops marking the instance configured) leaves all 517 tests in
all seven files green, with and without an overlay present.

So this file drives the REAL `_resolve_file` against real temporary directories,
steering it with `WORKSPACE_ROOT` and `HEADING_OS_DATA` instead of replacing it.
It never monkeypatches `_resolve_file`; a test below enforces that on this file's
own source, because patching it away is the defect being repaired, not a style
preference.

Story two: a line number counted one way and indexed another.

`scripts/utils/sanitize.scan_for_terms` computed each finding's line number by
counting `"\\n"`, then used that number to index into `content.splitlines()`.
`splitlines()` also breaks on `\\r`, `\\x0b`, `\\x0c`, `\\x85`, U+2028 and U+2029,
so on any content carrying one of those the printed source line came from a
different line than the number printed beside it. The verdict was right and the
evidence under it pointed somewhere else. Measured 2026-08-29: six of seven break
characters printed a line the term was not on. The module had zero test
references anywhere in `tests/` at the time, which is the larger half of it.

The fix routes both halves through one split; these tests pin one break character
each, so a regression that reintroduces splitting on any single one of them is
caught by the test named for it.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import scripts.utils.operator_identity as operator_identity
from scripts.utils.sanitize import scan_for_terms

# An invented identity. The engine repo is public and a pre-commit gate refuses
# real people and companies, so every fixture value here is made up.
OVERLAY_YAML = (
    "name: Ada Lovelace\n"
    "slug: ada-lovelace\n"
    "github_org: adalovelace\n"
    "voice_reference: reference/ada-voice.md\n"
    "email: ada@example.com\n"
)
ENGINE_LOCAL_YAML = (
    "name: Grace Hopper\n"
    "slug: grace-hopper\n"
    "github_org: gracehopper\n"
    "voice_reference: reference/grace-voice.md\n"
    "email: grace@example.com\n"
)

running_as_root = os.geteuid() == 0 if hasattr(os, "geteuid") else False
skip_if_root = pytest.mark.skipif(
    running_as_root,
    reason="chmod 000 does not deny root, so this euid cannot produce an unreadable file",
)


class Seam:
    """The two temporary roots the resolver reads, plus the two files it looks for."""

    def __init__(self, engine: Path, overlay: Path):
        self.engine = engine
        self.overlay = overlay
        self.engine_local_file = engine / "config" / "operator.yaml"
        self.overlay_file = overlay / "config" / "operator.yaml"


@pytest.fixture
def seam(tmp_path, monkeypatch):
    """A clean engine root and a clean data overlay, both empty of operator.yaml.

    `WORKSPACE_ROOT` steers `get_workspace_root()`, which decides where the
    engine-local tier looks. `HEADING_OS_DATA` steers `get_data_root()`, and
    through it `get_data_config_dir()`, which is where the overlay tier looks.
    Both are real directories on disk, so the resolver does real `exists()` work.
    """
    engine = tmp_path / "engine"
    (engine / "config").mkdir(parents=True)
    overlay = tmp_path / "overlay"
    (overlay / "config").mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_ROOT", str(engine))
    monkeypatch.setenv("HEADING_OS_DATA", str(overlay))
    for env in operator_identity._ENV_KEYS.values():
        monkeypatch.delenv(env, raising=False)
    operator_identity._reset_cache()
    yield Seam(engine, overlay)
    operator_identity._reset_cache()


def resolved_identity():
    """Re-resolve from scratch: the module caches, and every test mutates the disk."""
    operator_identity._reset_cache()
    return operator_identity.get_operator()


# ==========================================================================
# Tier 1 - the data overlay
# ==========================================================================

def test_the_overlay_tier_resolves_the_data_overlay_operator_yaml_and_calls_it_real(seam):
    seam.overlay_file.write_text(OVERLAY_YAML, encoding="utf-8")

    path, is_real = operator_identity._resolve_file()

    assert path == seam.overlay_file
    assert is_real is True
    assert resolved_identity()["slug"] == "ada-lovelace"
    assert operator_identity.operator_is_default() is False


def test_an_absent_overlay_file_falls_through_to_the_engine_local_tier(seam):
    seam.engine_local_file.write_text(ENGINE_LOCAL_YAML, encoding="utf-8")
    assert not seam.overlay_file.exists()

    path, is_real = operator_identity._resolve_file()

    assert path == seam.engine_local_file
    assert is_real is True


@skip_if_root
def test_an_unreadable_overlay_file_is_still_chosen_and_degrades_to_the_generic_identity(seam):
    seam.overlay_file.write_text(OVERLAY_YAML, encoding="utf-8")
    seam.overlay_file.chmod(0o000)

    path, is_real = operator_identity._resolve_file()

    # The resolver answers on existence, not on readability, so it still picks
    # the overlay. The read failure is absorbed one layer up, in _load.
    assert path == seam.overlay_file
    assert is_real is True
    assert resolved_identity()["slug"] == "operator"
    assert operator_identity.operator_is_default() is True


def test_an_empty_overlay_file_is_real_but_configures_nothing(seam):
    seam.overlay_file.write_text("", encoding="utf-8")

    path, is_real = operator_identity._resolve_file()

    assert path == seam.overlay_file
    assert is_real is True
    # An empty file supplies no key, so the instance still reports as default
    # even though a real file was chosen. That distinction is the whole point of
    # the second element of the tuple.
    assert resolved_identity()["slug"] == "operator"
    assert operator_identity.operator_is_default() is True


# ==========================================================================
# Tier 2 - the engine-local config/operator.yaml (the tier no test ran)
# ==========================================================================

def test_the_engine_local_tier_resolves_config_operator_yaml_when_the_overlay_has_none(seam):
    seam.engine_local_file.write_text(ENGINE_LOCAL_YAML, encoding="utf-8")
    assert not seam.overlay_file.exists()

    path, is_real = operator_identity._resolve_file()

    assert path == seam.engine_local_file
    assert path.parent == seam.engine / "config"
    assert is_real is True
    identity = resolved_identity()
    assert identity["slug"] == "grace-hopper"
    assert identity["github_org"] == "gracehopper"
    assert identity["name"] == "Grace Hopper"
    assert operator_identity.operator_is_default() is False
    assert operator_identity.operator_email_domain() == "example.com"


def test_an_absent_engine_local_file_falls_through_to_the_shipped_example(seam):
    assert not seam.overlay_file.exists()
    assert not seam.engine_local_file.exists()

    path, is_real = operator_identity._resolve_file()

    assert path == operator_identity._EXAMPLE_PATH
    assert is_real is False


@skip_if_root
def test_an_unreadable_engine_local_file_is_still_chosen_and_degrades_to_the_generic_identity(seam):
    seam.engine_local_file.write_text(ENGINE_LOCAL_YAML, encoding="utf-8")
    seam.engine_local_file.chmod(0o000)

    path, is_real = operator_identity._resolve_file()

    assert path == seam.engine_local_file
    assert is_real is True
    assert resolved_identity()["slug"] == "operator"
    assert operator_identity.operator_is_default() is True


def test_an_engine_local_path_that_is_a_directory_is_chosen_and_never_raises(seam):
    """The root-proof half of the unreadable case: a directory always fails read_text.

    `IsADirectoryError` is an `OSError`, which `_load` catches, so the promise in
    the module docstring - never raises, returns the generic dict on any read or
    parse error - has to hold here too.
    """
    seam.engine_local_file.mkdir(parents=True)

    path, is_real = operator_identity._resolve_file()

    assert path == seam.engine_local_file
    assert is_real is True
    assert resolved_identity()["slug"] == "operator"
    assert operator_identity.operator_is_default() is True


def test_an_engine_local_file_in_a_non_utf8_encoding_degrades_instead_of_raising(seam):
    """The decode class, on the read the module docstring promises never raises.

    `_load` caught `(OSError, yaml.YAMLError)`. `UnicodeDecodeError` is a
    `ValueError` and a SIBLING of `yaml.YAMLError`, and the decode fails inside
    `read_text` before the parser sees anything, so both handlers walked past it.
    An `operator.yaml` written in UTF-16 - or any single high byte from a Latin-1
    editor - raised out of `get_operator()`. Several scripts bind this seam at
    MODULE scope, so that traceback arrived during import, before argparse: the
    exact failure the docstring says was closed on 2026-08-30 for the
    `DataRootError` path, still open on the encoding path until 2026-09-01.

    Measured before the fix: `RAISED: UnicodeDecodeError 'utf-8' codec can't
    decode byte 0xff in position 0`.
    """
    seam.engine_local_file.write_bytes(ENGINE_LOCAL_YAML.encode("utf-16"))

    path, is_real = operator_identity._resolve_file()
    assert path == seam.engine_local_file
    assert is_real is True

    # No exception, and the documented sentinel rather than a half-read identity.
    identity = resolved_identity()
    assert identity["slug"] == "operator"
    assert identity["name"] == "Operator"
    assert operator_identity.operator_is_default() is True
    # The file is genuinely undecodable, so the guard is not passing on a file
    # that happened to be readable after all.
    with pytest.raises(UnicodeDecodeError):
        seam.engine_local_file.read_text(encoding="utf-8")


def test_a_single_high_byte_in_the_overlay_file_degrades_too(seam):
    """The Latin-1 shape, on the other file tier. One fix, both readers."""
    seam.overlay_file.write_bytes(b"name: Andr\xe9 Citro\xebn\nslug: andre\n")

    path, is_real = operator_identity._resolve_file()
    assert path == seam.overlay_file
    assert is_real is True
    assert resolved_identity()["slug"] == "operator"
    assert operator_identity.operator_is_default() is True


def test_an_empty_engine_local_file_is_real_but_configures_nothing(seam):
    seam.engine_local_file.write_text("", encoding="utf-8")

    path, is_real = operator_identity._resolve_file()

    assert path == seam.engine_local_file
    assert is_real is True
    assert resolved_identity()["slug"] == "operator"
    assert operator_identity.operator_is_default() is True


# ==========================================================================
# Tier 3 - the shipped example
# ==========================================================================

def test_the_shipped_example_is_the_last_resort_and_is_never_called_real(seam):
    path, is_real = operator_identity._resolve_file()

    assert path == operator_identity._EXAMPLE_PATH
    assert path.name == "operator.example.yaml"
    assert path.is_file(), "the engine must ship the example this tier depends on"
    assert is_real is False
    # The example carries values, and they load - but is_real False keeps the
    # instance reporting as unconfigured.
    assert resolved_identity()["slug"] == "operator"
    assert operator_identity.operator_is_default() is True


def test_an_absent_example_leaves_the_resolver_with_no_path_at_all(seam, tmp_path, monkeypatch):
    """Patching the example CONSTANT, never the resolver: the function still runs."""
    monkeypatch.setattr(operator_identity, "_EXAMPLE_PATH", tmp_path / "nowhere.yaml")

    path, is_real = operator_identity._resolve_file()

    assert path is None
    assert is_real is False
    assert resolved_identity()["slug"] == "operator"
    assert operator_identity.operator_is_default() is True


@skip_if_root
def test_an_unreadable_example_degrades_to_the_generic_identity_without_raising(
    seam, tmp_path, monkeypatch
):
    stand_in = tmp_path / "operator.example.yaml"
    stand_in.write_text(ENGINE_LOCAL_YAML, encoding="utf-8")
    stand_in.chmod(0o000)
    monkeypatch.setattr(operator_identity, "_EXAMPLE_PATH", stand_in)

    path, is_real = operator_identity._resolve_file()

    assert path == stand_in
    assert is_real is False
    assert resolved_identity()["slug"] == "operator"


def test_an_empty_example_yields_the_generic_identity(seam, tmp_path, monkeypatch):
    stand_in = tmp_path / "operator.example.yaml"
    stand_in.write_text("", encoding="utf-8")
    monkeypatch.setattr(operator_identity, "_EXAMPLE_PATH", stand_in)

    path, is_real = operator_identity._resolve_file()

    assert path == stand_in
    assert is_real is False
    identity = resolved_identity()
    assert identity["slug"] == "operator"
    assert identity["name"] == "Operator"
    assert operator_identity.operator_is_default() is True


# ==========================================================================
# Precedence between the tiers, asserted directly
# ==========================================================================

def test_the_overlay_beats_the_engine_local_file_when_both_exist(seam):
    seam.overlay_file.write_text(OVERLAY_YAML, encoding="utf-8")
    seam.engine_local_file.write_text(ENGINE_LOCAL_YAML, encoding="utf-8")

    path, is_real = operator_identity._resolve_file()

    assert path == seam.overlay_file
    assert path != seam.engine_local_file
    assert is_real is True
    assert resolved_identity()["slug"] == "ada-lovelace"


def test_the_engine_local_file_beats_the_shipped_example_when_both_exist(seam):
    seam.engine_local_file.write_text(ENGINE_LOCAL_YAML, encoding="utf-8")
    assert operator_identity._EXAMPLE_PATH.is_file()

    path, is_real = operator_identity._resolve_file()

    assert path == seam.engine_local_file
    assert path != operator_identity._EXAMPLE_PATH
    assert is_real is True
    assert resolved_identity()["slug"] == "grace-hopper"


def test_an_environment_variable_beats_every_file_tier(seam, monkeypatch):
    seam.overlay_file.write_text(OVERLAY_YAML, encoding="utf-8")
    seam.engine_local_file.write_text(ENGINE_LOCAL_YAML, encoding="utf-8")
    monkeypatch.setenv("HEADING_OS_OPERATOR_SLUG", "katherine-johnson")

    # The file tier still resolves the overlay; env wins only at the merge in _load.
    path, _ = operator_identity._resolve_file()
    assert path == seam.overlay_file

    identity = resolved_identity()
    assert identity["slug"] == "katherine-johnson"
    assert identity["name"] == "Ada Lovelace", "env overrode only the key it set"
    assert operator_identity.operator_is_default() is False


def test_this_file_never_monkeypatches_the_resolver_it_exists_to_cover():
    """The anti-regression on the mistake being repaired.

    Seven test files reached this module and every test in the dedicated one
    replaced `_resolve_file` with a lambda, so the tiers above had no coverage at
    all. Re-introducing that patch HERE would silently empty this whole file, and
    the emptying would look like a passing suite.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    # The module docstring QUOTES the offending patch on purpose, so the check
    # runs on everything after it. maxsplit=2 yields ['', <docstring>, <rest>].
    head, docstring, rest = source.split('"""', 2)
    assert head == "", "the module docstring must be the first thing in the file"
    assert "monkeypatch.setattr" in docstring, "the docstring must still tell the story"
    # Built by concatenation so this check cannot flag its own source line.
    needle = "_resolve" + "_file"
    compact = " ".join(rest.split())
    for quote in ('"', "'"):
        patch_call = f"setattr(operator_identity, {quote}{needle}{quote}"
        assert patch_call not in compact, (
            f"{patch_call} appears in this file, which means the resolver is being "
            "patched away again and every tier test above is measuring a lambda"
        )
    # Corpus floor: without it, a rename of the tests would leave the loop above
    # passing over a file that no longer covers anything.
    assert "def test_the_engine_local_tier_resolves_config_operator_yaml" in rest
    assert compact.count("operator_identity." + needle + "()") >= 12, (
        "the tier tests must call the real resolver directly"
    )


# ==========================================================================
# scan_for_terms - the reported line number and the line it prints
# ==========================================================================

TERM = "zephyrine"

# Every character `str.splitlines()` breaks on that `str.split("\n")` does not,
# plus "\n" itself as the ordinary control. Built with chr() rather than typed,
# so this file carries no invisible Unicode of its own.
LINE_BREAKS = [
    pytest.param("\n", id="newline-the-ordinary-control"),
    pytest.param("\r", id="carriage-return"),
    pytest.param(chr(0x0B), id="vertical-tab-x0b"),
    pytest.param(chr(0x0C), id="form-feed-x0c"),
    pytest.param(chr(0x85), id="next-line-x85"),
    pytest.param(chr(0x2028), id="line-separator-u2028"),
    pytest.param(chr(0x2029), id="paragraph-separator-u2029"),
]


def content_with(break_char: str) -> str:
    """An odd break on line 1, then a real newline, then the term on line 2.

    Under `split("\\n")` the term is on line 2. Under `splitlines()` the odd break
    makes it index 2, i.e. "line 3" - and the old code asked one for the number
    and the other for the text, so it printed line 1's tail beside the number 2.
    """
    return f"alpha{break_char}beta\ncarrying {TERM} here\nomega"


def true_line_of_term(content: str) -> tuple[int, str]:
    """Where an editor would put the term: 1-based, counting "\\n" and nothing else."""
    number = content[: content.index(TERM)].count("\n") + 1
    return number, content.split("\n")[number - 1].strip()


@pytest.mark.parametrize("break_char", LINE_BREAKS)
def test_the_substring_finding_prints_the_line_that_really_carries_the_term(break_char):
    content = content_with(break_char)

    findings = scan_for_terms(content, {TERM})

    # Anti-vacuity: a test that asserts about findings must have findings.
    assert len(findings) == 1, "the scanner found nothing, so nothing below was tested"
    term, line_num, line_text, match_type = findings[0]
    assert term == TERM
    assert match_type == "substring"

    expected_number, expected_text = true_line_of_term(content)
    assert line_num == expected_number
    assert line_text == expected_text
    assert TERM in line_text, (
        f"line {line_num} was printed as evidence but does not contain the term: {line_text!r}"
    )


@pytest.mark.parametrize("break_char", LINE_BREAKS)
def test_the_word_boundary_finding_prints_the_line_that_really_carries_the_term(break_char):
    content = content_with(break_char)

    findings = scan_for_terms(content, set(), word_boundary_terms={TERM})

    assert len(findings) == 1, "the scanner found nothing, so nothing below was tested"
    term, line_num, line_text, match_type = findings[0]
    assert term == TERM
    assert match_type == "word-boundary"

    expected_number, expected_text = true_line_of_term(content)
    assert line_num == expected_number
    assert line_text == expected_text
    assert TERM in line_text


def test_a_term_on_the_last_line_after_an_odd_break_is_still_located():
    """The index case the old bounds guard was quietly absorbing.

    With `splitlines()` the list was longer than the "\\n" count allowed for, so a
    term on the final line printed an earlier line instead of tripping the guard.
    """
    content = f"alpha{chr(0x0C)}beta\nomega\ntrailing {TERM}"

    findings = scan_for_terms(content, {TERM})

    assert len(findings) == 1
    _, line_num, line_text, _ = findings[0]
    assert line_num == 3
    assert line_text == f"trailing {TERM}"


def test_a_multi_line_hit_reports_each_line_once_and_each_with_its_own_text():
    content = (
        f"first {TERM} here{chr(0x0B)}not a real line\n"
        f"second line is clean\n"
        f"third {TERM} again"
    )

    findings = sorted(scan_for_terms(content, {TERM}), key=lambda f: f[1])

    assert [f[1] for f in findings] == [1, 3]
    assert findings[0][2] == f"first {TERM} here{chr(0x0B)}not a real line"
    assert findings[1][2] == f"third {TERM} again"


def test_the_scanner_finds_nothing_when_the_term_is_absent():
    """The control that makes every "a finding was returned" assertion above mean something."""
    content = content_with(chr(0x0C)).replace(TERM, "quorrindale")

    assert scan_for_terms(content, {TERM}) == []
    assert scan_for_terms(content, set(), word_boundary_terms={TERM}) == []

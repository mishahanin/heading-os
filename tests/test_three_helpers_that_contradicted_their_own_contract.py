#!/usr/bin/env python3
"""Three small helpers whose code said one thing and their contract another.

Each was found by reading, then MEASURED by running the code, on 2026-08-29.
None of the three is dramatic on its own. They share a shape worth pinning:
a statement of fact that nothing checked, sitting next to code that disagreed
with it.

1. `scripts/firecrawl.py` module header

   The header said "Use --no-cache to bypass, --clear-cache to wipe". There is
   no such flag. MEASURED: `python scripts/firecrawl.py --clear-cache` answers
   `error: unrecognized arguments: --clear-cache` and prints the usage line.
   `clear-cache` is a SUBCOMMAND, registered with `add_parser`, and the same
   header spelled it correctly two lines above, so the file contradicted
   itself inside four lines. Nothing fails closed here, but a header is where
   an operator looks before typing, and this one sent them to an error.

2. `scripts/inbox_pulse/paths.py::get_workspace_root`

   A second implementation of a question `scripts/utils/paths.py` already
   answered. It walked up from its own file for a directory holding both
   `config/` and `scripts/`, which is right on the laptop and right on the
   service host, so nothing ever looked at it again. It also ignored the
   `WORKSPACE_ROOT` environment override the shared helper honours.
   MEASURED with `WORKSPACE_ROOT=/tmp/pretend-workspace` exported and seeded:

       scripts.utils.paths        -> /tmp/pretend-workspace
       scripts.inbox_pulse.paths  -> the real checkout

   Two answers to one question, and the daemon read the one that cannot be
   redirected. The resolution is not to teach the copy about the variable. It
   is to make there be one implementation.

3. `scripts/utils/markdown.py::parse_md_table`

   A `return None` sat one line below an unconditional `return rows`, in a
   function annotated `-> List[Dict[str, str]]`. Unreachable, so it changed no
   behaviour, but it is a written claim that the function can hand back None.
   No caller checks for it; a future one might add the check and carry a branch
   that can never run. Removed, and the contract is now asserted instead of
   annotated only.
"""
from __future__ import annotations

import ast
import importlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.markdown import parse_md_table  # noqa: E402


# ============================================================
# 1. The header names a flag that does not exist
# ============================================================

# A sentence that DENIES a flag is the correct way to warn a reader off one,
# and the first version of this rule flagged it, which made the rule impossible
# for a corrected header to satisfy. That is the same trap a rule in this repo
# already fell into once: a document written to remove a defect was reported as
# the defect. Sentences carrying any of these are read as warnings, not offers.
_NEGATORS = ("no ", "not ", "never", "unrecognized", "instead of", "rather than",
             "there is no", "does not", "is not")


def _offers(sentence: str) -> bool:
    return not any(word in sentence.lower() for word in _NEGATORS)


def documented_long_flags(header: str) -> set[str]:
    """Every `--flag` the prose of a module header OFFERS the reader.

    Pure, so both directions can be measured on synthetic text. Over a
    corrected header it returns only real flags, which means a rule that only
    ever looked at the real file would be green even with its body deleted.
    """
    out = set()
    for sentence in re.split(r"[.\n]", header):
        if not _offers(sentence):
            continue
        for token in sentence.replace("`", " ").replace(",", " ").split():
            if token.startswith("--") and len(token) > 2:
                out.add(token.strip(".:;)").rstrip("."))
    return out


def argparse_long_flags(source: str) -> set[str]:
    """Every `--flag` the module actually registers with argparse."""
    out = set()
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                    and arg.value.startswith("--"):
                out.add(arg.value)
    return out


_HEADER_FIXTURE_BAD = "Use --no-cache to bypass, --clear-cache to wipe."
_HEADER_FIXTURE_GOOD = "Use --no-cache to bypass, and the `clear-cache` command."
_HEADER_FIXTURE_WARNS = ("Use --no-cache to bypass. There is no --clear-cache "
                         "flag; argparse answers unrecognized arguments.")
_ARGPARSE_FIXTURE = (
    "import argparse\n"
    "p = argparse.ArgumentParser()\n"
    "p.add_argument('--no-cache', action='store_true')\n"
)


def test_the_detector_finds_a_flag_the_parser_never_registered():
    documented = documented_long_flags(_HEADER_FIXTURE_BAD)
    registered = argparse_long_flags(_ARGPARSE_FIXTURE)
    assert sorted(documented - registered) == ["--clear-cache"]


def test_the_detector_stays_quiet_when_the_header_is_honest():
    documented = documented_long_flags(_HEADER_FIXTURE_GOOD)
    registered = argparse_long_flags(_ARGPARSE_FIXTURE)
    assert documented - registered == set()


def test_the_detector_lets_a_header_warn_a_reader_off_a_flag():
    """A rule a CORRECTED document cannot satisfy is a rule with a hole. The
    first version of this detector flagged the sentence written to remove the
    defect, because that sentence has to name the flag in order to deny it."""
    documented = documented_long_flags(_HEADER_FIXTURE_WARNS)
    registered = argparse_long_flags(_ARGPARSE_FIXTURE)
    assert documented - registered == set()
    assert "--no-cache" in documented, (
        "the carve-out swallowed the offer as well as the warning")


def test_the_firecrawl_header_offers_no_flag_the_parser_lacks():
    source = (ROOT / "scripts" / "firecrawl.py").read_text(encoding="utf-8")
    header = ast.get_docstring(ast.parse(source)) or ""
    assert header, "the module lost its header"

    missing = documented_long_flags(header) - argparse_long_flags(source)

    assert missing == set(), (
        f"the header offers flags argparse never registers: {sorted(missing)}")


def test_clear_cache_really_is_a_subcommand_and_not_a_flag():
    """The measurement itself, kept as a test. A future author could add a
    `--clear-cache` alias, at which point the header would be right again and
    the sentence above it would be wrong."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "firecrawl.py"), "--clear-cache"],
        capture_output=True, text=True, timeout=60,
        cwd=str(ROOT),
        env=dict(os.environ, HEADING_OS_DATA="/nonexistent-for-this-probe"),
    )
    combined = proc.stdout + proc.stderr
    assert "unrecognized arguments: --clear-cache" in combined, combined[-400:]
    assert "clear-cache" in combined, "the subcommand vanished from the usage line"


# ============================================================
# 2. One question, one implementation
# ============================================================

def test_the_daemon_helper_honours_the_workspace_root_override(tmp_path,
                                                               monkeypatch):
    """The measurement that found the second copy. The override must reach the
    daemon's helper, not only the shared one."""
    pretend = tmp_path / "pretend-workspace"
    (pretend / "config").mkdir(parents=True)
    (pretend / "scripts").mkdir()
    monkeypatch.setenv("WORKSPACE_ROOT", str(pretend))

    shared = importlib.import_module("scripts.utils.paths")
    pulse = importlib.import_module("scripts.inbox_pulse.paths")

    assert shared.get_workspace_root() == pretend.resolve()
    assert pulse.get_workspace_root() == pretend.resolve(), (
        "the daemon's helper still answers from a private copy")


def test_the_two_helpers_agree_without_an_override(monkeypatch):
    """The mirror. Delegating must not change the ordinary answer."""
    monkeypatch.delenv("WORKSPACE_ROOT", raising=False)

    shared = importlib.import_module("scripts.utils.paths")
    pulse = importlib.import_module("scripts.inbox_pulse.paths")

    assert pulse.get_workspace_root() == shared.get_workspace_root()


def _root_resolvers(source: str) -> list[str]:
    """Names of functions in `source` that resolve a workspace root by walking
    parents themselves, rather than asking the shared helper.

    Pure, so the synthetic cases below prove it still discriminates over a tree
    where the real answer is empty.
    """
    out = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.FunctionDef):
            continue
        if "workspace_root" not in node.name:
            continue
        body = ast.dump(node)
        walks = "'parent'" in body or "parents" in body
        delegates = "_shared_workspace_root" in body
        if walks and not delegates:
            out.append(node.name)
    return out


_WALKER_FIXTURE = (
    "def get_workspace_root():\n"
    "    candidate = HERE\n"
    "    while True:\n"
    "        candidate = candidate.parent\n"
    "    return candidate\n"
)
_DELEGATE_FIXTURE = (
    "def get_workspace_root():\n"
    "    return _shared_workspace_root()\n"
)


def test_the_copy_detector_finds_a_private_walker():
    assert _root_resolvers(_WALKER_FIXTURE) == ["get_workspace_root"]


def test_the_copy_detector_leaves_a_delegation_alone():
    assert _root_resolvers(_DELEGATE_FIXTURE) == []


def test_the_copy_detector_needs_both_halves_to_fire():
    """A delegating function that also touches `parent` for an unrelated reason.

    Neither fixture above separates `walks AND NOT delegates` from
    `walks OR NOT delegates`: in both, one half is false and the other decides.
    Mutation caught that, so this case sets BOTH halves true, where only the
    `and` form stays quiet.
    """
    both = (
        "def get_workspace_root():\n"
        "    if OVERRIDE:\n"
        "        return Path(OVERRIDE).parent\n"
        "    return _shared_workspace_root()\n"
    )
    assert _root_resolvers(both) == []


def test_the_daemon_module_holds_no_private_root_walker():
    source = (ROOT / "scripts" / "inbox_pulse" / "paths.py").read_text(
        encoding="utf-8")
    assert _root_resolvers(source) == [], (
        "a second implementation of the workspace root came back")


# ============================================================
# 3. The parser's contract, asserted rather than annotated only
# ============================================================

@pytest.mark.parametrize("text, pattern", [
    ("", None),
    ("no table here at all\n", None),
    ("## Heading\n\nprose only\n", r"##\s*Heading"),
    ("## Heading\n\n| a | b |\n| --- | --- |\n", r"##\s*Missing"),
])
def test_the_table_parser_always_returns_a_list(text, pattern):
    """Every early exit. The annotation says `List[Dict[str, str]]` and a
    `return None` sat one line under the final `return rows`, unreachable but
    written down. A caller reading the code could reasonably have added a None
    branch that can never run."""
    result = parse_md_table(text, pattern, warn=lambda message: None)
    assert isinstance(result, list)


def test_the_table_parser_still_returns_the_rows():
    """The mirror. Always returning `[]` would satisfy every case above."""
    text = "| name | rank |\n| --- | --- |\n| Bond | Commander |\n"
    assert parse_md_table(text) == [{"name": "Bond", "rank": "Commander"}]


def test_the_parser_has_no_statement_after_its_final_return():
    """Unreachable code carries a claim nothing can test. Asserted on the AST
    so a future re-addition fails here rather than sitting for months."""
    source = (ROOT / "scripts" / "utils" / "markdown.py").read_text(
        encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == "parse_md_table":
            assert isinstance(node.body[-1], ast.Return), (
                "the last statement of parse_md_table is not its return")
            break
    else:  # pragma: no cover - the function was renamed
        pytest.fail("parse_md_table not found")

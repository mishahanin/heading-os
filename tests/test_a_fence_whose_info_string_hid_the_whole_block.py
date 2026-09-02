"""A ```bash fence carrying anything after the word `bash` was skipped entirely.

`scripts/audit-skill-bash-paths.py` decided whether a fence was shell by asking
``stripped.strip("`").lower() in _BASH_FENCES``. A fence line is an opening
delimiter plus an INFO STRING, and only its first token is the language;
everything after it is metadata for whatever renders the block. So

    ```bash linenos

yields the string ``"bash linenos"``, which is in no tuple, and the scanner set
`cur_bash = False` and walked past every command in the block.

MEASURED 2026-09-02 on a synthetic skill holding one line,
``python scripts/x.py -o outputs/a.png``: inside ```` ```bash ```` the scanner
returned one hit, and inside ```` ```bash linenos ```` it returned none. The
command is identical; only the fence's metadata differed.

Why this matters more than a normal false negative. The audit's own coverage
floor (`Coverage.refusals`, pinned by
`tests/test_a_path_audit_that_never_opened_a_reference_file.py`) counts FILES
OPENED, and a file whose fences are all mislabelled is opened, read, and scored
zero. The refusal machinery cannot see it. `BASELINE` is empty by design, so
`counts == BASELINE` holds perfectly over a corpus whose blocks were all
skipped, and every question this gate asks is an absence question. A fence
label is therefore the one input that can silence the ratchet without tripping
any of its guards.

The fence set has been narrow since the empty string left it (see
`tests/test_a_gate_that_passed_the_plan_it_had_just_failed.py`, "bash blocks
that were every fence"), and that narrowing is not weakened here: the first
token still has to be one of `bash`, `sh`, `shell`. What changes is that the
tokens after it stop deciding anything.
"""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "audit_skill_bash_paths_info_string",
    str(ROOT / "scripts" / "audit-skill-bash-paths.py"))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_COMMAND = "python scripts/x.py -o outputs/a.png"


def _skill(tmp_path: Path, fence: str) -> Path:
    path = tmp_path / "SKILL.md"
    path.write_text(f"# s\n\n```{fence}\n{_COMMAND}\n```\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("fence", [
    "bash linenos",
    "bash title=\"render the deck\"",
    "sh {1,3}",
    "shell copy",
    "bash\t",
])
def test_an_info_string_after_the_language_does_not_hide_the_block(tmp_path, fence):
    hits = _mod.scan_skill(_skill(tmp_path, fence))
    assert len(hits) == 1, f"fence ```{fence} was skipped entirely: {hits}"
    assert "outputs/a.png" in hits[0][1], hits


@pytest.mark.parametrize("fence", ["bash", "sh", "shell", "BASH", " bash "])
def test_a_bare_language_label_is_unchanged(tmp_path, fence):
    """Anchor: the plain form the corpus mostly uses must keep behaving."""
    assert len(_mod.scan_skill(_skill(tmp_path, fence))) == 1


@pytest.mark.parametrize("fence", ["python", "json", "yaml", "text", "", "  ",
                                   "pythonbash", "bashful"])
def test_a_non_shell_language_is_still_not_scanned(tmp_path, fence):
    """The narrowing is not traded away for the fix.

    The last two are the interesting ones: taking the first TOKEN must not
    become a prefix or substring test, or `bashful` and `pythonbash` walk in.
    The empty and whitespace-only fences are the unlabelled blocks that left
    `_BASH_FENCES` deliberately, and they must stay out.
    """
    assert _mod.scan_skill(_skill(tmp_path, fence)) == []


def test_the_closing_fence_never_reopens_the_block(tmp_path):
    """A closing ``` is also a fence line, and it carries no info string.

    Reading the language off the first token must not make the closing
    delimiter look like a bare label and flip the block back open.
    """
    path = tmp_path / "SKILL.md"
    path.write_text(
        "# s\n\n```bash linenos\n" + _COMMAND + "\n```\n\n"
        "prose that mentions outputs/b.png and scripts/y.py\n",
        encoding="utf-8")
    hits = _mod.scan_skill(path)
    assert len(hits) == 1, hits
    assert "outputs/b.png" not in hits[0][1], hits


def test_the_scanner_and_its_test_helper_read_the_same_fences():
    """`tests/test_skill_bash_paths.py` reimplements fence selection so it can
    parse commands out of the corpus, and its docstring says the two mirror each
    other. A mirror maintained by hand is one edit from being a copy that lags,
    and this defect was in exactly that expression. Both now call the scanner's
    own helper, so the mirroring is a fact rather than a promise.
    """
    assert _mod.fence_language("```bash linenos") == "bash"
    assert _mod.fence_language("```") == ""
    assert _mod.fence_language("```Python") == "python"

    helper = (ROOT / "tests" / "test_skill_bash_paths.py").read_text(encoding="utf-8")
    assert "fence_language" in helper, (
        "the test helper stopped using the scanner's fence parser; the two can "
        "now disagree about which blocks are shell")

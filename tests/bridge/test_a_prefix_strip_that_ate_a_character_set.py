"""Six readers normalised a path with `.lstrip("./")`, which strips a SET.

Every file reader under `scripts/bridge_daemon/sources/` takes a caller-supplied
`rel_path` from an HTTP query parameter, normalises it, then checks the result
starts with its own directory prefix. Six wrote that normalisation as
`rel_path.replace("\\\\", "/").lstrip("./")`.

`str.lstrip` takes a CHARACTER SET, not a prefix. It removes EVERY leading `.`
and `/`, so `"...knowledge/x.md"`, `"/knowledge/x.md"` and
`"../../knowledge/x.md"` all arrive at the prefix check as `"knowledge/x.md"`.
The check then passes on a string the caller never sent.

`approvals.validate_draft_rel_path` found this first and dropped the strip
entirely. The other five kept it. Measured 2026-08-29 with a probe driving the
real functions over 6 inputs each: 25 of the 30 hostile inputs were accepted by
`library.read_note`, `threads.read_thread`, `investors.read_dossier`,
`studio.read_inflight` and `studio.resolve_artifact_image`, while
`approvals.validate_draft_rel_path` refused all 5 of the same shapes and 0 of
its 6 rows mismatched. After the fix: 0 of 30.

Severity, measured rather than assumed: nothing escaped the served tree.
`lstrip` eats the `..` along with the dots, so by the time the path is joined
there is no traversal segment left, and `../secret.md` was refused before the
fix as well. What was lost is refusal fidelity plus a silent rewrite:
`read_note(root, "../../knowledge/note.md")` returned
`{"ok": True, "path": "knowledge/note.md"}`, serving a file under a name the
caller had not asked for, and disagreeing with the one validator that had
already been corrected. Latent as a containment hole; live as the same
reader-versus-reader asymmetry `validate_draft_rel_path` was written to end.

Fix: one `normalize_rel_path` in `scripts/bridge_daemon/_safepath.py`, called by
all six. It trims and unifies separators and strips no prefix, leaving each
reader's own prefix check to refuse the rest.
"""
import ast
from pathlib import Path

import pytest

from scripts.bridge_daemon._safepath import normalize_rel_path
from scripts.bridge_daemon.sources.approvals import (
    EMAIL_DRAFTS_DIR, validate_draft_rel_path,
)
from scripts.bridge_daemon.sources.investors import PROGRAM_DIR, read_dossier
from scripts.bridge_daemon.sources.library import read_note
from scripts.bridge_daemon.sources.studio import (
    ARTIFACT_ROOT, read_inflight, resolve_artifact_image,
)
from scripts.bridge_daemon.sources.threads import (
    THREADS_BUSINESS_DIR, read_thread,
)
from tests.repo_files import read_sources

# The prefixes `lstrip("./")` ate. Every one of these, prepended to an
# otherwise-valid path, was swallowed whole before the prefix check ran.
HOSTILE_PREFIXES = ["./", "/", "../../", "...", ".././/", "//", "."]

# (reader name, served prefix, leaf, accepts(root, rel) -> bool)
READERS = [
    ("library.read_note", "knowledge", "note.md",
     lambda root, rel: bool(read_note(root, rel).get("ok"))),
    ("threads.read_thread", THREADS_BUSINESS_DIR, "deal.md",
     lambda root, rel: bool(read_thread(root, rel).get("ok"))),
    ("investors.read_dossier", PROGRAM_DIR, "firm.md",
     lambda root, rel: bool(read_dossier(root, rel).get("ok"))),
    ("studio.read_inflight", "outputs/intel", "brief.md",
     lambda root, rel: bool(read_inflight(root, rel).get("ok"))),
    ("studio.resolve_artifact_image", ARTIFACT_ROOT + "/posts/demo", "cover.png",
     lambda root, rel: resolve_artifact_image(root, rel) is not None),
    # The site that was already correct. It rides the same battery so a
    # regression here is caught by the test that documents its own precedent.
    ("approvals.validate_draft_rel_path", EMAIL_DRAFTS_DIR, "draft.md",
     lambda root, rel: validate_draft_rel_path(rel) is None),
]

READER_IDS = [name for name, _, _, _ in READERS]


def _plant(root: Path, prefix: str, leaf: str) -> str:
    """Create the served file and return the rel_path that names it."""
    target = root / prefix / leaf
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"served content")
    return f"{prefix}/{leaf}"


@pytest.mark.parametrize("reader", READERS, ids=READER_IDS)
def test_every_reader_still_serves_a_plain_relative_path(reader, tmp_path):
    """Positive control. The fix must not turn a working read into a refusal."""
    _name, prefix, leaf, accepts = reader
    rel = _plant(tmp_path, prefix, leaf)
    assert accepts(tmp_path, rel) is True, (
        f"{_name} refused the ordinary path {rel!r}; the fix broke the happy path"
    )


@pytest.mark.parametrize("hostile", HOSTILE_PREFIXES)
@pytest.mark.parametrize("reader", READERS, ids=READER_IDS)
def test_every_reader_refuses_a_path_wearing_a_leading_dot_or_slash(
    reader, hostile, tmp_path
):
    """The negative case. Each of these was ACCEPTED before 2026-08-29."""
    _name, prefix, leaf, accepts = reader
    rel = _plant(tmp_path, prefix, leaf)
    assert accepts(tmp_path, hostile + rel) is False, (
        f"{_name} accepted {hostile + rel!r}: the leading {hostile!r} was "
        f"stripped away and the prefix check ran on a string the caller "
        f"never sent"
    )


def test_the_refusal_names_the_path_it_refused_rather_than_a_rewrite(tmp_path):
    """The silent-rewrite half, pinned on the reader that measured it.

    Before the fix this returned ok=True with path='knowledge/note.md': a file
    served under a name the caller had not asked for.
    """
    _plant(tmp_path, "knowledge", "note.md")
    result = read_note(tmp_path, "../../knowledge/note.md")
    assert result["ok"] is False
    assert result["error"] == "path must be under knowledge/"
    assert "content" not in result


def test_containment_held_even_while_the_strip_was_wrong(tmp_path):
    """Severity control: this was never a traversal escape, and still is not.

    `lstrip("./")` destroyed the `..` before the join, so a path aimed OUT of
    the served tree was refused by the prefix check both before and after. The
    test exists so a future reader does not upgrade this defect's history into
    a containment hole it never was.
    """
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "secret.md").write_text("outside knowledge/")
    for escaping in ["../secret.md", "./../secret.md", "knowledge/../secret.md"]:
        result = read_note(tmp_path, escaping)
        assert result["ok"] is False, f"{escaping!r} escaped the served tree"


# ============================================================
# Anti-vacuity: does the battery above distinguish the two normalisers?
# ============================================================

def _old_normalizer(rel_path: str) -> str:
    """The expression the six readers used, verbatim, for contrast only."""
    return rel_path.replace("\\", "/").lstrip("./")


@pytest.mark.parametrize("hostile", HOSTILE_PREFIXES)
def test_the_shipped_normalizer_keeps_the_marker_the_prefix_check_needs(hostile):
    """The property every reader depends on: a hostile lead SURVIVES.

    A prefix check can only refuse what reaches it. `normalize_rel_path` leaves
    the leading dot or slash in place, so `startswith("knowledge/")` is false
    and the reader refuses.
    """
    rel = hostile + "knowledge/note.md"
    assert normalize_rel_path(rel).startswith((".", "/")), (
        f"normalize_rel_path ate the leading {hostile!r}; the prefix check "
        f"would see a string the caller never sent"
    )
    assert not normalize_rel_path(rel).startswith("knowledge/")


@pytest.mark.parametrize("hostile", HOSTILE_PREFIXES)
def test_the_defect_shape_fails_the_property_the_fix_passes(hostile):
    """Proves the assertion above is not vacuous: the old expression breaks it.

    If this ever passes, the battery has stopped measuring anything, because
    the exact code that shipped the bug would satisfy it.
    """
    rel = hostile + "knowledge/note.md"
    assert _old_normalizer(rel) == "knowledge/note.md", (
        "the historical defect no longer reproduces; either lstrip changed "
        "semantics or this case never exercised it"
    )
    assert not _old_normalizer(rel).startswith((".", "/"))


def test_the_normalizer_still_does_the_two_jobs_it_is_for():
    """It is not a no-op. Separators unify and surrounding whitespace goes."""
    assert normalize_rel_path("knowledge\\sub\\note.md") == "knowledge/sub/note.md"
    assert normalize_rel_path("  knowledge/note.md\n") == "knowledge/note.md"
    assert normalize_rel_path("knowledge/note.md") == "knowledge/note.md"
    # It does NOT lowercase: the served trees are on a case-sensitive filesystem.
    assert normalize_rel_path("Knowledge/Note.MD") == "Knowledge/Note.MD"


def _lstrip_dotslash_calls(tree: ast.AST) -> list[int]:
    """Line numbers of every `<expr>.lstrip("./")` CALL in a parsed module.

    The AST, not the text. Both `_safepath.py` and `approvals.py` now DESCRIBE
    this defect in prose, and a substring scan calls the fix's own docstring an
    offender. Asking the syntax tree distinguishes code from the comment about
    the code.
    """
    hits = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "lstrip"
                and len(node.args) == 1
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "./"):
            hits.append(node.lineno)
    return hits


def test_no_module_under_bridge_daemon_reintroduces_the_character_set_strip():
    """Guards the seventh copy. Five of six inherited this bug by paste."""
    repo = Path(__file__).resolve().parents[2]
    daemon = repo / "scripts" / "bridge_daemon"
    walked = sorted(daemon.rglob("*.py"))
    # A SCAN: it looks for a shape and collects offenders. A module that
    # vanished between the walk and the read cannot hold the seventh copy, so
    # `read_sources` skips it with a warning naming it rather than dying of
    # FileNotFoundError inside the guard. The floor below is asserted on what
    # was actually READ, not on what was walked, so a corpus that shrank
    # underneath the sweep still trips it.
    offenders = {}
    vanished = []
    read = 0
    for path, source in read_sources(walked, vanished):
        read += 1
        lines = _lstrip_dotslash_calls(ast.parse(source, filename=str(path)))
        if lines:
            offenders[str(path.relative_to(repo))] = lines
    assert read > 20, (
        f"only {read} modules read under {daemon} ({len(vanished)} vanished "
        f"between the walk and the read); a guard over an empty-ish corpus "
        f"passes for the wrong reason"
    )
    assert offenders == {}, (
        f"{offenders} call .lstrip('./'), which strips a character SET, not a "
        f"prefix. Call scripts.bridge_daemon._safepath.normalize_rel_path."
    )


def test_the_reintroduction_guard_actually_sees_the_defect_shape():
    """Anti-vacuity for the guard above: an AST walk that matches nothing
    passes every file. Feed it the exact expression the six readers shipped."""
    defect = ast.parse('rel_path = rel_path.replace("\\\\", "/").lstrip("./")')
    assert _lstrip_dotslash_calls(defect) == [1]
    fixed = ast.parse("rel_path = normalize_rel_path(rel_path)")
    assert _lstrip_dotslash_calls(fixed) == []
    # A different character set is a different question, and not this guard's.
    assert _lstrip_dotslash_calls(ast.parse('host.lstrip("@")')) == []

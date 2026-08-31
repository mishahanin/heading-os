"""Four documentation claims that no gate could see, each bound by derivation.

All four came out of the 2026-08-31 `docs/` audit, and all four are the same
shape: a page asserting a fact that lives in code, config, or a sibling page,
with nothing comparing the two. Correcting the sentence is not the fix. Deriving
one side from the other is, because then the next change moves both.

None of these assertions is a grep for a wrong string. A corrected page has to
be free to NAME the thing it got wrong in order to explain it, and a source-text
grep punishes exactly that. Each test below resolves a path, parses a table, or
compares two derived numbers.

1. `docs/index.html` carried `v0.9.0` twice while `pyproject.toml` said
   `0.13.0`, in a paragraph claiming "Every figure here is produced by CI". Two
   gates exist and their intersection is empty: `scripts/check-version-sync.py`
   checks the version but only on `README.md`, `CHANGELOG.md` and `ROADMAP.md`,
   while `scripts/dev/check-readme-numbers.py` does reach `docs/index.html` but
   only holds patterns for `(\\d+) security tests` and `(\\d+) enforcement
   layers`. The page was reached by a gate that cannot see versions and checked
   by a gate that cannot see the page. `test_index_html_version_matches_pyproject`
   is the missing intersection.

2. `docs/THREAT-MODEL.md` cited `CI guard "HEADING OS data-root guard"`. That
   name is the pre-commit hook id `data-root-bypass-guard`, and no workflow
   invokes `pre-commit`, so the pointer resolved to nothing while the coverage
   it described was real (the test runs in CI under "Engine tree clean"). The
   page's own preamble defines a CI guard as a named step in `ci.yml`, so the
   page contradicted itself. Both directions are now bound: every `pytest`
   path must exist, and every cited guard name must prefix a real step name.

3. `docs/prerequisites.html` named `weasyprint`, `replicate` and `google-genai`
   inside a sentence that promises "exact pins in `pyproject.toml`". None of the
   three is declared in `pyproject.toml` or `requirements.txt`, and nothing in
   the tree imports any of them, so a reader ran `uv sync` and got a manifest
   that did not match the page. This is a setup page a reader EXECUTES, which is
   what makes it worse than a wrong sentence elsewhere.

4. `docs/engine-data-segregation-contract.md`, `docs/SECURITY-MODEL.md` and
   `SECURITY.md` disagreed on how many layers hold the engine/data boundary. The
   contract was headed "The six layers" and omitted the content guard; the
   security model listed seven bullets including it; the root policy said six.
   The content guard is a guarantee layer (unbypassable on the sanctioned push
   path via `push-all.py engine_content_scan`, answering a question no routing
   layer answers, and failing closed on a file it cannot read), so seven is the
   true count. The table is the authority here, and the two prose numerals are
   derived from it.
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.repo_files import tracked_paths  # noqa: E402

# Any walk over a corpus asserts a floor first. A pattern that silently matches
# nothing is indistinguishable from a corpus that is clean, and this repository
# has already shipped one gate that missed 77 of 252 blocks that way.
MIN_PYTEST_CITATIONS = 20
MIN_CI_GUARD_CITATIONS = 4
MIN_PINNED_PACKAGES = 15

_SEMVER_TOKEN = re.compile(r"\bv(\d+\.\d+\.\d+)\b")


def _pyproject_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def _declared_packages() -> set[str]:
    """Every distribution name `pyproject.toml` declares, normalised.

    Reads the parsed TOML rather than the file text, so a requirement moved
    between `dependencies` and an optional group still counts as declared.
    """
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data.get("project", {})
    raw: list[str] = list(project.get("dependencies", []) or [])
    for group in (project.get("optional-dependencies", {}) or {}).values():
        raw.extend(group or [])
    for group in (data.get("dependency-groups", {}) or {}).values():
        raw.extend(g for g in (group or []) if isinstance(g, str))

    names = set()
    for spec in raw:
        # Strip environment markers, extras, and the version pin.
        head = spec.split(";", 1)[0].strip()
        head = head.split("[", 1)[0]
        name = re.split(r"[=<>!~ ]", head, maxsplit=1)[0].strip()
        if name:
            names.add(name.lower().replace("_", "-"))
    return names


def test_index_html_version_matches_pyproject():
    """Every engine-version token on the docs front door is the real version.

    `docs/index.html` is the one front door carrying a version that neither
    existing gate compares to `pyproject.toml`. If a third-party version is ever
    added to this page, this test fails loudly and someone narrows it, which is
    the correct outcome: a silent pass is what let v0.9.0 sit here for four
    minor releases.
    """
    page = ROOT / "docs" / "index.html"
    assert page.is_file(), f"{page} is missing"

    found = _SEMVER_TOKEN.findall(page.read_text(encoding="utf-8"))
    assert found, (
        "docs/index.html carries no vX.Y.Z token at all. The page states the "
        "release its figures were counted at; if that sentence went away, "
        "delete this test deliberately rather than let it pass on an empty set."
    )

    expected = _pyproject_version()
    wrong = sorted({v for v in found if v != expected})
    assert not wrong, (
        f"docs/index.html cites version(s) {wrong} but pyproject.toml declares "
        f"{expected!r}. Update the page, or narrow this test if the token "
        f"belongs to a third-party tool rather than to HEADING OS."
    )


def test_threat_model_test_paths_all_resolve():
    """Every `pytest <path>` cited in the threat model exists on disk."""
    page = ROOT / "docs" / "THREAT-MODEL.md"
    text = page.read_text(encoding="utf-8")

    cited: set[str] = set()
    # Only the backticked citations, which is the page's own convention. The
    # preamble's prose `pytest <path>` placeholder carries no slash and drops
    # out on the `tests/` prefix test below.
    for span in re.findall(r"`pytest ([^`]+)`", text):
        for token in span.split():
            if token.startswith("tests/"):
                cited.add(token)

    assert len(cited) >= MIN_PYTEST_CITATIONS, (
        f"only {len(cited)} pytest citations parsed out of THREAT-MODEL.md, "
        f"expected at least {MIN_PYTEST_CITATIONS}. The citation format "
        f"probably changed, and this test would otherwise pass over nothing."
    )

    def _resolves(token: str) -> bool:
        """A citation may be a file, a directory, or a glob. All three run."""
        target = ROOT / token
        if target.is_file() or target.is_dir():
            return True
        if any(ch in token for ch in "*?["):
            # The citations are repo-relative, so the repo root is the base.
            # `tracked_paths` rather than `ROOT.glob`, because a bare root glob
            # also reaches an agent worktree under `.claude/worktrees/` -- a
            # full second copy of the tree -- and a citation that resolves only
            # inside somebody's scratch checkout is a citation that resolves to
            # nothing for the reader. `files_only=False`: a citation may name a
            # directory of tests, and that still runs.
            return bool(tracked_paths((token,), files_only=False))
        return False

    missing = sorted(p for p in cited if not _resolves(p))
    assert not missing, (
        f"THREAT-MODEL.md cites test paths that resolve to nothing: {missing}. "
        f"A threat-model row whose proof cannot be run is an unproven row."
    )


def test_threat_model_ci_guard_names_resolve_to_workflow_steps():
    """Every CI guard the threat model names prefixes a real `ci.yml` step.

    The page's own preamble says a CI guard IS a named step in `ci.yml`, and it
    abbreviates long step names to their leading words (`Engine tree clean` for
    `Engine tree clean (no data-class artifact in the engine clone)`). Prefix
    matching is therefore the honest comparison, not equality.
    """
    page = ROOT / "docs" / "THREAT-MODEL.md"
    text = page.read_text(encoding="utf-8")

    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    step_names = [m.strip() for m in re.findall(r"^\s*- name:\s*(.+?)\s*$", workflow, re.M)]
    assert step_names, "no step names parsed out of ci.yml"

    cited: set[str] = set()
    # A cell reads `...; CI guard `A`, `B`; SEC-004 `pytest ...``. The guard
    # clause ends at the next `;` or at the cell boundary; without that stop the
    # match ran on and swallowed the pytest citation beside it as a guard name.
    for tail in re.findall(r"CI guards?\s+((?:`[^`]+`[,\s]*)+)", text):
        cited.update(name.strip() for name in re.findall(r"`([^`]+)`", tail))
    # `= \`python scripts/...\`` spells out what one guard runs; it is a command,
    # not a second guard name.
    cited = {c for c in cited if not c.startswith(("python ", "pytest "))}

    assert len(cited) >= MIN_CI_GUARD_CITATIONS, (
        f"only {len(cited)} CI guard names parsed out of THREAT-MODEL.md, "
        f"expected at least {MIN_CI_GUARD_CITATIONS}."
    )

    unresolved = sorted(
        name for name in cited
        if not any(step.startswith(name) for step in step_names)
    )
    assert not unresolved, (
        f"THREAT-MODEL.md names CI guards with no matching step in ci.yml: "
        f"{unresolved}. Known step names: {step_names}"
    )


def test_prerequisites_pinned_packages_are_actually_declared():
    """Packages the setup page calls pinned are declared in `pyproject.toml`.

    Scoped to the one sentence that makes the promise, found by its own words
    rather than by a hardcoded package list, so adding a package to the sentence
    puts it under this test automatically.
    """
    page = ROOT / "docs" / "prerequisites.html"
    text = page.read_text(encoding="utf-8")

    sentences = [
        line for line in text.splitlines()
        if "exact pins in <code>pyproject.toml</code>" in line
    ]
    assert len(sentences) == 1, (
        f"expected exactly one 'exact pins in pyproject.toml' sentence on "
        f"docs/prerequisites.html, found {len(sentences)}. Re-scope this test "
        f"rather than let it check the wrong paragraph, or none at all."
    )

    named = {
        m.lower().replace("_", "-")
        for m in re.findall(r"<code>([A-Za-z][A-Za-z0-9._-]+)</code>", sentences[0])
    }
    named.discard("pyproject.toml")
    named.discard("uv")

    assert len(named) >= MIN_PINNED_PACKAGES, (
        f"only {len(named)} packages parsed out of the pinned-stack sentence, "
        f"expected at least {MIN_PINNED_PACKAGES}. The markup changed and this "
        f"test would otherwise pass over an empty set."
    )

    declared = _declared_packages()
    # The page names the Google API clients collectively; that phrase is prose,
    # not a distribution name, and it carries no <code> markup.
    undeclared = sorted(named - declared)
    assert not undeclared, (
        f"docs/prerequisites.html promises exact pins in pyproject.toml for "
        f"{undeclared}, and pyproject.toml declares none of them. Fix the page. "
        f"Do NOT add a dependency to satisfy this test."
    )


def test_segregation_layer_count_agrees_across_all_three_pages():
    """The contract's layer TABLE is the authority; both prose numerals derive.

    Three pages carried three different answers until 2026-08-31. The table is
    the only one of the four places that enumerates the layers rather than
    counting them, so it wins, and the two sentences are checked against it.
    """
    words = {6: "six", 7: "seven", 8: "eight", 9: "nine"}

    contract = ROOT / "docs" / "engine-data-segregation-contract.md"
    contract_text = contract.read_text(encoding="utf-8")

    # Rows of the layer table: `| <n> | **Name** | ...`
    numbers = [
        int(m) for m in re.findall(r"^\|\s*(\d+)\s*\|", contract_text, re.M)
    ]
    assert numbers, "no numbered layer rows parsed out of the contract table"
    count = len(numbers)
    assert numbers == list(range(1, count + 1)), (
        f"the contract's layer table is not numbered 1..{count}: {numbers}. "
        f"A gap or a repeat means the count below cannot be trusted."
    )
    assert count >= 6, f"only {count} layer rows found; the contract had 6 in 2026-06"

    word = words[count]
    assert f"## The {word} layers" in contract_text, (
        f"the contract's layer table holds {count} rows, so its heading must "
        f"read '## The {word} layers'."
    )
    assert f"the {word}\nlayers" in contract_text or f"the {word} layers" in contract_text, (
        f"the contract's opening sentence must also say '{word}'."
    )

    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert f"{word.capitalize()} enforcement layers" in security, (
        f"SECURITY.md must say '{word.capitalize()} enforcement layers' to "
        f"match the contract's {count}-row table."
    )

    model = (ROOT / "docs" / "SECURITY-MODEL.md").read_text(encoding="utf-8")
    # The security model lists the layers as bullets rather than counting them,
    # so the binding is one bullet per layer. The list is located by the sentence
    # that introduces it, never by a fixed line number, and the bolded phrase can
    # sit anywhere in the bullet ("a static **bypass guard**", "a runtime
    # **tree-clean check**") rather than right after the article.
    intro = "each catching a different way\nthe boundary could be crossed:"
    assert intro in model, (
        "docs/SECURITY-MODEL.md no longer introduces its enforcement-layer list "
        "with the expected sentence, so this test cannot locate the list. "
        "Re-anchor it rather than let it pass over nothing."
    )
    tail = model.split(intro, 1)[1]
    block = tail.split("\n\n", 2)[1] if tail.startswith("\n\n") else tail.split("\n\n", 1)[0]
    bullets = [ln for ln in block.splitlines() if ln.startswith("- ")]
    assert len(bullets) == count, (
        f"docs/SECURITY-MODEL.md lists {len(bullets)} enforcement-layer bullets "
        f"against the contract's {count} numbered rows. The two must agree, and "
        f"the contract's table is the authority. Bullets: {bullets}"
    )

"""CAP-2: personal/ fails closed to private so exec PII can never default to engine
and slip past the engine-tree-clean guard. (2026-06-26 exec-deferral lift.)

Before the fix, config/routing-map.yaml had no rule for personal/, so legacy-shaped
exec data under personal/ resolved to the engine default and the engine guard
(which flags only private/corporate destinations) never caught it. Only GitHub's
server-side write-deny stopped a real leak on an executive's machine; these tests pin the
local guard so the protection no longer depends on remote permissions.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.workspace import get_routing_destination
from scripts.utils.engine_guard import find_data_artifacts, scan_engine_repo


def test_personal_dir_routes_private():
    for p in [
        "personal/context/personal-info.md",
        "personal/reference/voice.md",
        "personal/reference/calendar-policy.md",
        "personal/crm/contacts/alice.md",
        "personal/outputs/report.md",
    ]:
        assert get_routing_destination(p) == "private", p


def test_engine_guard_flags_personal_file():
    flagged = find_data_artifacts([
        "personal/context/personal-info.md",
        "scripts/foo.py",
        "config/routing-map.yaml",
    ])
    assert "personal/context/personal-info.md" in flagged
    # engine code is never flagged
    assert "scripts/foo.py" not in flagged
    assert "config/routing-map.yaml" not in flagged


def test_personal_wins_over_engine_default():
    # an unmatched path still routes engine (default unchanged by this rule)
    assert get_routing_destination("some/random/path.py") == "engine"
    # but any personal/ path fails closed to private
    assert get_routing_destination("personal/anything.md") == "private"


# --- the layer that actually reads a tree ------------------------------------
#
# Everything above asks the classifier and the pure core a question about a
# STRING. Neither opens a repository, so neither can tell a working `personal/`
# rule from one the wall never consults. `scan_engine_repo` is the entry point
# the push wall and `scripts/utils/git_push.py` both call, and the leak-path
# matrix (`tests/security/test_leak_path_matrix.py`) parametrizes it over
# `crm/contacts/`, `threads/` and `context/` -- not over `personal/`. So the one
# prefix this file exists for was the one no tree-level test named.
#
# Honest scope: a non-empty return is what the wall REFUSES on; the refusal
# itself, and its exit status, are pinned by the wall family
# (`tests/test_a_wall_that_switched_itself_off.py`,
# `tests/test_a_wall_that_read_the_present_and_shipped_the_past.py`,
# `tests/security/test_leak_path_matrix.py`). This adds the prefix, not a second
# copy of the stop.


def _engine_clone(tmp_path: Path) -> Path:
    repo = tmp_path / "engine"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True,
                   capture_output=True, timeout=60)
    return repo


def _plant(repo: Path, rel: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    # Invented content. No real person, contact or note ever enters this repo.
    p.write_text("# Placeholder\n\nSynthetic fixture text.\n", encoding="utf-8")


def _commit(repo: Path) -> None:
    for args in (["add", "-A", "-f"],
                 ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True,
                       capture_output=True, timeout=60)


def test_an_untracked_personal_file_is_seen_by_the_wall_entry_point(tmp_path):
    """`git add` has not run yet, and it is already an artifact.

    Untracked-but-not-ignored is what `repo_carried_paths` calls "would go to the
    remote on the next commit", so this is the state a leak is actually in when
    somebody notices it.
    """
    repo = _engine_clone(tmp_path)
    _plant(repo, "personal/context/personal-info.md")
    _plant(repo, "scripts/ordinary.py")

    found = scan_engine_repo(repo)
    assert "personal/context/personal-info.md" in found, (
        f"the wall entry point does not see personal/ in a tree: {found}")
    assert "scripts/ordinary.py" not in found, (
        "engine code must never be flagged; the guard would be unusable")


def test_a_committed_personal_file_is_seen_too(tmp_path):
    """The other half of `repo_carried_paths`: tracked, not just untracked."""
    repo = _engine_clone(tmp_path)
    _plant(repo, "personal/crm/contacts/example-contact.md")
    _commit(repo)

    assert "personal/crm/contacts/example-contact.md" in scan_engine_repo(repo)


def test_a_clean_engine_tree_still_reads_clean(tmp_path):
    """The negative case, so the two above cannot pass by flagging everything."""
    repo = _engine_clone(tmp_path)
    _plant(repo, "scripts/ordinary.py")
    _plant(repo, "docs/ARCHITECTURE-example.md")
    _commit(repo)

    assert scan_engine_repo(repo) == []

"""Shard `scripts-11-p4`: a leak wall the operator's own language walked past.

`push-all.py` step 1 refuses to push when a secret-like path is tracked: `.env`,
`*.session`, `.sessions/`, `cookies.json`. It listed those paths with a plain
`git ls-files`, and git C-quotes any path holding a non-ASCII byte, wrapping the
whole thing in double quotes. `SECRET_TRACKED` anchors three of its five
branches on `$`, and a trailing `"` beats every one of them; step 2's
`.memory-index/` prefix test is beaten by the leading `"`. So a tracked
credential under a Cyrillic-named directory was not refused, on a workspace
whose operator writes in Russian, and whose DATA clone carries C-quoted paths
today.

`content_scan()` is no backstop: it reads the push DELTA, and step 1 exists
precisely for a credential tracked long before this push. The same defect was
found and fixed in `_push_delta_files` on 2026-08-23, four hundred lines up in
the same file, and `tests/test_push_all_gate.py` pins it there. One call site
was fixed and its neighbour was left.

The rest of the shard:

  - `_push_delta_files` gated its `origin/main..HEAD` diff on `have_base`, which
    proves only that origin/main resolves. With an unborn HEAD git exits 128,
    `run` defaults to `check=True`, and the CalledProcessError is not one of the
    two things `_attempt` absorbs: the backup died with a traceback and NEITHER
    repo was pushed. `engine_content_scan` calls it at step 0, before the
    commit, so a clone that has fetched a remote but committed nothing of its
    own met this on an ordinary run.
  - `regenerate-docs-html.py` resolved every relative `.md` link against the
    hardcoded SITE_DIR, while `tracked_dirs()` also renders `templates/` and the
    DATA overlay's `docs/` and `templates/`. The dangerous half is not the link
    left alone: it is the coincidence, where a `templates/X.md` link rewrites to
    `X.html` because `docs/X.html` exists.
  - The same function's docstring said an unresolved link was "reported by the
    caller". It returned `str`, so no caller COULD learn which targets missed,
    and nothing anywhere printed one. That silence is what made the wrong-
    directory resolution above invisible at generation time.
  - `render-doctype.py` ran a non-ISO date through `slugify(date, 10)`. Ten
    characters cuts mid-token: "21 April 2026" became "21-april-2", and any
    month of seven or more letters lost the year entirely, so "December 2026"
    became "december-2". The date prefix exists to make the document directory
    sort, and the comment two lines above names the very format that breaks.

Fixed 2026-08-25.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# push-all.py calls ensure_venv() at MODULE scope; tests/conftest.py sets the
# guard that stops it re-execing pytest. Same note as tests/test_push_all_gate.py.
push_all = _load("push_all_11p4", "scripts/push-all.py")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


def _init_repo(tmp_path: Path, name: str = "repo") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    return repo


def _write(repo: Path, rel: str, body: str = "x") -> Path:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


@pytest.fixture
def no_denial_log(monkeypatch):
    """The wall records refusals into the real data overlay; not from a test.

    The stub RECORDS rather than discards. A stub that throws its argument away
    makes every call look identical to no call at all, which is how the
    `log_denial` call below went untested: measured 2026-09-01, deleting it left
    this file and `test_push_all_gate.py` and `test_denial_log_isolation.py` all
    green while `push:secret-tracked-files`, a DECLARED WALL in
    `scripts/utils/gate_yield.WALLS`, reported zero firings forever.
    """
    recorded: list[dict] = []
    monkeypatch.setattr(push_all, "log_denial",
                        lambda **kw: recorded.append(kw) or True)
    return recorded


def _step_one(repo: Path):
    """Run push_repo far enough to exercise steps 1 and 2, and no further."""
    return push_all.push_repo(
        "test", repo, "msg", do_commit=False, dry_run=True, push_env={},
        is_engine=False)


# ===========================================================================
# The wall itself
# ===========================================================================

def test_a_credential_under_a_cyrillic_directory_is_refused(tmp_path, capsys,
                                                            no_denial_log):
    """The whole finding: git quotes the path, the trailing quote beats `\\.env$`,
    and the wall waves a tracked credential through to a public remote."""
    repo = _init_repo(tmp_path)
    _write(repo, "документы/.env", "API_KEY=live\n")
    _git(repo, "add", "-A")
    with pytest.raises(SystemExit) as exc:
        _step_one(repo)
    assert exc.value.code == 2
    assert "документы/.env" in capsys.readouterr().out


def test_a_session_file_under_a_cyrillic_directory_is_refused(tmp_path, capsys,
                                                              no_denial_log):
    """`\\.session$` is anchored the same way, so it failed the same way."""
    repo = _init_repo(tmp_path)
    _write(repo, "данные/telegram.session", "blob\n")
    _git(repo, "add", "-A")
    with pytest.raises(SystemExit):
        _step_one(repo)
    assert "данные/telegram.session" in capsys.readouterr().out


def test_cookies_under_a_cyrillic_directory_are_refused(tmp_path, capsys,
                                                        no_denial_log):
    repo = _init_repo(tmp_path)
    _write(repo, "браузер/cookies.json", "[]\n")
    _git(repo, "add", "-A")
    with pytest.raises(SystemExit):
        _step_one(repo)
    assert "браузер/cookies.json" in capsys.readouterr().out


def test_a_tracked_memory_index_under_a_cyrillic_path_is_refused(tmp_path, capsys,
                                                                 no_denial_log):
    """Step 2's prefix test is beaten by the LEADING quote, not the trailing one."""
    repo = _init_repo(tmp_path)
    _write(repo, ".memory-index/заметки.db", "blob\n")
    _git(repo, "add", "-A")
    with pytest.raises(SystemExit) as exc:
        _step_one(repo)
    assert exc.value.code == 2
    assert ".memory-index/" in capsys.readouterr().out


def test_the_refused_paths_are_printed_unquoted(tmp_path, capsys, no_denial_log):
    """A C-quoted name in the refusal message is not a path the operator can
    `git rm --cached`, which is the one instruction the message gives."""
    repo = _init_repo(tmp_path)
    _write(repo, "документы/.env", "k=v\n")
    _git(repo, "add", "-A")
    with pytest.raises(SystemExit):
        _step_one(repo)
    out = capsys.readouterr().out
    assert '\\320' not in out
    assert '"документы' not in out


def test_the_refusal_is_recorded_in_the_denial_ledger(tmp_path, capsys,
                                                      no_denial_log):
    """`push:secret-tracked-files` is a declared WALL in
    `scripts/utils/gate_yield.WALLS`, and the denial log is the only place its
    firings are counted. `tests/test_yield_axes.py` asserts the NAME is
    declared; nothing asserted the call site still exists, so the ledger could
    report a wall that never fires while the wall fires every time."""
    repo = _init_repo(tmp_path)
    _write(repo, "config/.env", "k=v\n")
    _git(repo, "add", "-A")
    with pytest.raises(SystemExit):
        _step_one(repo)

    assert len(no_denial_log) == 1, no_denial_log
    record = no_denial_log[0]
    assert record["mechanism"] == "push:secret-tracked-files"
    assert record["action"] == "push"
    assert record["path"] == "config/.env"
    assert record["reason"]


def test_every_refused_path_gets_its_own_record(tmp_path, capsys, no_denial_log):
    """One record per file, not one per run: the ledger counts refusals, and a
    push carrying three credentials refused three things."""
    # Escaped rather than typed. The sibling cases above spell these names
    # literally, which is fine there; new lines in this campaign are written
    # ASCII-only so the file's byte content is decidable without a hex dump.
    docs = "\u0434\u0430\u043d\u043d\u044b\u0435"      # "dannye"
    browser = "\u0431\u0440\u0430\u0443\u0437\u0435\u0440"  # "brauzer"
    repo = _init_repo(tmp_path)
    _write(repo, "config/.env", "k=v\n")
    _write(repo, f"{docs}/telegram.session", "blob\n")
    _write(repo, f"{browser}/cookies.json", "[]\n")
    _git(repo, "add", "-A")
    with pytest.raises(SystemExit):
        _step_one(repo)

    paths = sorted(r["path"] for r in no_denial_log)
    assert paths == sorted(["config/.env",
                            f"{browser}/cookies.json",
                            f"{docs}/telegram.session"]), paths


def test_a_clean_repo_records_no_refusal(tmp_path, no_denial_log, monkeypatch):
    """The other jaw. A wall that logs on every run reports a firing count that
    means nothing."""
    repo = _init_repo(tmp_path)
    _write(repo, "notes.md", "text\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    monkeypatch.setattr(push_all, "content_scan",
                        lambda _r: (_ for _ in ()).throw(RuntimeError("reached step 3.5")))
    with pytest.raises(RuntimeError, match="step 3.5"):
        _step_one(repo)

    assert no_denial_log == []


def test_an_ascii_credential_is_still_refused(tmp_path, capsys, no_denial_log):
    """Anchor: the case that always worked must keep working."""
    repo = _init_repo(tmp_path)
    _write(repo, "config/.env", "k=v\n")
    _git(repo, "add", "-A")
    with pytest.raises(SystemExit) as exc:
        _step_one(repo)
    assert exc.value.code == 2
    assert "config/.env" in capsys.readouterr().out


def test_an_example_file_is_still_allowed(tmp_path, no_denial_log, monkeypatch):
    """Anchor: `.env.example` is the template every clone needs."""
    repo = _init_repo(tmp_path)
    _write(repo, ".env.example", "API_KEY=\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    # Stop the run right after step 2; steps 3+ commit, scan and push.
    monkeypatch.setattr(push_all, "content_scan",
                        lambda _r: (_ for _ in ()).throw(RuntimeError("reached step 3.5")))
    with pytest.raises(RuntimeError, match="step 3.5"):
        _step_one(repo)


def test_a_cyrillic_named_ordinary_file_is_not_refused(tmp_path, no_denial_log,
                                                       monkeypatch):
    """Anchor: unquoting must not turn every non-ASCII name into a leak."""
    repo = _init_repo(tmp_path)
    _write(repo, "документы/обзор.md", "text\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    monkeypatch.setattr(push_all, "content_scan",
                        lambda _r: (_ for _ in ()).throw(RuntimeError("reached step 3.5")))
    with pytest.raises(RuntimeError, match="step 3.5"):
        _step_one(repo)


# ===========================================================================
# The diff that needed both ends of the range
# ===========================================================================

def _repo_with_remote_but_no_commits(tmp_path) -> Path:
    """A clone that has fetched origin/main and committed nothing of its own."""
    upstream = _init_repo(tmp_path, "upstream")
    _write(upstream, "a.txt", "hello\n")
    _git(upstream, "add", "-A")
    _git(upstream, "commit", "-qm", "base")

    local = _init_repo(tmp_path, "local")
    _git(local, "remote", "add", "origin", str(upstream))
    _git(local, "fetch", "-q", "origin")
    _git(local, "update-ref", "refs/remotes/origin/main", "refs/remotes/origin/main")
    return local


def test_an_unborn_head_does_not_kill_the_whole_backup(tmp_path):
    """`origin/main..HEAD` needs both ends; `have_base` proved only the left one,
    and the CalledProcessError escaped `_attempt` to take both repos down."""
    local = _repo_with_remote_but_no_commits(tmp_path)
    assert push_all._push_delta_files(local) == set()


def test_an_unborn_head_still_scans_the_staged_tree(tmp_path):
    """The point of scanning pre-commit: a staged secret must still be seen."""
    local = _repo_with_remote_but_no_commits(tmp_path)
    _write(local, "config/.env", "k=v\n")
    _git(local, "add", "-A")
    assert "config/.env" in push_all._push_delta_files(local)


def test_an_unborn_head_still_scans_untracked_files(tmp_path):
    local = _repo_with_remote_but_no_commits(tmp_path)
    _write(local, "notes.md", "text\n")
    assert "notes.md" in push_all._push_delta_files(local)


def test_a_git_failure_takes_down_the_whole_run(tmp_path, monkeypatch):
    """Why an unborn HEAD cost BOTH repos and not one: `_attempt` absorbs only
    RepoNotPushable, deliberately, so nothing else is contained."""
    monkeypatch.setattr(push_all, "push_repo", lambda *_a, **_k: (_ for _ in ()).throw(
        subprocess.CalledProcessError(128, ["git", "diff"])))
    with pytest.raises(subprocess.CalledProcessError):
        push_all._attempt([], "engine", tmp_path, "m", False, True, {})


def test_a_normal_repo_still_uses_the_three_diffs(tmp_path):
    """Anchor: the committed-but-unpushed DELTA is the main job of this set, and
    it is narrower than the index. A file already identical to origin/main is
    not about to be pushed, so falling back to `ls-files` for a born HEAD would
    hand the scanner the whole repository on every run."""
    local = _repo_with_remote_but_no_commits(tmp_path)
    _git(local, "reset", "--hard", "-q", "origin/main")   # a.txt, already pushed
    # A file the upstream does NOT have, or `origin/main..HEAD` compares two
    # identical trees and correctly reports nothing.
    _write(local, "c.txt", "mine\n")
    _git(local, "add", "-A")
    _git(local, "commit", "-qm", "mine")
    _write(local, "b.txt", "new\n")
    _git(local, "add", "-A")
    delta = push_all._push_delta_files(local)
    assert {"c.txt", "b.txt"} <= delta
    assert "a.txt" not in delta, "the delta collapsed into a full index listing"


def test_a_repo_with_no_remote_still_lists_the_index(tmp_path):
    """Anchor: the pre-existing no-base branch must be untouched."""
    repo = _init_repo(tmp_path)
    _write(repo, "a.txt", "hello\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    assert "a.txt" in push_all._push_delta_files(repo)


# ===========================================================================
# The link rewriter that looked in the wrong directory
# ===========================================================================

@pytest.fixture
def rd():
    return _load("regen_11p4", "scripts/regenerate-docs-html.py")


_LINK = '<p><a href="NEIGHBOUR.md">n</a></p>'


def test_a_link_resolves_against_the_page_being_rendered(rd, tmp_path):
    """The finding: SITE_DIR for every page, while templates/ and the DATA
    overlay's docs/ and templates/ are rendered through the same function."""
    (tmp_path / "NEIGHBOUR.html").write_text("x", encoding="utf-8")
    html, unresolved = rd._point_md_links_at_the_rendered_page(_LINK, tmp_path)
    assert 'href="NEIGHBOUR.html"' in html
    assert unresolved == []


def test_a_coincidence_in_the_site_dir_no_longer_rewrites(rd, tmp_path,
                                                          monkeypatch):
    """The dangerous half: a confident WRONG href, not an unchanged one."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "NEIGHBOUR.html").write_text("x", encoding="utf-8")
    other = tmp_path / "templates"
    other.mkdir()
    monkeypatch.setattr(rd, "SITE_DIR", site)
    html, unresolved = rd._point_md_links_at_the_rendered_page(_LINK, other)
    assert 'href="NEIGHBOUR.md"' in html, "rewritten against a directory it does not live in"
    assert unresolved == ["NEIGHBOUR.md"]


def test_an_unresolved_link_is_returned_to_the_caller(rd, tmp_path):
    """The docstring promised the caller was told; `str` was the whole return
    type, so no caller COULD be."""
    _html, unresolved = rd._point_md_links_at_the_rendered_page(_LINK, tmp_path)
    assert unresolved == ["NEIGHBOUR.md"]


def test_an_unresolved_link_is_left_as_written(rd, tmp_path):
    """Anchor: pointing it at a second missing page would just move the 404."""
    html, _ = rd._point_md_links_at_the_rendered_page(_LINK, tmp_path)
    assert 'href="NEIGHBOUR.md"' in html


def test_an_absolute_link_is_never_touched(rd, tmp_path):
    """Anchor: the pattern excludes http, mailto and fragments by design."""
    src = '<a href="https://example.com/x.md">x</a>'
    html, unresolved = rd._point_md_links_at_the_rendered_page(src, tmp_path)
    assert html == src
    assert unresolved == []


def test_a_fragment_survives_the_rewrite(rd, tmp_path):
    """Anchor: `X.md#section` must become `X.html#section`, not lose the anchor."""
    (tmp_path / "N.html").write_text("x", encoding="utf-8")
    html, _ = rd._point_md_links_at_the_rendered_page(
        '<a href="N.md#top">n</a>', tmp_path)
    assert 'href="N.html#top"' in html


def test_md_to_html_defaults_to_the_site_dir(rd, tmp_path, monkeypatch):
    """Anchor: the public site is still the base when no page dir is supplied."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "N.html").write_text("x", encoding="utf-8")
    monkeypatch.setattr(rd, "SITE_DIR", site)
    html, _ = rd.md_to_html("[n](N.md)")
    assert 'href="N.html"' in html


def test_md_to_html_honours_an_explicit_base(rd, tmp_path, monkeypatch):
    site = tmp_path / "site"
    site.mkdir()
    other = tmp_path / "templates"
    other.mkdir()
    (other / "N.html").write_text("x", encoding="utf-8")
    monkeypatch.setattr(rd, "SITE_DIR", site)
    html, _ = rd.md_to_html("[n](N.md)", other)
    assert 'href="N.html"' in html


def test_regenerate_reports_an_unresolved_link(rd, tmp_path, monkeypatch, capsys):
    """End to end: nothing printed one, so every wrong-directory resolution was
    invisible at generation time."""
    monkeypatch.setattr(rd, "SITE_DIR", tmp_path / "site")
    page = tmp_path / "guide.md"
    page.write_text("# Guide\n\n[n](MISSING.md)\n", encoding="utf-8")
    assert rd.regenerate(page, quiet=True) is True
    err = capsys.readouterr().err
    assert "[unresolved]" in err
    assert "MISSING.md" in err


def test_regenerate_resolves_against_the_pages_own_directory(rd, tmp_path,
                                                             monkeypatch, capsys):
    monkeypatch.setattr(rd, "SITE_DIR", tmp_path / "site")
    (tmp_path / "NEIGHBOUR.html").write_text("x", encoding="utf-8")
    page = tmp_path / "guide.md"
    page.write_text("# Guide\n\n[n](NEIGHBOUR.md)\n", encoding="utf-8")
    rd.regenerate(page, quiet=True)
    assert 'href="NEIGHBOUR.html"' in (tmp_path / "guide.html").read_text(encoding="utf-8")
    assert "[unresolved]" not in capsys.readouterr().err


def test_the_report_survives_quiet_mode(rd, tmp_path, monkeypatch, capsys):
    """`--quiet` silences the progress line, never a finding."""
    monkeypatch.setattr(rd, "SITE_DIR", tmp_path / "site")
    page = tmp_path / "guide.md"
    page.write_text("# Guide\n\n[n](MISSING.md)\n", encoding="utf-8")
    rd.regenerate(page, quiet=True)
    captured = capsys.readouterr()
    assert "-> " not in captured.out
    assert "[unresolved]" in captured.err


# ===========================================================================
# The date that lost its year
# ===========================================================================

@pytest.fixture
def rdoc():
    return _load("rdoc_11p4", "scripts/render-doctype.py")


@pytest.mark.parametrize(("prose", "expected"), [
    ("21 April 2026", "2026-04-21"),
    ("April 21, 2026", "2026-04-21"),
    ("21 Apr 2026", "2026-04-21"),
    ("1 May 2026", "2026-05-01"),
    ("December 2026", "2026-12"),
    ("31 December 2026", "2026-12-31"),
])
def test_a_prose_date_becomes_iso(rdoc, prose, expected):
    assert rdoc.iso_from_prose(prose) == expected


@pytest.mark.parametrize("prose", [
    "undated", "2026", "next quarter", "", "May",
    # "a" is a prefix of april and august, and an ordinary English word. Only a
    # full name or a three-letter abbreviation may name a month, or a phrase
    # with no date in it at all resolves to a confident wrong one.
    "effective a year from 2026",
])
def test_an_unparseable_date_returns_none(rdoc, prose):
    assert rdoc.iso_from_prose(prose) is None


def test_a_trailing_number_does_not_steal_the_day(rdoc):
    """The FIRST day-sized number wins. A second one later in the string is a
    revision marker, not the date."""
    assert rdoc.iso_from_prose("21 April 2026 rev 2") == "2026-04-21"


def test_the_named_example_no_longer_truncates(rdoc):
    """The comment above the code names "21 April 2026", and that is the input
    that became "21-april-2"."""
    name = rdoc.build_filename({"DATE": "21 April 2026", "RECIPIENT_ORG": "Globex",
                                "SUBJECT": "Renewal"}, "letter", "pdf")
    assert name.startswith("2026-04-21_letter_")
    assert "21-april-2" not in name


def test_a_long_month_no_longer_loses_the_year(rdoc):
    """Seven letters or more and the year fell off the end entirely."""
    name = rdoc.build_filename({"DATE": "December 2026", "RECIPIENT_ORG": "Globex",
                                "SUBJECT": "Renewal"}, "letter", "pdf")
    assert name.startswith("2026-12_letter_")


def test_an_unparseable_date_is_reproduced_whole(rdoc):
    """A plausible fragment of a date reads as a date; the whole string does not."""
    name = rdoc.build_filename({"DATE": "sometime next quarter"}, "official", "pdf")
    assert name.startswith("sometime-next-quarter_official_")


def test_an_iso_date_is_still_passed_through(rdoc):
    """Anchor: the common path must be untouched."""
    name = rdoc.build_filename({"DATE": "2026-04-21", "RECIPIENT_ORG": "Globex",
                                "SUBJECT": "Renewal"}, "letter", "pdf")
    assert name.startswith("2026-04-21_letter_globex_renewal")


def test_an_iso_date_inside_prose_is_extracted(rdoc):
    """Anchor: the ISO branch uses `re.search`, not a full match, so a date
    wrapped in words still yields the sortable prefix and not the whole phrase."""
    name = rdoc.build_filename({"DATE": "as of 2026-04-21", "SUBJECT": "Notice"},
                               "official", "pdf")
    assert name.startswith("2026-04-21_official_")


def test_a_missing_date_is_still_undated(rdoc):
    """Anchor: `undated` is a deliberate, readable marker."""
    name = rdoc.build_filename({"SUBJECT": "Renewal"}, "official", "pdf")
    assert name.startswith("undated_official_")


def test_the_effective_date_field_is_still_read(rdoc):
    """Anchor: partnership documents carry EFFECTIVE_DATE, not DATE."""
    name = rdoc.build_filename({"EFFECTIVE_DATE": "3 March 2027",
                                "PARTY_B_SHORT": "Globex"}, "partnership", "pdf")
    assert name.startswith("2027-03-03_partnership_globex_")


def test_the_filename_still_matches_the_locked_convention(rdoc):
    """`.claude/rules/corporate-docs.md` fixes the shape; the date prefix exists
    to make the output directory sort."""
    name = rdoc.build_filename({"DATE": "21 April 2026", "RECIPIENT_ORG": "Globex",
                                "SUBJECT": "Renewal terms"}, "letter", "pdf")
    assert name == "2026-04-21_letter_globex_renewal-terms.pdf"

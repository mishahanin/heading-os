#!/usr/bin/env python3
"""Verified, supervised git push — the one must-complete push primitive.

Wraps ``scripts/utils/supervise.run_supervised`` around ``git push`` with an
``ahead/behind == (0, 0)`` postcondition, so every push path in the workspace
(the safe-push CLI, the /backup → push-all flow, the corporate promote/rollback
gates, offboard) shares ONE mechanism that:

  (a) is bounded by *inactivity*, not a wall-clock guess — a slow-but-healthy
      pre-push test gate (~2.5 min and growing) is never clipped, while a truly
      stalled connection is caught and killed; and
  (b) never trusts a bare-push exit code — a ``git push`` that reports success
      while the ref did not advance is caught by the postcondition (the
      documented "bare push silently fails" case).

Auth is flexible so each caller keeps its existing credential model:
  * ``token=`` COPIES the ambient environment and adds the GH_TOKEN credential
    helper to that copy (the token never touches argv);
  * ``env=`` uses a caller-built env as-is, and nothing ambient is added;
  * passing neither leaves ``env=None``, which ``Popen`` resolves by inheriting
    the ambient environment in full.

That third bullet read "neither inherits the ambient environment (preserves a
caller's own setup)" until 2026-08-29. It was false on two of the four paths,
and on a primitive whose whole job is refusing the wrong push it was the
security promise a reviewer would rely on.

ONE ENVIRONMENT, for the wall and for the push. Every git call this module makes
on behalf of a push - the remote-identity wall, the repository-root check, the
ahead/behind postcondition - takes the SAME ``env`` the push itself will run
with. They used to differ: the wall shelled out with the ambient environment
while the push ran the caller's, so the two answered questions about different
worlds. Measured 2026-08-29 with a local ``file://`` remote standing in for the
engine's:

  * a ``url.<base>.insteadOf`` (or ``pushInsteadOf``) pair present only in the
    PUSH env sent the private overlay to the engine remote with the wall silent,
    ``state: "ok"`` and the ahead/behind postcondition satisfied;
  * the same pair present only in the AMBIENT environment refused a push that
    would have been perfectly safe.

The fix is not a denylist of dangerous variables - that is always one variable
short. It is that both halves ask git the same question in the same world.
``git remote get-url --push`` already reports the URL a push will really use,
including ``pushurl``, ``insteadOf`` and ``pushInsteadOf``; it was simply being
asked under the wrong environment.
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.utils.engine_guard import scan_engine_repo
from scripts.utils.supervise import run_supervised
from scripts.utils.workspace import (
    get_data_root,
    get_workspace_root,
    read_env_value,
)

# Echoes the token from the child env (NOT argv) into git's credential protocol.
_CRED_HELPER = '!f(){ echo username=x-access-token; echo "password=$GH_PUSH_TOKEN"; }; f'

logger = logging.getLogger(__name__)

_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")

# GitHub answers pushes addressed to more than one hostname for the same
# identity. `www.github.com` 301-redirects `info/refs?service=git-receive-pack`
# to `github.com`, and git follows that redirect by default, so `git ls-remote`
# and a real push both land on the repository named by `github.com`: it is
# the same remote, not a different one. `ssh.github.com` is GitHub's own
# documented port-443 workaround for networks that block outbound 22, and it is
# the identical SSH endpoint. A narrow, explicit mapping rather than a general
# "strip any subdomain" rule, because folding an unrelated host (a GitHub
# Enterprise Server instance, say) onto github.com would be wrong in the other
# direction. Before this mapping existed, Check A's set-membership test, Check
# B's `host != "github.com"` guard, and the cannot-verify warning below all
# keyed on this same literal host string, so an operator (or an attacker)
# spelling the engine's remote either way defeated all three simultaneously
# and the private overlay reached the public engine repository with no
# objection at all.
_GITHUB_HOST_ALIASES = {
    "www.github.com": "github.com",
    "ssh.github.com": "github.com",
}

# Visibility answers for the life of the process. A real push-all run asks the
# same question twice per repository (the precondition and then the chokepoint),
# and each miss can cost a network timeout on the one command that must not be
# slow. A cache miss is also what gates the warning below, so one mechanism
# serves both.
#
# The key carries whether the lookup was authenticated, and that is not a detail:
# an unauthenticated probe of a private repository gets a 404, which is stored as
# "cannot answer". create-data-repo calls supervised_push with neither token= nor
# env=, so the chokepoint resolves its token through load_gh_token() while other
# callers pass one explicitly. Keying on the URL alone would let one tokenless
# answer poison every later tokened lookup in the same process and quietly hold
# the wall at its weaker reading.
_VIS_CACHE: dict[tuple[str, bool], Optional[str]] = {}


# The one file that only the engine clone carries. `CLAUDE.md` and `.claude/` are
# not enough: the data overlay has `.claude/` too.
_ENGINE_MARKER = Path("scripts") / "utils" / "engine_guard.py"


def _is_split_engine(repo: Path) -> bool:
    """True iff ``repo`` is the ENGINE clone and must therefore stay code-only.

    Only the engine is walled. The DATA overlay and the corporate/CRM repos
    legitimately carry private/corporate content, so they are exempt, and they are
    identified by not being the workspace root.

    The second test used to be "the data root resolves somewhere else", exempting a
    repository whose data root collapsed onto itself. That is an inversion, because
    of HOW the collapse happens: `get_data_root()` rule 2 returns the workspace root
    when it finds `crm/contacts/` or `knowledge/` INSIDE it. Private data appearing
    in the engine clone is the single condition this wall exists to catch, and it
    was the condition that switched the wall off. Reproduced on 2026-08-25 against a
    scratch engine clone holding `knowledge/note.md` and `outputs/deal.md`:
    `_is_split_engine` answered False, `_roots_unreadable` answered None, the wall
    never ran, and `scan_engine_repo` - had it been called - flagged all four
    artifacts. `get_data_root` logs a warning there, to a logger no one is reading
    at push time.

    So a collapsed data root no longer exempts anything. The engine marker decides
    instead, and the wall then REFUSES only if the scan actually finds something -
    the same direction `_roots_unreadable` already takes for an unreadable
    environment. A clean engine clone still pushes.
    """
    try:
        engine = get_workspace_root().resolve()
        data = get_data_root().resolve()
    except Exception:
        return False
    resolved = repo.resolve()
    if resolved != engine:
        return False
    if data != engine:
        return True
    return (resolved / _ENGINE_MARKER).is_file()


def _roots_unreadable(repo: Path) -> str | None:
    """Why the workspace roots could not be resolved, when `repo` is the engine.

    None means "no problem here": either the roots resolved, or this repository
    is not the engine clone and was never walled.

    This exists because `_is_split_engine` answers False on an unreadable
    environment, and False means "exempt". So the leak wall stopped running in
    exactly the broken state where misrouting is most likely, and said nothing -
    found by the 2026-08-23 audit. The remote-check leg already warned loudly for
    the same condition; the tree wall did not.
    """
    try:
        get_workspace_root().resolve()
        get_data_root().resolve()
    except Exception as exc:
        if (Path(repo) / _ENGINE_MARKER).is_file():
            return str(exc)
    return None


def _normalize_remote_url(url: str) -> str:
    """Canonical ``host/owner/repo`` for a git remote URL, in any form it takes.

    Two URLs naming the same repository must compare equal, so scheme, userinfo,
    port, a ``.git`` suffix, a trailing slash and case are all removed. GitHub
    treats host and owner/repo case-insensitively, so lowercasing is safe.

    Stripping userinfo happens HERE, before any comparison, so that no reason
    string, warning or log line downstream can carry a token that a remote
    legitimately embeds for authentication. A wall whose refusal message leaks
    the credential it was protecting is worse than no wall.
    """
    s = url.strip()
    scheme = _SCHEME_RE.match(s)
    if scheme:
        s = s[scheme.end():]
    head, _sep, tail = s.partition("/")
    if "@" in head:
        head = head.rsplit("@", 1)[1]
    if ":" in head:
        host, _, after = head.partition(":")
        head = host
        # A numeric tail is a port and carries no identity. Anything else is the
        # scp-style form where the colon separates host from path.
        if after and not after.isdigit():
            tail = f"{after}/{tail}" if tail else after
    head = _GITHUB_HOST_ALIASES.get(head.lower(), head)
    path = tail.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return f"{head}/{path}".rstrip("/").lower()


def _push_url(repo, remote: str = "origin", *,
              env: Optional[dict] = None) -> Optional[str]:
    """The URL git would actually PUSH ``repo`` to, or None.

    ``--push`` is load-bearing: a remote may carry a ``pushurl`` that differs
    from its fetch URL, and the question this wall asks is where a push lands,
    not where a fetch came from. Measured 2026-08-29, this form also applies
    ``url.<base>.insteadOf`` and ``url.<base>.pushInsteadOf``, and agrees with
    ``git push --dry-run`` in every combination of the three.

    ``env`` is the environment the PUSH will run under, and it must be the same
    one. A rewrite pair lives in the environment, so reading the URL in a
    different environment answers a question about a different push.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "remote", "get-url", "--push", remote],
            capture_output=True, text=True, timeout=15, env=env,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("push url unreadable for %s: %s", repo, exc)
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def _gh_visibility(normalized: str, *, token: Optional[str] = None) -> Optional[str]:
    """GitHub's own answer for ``host/owner/repo``, or None when it cannot answer.

    None is returned for every unanswerable case without distinction: no token,
    a network error, a 404 on a repository this token cannot see, a rate limit,
    or a host that is not GitHub. None of those carries information about
    whether the repository is private, so none of them is a refusal.
    """
    host, _, path = normalized.partition("/")
    if host != "github.com" or path.count("/") != 1:
        return None
    try:
        req = urllib.request.Request(  # noqa: S310 - https literal, host pinned
            f"https://api.github.com/repos/{path}",
            headers={"User-Agent": "heading-os-remote-wall",
                     "Accept": "application/vnd.github+json"},
        )
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - https literal
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        logger.debug("remote wall: HTTP %s for %s", exc.code, normalized)
        return None
    except (URLError, OSError, http.client.HTTPException) as exc:
        # HTTPException is neither URLError nor OSError. IncompleteRead comes out
        # of resp.read() on a truncated body, and BadStatusLine comes out of
        # getresponse(), which urllib does not wrap. A flaky uplink is an ordinary
        # event and must not abort a backup.
        logger.debug("remote wall: network error for %s: %s", normalized, exc)
        return None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.debug("remote wall: bad JSON for %s: %s", normalized, exc)
        return None
    except Exception as exc:  # the family, closed by shape
        # The family, closed by shape rather than by enumeration. Three members
        # reached production one at a time: a non-dict body, an HTTPException,
        # and a UnicodeEncodeError from a non-ASCII repository name, which is a
        # ValueError and matched nothing. Each aborted push-all with a traceback,
        # and DATA is attempted first, so nothing at all was pushed.
        #
        # Not knowing the visibility is the case this function exists to fail
        # open on, so ANY failure to determine it means the same thing: return
        # None and let the offline check carry the decision. Logged, never
        # swallowed silently.
        logger.debug("remote wall: visibility unreadable for %s: %s", normalized, exc)
        return None
    if not isinstance(data, dict):
        # A 200 whose body is null, a list, or a scalar decodes without error and
        # then has no .get. An intercepting proxy answering for api.github.com is
        # enough to produce it. Escaping here would abort the whole backup, and
        # DATA is attempted first, so nothing at all would be pushed. Not
        # knowing the visibility is exactly the case this function fails open on.
        logger.debug("remote wall: non-object body for %s", normalized)
        return None
    visibility = data.get("visibility")
    return visibility if visibility in ("public", "private", "internal") else None


def _visibility_cached(normalized: str,
                       token: Optional[str]) -> tuple[Optional[str], bool]:
    """(visibility, was_freshly_looked_up) for ``normalized``."""
    key = (normalized, bool(token))
    if key in _VIS_CACHE:
        return _VIS_CACHE[key], False
    visibility = _gh_visibility(normalized, token=token)
    _VIS_CACHE[key] = visibility
    return visibility, True


def _engine_push_urls(engine, *, env: Optional[dict] = None) -> set:
    """Every normalized push URL the ENGINE clone has, under any remote name.

    Deliberately not `_push_url(engine, remote)`. The caller's remote NAME says
    nothing about what the engine calls its own remotes, and safe-push takes that
    name from the command line. Reading all of them means Check A compares
    identities rather than labels.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(engine), "remote"],
            capture_output=True, text=True, timeout=15, env=env,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("engine remotes unreadable: %s", exc)
        return set()
    if out.returncode != 0:
        return set()
    urls = set()
    for name in out.stdout.split():
        url = _push_url(engine, name, env=env)
        if url:
            urls.add(_normalize_remote_url(url))
    return urls


def remote_objection(repo, *, token: Optional[str] = None,
                     remote: str = "origin",
                     env: Optional[dict] = None) -> Optional[str]:
    """Why *repo* must not be pushed to its current remote, or None.

    ``env`` is the environment the push will run under. Pass it. Omitting it
    asks git about the ambient world while the push happens in another one, and
    a rewrite pair that exists in only one of the two is invisible to the half
    that does not have it. See the module docstring for the measurement.

    Pure: it reads git config and may read the GitHub API, and it changes
    nothing. Every segregation layer in this workspace answers "does this TREE
    carry the wrong content". This answers the other half, "does this REMOTE
    accept the wrong content", which nothing asked before.

    The engine is exempt: it is expected to point at the public engine
    repository, and that is the whole reason the question is interesting for
    everything else.
    """
    repo = Path(repo)
    if _is_split_engine(repo):
        return None
    try:
        engine = get_workspace_root().resolve()
        data = get_data_root().resolve()
    except Exception as exc:
        # Fail open, but never silently. Check A is the leg the design calls the
        # hard guarantee BECAUSE it is offline and therefore always available,
        # and this is the one branch where that is not true. _is_split_engine
        # answers False on the same condition, so a repository arriving here with
        # unreadable roots is neither exempted nor checked. Say so out loud
        # rather than returning a clean "no objection" that reads like a pass.
        logger.debug("remote wall: workspace roots unreadable: %s", exc)
        print(f"WARNING: could not resolve the workspace roots, so the offline "
              f"remote check did not run for {Path(repo).name}. Reason: {exc}")
        return None
    if data == engine:
        # Pre-cutover single repository: one repo, one remote, nothing to
        # compare. Comparing it to itself would refuse every backup.
        return None

    url = _push_url(repo, remote, env=env)
    # `remote` may itself BE a location rather than the name of a configured
    # one: `git push <url> <branch>` is valid git and needs no remote at all,
    # so an unconfigured NAME is not evidence that nothing can be pushed. An
    # earlier version returned no objection here on the reasoning that git
    # would fail on its own, and it does not. Measured on 2026-07-30:
    # safe-push --remote <the engine push URL> published the overlay to the
    # engine remote with every wall silent, and the only complaint came
    # afterwards from the ahead/behind postcondition.
    #
    # A plain remote name normalizes to a bare word, which matches no
    # host/owner/repo URL, so an unconfigured name still raises no objection
    # and `git push` still gets to fail on its own.
    here = _normalize_remote_url(url if url is not None else remote)

    engine_urls = _engine_push_urls(engine, env=env)
    if here in engine_urls:
        return (f"{repo.name} pushes to the ENGINE remote ({here}), which is the "
                f"public code repository. Refusing: this would publish private "
                f"content.")

    visibility, fresh = _visibility_cached(here, token)
    if visibility == "public":
        return (f"{repo.name} pushes to {here}, which GitHub reports as PUBLIC. "
                f"Refusing: only the engine may push to a public repository.")
    if visibility is None and fresh and token and here.partition("/")[0] == "github.com":
        # Fail open, loudly. Check A carries the hard guarantee precisely
        # because it is offline and therefore always available; Check B raises
        # the ceiling when it can and says so when it cannot.
        #
        # Scoped to github.com hosts on purpose. A non-GitHub remote is not a
        # lookup that failed, it is a question GitHub was never asked, and a
        # warning that fires on every local bare remote and every self-hosted
        # host is noise the operator learns to scroll past. That would cost the
        # warning its meaning on the one occasion it matters, which is a GitHub
        # remote whose visibility genuinely could not be read.
        #
        # Also requires a token. `push-all.py --dry-run` is explicitly supported
        # with no GH_TOKEN, and a tokenless probe of a private repository always
        # 404s, which is not a lookup that failed but a question that could not
        # be asked; warning on every tokenless dry run would teach the operator
        # to scroll past the one warning that matters.
        print(f"WARNING: could not verify the visibility of {here}. "
              f"Pushing on the offline check alone.")
    elif fresh:
        # A separate signal for the other ways this function reaches "no
        # objection" without either check having actually evaluated anything:
        # the engine's own remotes were unreadable (Check A ran against an
        # empty set), the repository being pushed has no resolvable push URL
        # under `remote` (only its bare name was compared), or Check B's own
        # guard never asked GitHub at all (a non-GitHub host, or a path that is
        # not `owner/repo`). Each of these produces a clean "no objection"
        # return that reads exactly like a pass on a repository the wall
        # actually evaluated, and the case above already covers the one
        # remaining "asked and failed" shape, so this is only the cases it
        # does not. Gated on the same `fresh` flag as the warning above so a
        # real push-all run (the precondition, then the chokepoint) prints
        # this once per repository rather than twice.
        parts = []
        if not engine_urls:
            parts.append("the engine's own push remotes could not be read")
        if url is None:
            parts.append(f"'{remote}' does not resolve to a push URL for "
                          f"{repo.name}, so only its bare name was compared")
        elif visibility is None:
            parts.append("GitHub was not asked, or could not answer, whether "
                          "this remote is public")
        if parts:
            print(f"NOTE: the remote wall reached its lower ceiling for "
                  f"{repo.name} ({here}): {'; '.join(parts)}. Proceeding "
                  f"without full confirmation.")
    return None


def load_gh_token() -> Optional[str]:
    """Return GH_TOKEN from the engine ``.env`` (the git pushgh source of truth).

    Parsing is `paths.parse_env_line`, the one grammar every reader and writer
    of this file now shares. This function used to match with
    `line.startswith("GH_TOKEN=")`, so a single leading space in front of the
    key made the token invisible to it while `load_env` read it perfectly well:
    safe-push then reported "no GH_TOKEN in engine .env" and named a cause that
    was not true. It also unquoted with a chained `.strip('"').strip("'")`,
    which is a character-class strip, not a pair strip.

    `read_env_value` is fail-soft, which this caller requires: a wall built to
    fail open must not carry a hard-crash path, and this expression is evaluated
    eagerly by every `supervised_push` caller, including `offboard-exec` and
    `create-data-repo`. A single non-UTF-8 byte in `.env` must not crash them.
    """
    return read_env_value(get_workspace_root() / ".env", "GH_TOKEN")


def enclosing_repo_root(path, *, env: Optional[dict] = None) -> Optional[Path]:
    """The work-tree root of the repository containing ``path``, or None.

    None means "could not establish one" and NEVER "the path is a root": not a
    repository at all, a bare repository (which has no work tree), or git
    unavailable. Callers must treat None as unknown, so this can only ever
    REFUSE on positive evidence.

    Every git call in this module is `git -C <path> ...`, and git walks UP from
    that path to the enclosing repository. Nothing here ever checked that the
    path it was handed was a root. MEASURED 2026-08-28 against a bare engine
    clone: `ahead_behind` answered `(0, 20)` for the repo root, for
    `<root>/examples`, and for `<root>/scripts/utils` alike - the same three
    numbers, because all three questions were about the enclosing repository.
    A linked git worktree IS its own toplevel, so this does not object to one.
    """
    # BYTES, then a deliberate decode. `git` answers with a filesystem PATH, and
    # any subprocess text mode turns on universal newlines and rewrites every CR
    # byte to LF - `subprocess` exposes no `newline=` knob to switch it off, and
    # naming an `encoding=` is the same translation. MEASURED 2026-09-01 on ext4
    # against real scratch repositories:
    #
    #   /tmp/gpprobe/re\rpo   -> text mode answered '/tmp/gpprobe/re\npo', which
    #                            compares unequal to `repo.resolve()`, so the
    #                            chokepoint below REFUSED a genuine root as a
    #                            subdirectory of itself. A false refusal is the
    #                            expensive direction, as the note on the
    #                            `.resolve()` call below already says.
    #   /tmp/gpprobe/re\xffpo -> UnicodeDecodeError, a `ValueError`, which
    #                            neither clause here catches. This function
    #                            documents None as "could not establish" and
    #                            raised out of the universal chokepoint instead.
    #
    # `surrogateescape` round-trips an undecodable byte back through
    # `os.fsencode`, so `Path` names the file git actually named.
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True, timeout=15, env=env,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("repo root unreadable for %s: %s", path, exc)
        return None
    if out.returncode != 0:
        return None
    # `removesuffix`, not `.strip()`. git terminates this answer with exactly one
    # newline, and a leading or trailing SPACE is a legal path component:
    # stripping one off names a directory that does not exist, and the guard
    # below then refuses a root for being a subdirectory of itself. That is the
    # same `.strip()` corruption already fixed in `ops_signals._repo_uncommitted`.
    top = out.stdout.removesuffix(b"\n").decode("utf-8", "surrogateescape")
    if not top:
        return None
    try:
        return Path(top).resolve()
    except OSError as exc:
        logger.debug("repo root unresolvable for %s: %s", path, exc)
        return None


def current_branch(repo) -> Optional[str]:
    """Return the current branch name of ``repo`` (or None)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def ahead_behind(repo, remote: str = "origin", branch: str = "main", *,
                 env: Optional[dict] = None) -> Optional[tuple[int, int]]:
    """Return (behind, ahead) of HEAD vs ``remote/branch``, or None on error.

    ``env`` is the environment the push ran under, for the same reason the wall
    takes one: ``GIT_DIR`` and friends decide which repository the question is
    about, so verifying in a different environment verifies a different tree.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-list", "--left-right", "--count",
             f"{remote}/{branch}...HEAD"],
            capture_output=True, text=True, timeout=30, env=env,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if out.returncode != 0:
        return None
    parts = out.stdout.split()
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def supervised_push(
    repo,
    *,
    remote: str = "origin",
    branch: str = "main",
    env: Optional[dict] = None,
    token: Optional[str] = None,
    stall_window: float = 120.0,
    status_path: Optional[str] = None,
    label: Optional[str] = None,
    log_dir: Optional[str] = None,
) -> dict:
    """Push ``repo`` to ``remote/branch`` under the progress watchdog and verify
    the ref actually advanced (``ahead/behind == 0 0``) before reporting success.

    Returns the ``run_supervised`` verdict dict (state ∈ ok/failed/hung/
    postcondition_failed). The caller decides what a non-"ok" state means.

    ``log_dir`` is forwarded verbatim to ``run_supervised``: where to put the
    run's log, ``None`` meaning the system temp directory. Production wants
    exactly that default, because the log is the thing an operator opens after
    a push that went wrong.

    IT EXISTS BECAUSE THERE WAS NO SEAM. This is the only caller of
    ``run_supervised`` outside its own tests, and four test files drive a real
    push through it. None of them could say "do not keep the log", because the
    parameter stopped one level below and there was nothing here to pass it to.
    MEASURED 2026-09-04 over a full run: 20 surviving ``supervise-*.log`` files,
    the largest remaining family after the rest of the suite was cleaned up.
    """
    repo = Path(repo)

    # The child environment is resolved FIRST, before any wall runs, because
    # every wall below has to ask git its question in the same world the push
    # will happen in. It used to be built after them, so they read the ambient
    # environment while the push ran the caller's. See the module docstring for
    # what that cost, measured.
    run_env = dict(env) if env is not None else None
    cmd = ["git", "-C", str(repo)]
    if token:
        run_env = dict(run_env if run_env is not None else os.environ)
        run_env["GH_PUSH_TOKEN"] = token
        run_env["GIT_TERMINAL_PROMPT"] = "0"
        cmd += ["-c", f"credential.helper={_CRED_HELPER}"]
    cmd += ["push", remote, branch]

    # Is this path the repository it claims to be? `git -C <path>` walks UP to
    # the enclosing repository, so a SUBDIRECTORY pushes its parent and the
    # ahead/behind postcondition then verifies the PARENT's ref: the run reports
    # a verified push of a repository it was never given. Six callers reach this
    # function, each passing a path it believes is a root, and none of them
    # checked. Refusing here covers all six, which is what "universal
    # chokepoint" below is supposed to mean.
    #
    # Only ever refuses on positive evidence. `enclosing_repo_root` returns None
    # for a bare repository and for a path that is no repository at all, and
    # None is treated as unknown so git still gets to fail on its own. A linked
    # git worktree is its own toplevel and passes.
    root = enclosing_repo_root(repo, env=run_env)
    if root is not None and root != repo.resolve():
        return {
            "state": "failed",
            "reason": (
                f"{repo} is not a git repository root: it sits inside the "
                f"repository at {root}. Pushing from here would push "
                f"'{root.name}', and the ahead/behind postcondition would "
                f"verify '{root.name}' too, so this run would report a "
                f"verified push of a repository it was never given."
            ),
            "elapsed_s": 0.0,
            "exit_code": None,
            "tail": "",
            "flagged": [],
        }

    # Engine/data leak wall (universal chokepoint). EVERY engine push -- push-all,
    # safe-push, or any future caller -- routes through here, so a private/corporate-
    # routed file in the engine clone can never leave the machine, on any path, with no
    # skip flag. Runs BEFORE the push subprocess (refuse, do not push-then-detect).
    # The DATA/corporate/CRM repos are exempt (they legitimately carry such files).
    # Refuse rather than skip when the roots are unreadable and this IS the engine
    # clone: an unbypassable wall that quietly stops scanning is not a wall.
    unreadable = _roots_unreadable(repo)
    if unreadable:
        return {
            "state": "failed",
            "reason": (
                f"cannot resolve the workspace roots, so the engine/data leak wall "
                f"could not run on this engine clone; refusing to push. Reason: "
                f"{unreadable}"
            ),
            "elapsed_s": 0.0,
            "exit_code": None,
            "tail": "",
            "flagged": [],
        }

    if _is_split_engine(repo):
        flagged = scan_engine_repo(repo)
        if flagged:
            preview = ", ".join(flagged[:5]) + (" ..." if len(flagged) > 5 else "")
            return {
                "state": "failed",
                "reason": (
                    f"engine clone carries {len(flagged)} data-class artifact(s) "
                    f"(route private/corporate); refusing to push: {preview}"
                ),
                "elapsed_s": 0.0,
                "exit_code": None,
                "tail": "\n".join(flagged),
                "flagged": flagged,
            }

    # Remote-identity wall (the same chokepoint, the other end of the push).
    # The block above asks whether this TREE carries the wrong content. This
    # asks whether this REMOTE accepts it. The token is resolved from whatever
    # the caller already had: push-all passes GH_TOKEN inside env rather than
    # as the token argument.
    objection = remote_objection(
        repo, remote=remote, env=run_env,
        token=token or (env or {}).get("GH_TOKEN") or load_gh_token(),
    )
    if objection:
        return {
            "state": "failed",
            "reason": objection,
            "elapsed_s": 0.0,
            "exit_code": None,
            "tail": "",
            # The three sibling refusals above all carry this key and this one
            # did not, so a caller reading `verdict["flagged"]` uniformly got a
            # KeyError on the highest-stakes refusal in the function: the one
            # that fires when a private repository is about to be pushed to the
            # public engine remote. Nothing was flagged in the TREE here - the
            # objection is about the remote - so the empty list is the honest
            # value, not a placeholder.
            "flagged": [],
        }

    def postcondition() -> bool:
        return ahead_behind(repo, remote, branch, env=run_env) == (0, 0)

    return run_supervised(
        cmd, env=run_env, stall_window=stall_window, poll=3,
        postcondition=postcondition, status_path=status_path,
        label=label or f"push:{repo.name}", log_dir=log_dir,
    )

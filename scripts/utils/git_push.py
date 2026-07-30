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
  * ``token=`` injects the GH_TOKEN credential helper through the child env
    (the token never touches argv);
  * ``env=`` uses a caller-built auth env as-is;
  * neither inherits the ambient environment (preserves a caller's own setup).
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
from scripts.utils.workspace import get_data_root, get_workspace_root

# Echoes the token from the child env (NOT argv) into git's credential protocol.
_CRED_HELPER = '!f(){ echo username=x-access-token; echo "password=$GH_PUSH_TOKEN"; }; f'

logger = logging.getLogger(__name__)

_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")

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


def _is_split_engine(repo: Path) -> bool:
    """True iff ``repo`` is the split-topology ENGINE clone (data lives in a sibling).

    Only the engine must stay code-only. The DATA overlay and the corporate/CRM repos
    legitimately carry private/corporate content, so they are exempt. Detected from the
    data-root seam: engine == workspace root AND data root resolves elsewhere. On a
    pre-cutover single repo (data root == workspace root) nothing is walled here.
    """
    try:
        engine = get_workspace_root().resolve()
        data = get_data_root().resolve()
    except Exception:
        return False
    return data != engine and repo.resolve() == engine


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
    path = tail.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return f"{head}/{path}".rstrip("/").lower()


def _push_url(repo, remote: str = "origin") -> Optional[str]:
    """The URL git would actually PUSH ``repo`` to, or None.

    ``--push`` is load-bearing: a remote may carry a ``pushurl`` that differs
    from its fetch URL, and the question this wall asks is where a push lands,
    not where a fetch came from.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "remote", "get-url", "--push", remote],
            capture_output=True, text=True, timeout=15,
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
    except Exception as exc:  # noqa: BLE001 - the family, closed by shape
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


def _engine_push_urls(engine) -> set:
    """Every normalized push URL the ENGINE clone has, under any remote name.

    Deliberately not `_push_url(engine, remote)`. The caller's remote NAME says
    nothing about what the engine calls its own remotes, and safe-push takes that
    name from the command line. Reading all of them means Check A compares
    identities rather than labels.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(engine), "remote"],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("engine remotes unreadable: %s", exc)
        return set()
    if out.returncode != 0:
        return set()
    urls = set()
    for name in out.stdout.split():
        url = _push_url(engine, name)
        if url:
            urls.add(_normalize_remote_url(url))
    return urls


def remote_objection(repo, *, token: Optional[str] = None,
                     remote: str = "origin") -> Optional[str]:
    """Why *repo* must not be pushed to its current remote, or None.

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

    url = _push_url(repo, remote)
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

    if here in _engine_push_urls(engine):
        return (f"{repo.name} pushes to the ENGINE remote ({here}), which is the "
                f"public code repository. Refusing: this would publish private "
                f"content.")

    visibility, fresh = _visibility_cached(here, token)
    if visibility == "public":
        return (f"{repo.name} pushes to {here}, which GitHub reports as PUBLIC. "
                f"Refusing: only the engine may push to a public repository.")
    if visibility is None and fresh and here.partition("/")[0] == "github.com":
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
        print(f"WARNING: could not verify the visibility of {here}. "
              f"Pushing on the offline check alone.")
    return None


def load_gh_token() -> Optional[str]:
    """Return GH_TOKEN from the engine ``.env`` (the git pushgh source of truth)."""
    env_path = get_workspace_root() / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("GH_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return None
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


def ahead_behind(repo, remote: str = "origin", branch: str = "main") -> Optional[tuple[int, int]]:
    """Return (behind, ahead) of HEAD vs ``remote/branch``, or None on error."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-list", "--left-right", "--count",
             f"{remote}/{branch}...HEAD"],
            capture_output=True, text=True, timeout=30,
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
) -> dict:
    """Push ``repo`` to ``remote/branch`` under the progress watchdog and verify
    the ref actually advanced (``ahead/behind == 0 0``) before reporting success.

    Returns the ``run_supervised`` verdict dict (state ∈ ok/failed/hung/
    postcondition_failed). The caller decides what a non-"ok" state means.
    """
    repo = Path(repo)

    # Engine/data leak wall (universal chokepoint). EVERY engine push -- push-all,
    # safe-push, or any future caller -- routes through here, so a private/corporate-
    # routed file in the engine clone can never leave the machine, on any path, with no
    # skip flag. Runs BEFORE the push subprocess (refuse, do not push-then-detect).
    # The DATA/corporate/CRM repos are exempt (they legitimately carry such files).
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
        repo, remote=remote,
        token=token or (env or {}).get("GH_TOKEN") or load_gh_token(),
    )
    if objection:
        return {
            "state": "failed",
            "reason": objection,
            "elapsed_s": 0.0,
            "exit_code": None,
            "tail": "",
        }

    run_env = dict(env) if env is not None else None
    cmd = ["git", "-C", str(repo)]
    if token:
        run_env = dict(run_env if run_env is not None else os.environ)
        run_env["GH_PUSH_TOKEN"] = token
        run_env["GIT_TERMINAL_PROMPT"] = "0"
        cmd += ["-c", f"credential.helper={_CRED_HELPER}"]
    cmd += ["push", remote, branch]

    def postcondition() -> bool:
        return ahead_behind(repo, remote, branch) == (0, 0)

    return run_supervised(
        cmd, env=run_env, stall_window=stall_window, poll=3,
        postcondition=postcondition, status_path=status_path,
        label=label or f"push:{repo.name}",
    )

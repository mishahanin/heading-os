#!/usr/bin/env python3
"""
install-hooks.py - SUPERSEDED legacy git-hook installer.

As of 2026-05-31 the workspace commit gate is the pre-commit framework
(`.pre-commit-config.yaml`, installed via `pre-commit install`). The standalone
`# 31C-SECRET-SCANNER` hook this script used to write is now folded into that
config as the `secret-scanner-31c` local hook. Running the install path here
would overwrite the framework's `.git/hooks/pre-commit` and re-introduce the
dual-mechanism conflict that silently bypassed all hooks in May 2026.

This script therefore refuses to install whenever `.pre-commit-config.yaml`
exists. Use `pre-commit install` instead.

`--check` answers one question: is a commit gate ARMED in this clone? Until
2026-09-02 it did not. The framework branch printed a green "managed by the
pre-commit framework" and exited 0 on the sole evidence that
`.pre-commit-config.yaml` was on disk, and that file is committed, so it is
present in every clone including one where `pre-commit install` was never run.
MEASURED 2026-09-02 in a scratch repository holding the config and no
`.git/hooks/pre-commit` at all: exit 0, green line, no warning. Two documents
named this command as the way to detect exactly that state
(`.claude/rules/security.md`, `docs/DEPLOYMENT.md` § troubleshooting), so the
diagnostic for an unarmed clone certified an unarmed clone.

It now asks the hook file itself, through the verifier that already existed in
`scripts/install-git-hooks.py`, rather than a second copy of it.

Usage:
  python3 scripts/install-hooks.py          # refuses if framework config present
  python3 scripts/install-hooks.py --check  # is a commit gate armed here?

Tests: tests/test_a_scanner_that_sat_below_an_exit.py,
       tests/test_a_hook_check_that_passed_on_an_unarmed_clone.py
"""

import importlib.util
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_workspace_root
from scripts.utils.colors import GREEN, YELLOW, RED, BOLD, RESET

HOOK_MARKER = "# 31C-SECRET-SCANNER"

# The scanner, with NO unconditional `exit` on the clean path. That matters
# only when it is merged into somebody else's hook: an early `exit 0` there
# would skip whatever the original hook does (git-lfs, most often). The
# standalone hook below adds its own terminating exit.
SCANNER_BLOCK = f"""{HOOK_MARKER}
# Pre-commit hook: scan staged files for secrets
# Coexists with existing Git LFS hooks

STAGED=$(git diff --cached --name-only --diff-filter=ACMR)
if [ -n "$STAGED" ]; then
    # The scanner takes PATHS and reads them from the WORKING TREE, while the
    # list above comes from the INDEX. When the two differ it scans bytes that
    # will not be committed and never sees the bytes that will. MEASURED
    # 2026-08-29 by installing this hook in a scratch repository: an AWS secret
    # was staged, the worktree copy was then cleaned without re-staging, the
    # hook printed "No secrets detected", and the secret landed in the commit.
    #
    # The pre-commit FRAMEWORK, which superseded this installer, stashes
    # unstaged changes before running its hooks, so its pass_filenames gates do
    # not have this hole (measured the same day). This standalone hook has no
    # stash, so it refuses instead: a partially-staged file is re-staged by the
    # author, not silently scanned in the wrong version.
    # The pathspec list is built NUL-delimited and handed over by `xargs -0`,
    # never by letting the shell split `$STAGED`.
    #
    # Unquoted, the guard failed OPEN. MEASURED 2026-08-30 in a scratch
    # repository: with `my secret.env` staged and its worktree copy then changed
    # without re-staging, `git diff --name-only -- $STAGED` split the name into
    # `my` and `secret.env`, matched neither, reported NO dirty file, and the
    # hook went on to scan the harmless worktree bytes while the staged bytes
    # went into the commit. That is precisely the hole the refusal below exists
    # to close, reopened by the quoting.
    #
    # WHITESPACE is the whole of it, and glob characters are NOT a second case:
    # measured the same day, a staged `a*.env` beside `ab.env` and `ac.env` was
    # still reported dirty, because git's own pathspec matching is glob-aware
    # and the expansion only WIDENS what matches. A wider pathspec can block a
    # commit it need not have blocked; it cannot let one through.
    #
    # Scope, stated rather than implied: on a clone using the pre-commit
    # FRAMEWORK this block is not what runs, and the framework stashes unstaged
    # changes so it never had the hole. This template is what `install-hooks.py`
    # writes for a clone without it.
    DIRTY=$(git diff --cached --name-only --diff-filter=ACMR -z |
            xargs -0 git diff --name-only --)
    if [ -n "$DIRTY" ]; then
        echo ""
        echo "COMMIT BLOCKED: these files have unstaged edits, so the secret"
        echo "scanner cannot see the bytes that would be committed:"
        echo "$DIRTY"
        echo "Re-stage them (git add), or commit them separately."
        exit 1
    fi
    # An ESCAPED path is refused rather than committed.
    #
    # `$STAGED` above is the un-`-z` listing, in which git C-quotes any path
    # holding a newline, a quote, a backslash or a non-ASCII byte, wrapping the
    # whole thing in double quotes. MEASURED 2026-08-30 with a staged file
    # called `two\\nlines.env`, back when that listing was also what the scanner
    # was handed: the scanner printed "No secrets detected." and exited 0 over a
    # file it never opened. A clean verdict for an unread file is the failure
    # this whole hook exists to prevent.
    #
    # Since 2026-09-01 the scanner is fed NUL-delimited (below), so an escaped
    # path can no longer reach it as a literal and this refusal is the SECOND
    # line, not the only one. It is kept deliberately: it is the operator's
    # standing posture that a name git has to escape gets renamed rather than
    # committed, and a refusal can only over-block, never let a secret through.
    #
    # A line beginning with a double quote is git's own signal that it escaped
    # the path, and a filename that genuinely starts with one is escaped too, so
    # the test is right in both directions.
    if printf '%s\\n' "$STAGED" | grep -q '^"'; then
        echo ""
        echo "COMMIT BLOCKED: a staged path has characters git must escape (a"
        echo "newline, a quote, a backslash or a non-ASCII byte), so the secret"
        echo "scanner cannot be handed the real path. Rename the file, then commit:"
        printf '%s\\n' "$STAGED" | grep '^"'
        exit 1
    fi
    # NUL-delimited handoff, NOT `echo "$STAGED" | ... --stdin`.
    #
    # `--stdin` reads one path per LINE and strips each line. A leading or
    # trailing space is legal in a POSIX filename and git prints it verbatim
    # WITHOUT C-quoting it, so the escape guard above does not see it either.
    # Stripped, the name opens nothing, and `scan_files` skips a path that is
    # not a file in silence. MEASURED 2026-09-01 in a scratch repository running
    # this generated hook: `harmless.txt`, `" leading-space.env"` and
    # `"trailing-space.env "` staged together, the two padded files each holding
    # a `ghp_`-shaped token, produced "No secrets detected." and exit 0. The
    # identical token in `control.env` was refused. A file whose name is padded
    # with a space carried a secret straight past the commit gate.
    #
    # The fix is to stop the shell being the transport for a filename, not to
    # quote harder: `-z` emits raw NUL-separated bytes and `--stdin0` splits on
    # NUL, so nothing is stripped and nothing is split on a newline. This is the
    # same handoff `scripts/push-all.py` and `scripts/publish-service.py`
    # already use; the commit-time gate, which is the bypassable layer, had
    # fallen behind the unbypassable push wall.
    #
    # `EXIT_CODE=$?` after a pipeline is the last command's status, i.e.
    # python3's, exactly as it was before.
    git diff --cached --name-only --diff-filter=ACMR -z |
        python3 scripts/secret-scanner.py --stdin0
    EXIT_CODE=$?

    if [ $EXIT_CODE -eq 1 ]; then
        echo ""
        echo "COMMIT BLOCKED: Secrets detected in staged files."
        echo "Remove the secrets, then try again."
        echo "To bypass (DANGEROUS): git commit --no-verify"
        exit 1
    fi

    if [ $EXIT_CODE -gt 1 ]; then
        echo "WARNING: Secret scanner encountered an error. Commit proceeding."
    fi
fi
"""

PRE_COMMIT_HOOK = f"""#!/bin/sh
{SCANNER_BLOCK}
exit 0
"""

# An `exit` at column 0 is unconditional: nothing after it in the file runs.
_TOP_LEVEL_EXIT = re.compile(r"^exit\b")


def scanner_reachability(content: str) -> "tuple[bool, str]":
    """Can the scanner block in `content` actually run? Plus what was checked.

    Until 2026-08-25 the merge path APPENDED the scanner to the end of an
    existing hook, and the check path then looked only for the marker. A hook
    ending in `exit 0` -- the ordinary shape, and what git-lfs writes -- made
    every appended line dead, while `--check` printed "pre-commit: installed".
    A security control switched off, reporting healthy.

    This is not a shell parser and does not pretend to be one. It answers one
    question: does an UNINDENTED `exit` sit above the marker? An exit nested in
    an `if`, a function, or a `case` arm is not counted and not detected, so a
    False from here is conclusive and a True means "nothing of that shape was
    found", never "proved reachable". The returned sentence says which.
    """
    lines = content.splitlines()
    marker_at = next((i for i, ln in enumerate(lines) if HOOK_MARKER in ln), None)
    if marker_at is None:
        return False, "the scanner marker is not in this hook"
    for i, line in enumerate(lines[:marker_at]):
        if _TOP_LEVEL_EXIT.match(line):
            return False, (f"an unconditional `exit` at line {i + 1} runs before the "
                           f"scanner at line {marker_at + 1}, so the scanner never does")
    return True, "no unconditional exit above the scanner block; nested exits not checked"


def install_pre_commit(hooks_dir: Path, check_only: bool = False) -> bool:
    """Install or check the pre-commit hook."""
    hook_path = hooks_dir / "pre-commit"

    if hook_path.exists():
        content = hook_path.read_text(encoding="utf-8", errors="replace")
        if HOOK_MARKER in content:
            reachable, why = scanner_reachability(content)
            if not reachable:
                print(f"  {RED}pre-commit: scanner present but DEAD -- {why}{RESET}")
                return False
            if check_only:
                print(f"  {GREEN}pre-commit: installed{RESET} ({why})")
            else:
                print(f"  {GREEN}pre-commit: already installed (skipping){RESET}")
            return True

        if check_only:
            print(f"  {YELLOW}pre-commit: exists but missing secret scanner{RESET}")
            return False

        # Existing hook without our marker - merge the scanner in FIRST.
        #
        # This used to append. An existing hook that ends in `exit 0` -- the
        # ordinary shape -- left every appended line unreachable, and the marker
        # was in the file all the same, so `--check` certified a scanner that
        # could not run. Going first is also why SCANNER_BLOCK carries no `exit`
        # on the clean path: it must fall through into whatever was already here.
        print(f"  {YELLOW}pre-commit: merging secret scanner into existing hook{RESET}")
        if content.startswith("#!"):
            shebang, _, rest = content.partition("\n")
        else:
            shebang, rest = "#!/bin/sh", content
        hook_path.write_text(f"{shebang}\n\n{SCANNER_BLOCK}\n{rest}", encoding="utf-8")
    else:
        if check_only:
            print(f"  {RED}pre-commit: not installed{RESET}")
            return False

        print(f"  {GREEN}pre-commit: installing{RESET}")
        hook_path.write_text(PRE_COMMIT_HOOK, encoding="utf-8")

    # Make executable
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return True


GIT_HOOKS_INSTALLER = Path(__file__).resolve().parent / "install-git-hooks.py"


def git_hooks_module():
    """The sibling installer, loaded by path because its filename has hyphens.

    `scripts/install-git-hooks.py` already owns the two answers this file needs:
    `_hooks_dir`, which asks git where hooks really live rather than spelling
    `<repo>/.git/hooks` by hand, and `check_pre_commit`, which reads the hook
    file and looks for the marker the framework stamps into what it generates.
    Copying either one here is how the copy that stops being fixed gets made;
    `_hooks_dir` is itself a fix for a wrong hand-spelled path, and a second
    hand-spelled path in this file would have missed it.

    Loaded under a private module name, never as `scripts.install_git_hooks`.
    A loader that binds a package name during someone else's import has taken a
    process-wide decision it was not asked to take.
    """
    spec = importlib.util.spec_from_file_location(
        "_heading_os_install_git_hooks", GIT_HOOKS_INSTALLER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def hooks_path_override(repo: Path) -> "str | None":
    """The repo's `core.hooksPath` value, or None when it is unset.

    `.claude/rules/security.md` names setting this as one of the two gates an
    operator can disarm by hand, because a literal path here once bypassed every
    hook in this workspace. It is not itself the verdict: `_hooks_dir` follows
    the redirect (MEASURED 2026-09-02 on git 2.43.0, `git rev-parse --git-path
    hooks` returns the configured directory), so a redirect that still holds an
    armed hook is armed. It is reported because it changes the REMEDY. MEASURED
    2026-09-02: `pre-commit install` exits 1 with "Cowardly refusing to install
    hooks with `core.hooksPath` set", so an operator told only to run that
    command would be sent in a circle.
    """
    proc = subprocess.run(
        ["git", "-C", str(repo), "config", "--get", "core.hooksPath"],
        capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def framework_gate_state(repo: Path) -> "tuple[bool, list[str]]":
    """Is the pre-commit framework's commit gate armed in `repo`? Plus the evidence.

    Three things have to hold, and the old check tested none of them: a hook
    file exists at the path git would actually run, that file was generated by
    the framework rather than left over from something else, and it is
    executable. The third is not decoration. MEASURED 2026-09-02, a `chmod -x`
    hook made git print "the '.git/hooks/pre-commit' hook was ignored because
    it's not set as executable" and run nothing, so a present-but-unexecutable
    hook is an unarmed clone that looks armed in a directory listing.

    What this does NOT establish, said here so the printed line does not imply
    it: nothing runs the hook. A framework hook whose `.pre-commit-config.yaml`
    has since grown a stage this clone never installed, or whose hook script is
    older than the config, passes here. This answers "would git execute a
    framework-generated commit hook", not "would that hook catch a secret".
    """
    igh = git_hooks_module()
    hook = igh._hooks_dir(repo) / "pre-commit"

    notes = []
    redirect = hooks_path_override(repo)
    if redirect:
        notes.append(
            f"core.hooksPath redirects hooks to {redirect}; unset it first "
            f"(git config --unset-all core.hooksPath), because `pre-commit "
            f"install` refuses to run while it is set")

    marker_ok = igh.check_pre_commit(repo)
    if marker_ok is None:
        # `main` reaches this branch on `.exists()`, the verifier asks
        # `.is_file()`. A directory of that name lands here, and a config that
        # is not a readable file is not a config.
        return False, [f"no readable .pre-commit-config.yaml in {repo}"] + notes
    if not marker_ok:
        if not hook.is_file():
            return False, [f"no hook file at {hook}"] + notes
        return False, [f"{hook} exists but carries no "
                       f"'{igh.PRE_COMMIT_FRAMEWORK_MARKER}' marker, so the "
                       f"framework did not generate it"] + notes
    if not os.access(hook, os.X_OK):
        return False, [f"{hook} is not executable, and git skips a hook it "
                       f"cannot execute"] + notes
    return True, [f"{hook} exists, carries the framework's marker, and is "
                  f"executable"] + notes


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Install git hooks for the workspace.")
    parser.add_argument("--check", action="store_true", help="Check if hooks are installed")
    args = parser.parse_args()

    root = get_workspace_root()
    # Asked of git, not spelled by hand: in a linked worktree `.git` is a FILE
    # and `<repo>/.git/hooks` names a path under it, so the guard below used to
    # exit 1 with "Is this a git repository?" in a perfectly good worktree.
    hooks_dir = git_hooks_module()._hooks_dir(root)

    if not hooks_dir.exists():
        print(f"{RED}Error: {hooks_dir} not found. Is this a git repository?{RESET}")
        sys.exit(1)

    # Superseded guard: the pre-commit framework is the canonical commit gate.
    # Refuse to clobber its generated hook with the legacy standalone scanner.
    framework_config = root / ".pre-commit-config.yaml"
    if framework_config.exists():
        if args.check:
            print(f"{BOLD}Git hooks status:{RESET}")
            armed, evidence = framework_gate_state(root)
            for line in evidence:
                print(f"  {line}")
            if armed:
                print(f"  {GREEN}pre-commit framework gate ARMED{RESET}")
                print(f"  Checked: the hook file git would run exists, names the "
                      f"framework as its generator, and is executable. NOT checked: "
                      f"whether that hook still matches .pre-commit-config.yaml, or "
                      f"whether its hooks pass.")
                sys.exit(0)
            print(f"  {RED}pre-commit framework gate NOT ARMED{RESET} "
                  f"(.pre-commit-config.yaml is present, so this clone expects one)")
            print(f"  Arm it: run {BOLD}pre-commit install{RESET} in {root}")
            sys.exit(1)
        print(f"{RED}Refusing to install:{RESET} this workspace's commit gate is the "
              f"pre-commit framework (.pre-commit-config.yaml).")
        print(f"Installing the legacy standalone hook would overwrite the "
              f"framework's .git/hooks/pre-commit and bypass every other check.")
        print(f"Run {BOLD}pre-commit install{RESET} instead.")
        sys.exit(1)

    print(f"{BOLD}Git hooks {'status' if args.check else 'installation'}:{RESET}")
    installed = install_pre_commit(hooks_dir, check_only=args.check)

    # The result decides the exit code in BOTH modes.
    #
    # Only `--check` used to read it. The install branch printed a green "Done."
    # and exited 0 whatever came back, and `install_pre_commit` returns False on
    # a path it reaches in install mode too: a hook that carries the marker but
    # whose scanner block is UNREACHABLE. So the script printed
    # "pre-commit: scanner present but DEAD" and then "Done." at exit 0, three
    # lines apart. An installer that reports a dead secret scanner and exits 0
    # is telling a caller the gate is armed, and a caller is usually a setup
    # script that only reads the code.
    #
    # Not repaired automatically, deliberately. The dead case means somebody's
    # own hook content sits above the scanner block, and rewriting it here would
    # destroy work this script did not author. Refusing by name is the honest
    # move; the remedy is one documented command.
    # The success path RETURNS rather than exiting, which is not a style choice:
    # `main()` is driven in-process by
    # `tests/test_a_hook_check_that_passed_on_an_unarmed_clone.py`, and a
    # `sys.exit(0)` here turns every one of those calls into a SystemExit the
    # test has to catch to assert anything about the hook that was written. The
    # process exit code is 0 either way.
    if installed:
        if not args.check:
            print(f"\n{GREEN}Done.{RESET}")
        return
    if not args.check:
        print(f"\n{RED}Not installed.{RESET} The commit gate is NOT armed, so "
              f"nothing above scanned anything.")
        print(f"Arm it with {BOLD}pre-commit install{RESET} in {root}, then "
              f"re-run {BOLD}python scripts/install-hooks.py --check{RESET}.")
    sys.exit(1)


if __name__ == "__main__":
    main()

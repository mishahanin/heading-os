from scripts.bridge_daemon.terminal import (
    build_osascript_attach_command,
    build_tmux_command,
    build_wt_command,
)

def test_build_with_session_id():
    cmd = build_wt_command(
        user_slug="misha",
        title="ODIN-5",
        cwd=r"C:\work\31c",
        action="email-respond",
        session_id="abc123",
    )
    assert cmd[0] == "wt.exe"
    assert "-w" in cmd
    assert "31c-misha" in cmd
    assert "new-tab" in cmd
    assert "--title" in cmd
    joined = " ".join(cmd)
    assert "BRIDGE_ORIGIN=browser" in joined
    assert "claude --resume abc123" in joined

def test_build_without_session_id_starts_fresh():
    cmd = build_wt_command(
        user_slug="misha", title="OSINT", cwd=r"C:\work\31c",
        action="osint", session_id=None,
    )
    joined = " ".join(cmd)
    assert "--resume" not in joined
    assert "claude" in joined


import pytest


def test_build_rejects_session_id_with_cmd_metacharacters():
    """CRITICAL: session_id flows into cmd /k <inner>. Any cmd.exe metachar
    (&, |, >, <, (, ), space, quote, newline) must be rejected to prevent
    command injection. See security regression notes in CLAUDE.md."""
    evils = [
        "abc & calc.exe", "abc && rm -rf /", "abc|cat",
        "abc>file", 'abc"|whoami', "abc;ls",
        "abc\nrun", "abc(echo)", "abc()",
    ]
    for evil in evils:
        with pytest.raises(ValueError, match="invalid session_id"):
            build_wt_command("misha", "t", r"C:\w", "osint", evil)

def test_build_accepts_legitimate_session_ids():
    """UUIDs and short alphanumeric tokens must continue to work."""
    legitimates = [
        "abc123",
        "550e8400-e29b-41d4-a716-446655440000",  # pragma: allowlist secret
        "session_42",
        "a-b-c",
        "ABC123",
    ]
    for ok in legitimates:
        # Should not raise:
        build_wt_command("misha", "t", r"C:\w", "osint", ok)

def test_build_rejects_user_slug_with_metacharacters():
    with pytest.raises(ValueError, match="invalid user_slug"):
        build_wt_command("misha & evil", "t", r"C:\w", "osint", None)
    with pytest.raises(ValueError, match="invalid user_slug"):
        build_wt_command("MISHA", "t", r"C:\w", "osint", None)  # uppercase rejected

def test_build_rejects_action_with_metacharacters():
    with pytest.raises(ValueError, match="invalid action"):
        build_wt_command("misha", "t", r"C:\w", "osint && pwn", None)

def test_build_sanitizes_title_with_quote_and_control_chars():
    cmd = build_wt_command("misha", 'evil"\ntitle', r"C:\w", "osint", None)
    # Title in cmd should have the quote and newline stripped:
    title_idx = cmd.index("--title") + 1
    assert '"' not in cmd[title_idx]
    assert "\n" not in cmd[title_idx]

def test_build_includes_bridge_action_env_var():
    """The `action` parameter is wired through as BRIDGE_ACTION env var so the
    Stop hook (Task 20) can render 'you were doing /email-respond' messages."""
    cmd = build_wt_command("misha", "t", r"C:\w", "email-respond", None)
    joined = " ".join(cmd)
    assert "BRIDGE_ACTION=email-respond" in joined  # pragma: allowlist secret


# Phase A (macOS launcher) tests. build_tmux_command + build_osascript_attach_command.
# These don't spawn a real tmux - just check argv shape and allowlist enforcement.


def test_tmux_build_with_session_id():
    cmd = build_tmux_command(
        user_slug="misha", title="ODIN-5", cwd="/Users/misha/work",
        action="email-respond", session_id="abc123",
    )
    assert cmd[0] == "tmux"
    assert "new-session" in cmd
    # No -A. It read as "idempotent attach-or-create" and is the opposite from
    # a daemon: with the session already present, -A turns new-session into
    # attach-session, which needs a terminal. Measured on tmux 3.4 with no
    # controlling tty, the repeat launch exits 1 with "open terminal failed:
    # not a terminal". _run_tmux_session owns the already-exists case now.
    assert "-A" not in cmd
    assert "-d" in cmd  # detached so daemon can spawn it
    assert "31c-misha" in cmd
    # tmux passes the shell command as the last arg; check both env + claude resume.
    inner = cmd[-1]
    assert "BRIDGE_ORIGIN=browser" in inner
    assert "BRIDGE_ACTION=email-respond" in inner  # pragma: allowlist secret
    assert "claude --resume abc123" in inner
    # cwd flows in as a -c arg
    assert "-c" in cmd
    assert "/Users/misha/work" in cmd


def test_tmux_build_without_session_id_starts_fresh():
    cmd = build_tmux_command(
        user_slug="misha", title="OSINT", cwd="/Users/misha/work",
        action="osint", session_id=None,
    )
    inner = cmd[-1]
    assert "--resume" not in inner
    assert inner.endswith("claude")


def test_tmux_build_rejects_unsafe_session_id():
    with pytest.raises(ValueError, match="invalid session_id"):
        build_tmux_command("misha", "t", "/synthetic-cwd", "osint", "evil; rm -rf /")


def test_tmux_build_rejects_unsafe_user_slug():
    with pytest.raises(ValueError, match="invalid user_slug"):
        build_tmux_command("evil$(whoami)", "t", "/synthetic-cwd", "osint", None)


def test_tmux_build_rejects_unsafe_action():
    with pytest.raises(ValueError, match="invalid action"):
        build_tmux_command("misha", "t", "/synthetic-cwd", "evil; ls", None)


def test_tmux_build_sanitizes_title():
    """Title with control chars + quotes -> tmux -n window-name must be clean."""
    cmd = build_tmux_command("misha", 'evil"\ntitle', "/synthetic-cwd", "osint", None)
    name_idx = cmd.index("-n") + 1
    assert '"' not in cmd[name_idx]
    assert "\n" not in cmd[name_idx]


def test_osascript_attach_command_shape():
    cmd = build_osascript_attach_command("misha")
    assert cmd[0] == "osascript"
    assert cmd[1] == "-e"
    script = cmd[2]
    assert 'tell application "Terminal"' in script
    assert "tmux attach -t 31c-misha" in script


def test_osascript_attach_rejects_unsafe_user_slug():
    with pytest.raises(ValueError, match="invalid user_slug"):
        build_osascript_attach_command('"; rm -rf /; "')


# Context propagation (spec section 3.3 deep-link context). The /launch
# endpoint takes an optional context dict; both Windows and macOS
# command builders serialize it to BRIDGE_CONTEXT as base64-encoded
# JSON so skills can pre-populate state without the user retyping
# (e.g. conv_id for /email-respond).


def test_wt_context_serialized_as_base64_bridge_context_env(tmp_path):
    import base64
    import json as _json
    cmd = build_wt_command(
        "misha", "email: TradeExpo 2026", "/synthetic-cwd", "email-respond", None,
        context={"conv_id": "AAQkAD123", "subject": "TradeExpo 2026"},
    )
    inner = cmd[-1]
    # BRIDGE_CONTEXT should be set BEFORE the claude launch
    assert "set BRIDGE_CONTEXT=" in inner
    # Extract the base64 value
    import re as _re
    m = _re.search(r"set BRIDGE_CONTEXT=([A-Za-z0-9+/=]+)&&", inner)
    assert m, f"BRIDGE_CONTEXT base64 not found in inner: {inner!r}"
    decoded = _json.loads(base64.b64decode(m.group(1)).decode("utf-8"))
    assert decoded == {"conv_id": "AAQkAD123", "subject": "TradeExpo 2026"}


def test_wt_context_none_omits_env_var():
    """Legacy callers without context get the same shell as before -
    no BRIDGE_CONTEXT line."""
    cmd = build_wt_command("misha", "t", "/synthetic-cwd", "osint", None, context=None)
    inner = cmd[-1]
    assert "BRIDGE_CONTEXT" not in inner


def test_wt_context_empty_dict_omits_env_var():
    """Empty {} from the browser is treated the same as None - no env var."""
    cmd = build_wt_command("misha", "t", "/synthetic-cwd", "osint", None, context={})
    inner = cmd[-1]
    assert "BRIDGE_CONTEXT" not in inner


def test_tmux_context_serialized_as_base64_bridge_context_env():
    import base64
    import json as _json
    cmd = build_tmux_command(
        "misha", "email: TradeExpo 2026", "/synthetic-cwd", "email-respond", None,
        context={"conv_id": "AAQkAD123"},
    )
    inner = cmd[-1]
    assert "BRIDGE_CONTEXT=" in inner
    import re as _re
    m = _re.search(r"BRIDGE_CONTEXT=([A-Za-z0-9+/=]+) ", inner + " ")
    assert m, f"BRIDGE_CONTEXT base64 not found: {inner!r}"
    decoded = _json.loads(base64.b64decode(m.group(1)).decode("utf-8"))
    assert decoded == {"conv_id": "AAQkAD123"}


def test_context_oversize_silently_drops():
    """Payloads > 8KB are dropped (don't blow up cmd.exe's line length)."""
    huge = {"data": "x" * 20000}
    cmd = build_wt_command("misha", "t", "/synthetic-cwd", "osint", None, context=huge)
    inner = cmd[-1]
    # Caller proceeds without context rather than failing the launch
    assert "BRIDGE_CONTEXT" not in inner


def test_a_context_under_the_blob_cap_still_overran_the_command_line():
    """The cap sized the base64 blob; cmd.exe measures the whole inner string.

    Found by the 2026-08-24 campaign (shard `scripts-02-p3`, finding 2).
    `_encode_context` refuses an encoding over 8192 bytes and its docstring
    called that "what the cmd.exe length limit actually sees". It is not.
    `build_wt_command` wraps the blob in `set BRIDGE_ORIGIN=browser&& set
    BRIDGE_ACTION=<action>&& `, `claude`, an optional `--resume <id>` and an
    optional quoted initial prompt, and cmd.exe's 8,191 character limit applies
    to that finished string. So an encoding that PASSED the cap at just under
    8,192 produced an inner string over the limit: cmd.exe answers "The input
    line is too long." and the window shows an error instead of running claude.

    The context here is sized to pass `_encode_context` deliberately, so this
    test measures the wrapper and not the blob cap. `email-respond` is the
    action whose initial prompt is the long one, which is what made the overrun
    reachable with an ordinary payload rather than a contrived one.
    """
    from scripts.bridge_daemon.terminal import CMD_INNER_MAX, _encode_context

    ctx = {"conv_id": "AAQkAD123", "pad": "x" * 6050}
    encoded = _encode_context(ctx)
    assert encoded is not None and len(encoded) <= 8192, (
        f"the fixture must PASS the blob cap or it measures the wrong guard: "
        f"{None if encoded is None else len(encoded)}")

    cmd = build_wt_command("misha", "t", r"C:\work", "email-respond",
                           "abc123", context=ctx)
    inner = cmd[-1]
    assert len(inner) <= CMD_INNER_MAX, (
        f"the cmd.exe inner string is {len(inner)} characters, over the "
        f"{CMD_INNER_MAX} limit; cmd.exe refuses the line and the launch shows "
        f"an error instead of a session")
    assert "BRIDGE_CONTEXT" not in inner, (
        "the context is what gets dropped to fit; the launch itself must "
        "survive")
    assert "claude --resume abc123" in inner, (
        "dropping the context must not cost the session it was resuming")


def test_a_context_that_fits_the_whole_line_is_still_carried():
    """The anchor. Without it the fix above passes by dropping every context.

    `test_context_oversize_silently_drops` proves an oversized blob is dropped
    and `..._overran_the_command_line` proves an oversized LINE is dropped;
    neither would fail if the wrapper check started refusing everything.
    """
    cmd = build_wt_command("misha", "t", r"C:\work", "osint", None,
                           context={"conv_id": "AAQkAD123"})
    inner = cmd[-1]
    assert "set BRIDGE_CONTEXT=" in inner, inner


def test_find_linux_terminal_says_what_it_measures():
    """A docstring that named a condition the function never reads.

    Found by the 2026-08-24 campaign (shard `scripts-02-p3`, finding 3). The
    line read "Returns path or None on headless", and the function consults
    only `shutil.which`: on a headless host with xterm installed it returns a
    path, and on a full desktop with no candidate installed it returns None.
    Headless is `_is_linux_gui_session()`'s question, asked by the caller. A
    maintainer trusting the old line could delete that caller-side gate as
    redundant and reintroduce GUI-attach attempts on headless hosts, which is
    why this is pinned rather than left as prose.
    """
    from scripts.bridge_daemon.terminal import (
        _is_linux_gui_session,
        find_linux_terminal,
    )

    doc = find_linux_terminal.__doc__ or ""
    # The SUMMARY paragraph only, up to the first blank line. That is the
    # sentence a reader acts on, and it is the part that was wrong. The prose
    # below it has to be free to quote the retired wording while explaining it,
    # which a whole-docstring match would forbid.
    summary = doc.strip().split("\n\n", 1)[0]
    import ast
    import inspect
    body = ast.parse(inspect.getsource(find_linux_terminal).lstrip())
    reads_display = "DISPLAY" in ast.dump(body)
    assert not reads_display, (
        "find_linux_terminal now reads DISPLAY, so this test is the stale one; "
        "rewrite it against the new behaviour rather than deleting it")
    assert "headless" not in summary.lower(), (
        "the summary line promises a headless verdict this function cannot "
        f"reach: it calls shutil.which and nothing else. Got: {summary!r}")
    assert "installed" in summary.lower(), (
        f"the summary must say what None actually means: {summary!r}")
    assert "_is_linux_gui_session" in doc, (
        "the docstring must name where the headless question IS answered, or "
        "the next reader deletes the caller's gate as redundant")
    # The gate the docstring points at is real and reads what the docstring
    # says it reads, so the pointer cannot rot into a name that does nothing.
    assert "DISPLAY" in ast.dump(ast.parse(
        inspect.getsource(_is_linux_gui_session).lstrip()))


def test_an_object_json_cannot_encode_is_stringified_not_dropped():
    """Named for what happens, and asserting it.

    This was `test_context_unserializable_silently_drops`, and its only
    assertion was `isinstance(inner, str)` on a value that is a string by
    construction. Worse, the name said the opposite of the behaviour: its own
    comment admitted "default=str in json.dumps actually handles arbitrary
    objects, so this WILL serialize", and then asserted neither outcome. A
    reader took the name for a contract that does not exist, and a change from
    `default=str` to a real TypeError would not have failed anything here.

    `_encode_context` calls `json.dumps(context, default=str)`, so an arbitrary
    object becomes its `repr`. The env var IS set. The launch does not fail.
    That is the contract.
    """
    import base64
    import json as _json
    import re as _re

    class Weird:
        pass
    cmd = build_wt_command("misha", "t", "/synthetic-cwd", "osint", None,
                           context={"obj": Weird()})
    inner = cmd[-1]
    m = _re.search(r"set BRIDGE_CONTEXT=([A-Za-z0-9+/=]+)&&", inner)
    assert m, f"the object was dropped instead of stringified: {inner!r}"
    decoded = _json.loads(base64.b64decode(m.group(1)).decode("utf-8"))
    assert set(decoded) == {"obj"}, decoded
    assert isinstance(decoded["obj"], str), decoded
    assert "Weird" in decoded["obj"], decoded


# Initial-prompt builder: action+context -> ASCII-safe `claude "prompt"`
# suffix so the terminal opens with context already loaded.


def test_wt_email_respond_passes_initial_prompt():
    """email-respond + conv_id -> 'claude "<prompt>"' instead of bare claude.
    The prompt names the conversation id explicitly so the session can
    look it up without the user retyping."""
    cmd = build_wt_command(
        "misha", "email: PandaDoc", "/synthetic-cwd", "email-respond", None,
        context={"conv_id": "AAQkAD12345", "subject": "PandaDoc reminder"},
    )
    inner = cmd[-1]
    assert "claude \"" in inner
    assert "AAQkAD12345" in inner
    assert "PandaDoc reminder" in inner
    # Must read _latest-fetch.json so the session has the rich payload
    assert "_latest-fetch.json" in inner


def test_wt_email_respond_with_unsafe_conv_id_drops_prompt():
    """An unsafe conv_id (control chars or quotes) makes _build_initial_prompt
    return empty -> session opens bare instead of injecting unsafe text."""
    unsafe = "abc\"; rm -rf /;\""
    cmd = build_wt_command(
        "misha", "t", "/synthetic-cwd", "email-respond", None,
        context={"conv_id": unsafe},
    )
    inner = cmd[-1]
    # STRENGTHENED 2026-08-30. This was
    #     assert inner.rstrip().endswith("&& claude") or "claude \"\"" not in inner
    # whose right-hand disjunct is true under BOTH correct and injected
    # behaviour: an injected `claude "abc"; rm -rf /;"..."` contains no literal
    # `claude ""`, so the `or` short-circuits to True and the test passes over
    # the exact injection this file's own comments call CRITICAL. It was the
    # only guard on that variant and it could not fail. The assertion is now the
    # same one the sibling `..._without_conv_id_drops_prompt` uses -- the
    # command ENDS at a bare `claude`, so there is no suffix at all -- plus an
    # explicit absence of the unsafe payload anywhere in the command.
    assert inner.rstrip().endswith("claude"), (
        f"an unsafe conv_id was not dropped; the session opens with: {inner!r}")
    assert unsafe not in inner
    assert "rm -rf" not in inner


def test_wt_email_respond_without_conv_id_drops_prompt():
    """email-respond action but no conv_id in context -> no initial prompt."""
    cmd = build_wt_command(
        "misha", "t", "/synthetic-cwd", "email-respond", None,
        context={"other": "field"},
    )
    inner = cmd[-1]
    assert inner.rstrip().endswith("claude")


def test_wt_non_email_action_has_no_initial_prompt():
    """Other actions (osint, deal-strategy) currently have no template ->
    session opens bare. Adding more templates is a future commit."""
    cmd = build_wt_command(
        "misha", "t", "/synthetic-cwd", "osint", None,
        context={"target": "ExampleTelco"},
    )
    inner = cmd[-1]
    # 'claude' at the tail, no quote-wrapped prompt suffix
    assert inner.endswith("claude")


def test_tmux_email_respond_uses_single_quotes():
    """macOS tmux path uses single-quoted prompt arg (no shell expansion)."""
    cmd = build_tmux_command(
        "misha", "t", "/synthetic-cwd", "email-respond", None,
        context={"conv_id": "ABC123"},
    )
    inner = cmd[-1]
    assert "claude '" in inner
    assert "ABC123" in inner

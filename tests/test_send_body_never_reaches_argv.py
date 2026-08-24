"""The email body goes to the child on stdin, never on its command line.

Two findings of the 2026-08-23 engine audit, one cause.

**The body was an argv element.** `send_card` built
``[python, send-email.py, --to, X, --subject, Y, --body, <the whole body>]``.
For the up-to-120-second life of the send, the full draft sat in the process
table, readable by any local account with `ps aux` and by any process-audit
tool that records command lines. Outbound CRM content is the most commercially
sensitive text this engine handles.

This engine has already fixed this exact shape once, for credentials:
`tests/test_osint_advanced_api_helper.py` carries
`test_the_key_is_never_passed_on_a_command_line`, whose docstring reads "The
old pattern put a live credential in the process table." The body is the same
mistake with different content.

**And an argv element has a hard size limit.** Linux `MAX_ARG_STRLEN` is
131072 bytes. Measured on this machine 2026-08-23: a 100,000-byte `--body`
spawns; 131,072 raises `OSError: [Errno 7] Argument list too long`. `send_card`
caught only `subprocess.TimeoutExpired`, so that `OSError` propagated to the
caller as a raw traceback -- through `action-queue.py approve`, the CEO's send
path -- instead of a `send_failed` result the queue could record.

The batch caller `main()` compounded it. Its results are printed once, after
the loop, so one card raising discarded the results of every card already
SENT in that run. Those cards stay `approved` and a later run sends them again:
duplicate mail to an external counterparty, the one failure a send-gated queue
exists to prevent. That path is not reachable today -- the daemon stopped
spawning the executor on 2026-06-27 and `action-queue.py` is the sole send path
-- but the file is retained, and a per-card guard costs four lines.

Stdin fixes all three at once: nothing in argv, no size ceiling, and one
try/except that cannot be reached by a body of any length.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _executor():
    path = ROOT / "scripts" / "action-queue-execute.py"
    spec = importlib.util.spec_from_file_location("aq_execute_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


AQ = _executor()

BIG_BODY = "x" * 200_000


def _card(**over) -> dict:
    card = {"id": "card-1", "action_type": "email_send", "to": "a@b.test",
            "subject": "s", "draft_body": "hello", "draft_status": "ready_for_review"}
    card.update(over)
    return card


# --- the measurement the fix rests on ----------------------------------------

def test_a_large_argv_element_really_does_raise():
    """Anchor the premise. If the kernel limit ever goes away, the guard below
    still holds, but the reason recorded here would be wrong."""
    with pytest.raises(OSError):
        subprocess.run([sys.executable, "-c", "pass", "--body", BIG_BODY],
                       capture_output=True, timeout=30)


# --- 1. the body is not in argv ----------------------------------------------

def test_the_body_is_passed_on_stdin_not_argv(monkeypatch, tmp_path):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        seen["input"] = kw.get("input")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(AQ.subprocess, "run", fake_run)
    body = "the whole confidential draft"
    res = AQ.send_card(tmp_path, _card(draft_body=body))

    assert res["result"] == "sent"
    assert body not in seen["cmd"], (
        f"the draft body is still an argv element, readable via `ps`: {seen['cmd']!r}"
    )
    assert seen["input"] == body, "the body did not reach the child on stdin"
    assert "--body-stdin" in seen["cmd"], (
        "send-email.py is not being told to read the body from stdin"
    )


def test_the_recipient_still_travels_on_argv():
    """Narrow the claim. Only the body moved; --to and --subject are short and
    are not what this change is about."""
    src = (ROOT / "scripts" / "action-queue-execute.py").read_text(encoding="utf-8")
    assert '"--to", to' in src


# --- 2. a body of any size cannot crash the send -----------------------------

def test_a_two_hundred_kilobyte_body_returns_a_result_not_a_traceback(tmp_path):
    """End to end through a real spawn, against a stub that echoes nothing.

    No monkeypatch: the point is that the real `subprocess.run` no longer sees
    an oversized argv element.
    """
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "send-email.py").write_text(
        "import sys; sys.stdin.read(); sys.exit(0)\n", encoding="utf-8")
    res = AQ.send_card(tmp_path, _card(draft_body=BIG_BODY))
    assert res["result"] == "sent", res


def test_an_oserror_from_the_spawn_becomes_a_result(monkeypatch, tmp_path):
    """Belt and braces: even with the argv route gone, a spawn can fail for
    reasons this code does not control (ENOMEM, EMFILE, a missing script)."""
    def boom(*a, **kw):
        raise OSError(7, "Argument list too long")

    monkeypatch.setattr(AQ.subprocess, "run", boom)
    res = AQ.send_card(tmp_path, _card())
    assert res["result"] == "send_failed"
    assert "Argument list too long" in res["error"]
    assert res["classification"] in ("transient", "permanent")


def test_a_missing_send_script_is_a_result_not_a_traceback(tmp_path):
    res = AQ.send_card(tmp_path, _card())     # tmp_path has no scripts/ at all
    assert res["result"] == "send_failed", res


# --- 3. one bad card must not discard the batch ------------------------------

def test_one_raising_card_does_not_lose_the_cards_already_sent(monkeypatch, tmp_path, capsys):
    """The results of a card already SENT must reach stdout, or the daemon
    records nothing and the next run sends it again."""
    import json

    queue = tmp_path / "operations" / "action-queue"
    queue.mkdir(parents=True)
    (queue / "queue.json").write_text(json.dumps({"actions": [
        _card(id="good", status="approved"),
        _card(id="bad", status="approved"),
    ]}), encoding="utf-8")

    monkeypatch.setattr(AQ, "get_outputs_dir", lambda: tmp_path)
    monkeypatch.setattr(AQ, "get_workspace_root", lambda: tmp_path)

    real_send = AQ.send_card

    def flaky(engine_root, card, now=None):
        if card["id"] == "bad":
            raise RuntimeError("anything at all")
        return {"action_id": card["id"], "result": "sent",
                "classification": "sent", "attempt": 0}

    monkeypatch.setattr(AQ, "send_card", flaky)
    rc = AQ.main()
    out = capsys.readouterr().out
    assert rc == 0
    results = json.loads(out)
    ids = {r["action_id"]: r for r in results}
    assert ids["good"]["result"] == "sent", (
        "the card that WAS sent is missing from the output; the daemon would "
        "leave it `approved` and the next run would send it a second time"
    )
    assert ids["bad"]["result"] == "send_failed"
    assert real_send is not None      # keep the import meaningful


# --- 3b. the sender can be exercised without sending -------------------------
#
# Written after the omission cost a real send. Checking that `--body-stdin`
# parsed meant running the script, and running the script meant Exchange
# accepted a message for `a@b.test` -- an RFC 2606 reserved TLD, so it could
# only bounce, but it was still an outbound message nobody asked for.
#
# A CLI whose argument contract cannot be tested without performing the
# irreversible action is a defect in the CLI, not only in whoever ran it.

SEND_EMAIL = ROOT / "scripts" / "send-email.py"


def _dry_run(args: list[str], stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SEND_EMAIL), *args],
                          input=stdin, capture_output=True, text=True,
                          timeout=120, cwd=str(ROOT))


def test_dry_run_validates_and_sends_nothing():
    p = _dry_run(["--to", "a@b.test", "--subject", "s", "--body-stdin", "--dry-run"],
                 stdin="a body")
    assert p.returncode == 0, p.stderr[-500:]
    assert "[DRY-RUN] nothing was sent." in p.stdout
    assert "source=stdin" in p.stdout


def test_dry_run_returns_before_any_credential_is_read():
    """Read the order out of the source. A dry run that reached load_config()
    would still touch .env, and a dry run that reached connect() would open a
    session -- neither is 'nothing was sent', but both would print it."""
    src = SEND_EMAIL.read_text(encoding="utf-8")
    guard = src.index("if args.dry_run:")
    for call in ("load_config()", "connect(config)"):
        assert src.index(call, guard) > guard, f"{call} runs before the dry-run guard"
    # Strip comment lines before scanning: the guard's own comment names
    # load_config() to explain why it sits above it, and a naive substring
    # search matches the explanation. Same trap as the tty-prompt guard.
    between = src[src.index("args = parser.parse_args()"):guard]
    code = "\n".join(ln for ln in between.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "load_config()" not in code, (
        "a credential is read between parsing and the dry-run exit"
    )


def test_a_two_hundred_kilobyte_body_survives_the_real_cli():
    """The end-to-end proof for the argv limit, with no send: 200,000 bytes is
    over MAX_ARG_STRLEN and would have raised E2BIG as an argument."""
    p = _dry_run(["--to", "a@b.test", "--subject", "s", "--body-stdin", "--dry-run"],
                 stdin="x" * 200_000)
    assert p.returncode == 0, p.stderr[-500:]
    assert "body: 200000 char(s)" in p.stdout


def test_body_and_body_stdin_together_are_refused():
    p = _dry_run(["--to", "a@b.test", "--subject", "s", "--body", "x",
                  "--body-stdin", "--dry-run"], stdin="y")
    assert p.returncode == 2
    assert "not both" in p.stderr


def test_body_stdin_with_empty_stdin_is_refused():
    """Silently sending an empty body is worse than refusing."""
    p = _dry_run(["--to", "a@b.test", "--subject", "s", "--body-stdin", "--dry-run"],
                 stdin="")
    assert p.returncode == 2
    assert "stdin was empty" in p.stderr


# --- 3c. a test can never put a message on the wire --------------------------
#
# The control this file needed before it was written. Verifying the --dry-run
# guard by removing it sent a real message through Exchange, from inside
# pytest. Three went out to `a@b.test` before the pattern was noticed. RFC 2606
# reserves `.test`, so none could be delivered and all three bounce back to the
# sending mailbox -- but "it could only bounce" is luck, not a control.
#
# `send-email.py` now refuses outright when PYTEST_CURRENT_TEST is set, unless
# --dry-run is given. This is the only guard here that is NOT mutation-checked,
# on purpose: the mutation is "let a test send mail", which is the thing being
# prevented.

def test_the_sender_refuses_to_send_from_inside_a_test():
    p = _dry_run(["--to", "a@b.test", "--subject", "s", "--body", "x"])
    assert p.returncode == 3, (
        f"send-email.py did not refuse inside pytest (exit {p.returncode}). "
        f"stdout={p.stdout[:200]!r}"
    )
    assert "will not send from inside a test run" in p.stderr


def test_the_refusal_still_allows_a_dry_run():
    """A refusal that also blocked --dry-run would make the contract untestable
    again, which is how this started."""
    p = _dry_run(["--to", "a@b.test", "--subject", "s", "--body", "x", "--dry-run"])
    assert p.returncode == 0, p.stderr[-300:]
    assert "[DRY-RUN]" in p.stdout


def test_the_refusal_sits_above_every_send_path():
    """Read the order out of the source rather than proving it by sending."""
    src = SEND_EMAIL.read_text(encoding="utf-8")
    guard = src.index("PYTEST_CURRENT_TEST")
    after_parse = src.index("args = parser.parse_args()")
    assert after_parse < guard, "the guard runs before argparse; it needs args.dry_run"
    for call in ("load_config()", "connect(config)", "send_email(", "send_batch("):
        assert src.index(call, guard) > guard, f"{call} can be reached before the guard"


# --- 4. the file must not describe a spawner that no longer exists -----------

def test_the_docstring_does_not_claim_a_daemon_spawns_this():
    """`bridge-daemon.py:_executor_job` says the spawn was REMOVED 2026-06-27
    and that the terminal approve is the SOLE send path. This file's own
    docstring still advertised "Spawned every ~2 min by a config-gated daemon
    job", which is what made a reader treat the batch path as live."""
    import ast
    src = (ROOT / "scripts" / "action-queue-execute.py").read_text(encoding="utf-8")
    head = ast.get_docstring(ast.parse(src)) or ""
    assert head, "the module lost its docstring"
    # Stated as what the docstring MUST say, not as a phrase it must not
    # contain: the correction itself quotes the retired wording, and a
    # substring ban would forbid the fix from explaining itself.
    assert "Nothing spawns ``main()`` today" in head, (
        "the module docstring no longer states that nothing spawns the batch "
        "executor. A reader who believes a daemon runs it reasons about a "
        "duplicate-send window that has not existed since 2026-06-27."
    )
    assert "2026-06-27" in head, (
        "the docstring should name the date the daemon spawn was removed, so a "
        "reader can check the claim against bridge-daemon.py"
    )
    daemon = (ROOT / "scripts" / "bridge-daemon.py").read_text(encoding="utf-8")
    assert "send-executor spawn was REMOVED 2026-06-27" in daemon, (
        "the daemon no longer records the removal; re-check which side is current"
    )

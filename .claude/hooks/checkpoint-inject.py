#!/usr/bin/env python3
"""
checkpoint-inject.py - Claude Code SessionStart hook (matcher: compact|clear|resume).

Reads THIS session's handoff pointer files from
outputs/operations/handoff-archive/.latest/<session-slug>/ and prints them to
stdout, which Claude Code injects into the first user turn of the new session.

Two rules keep the injection honest:

  - Own session only. Until 2026-08-16 the pointer was one shared pair of files
    for the whole workspace, so a resumed session could be handed the handoff of
    a DIFFERENT session while this text asserted that a previous checkpoint had
    been found - a claim nothing in the hook had established
    (.claude/rules/scope-claims.md). There is deliberately no fallback to
    "the newest handoff on disk": with several sessions open, the newest is
    usually somebody else's work, and no handoff beats a foreign one.
  - No body on `compact`, and in MANUAL mode no output at all. The harness has
    just put a summary of THIS session into context; re-injecting a pointer only
    competes with it. What happens next depends on the mode, and the two are not
    the same run: in AUTO mode the hook prints one short continuation
    instruction and stops there; in manual mode it prints nothing and exits 0,
    because the operator drives the next turn himself and a "continue without
    asking" instruction would take that decision away from him.

    When there IS text - the auto path only - it says what this hook DID and
    never what is on disk: it takes the `compact` source as its whole evidence
    and never looks in the archive, and until 2026-08-20 it asserted "this
    session's saved handoffs are under the handoff archive" all the same.
    Measured that day: with zero pointer dirs for the session, the sentence
    printed unchanged (.claude/rules/scope-claims.md).

    Retired 2026-09-02, quoted for the record: "The text printed on that path
    says what this hook DID and never what is on disk". That describes a run
    that does not happen in manual mode, where there is no text. MEASURED that
    day, driving this hook with `source=compact` against a scratch project and
    data root: with CLAUDE_HANDOFF_AUTO unset, stdout was 0 bytes and the exit
    code 0; with it set, 645 bytes beginning "# Checkpoint". The code was the
    correct half and the sentence was corrected to it, because manual mode is
    the mode in which `checkpoint-offer.py` ASKS the operator what to do rather
    than compacting for him.

    The shared `.latest/summary.md` still exists for scripts/next-signal.py,
    which asks a different question ("the newest handoff in this workspace")
    where last-writer-wins is the right answer.

Silent on fresh sessions (matcher excludes 'startup' in settings registration).
Silent when this session has no pointer files.

Truncates to bounded sizes so a very large handoff cannot dominate context.
"""

import json
import sys
from pathlib import Path

_BOOT = Path(__file__).resolve()
_ROOT = _BOOT.parent.parent.parent
for _candidate in [_BOOT.parent, *_BOOT.parents]:
    if (_candidate / "scripts" / "utils" / "checkpoint_paths.py").is_file():
        _ROOT = _candidate
        sys.path.insert(0, str(_candidate))
        break
from scripts.utils import checkpoint_paths as CP  # noqa: E402

CP.force_utf8()

MAX_SUMMARY_CHARS = 8000
MAX_PROMPT_CHARS = 4000

AUTO_CLOSING = (
    "AUTO MODE: continue the latest unfinished task from this handoff without "
    "asking for confirmation. Briefly restate the objective and the next "
    "concrete action, then proceed. Repository state is authoritative."
)

MANUAL_CLOSING = (
    "Use the handoff to continue the latest unfinished task. Repository state "
    "is authoritative."
)

AUTO_AFTER_COMPACT = """# Checkpoint

AUTO MODE: continue the latest unfinished task from the compaction summary
above, without asking for confirmation. Briefly restate the objective and the
next concrete action, then proceed. Repository state is authoritative.

The summary may close with a next step that tells you to stop and wait for the
operator. That step describes the end of the turn the harness compacted, so it
is already done. Do not read it as a current instruction. Go on to the next
step that is still open.

No saved handoff is re-injected here: the compaction summary already in context
supersedes the pointer, so this hook does not add a second copy.
"""


def read_limited(path: Path, limit: int) -> str:
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 - an unreadable pointer is not fatal
        print(f"checkpoint-inject: unreadable pointer {path.name}: {exc}", file=sys.stderr)
        return ""
    if len(text) <= limit:
        return text
    # Left in place, and measured 2026-08-20 rather than assumed: the writer's
    # own bound (CP.MAX_POINTER_SUMMARY = 6000 plus a ~800-character header) caps
    # what checkpoint-save.py can produce at 6801 characters, which is what all 7
    # live pointers hold and what a synthetic 40 KB summary produced. So this
    # branch is unreachable from the CURRENT writer and is a guard against a
    # pointer written before that bound landed (2026-08-16), when the live one
    # reached 32261 bytes. The margin is 1199 characters, not much: raise
    # MAX_POINTER_SUMMARY or grow the pointer header and the blind mid-sentence
    # cut is back, which is why the cap is not being removed as dead.
    return text[:limit] + "\n\n[Truncated by checkpoint-inject]\n"


def main() -> int:
    raw = ""
    try:
        raw = sys.stdin.read()
    except Exception as exc:
        print(f"checkpoint-inject: stdin read failed: {exc}", file=sys.stderr)

    payload = {}
    if raw.strip():
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {}
    # A well-formed JSON list or string parses fine and then dies on the first
    # `.get`. Measured 2026-08-20 against the shipped hook: both exited 1 with an
    # uncaught AttributeError, which on SessionStart loses the whole injection.
    # An unreadable payload still has CLAUDE_CODE_SESSION_ID to fall back on, so
    # degrading to the empty payload recovers the handoff rather than dropping it.
    if not isinstance(payload, dict):
        payload = {}

    # Resolved from THIS session's state file, so a switch the operator flipped
    # mid-work is still in force on the other side of the compaction that ended
    # the previous half of it.
    project = CP.project_root(payload)
    slug = CP.session_slug(payload)
    state = CP.read_json(CP.state_path(project, slug))
    auto = CP.config(state)["auto"]

    # `x or ""` guards the FALSY non-strings and admits every truthy one, so a
    # `source` of `3` or `true` reached `.strip()` and raised. Measured
    # 2026-09-01 driving the real hook: both exited 1 with an uncaught
    # AttributeError. That is the identical defect this file's own comment,
    # fourteen lines up, records fixing for the PAYLOAD on 2026-08-20 - the
    # container was guarded and the field inside it was not.
    #
    # SessionStart is where the handoff is replayed, so the cost is the same one
    # that comment names: the whole injection, lost. An `isinstance` rather than
    # `str(...)`, because a non-string source is not a source this hook knows,
    # and coercing it would let `"3"` be compared as if the harness had sent it.
    source = payload.get("source")
    if not isinstance(source, str):
        source = ""
    if source.strip() == "compact":
        if auto:
            print(AUTO_AFTER_COMPACT)
        return 0

    latest = CP.latest_dir(CP.handoff_dir(project, _ROOT), slug)

    summary = read_limited(latest / "summary.md", MAX_SUMMARY_CHARS)
    prompt = read_limited(latest / "prompt.md", MAX_PROMPT_CHARS)

    sections: list[str] = []
    if summary:
        sections.append(f"## Latest summary\n\n{summary}")
    if prompt:
        sections.append(f"## Continuation prompt\n\n{prompt}")
    if not sections:
        return 0

    body = "\n\n".join(sections)
    closing = AUTO_CLOSING if auto else MANUAL_CLOSING

    # "saved by this session" is only true when the session HAS an id. With
    # neither a payload `session_id` nor `CLAUDE_CODE_SESSION_ID` - the hook's
    # own documented degraded path - `CP.session_id` returns the shared sentinel
    # and `.latest/session/` becomes a cross-session bucket, so session A's
    # handoff would be injected into session B under an authorship claim the
    # method never established. That is the sentence the 2026-08-16 change
    # removed, reappearing one slug down.
    if CP.session_id_is_known(payload):
        provenance = (f"A handoff saved by this session ({slug}) was found in the "
                      f"handoff archive.")
    else:
        provenance = (f"A handoff was found under the shared pointer slug "
                      f"'{slug}'. This session reported no id, so that bucket "
                      f"holds every id-less session's handoff and this one may "
                      f"belong to a DIFFERENT session.")

    print(
        f"""# Auto-injected handoff

{provenance} Check
its Generated timestamp before trusting it over the repository.

{body}

---

{closing}
"""
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

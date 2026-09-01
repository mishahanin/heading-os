"""The yield report must not become the thing it reports on.

Found at step 11 of the `gate-yield` slice, by probing the render seam rather
than reading it. A denial record's `reason` is derived from what a guard
refused, which is by definition something somebody tried to push past it, and
`redact()` substitutes credential patterns without touching control bytes.

Two failures, one input. A crafted reason carrying ESC replayed into the
operator's terminal, making the instrument a delivery mechanism. And an embedded
newline FORGED a row: the rendered output carried a line reading
"FAKE  approve  999 catch(es)" that was indistinguishable from a genuine one, so
a report whose entire job is to be trusted about numbers could be made to lie
about them.

The guard already existed. `scripts/denials.py` grew it on 2026-08-01 for the
same class of input and this sibling surface was written a day later without it,
which is why `printable` now lives in `scripts/utils/denial_log.py` and both
readers import the one implementation.
"""

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

_ESC = "\x1b" + "[2J"
_FORGERY = "FAKE  approve  999 catch(es)"


def _render(reason):
    from scripts.utils.gate_yield import render, summarise

    summary = summarise(
        denials=[{"mechanism": "check_tool_budget", "reason": reason,
                  "ts": "2026-08-01T00:00:00+00:00"}],
        since={"denials": "2026-08-01T00:00:00+00:00"},
        now="2026-08-02T00:00:00+00:00")
    return render(summary, now="2026-08-02T00:00:00+00:00")


def test_an_escape_sequence_in_a_record_is_shown_as_text_not_executed():
    assert _ESC not in _render(f"wipe{_ESC}")


def test_a_newline_in_a_record_cannot_forge_a_row():
    """The one that matters more. An ESC is ugly; a forged row is a lie about
    the numbers this report exists to produce."""
    out = _render(f"wipe\n{_FORGERY}")
    assert not any(line.startswith("FAKE") for line in out.split("\n")), out
    # And the payload is still SHOWN, escaped, on one line. Without this line
    # the assertion above is satisfied by a render that never emitted the cause
    # at all: measured 2026-09-01, emptying the causes loop in `render` left it
    # green, so "no forged row" could not be told apart from "no row".
    assert f"wipe\\n{_FORGERY}" in out, out


def test_a_crafted_mechanism_name_cannot_forge_a_row_either():
    """The name arrives from the same file as the reason and had the same gap."""
    from scripts.utils.gate_yield import render, summarise

    summary = summarise(
        denials=[{"mechanism": f"x\n{_FORGERY}",
                  "ts": "2026-08-01T00:00:00+00:00"}],
        since={"denials": "2026-08-01T00:00:00+00:00"},
        now="2026-08-02T00:00:00+00:00")
    out = render(summary, now="2026-08-02T00:00:00+00:00")
    assert not any(line.startswith("FAKE") for line in out.split("\n")), out
    assert f"x\\n{_FORGERY}" in out, out  # shown, escaped, on the mechanism row


def test_both_readers_share_one_implementation():
    """A guard repaired on one sibling and not the other is the pattern that
    produced this defect; this fails if the two drift apart again."""
    import scripts.denials as denials
    from scripts.utils.denial_log import printable

    assert denials._printable(f"a{_ESC}") == printable(f"a{_ESC}")
    assert denials.printable is printable


def test_the_cli_still_renders_clean():
    proc = subprocess.run([sys.executable, str(_ROOT / "scripts" / "gate-yield.py")],
                          capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "\x1b" not in proc.stdout

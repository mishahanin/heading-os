#!/usr/bin/env python3
"""The canary: positive evidence that the record path still writes a record.

The design rule this file exists to enforce, learned the expensive way. The
retired build's flagship record-keeping mechanism was wired into pytest through
three hooks, each wrapping it in a broad handler that printed one line to stderr
and continued. At a certain state it raised on EVERY invocation. The suite
exited 0, both import assertions passed, `--collect-only` passed, and a
purpose-built corpus criterion passed: total failure of the flagship changed the
40-file corpus by exactly zero tests. The whole green surface certified a dead
record.

So: **exit 0 is the absence of observed failure; what is wanted is the presence
of observed function.** This test drives the REAL write-and-read path end to end
and asserts on the artifact, never on the silence around it. It is deliberately
the shortest test in the suite, because its value is that it runs, not that it
is clever.

**The known gap, stated rather than left to be discovered.** This file can be
removed from any run by -k, --deselect, --ignore, or a bare path argument: a
builder who cannot edit it can still decline to run it. A sibling canary at
pytest_sessionstart, before collection, would sit where no filter reaches it;
that is not here. What closes the gap in practice is the CI step in the
`sovereignty guards` job, which runs the suite whole on every push, and
`scripts/canopus_check.py` clause C4, which reads per-file junit counts rather
than an exit code.
"""

from scripts.utils.canopus_note import digest_text, read_note, write_note

# Synthetic throughout, and the shas are ABBREVIATED refs -- this repository's
# convention (config/canopus-genesis.json), because a full 40-character sha
# reads to detect-secrets as a hex high-entropy string and every way to silence
# that is forbidden here. The digest is COMPUTED for the same reason.
VALID_FIELDS = {
    "slug": "canary",
    "value": "the record path is alive, and this is what proves it",
    "approval_sha": "1a2b3c4",
    "contract": "tests/contract/2026-01-02-canary/",
    "plan_digest": digest_text("the plan document's content, not its path"),
    "scrutinize_plan": "clean",
    "scrutinize_built": "clean",
    "undo": "delete the note; nothing else was written",
}


def test_the_record_path_writes_something_real(tmp_path):
    """Positive evidence of function, not absence of observed failure."""
    path = write_note(tmp_path, "canary", VALID_FIELDS)

    assert path.exists(), "write_note reported a path it did not write"
    assert read_note(tmp_path, "canary")["slug"] == "canary"

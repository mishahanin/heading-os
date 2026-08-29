"""Four controls over `scripts/fireside_webhook.py` that never ran it.

The webhook is a PUBLIC endpoint. Telegram POSTs to it, and so can anyone who
discovers the URL; the secret-token header is the only authorization. Its
message branch therefore guards three malformed bodies that the module's own
comment names: `"message": null`, `"message": "hi"`, and `"from": null`. Without
the guards each one reaches `.get` on a non-dict and leaves as a 500, past the
handler's own stated boundary that a malformed body is the caller's error.

Four tests claimed to hold that, and none of them executed the module.

Two of them restated the guard expression in the test body and asserted on the
restatement:

    msg = {"from": None, "text": "hi"}
    assert (msg.get("from") or {}).get("username", "?") == "?"

That is a statement about Python's `dict.get`, true in every tree. Measured
2026-08-29 by removing both guards from `scripts/fireside_webhook.py` and
driving the real endpoint:

    guards PRESENT: {'message is null': 200, 'message is a string': 200,
                     'from is null': 200, 'ordinary message': 200}
    guards REMOVED: {'message is null': 500, 'message is a string': 500,
                     'from is null': 500, 'ordinary message': 200}
    the two existing tests, guards PRESENT: 2 passed
    the two existing tests, guards REMOVED: 2 passed

Three real 500s, and the suite stayed green. A sibling file in the same suite
already warns about exactly this shape, in `_stop_parser`'s docstring: "a
restated copy would pass while the real CLI still lacked the flag".

The third read the module's SOURCE TEXT and counted `%` conversions in the
success log's format string. Its own docstring says "the format string and its
five arguments have to be counted together"; the code counted one side. Deleting
an ARGUMENT and leaving the conversions alone survives it, and at runtime
`logging` raises inside `emit`, prints a traceback to stderr and writes no line.
The daemon log then has nothing at all for a handled update, and the operator
reads the silence as no traffic. That is precisely the failure the docstring
gives as the reason for the test.

The fourth asserted that two COMMENT phrases appear in the source
(`"offset NOT advanced" in src`). A comment is not a behaviour. It stays true if
the code beneath it starts advancing the offset, which would make a permanently
failing update ack itself and vanish.

All four are replaced here by tests that build the real app and drive it.
"""
from __future__ import annotations

import asyncio
import logging
import threading

import pytest

pytest.importorskip("fastapi")  # F-7.1: skip on a core-only clone
pytest.importorskip("httpx")

from httpx import ASGITransport, AsyncClient

from scripts import fireside_webhook as fw

# noqa is for ruff S105, the pragma is for detect-secrets. Both are needed and
# both are honest: `create_app` refuses an empty token, so a literal is required
# here, and this one is invented for the test.
SECRET = "probe-secret-token"  # noqa: S105  # pragma: allowlist secret
LAST_UPDATE_ID = "last-update-id"


class _Bot:
    """Stands in for the fireside bot object; the handler never uses it here."""


class _FakeFB:
    """The dynamically-imported fireside-bot module, as the webhook uses it.

    Records rather than discards: a stub that swallowed its argument would let
    every assertion below pass over an empty call.
    """

    LAST_UPDATE_ID = LAST_UPDATE_ID

    def __init__(self, raises: BaseException | None = None):
        self._raises = raises
        self.handled: list[dict] = []
        self.state: dict[str, dict] = {}
        self.saves: list[tuple[str, dict]] = []
        self.done = threading.Event()

    def get_bot(self):
        return _Bot()

    def _handle_update(self, bot, update):
        self.handled.append(update)
        try:
            if self._raises is not None:
                raise self._raises
        finally:
            self.done.set()

    def load_state(self, key):
        return self.state.get(key)

    def save_state(self, key, value):
        self.state[key] = value
        self.saves.append((key, value))


class _Capture(logging.Handler):
    """Keeps the LogRecord, not a formatted string.

    `record.getMessage()` is what raises when the format string and the argument
    vector disagree, so the record has to survive to the assertion for that
    failure to be visible at all.
    """

    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


def _build(fb: _FakeFB):
    logger = logging.getLogger(f"shard74.{id(fb)}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    cap = _Capture()
    logger.handlers = [cap]
    return fw.create_app(fb, SECRET, logger), cap


async def _post(app, body):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://probe") as client:
        return await client.post(
            "/telegram-webhook", json=body,
            headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})


async def _drain(fb: _FakeFB, timeout: float = 5.0):
    """Await the background task itself, which is the only wait that is not a race.

    The handler runs through `asyncio.to_thread`, and everything these tests
    read afterwards (the success log, the offset write) happens after it
    returns. An earlier version here waited on `fb.done` and then spun a fixed
    number of loop turns. Mutation-checked 2026-08-29: deleting the wait and
    keeping the spin changed nothing, so the spin was doing the work and the
    wait was decoration. A fixed turn count is either flaky or slow, and it
    hides whether anything waited at all. Awaiting the task is deterministic,
    and removing it makes every assertion below race.
    """
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    # Falsifiable, not defensive. `asyncio` keeps only a WEAK reference to a
    # task, which is why the module holds a `background` set at all; if that set
    # were dropped, or `create_task` were, no task would exist here and every
    # assertion after the drain would read the state of an update nobody
    # processed. A bare `if pending:` would pass silently on exactly that.
    assert pending or fb.done.is_set(), "no background task was created"
    if pending:
        _done, still_running = await asyncio.wait(pending, timeout=timeout)
        assert not still_running, "the background handler outlasted the timeout"


# ============================================================
# The malformed bodies the module's comment names
# ============================================================

MALFORMED = [
    ("message is null", {"update_id": 101, "message": None}),
    ("message is a string", {"update_id": 102, "message": "hello"}),
    ("from is null", {"update_id": 103, "message": {"from": None, "text": "hi"}}),
]


@pytest.mark.parametrize("label,body", MALFORMED, ids=[m[0] for m in MALFORMED])
async def test_a_malformed_message_body_is_accepted_not_a_server_error(label, body):
    """Each of these returned 500 with the guards removed. Measured, not assumed."""
    fb = _FakeFB()
    app, _ = _build(fb)
    r = await _post(app, body)
    assert r.status_code == 200, (
        f"{label}: the public endpoint answered {r.status_code}; a malformed "
        f"body is the caller's error and the handler says so two comments up")


@pytest.mark.parametrize("label,body", MALFORMED, ids=[m[0] for m in MALFORMED])
async def test_a_malformed_message_body_still_logs_a_usable_preview(label, body):
    """Accepting it is half the guard; the log line must still be readable.

    `record.getMessage()` is the call that raises when a preview expression
    produced something the format string cannot take.
    """
    fb = _FakeFB()
    app, cap = _build(fb)
    await _post(app, body)
    recv = [r for r in cap.records if r.getMessage().startswith("webhook: recv")]
    assert len(recv) == 1, f"{label}: expected one recv line, got {len(recv)}"
    assert "message from=@?" in recv[0].getMessage(), recv[0].getMessage()


# The callback_query branch carries the SAME three guards, written the same way.
# It was never the reported defect, and nothing tested it either: mutation
# `callback-guard-removed` survived the first version of this file. One branch
# fixed and tested while its twin is only fixed is how a guard comes back.
CALLBACK_MALFORMED = [
    ("callback_query is null", {"update_id": 201, "callback_query": None}),
    ("callback_query is a string", {"update_id": 202, "callback_query": "nope"}),
    ("callback from is null",
     {"update_id": 203, "callback_query": {"from": None, "data": "x"}}),
    ("callback data is null",
     {"update_id": 204, "callback_query": {"from": {"username": "m"}, "data": None}}),
]


@pytest.mark.parametrize("label,body", CALLBACK_MALFORMED,
                         ids=[m[0] for m in CALLBACK_MALFORMED])
async def test_a_malformed_callback_body_is_accepted_not_a_server_error(label, body):
    fb = _FakeFB()
    app, cap = _build(fb)
    r = await _post(app, body)
    assert r.status_code == 200, f"{label}: the endpoint answered {r.status_code}"
    recv = [x for x in cap.records if x.getMessage().startswith("webhook: recv")]
    assert len(recv) == 1, f"{label}: expected one recv line, got {len(recv)}"
    assert "callback_query from=@" in recv[0].getMessage(), recv[0].getMessage()


async def test_an_ordinary_callback_still_logs_its_user_and_data():
    """Anchor for the four above: a branch that logged `@?` for every caller
    would satisfy them."""
    fb = _FakeFB()
    app, cap = _build(fb)
    await _post(app, {"update_id": 205,
                      "callback_query": {"from": {"username": "quartermaster"},
                                         "data": "swap:7"}})
    recv = [x for x in cap.records if x.getMessage().startswith("webhook: recv")]
    assert "from=@quartermaster" in recv[0].getMessage()
    assert "'swap:7'" in recv[0].getMessage()


async def test_an_ordinary_message_still_reaches_the_handler_and_the_log():
    """Anti-vacuity, and the case the guards sit in front of.

    Every test above would also pass against an endpoint that answered 200 and
    did nothing at all. This one shows the update reaches `_handle_update` with
    its contents intact and the username in the log line is the real one.
    """
    fb = _FakeFB()
    app, cap = _build(fb)
    body = {"update_id": 104,
            "message": {"from": {"username": "quartermaster"}, "text": "hello"}}
    r = await _post(app, body)
    assert r.status_code == 200
    await _drain(fb)

    assert fb.handled == [body], "the handler did not receive the update verbatim"
    recv = [x for x in cap.records if x.getMessage().startswith("webhook: recv")]
    assert "from=@quartermaster" in recv[0].getMessage()
    assert "'hello'" in recv[0].getMessage()


# ============================================================
# The success log must FORMAT, not merely contain five per-cent signs
# ============================================================

async def test_the_success_log_line_formats_with_every_field_it_promises():
    """Counting `%` in the source cannot see a missing ARGUMENT.

    `logging` defers formatting to `record.getMessage()`, so a five-conversion
    format string handed four arguments raises there, inside `emit`. The daemon
    prints a traceback to stderr and writes no line, and the operator reads the
    silence as no traffic. Calling `getMessage()` here is what makes that
    failure a red test instead of a missing log line in production.
    """
    fb = _FakeFB()
    app, cap = _build(fb)
    await _post(app, {"update_id": 105,
                      "message": {"from": {"username": "m"}, "text": "hi"}})
    await _drain(fb)

    ok = [r for r in cap.records if r.msg.startswith("webhook: ok update=")]
    assert len(ok) == 1, f"expected one success line, got {len(ok)}"
    rendered = ok[0].getMessage()  # raises if the vector and the format disagree

    assert "update=105" in rendered
    assert "kind=message" in rendered
    for field in ("handler_ms=", "queued_ms=", "total_ms="):
        assert field in rendered, f"{field} missing from {rendered!r}"
        value = rendered.split(field, 1)[1].split(" ", 1)[0]
        assert value.isdigit(), (
            f"{field} rendered as {value!r}; a `%d` fed a non-number, or the "
            f"argument vector slipped by one")


async def test_the_success_line_is_absent_when_the_handler_raises():
    """Anchor for the test above. `ok` must mean the handler returned."""
    fb = _FakeFB(raises=RuntimeError("handler exploded"))
    app, cap = _build(fb)
    await _post(app, {"update_id": 106, "message": {"text": "hi"}})
    await _drain(fb)

    assert not [r for r in cap.records if r.msg.startswith("webhook: ok update=")]


# ============================================================
# A failing update must be named, and must NOT be acked
# ============================================================

async def test_a_raising_handler_is_logged_at_error_with_its_update_id():
    """The comment-grep this replaces stayed true however the code behaved."""
    fb = _FakeFB(raises=RuntimeError("handler exploded"))
    app, cap = _build(fb)
    await _post(app, {"update_id": 107, "message": {"text": "hi"}})
    await _drain(fb)

    errors = [r for r in cap.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1, f"expected one ERROR record, got {len(errors)}"
    rendered = errors[0].getMessage()
    assert "update=107" in rendered
    assert "offset NOT advanced" in rendered, (
        "the operator reads this line to learn the update will come back")
    assert errors[0].exc_info is not None, "the traceback must reach the log"


async def test_a_raising_handler_does_not_advance_the_offset():
    """The consequence the comment describes, asserted against the state write.

    Acking a permanently failing update drops it. Nothing before this checked
    that the offset write is actually skipped, only that a comment said so.
    """
    fb = _FakeFB(raises=RuntimeError("handler exploded"))
    app, _ = _build(fb)
    await _post(app, {"update_id": 108, "message": {"text": "hi"}})
    await _drain(fb)

    assert fb.saves == [], f"the failed update was acked anyway: {fb.saves}"


async def test_a_successful_update_does_advance_the_offset():
    """Anchor for the test above: `saves == []` must be evidence of the refusal,
    not of a code path that never writes at all."""
    fb = _FakeFB()
    app, _ = _build(fb)
    await _post(app, {"update_id": 108, "message": {"text": "hi"}})
    await _drain(fb)

    assert fb.saves == [(LAST_UPDATE_ID, {"offset": 109})], fb.saves


async def test_the_offset_is_never_rewound_by_a_slow_update():
    """`max(current, update_id + 1)`, not `update_id + 1`.

    Two updates in flight finish in either order, and the write is done by
    whichever finishes LAST, not by whichever id is highest. Pinned here because
    the module's docstring is the only other place that says so.
    """
    fb = _FakeFB()
    fb.state[LAST_UPDATE_ID] = {"offset": 500}
    app, _ = _build(fb)
    await _post(app, {"update_id": 100, "message": {"text": "hi"}})
    await _drain(fb)

    assert fb.saves == [(LAST_UPDATE_ID, {"offset": 500})], (
        f"a lower id rewound the offset: {fb.saves}")


async def test_a_body_that_is_not_an_object_is_refused_with_400():
    """The boundary the message-branch guards defer to. A list and a bare string
    are valid JSON, and both used to leave as a 500."""
    fb = _FakeFB()
    app, _ = _build(fb)
    for body in ([], "x", 7):
        r = await _post(app, body)
        assert r.status_code == 400, f"{body!r} answered {r.status_code}"


async def test_a_wrong_secret_token_is_refused_before_anything_is_parsed():
    """Anti-vacuity for every test above: they all send the right token, so a
    handler that ignored the header entirely would pass all of them."""
    fb = _FakeFB()
    app, _ = _build(fb)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://probe") as client:
        r = await client.post("/telegram-webhook", json={"update_id": 1},
                              headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"})
    assert r.status_code == 401
    assert fb.handled == []

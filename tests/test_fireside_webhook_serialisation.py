"""Two updates arriving together must not clobber each other's state.

Found by the 2026-08-23 audit. The webhook receiver returns 200 OK immediately
and does the work in `asyncio.create_task(_process_in_background(...))`, which
hands `_handle_update` to a thread. Nothing serialised those tasks, and every
handler in `fireside-bot.py` works by load → mutate → save on a JSON file:
`schedule.json`, `opt-ins.json`, `helmsmen.json`, the swap records.

So two updates a second apart — a member taps a swap button while an expiry
sweep runs, or two people `/start` together — interleave load and save, and one
write silently disappears. Nothing logs it; the state file is valid JSON either
way.

Two smaller defects sat in the same function:

* The last-update-id offset was written as `update_id + 1` by whichever task
  finished LAST, not by whichever id was HIGHEST. A slow update 100 finishing
  after a fast 101 rewound the offset, and if the daemon ever falls back to
  polling it re-processes what it already handled.
* `asyncio.create_task` was called without keeping a reference. CPython only
  holds a weak one, so a task can be garbage-collected mid-flight and the update
  is dropped with no error at all.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import logging
import sys
import textwrap
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("fastapi")

from scripts import fireside_webhook as fw  # noqa: E402

LOGGER = logging.getLogger("test-fireside-webhook")


class _FakeBot:
    pass


def _fb_module(state: dict, handler):
    """A stand-in for the fireside-bot module the daemon imports."""
    module = types.SimpleNamespace()
    module.LAST_UPDATE_ID = "last-update-id"
    module.get_bot = lambda: _FakeBot()
    module._handle_update = handler
    module.save_state = lambda name, payload: state.__setitem__(name, payload)
    module.load_state = lambda name: state.get(name)
    return module


async def _drain(app):
    """Let every task the handler spawned finish."""
    for _ in range(200):
        pending = [t for t in asyncio.all_tasks()
                   if t is not asyncio.current_task() and not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_two_overlapping_updates_do_not_interleave():
    """The read-modify-write race, made deterministic.

    Each handler reads a counter, yields long enough for the other to start,
    then writes back what it read plus one. Unserialised, both read 0 and the
    counter ends at 1 — one member's action lost. Serialised, it ends at 2.
    """
    state: dict = {}
    counter = {"value": 0}
    order: list[str] = []

    def _handler(_bot, update):
        import time
        tag = update["tag"]
        order.append(f"start:{tag}")
        seen = counter["value"]
        time.sleep(0.05)                  # the window the other task raced into
        counter["value"] = seen + 1
        order.append(f"end:{tag}")

    app = fw.create_app(_fb_module(state, _handler), "s3cret", LOGGER)
    handler = _post_handler(app)

    await asyncio.gather(
        handler(_Request({"update_id": 1, "tag": "a", "message": {}}), "s3cret"),
        handler(_Request({"update_id": 2, "tag": "b", "message": {}}), "s3cret"),
    )
    await _drain(app)

    assert counter["value"] == 2, (
        f"one update's write was lost; handler order was {order}"
    )
    assert order == ["start:a", "end:a", "start:b", "end:b"] or \
           order == ["start:b", "end:b", "start:a", "end:a"], (
        f"handlers overlapped: {order}"
    )


@pytest.mark.asyncio
async def test_the_offset_never_moves_backwards():
    """A slow low id finishing after a fast high id must not rewind the offset."""
    state: dict = {}

    def _handler(_bot, update):
        import time
        time.sleep(0.05 if update["update_id"] == 100 else 0.0)

    app = fw.create_app(_fb_module(state, _handler), "s3cret", LOGGER)
    handler = _post_handler(app)

    await handler(_Request({"update_id": 101, "message": {}}), "s3cret")
    await _drain(app)
    await handler(_Request({"update_id": 100, "message": {}}), "s3cret")
    await _drain(app)

    assert state["last-update-id"] == {"offset": 102}, (
        f"the offset rewound to {state['last-update-id']}"
    )


@pytest.mark.asyncio
async def test_the_background_task_still_runs_after_the_request_returns():
    """The behaviour half: the update is processed, not lost with the request."""
    state: dict = {}
    ran = []

    def _handler(_bot, update):
        ran.append(update["update_id"])

    app = fw.create_app(_fb_module(state, _handler), "s3cret", LOGGER)
    handler = _post_handler(app)
    await handler(_Request({"update_id": 7, "message": {}}), "s3cret")
    await _drain(app)
    assert ran == [7]


def test_the_background_task_is_held_by_a_strong_reference():
    """The guard half, and it has to be structural.

    This used to be one test that called `gc.collect()` between the request and
    the drain, with the comment "the collection that used to lose it". That
    proves nothing: a running `asyncio.Task` is referenced by the event loop for
    its whole lifetime, so `gc.collect()` cannot reclaim it under any
    implementation. The test was green with the `background` set and green
    without it - the fix it was written to pin was not being measured at all.

    The reference-keeping is a property of the code, so read the code. The
    three parts have to be there together: create the task, put it in a
    container that outlives the request, and take it back out when it finishes
    so the container is not a leak.
    """
    src = inspect.getsource(fw.create_app)
    tree = ast.parse(textwrap.dedent(src))

    created = adds = discards = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr == "create_task":
            created += 1
        elif isinstance(fn, ast.Attribute) and fn.attr == "add":
            adds += 1
        elif isinstance(fn, ast.Attribute) and fn.attr == "add_done_callback":
            discards += 1

    assert created >= 1, "create_app no longer spawns a background task"
    assert adds >= 1, (
        "the task is created and dropped on the floor. A task with no strong "
        "reference outside the loop can be collected mid-flight and the update "
        "is lost with no error anywhere."
    )
    assert discards >= 1, (
        "nothing removes the finished task from the container it was added to, "
        "so the set grows for the life of the process"
    )
    assert "background.discard" in src, (
        "the done-callback does not put the task back; found:\n" + src[-800:]
    )


@pytest.mark.asyncio
async def test_a_handler_that_raises_does_not_hold_the_lock():
    """One bad update must not wedge every later one."""
    state: dict = {}
    ran = []

    def _handler(_bot, update):
        ran.append(update["update_id"])
        if update["update_id"] == 1:
            raise RuntimeError("boom")

    app = fw.create_app(_fb_module(state, _handler), "s3cret", LOGGER)
    handler = _post_handler(app)
    await handler(_Request({"update_id": 1, "message": {}}), "s3cret")
    await _drain(app)
    await asyncio.wait_for(
        handler(_Request({"update_id": 2, "message": {}}), "s3cret"), timeout=5)
    await _drain(app)
    assert ran == [1, 2]


# --- plumbing -------------------------------------------------------------

class _Request:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


def _post_handler(app):
    """The POST /telegram-webhook coroutine, called directly (no HTTP client)."""
    for route in app.routes:
        if getattr(route, "path", None) == "/telegram-webhook":
            endpoint = route.endpoint

            async def _call(request, token, _endpoint=endpoint):
                return await _endpoint(request, token)

            return _call
    raise AssertionError("the webhook route is gone")

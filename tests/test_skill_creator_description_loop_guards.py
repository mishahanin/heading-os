"""An empty model response is not an improved description, and a live report is not public.

Two defects found by the 2026-08-31 review.

F13. `improve_description.py` asks the model for a description wrapped in
``<new_description>`` tags, then does::

    match = re.search(r"<new_description>(.*?)</new_description>", text, re.DOTALL)
    description = match.group(1).strip().strip('"') if match else text.strip().strip('"')

`text` is assigned only from a ``text`` content block. A response truncated
inside extended thinking - ``max_tokens`` reached mid-``thinking`` block, which
is exactly what a 10,000-token thinking budget under a 16,000-token cap invites
- carries no text block at all. `text` stays ``""``, `re.search` returns None,
and the fallback yields ``""``. `run_loop.py` has no non-empty check: it assigns
that to `current_description` and the next iteration evaluates the empty string,
scores it, and can select it as "best".

`run_loop.py` already guards hard against the neighbouring failure - a dead
`claude` CLI - for precisely this reason, and `run_eval.py`'s module docstring
states the principle: a run that never happened is not a negative result. A
model call that returned nothing is the same shape, on the other subprocess.

F14. `run_loop.py::main` wrote the live HTML report to
``Path(tempfile.gettempdir()) / f"skill_description_report_{skill}_{timestamp}.html"``
with `write_text`. The path is fully predictable from the skill name and the
clock, and lands in the world-readable shared temp directory at the default
0644. On a multi-user box that is both a disclosure and a pre-created-symlink
target: `write_text` follows symlinks, so whoever guesses the name first
chooses which file this process overwrites. `tempfile.mkdtemp()` creates a
0700 directory with an unguessable name and costs one line.
"""
from __future__ import annotations

import importlib.util
import socket
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILL_CREATOR = ROOT / ".claude" / "skills" / "skill-creator"
SKILL_SCRIPTS = SKILL_CREATOR / "scripts"


def _load_shadowed(name: str, path: Path):
    """Import a skill-creator script without leaving the host's `scripts` broken.

    The skill's own package is also called `scripts`. Same dance as
    `tests/test_skill_creator_run_eval_reports_a_dead_cli.py`.
    """
    saved = {k: v for k, v in sys.modules.items() if k == "scripts" or k.startswith("scripts.")}
    for key in saved:
        del sys.modules[key]
    # Snapshot and restore the WHOLE path, not one `remove`. The script being
    # loaded runs its own `sys.path.insert(0, <skill-creator>)` at import, so a
    # single `remove` of that string takes one of the two copies and leaves the
    # other on the path for the rest of the xdist worker - where the skill's own
    # `scripts/` package shadows this repo's for every later test.
    saved_path = sys.path[:]
    sys.path.insert(0, str(SKILL_CREATOR))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = saved_path
        for key in [k for k in sys.modules if k == "scripts" or k.startswith("scripts.")]:
            del sys.modules[key]
        sys.modules.update(saved)


improve = _load_shadowed("_improve_desc_under_test", SKILL_SCRIPTS / "improve_description.py")
run_loop_mod = _load_shadowed("_run_loop_guards_under_test", SKILL_SCRIPTS / "run_loop.py")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """No test here may reach the Anthropic API or anything else."""
    def _refuse(*args, **kwargs):
        raise AssertionError("a test in this file attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)


# ---------------------------------------------------------------- F13


class _Block:
    def __init__(self, type_, **kwargs):
        self.type = type_
        for key, value in kwargs.items():
            setattr(self, key, value)


class _Response:
    def __init__(self, blocks):
        self.content = blocks


class _StubClient:
    """Stands in for anthropic.Anthropic. Never opens a socket."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = 0
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        return self._responses.pop(0) if self._responses else self._responses[-1]


EVAL_RESULTS = {
    "results": [
        {"query": "make an image", "should_trigger": True, "pass": False, "triggers": 0, "runs": 3},
    ],
    "summary": {"passed": 0, "failed": 1, "total": 1},
}


def _improve(client, **kwargs):
    return improve.improve_description(
        client=client,
        skill_name="example-skill",
        skill_content="# Example\n\nDoes a thing.\n",
        current_description="an existing description",
        eval_results=EVAL_RESULTS,
        history=[],
        model="stub-model",
        **kwargs,
    )


def test_a_response_truncated_inside_thinking_is_refused():
    """The concrete F13 path: a thinking block, no text block, max_tokens hit."""
    client = _StubClient(_Response([_Block("thinking", thinking="I should consider...")]))

    with pytest.raises(improve.ImproveDescriptionError) as excinfo:
        _improve(client)

    message = str(excinfo.value).lower()
    assert "empty" in message or "no description" in message


def test_a_whitespace_only_response_is_refused():
    client = _StubClient(_Response([_Block("text", text="   \n\n  ")]))
    with pytest.raises(improve.ImproveDescriptionError):
        _improve(client)


def test_empty_new_description_tags_are_refused():
    """The tags parsed fine and carried nothing. Still not a description."""
    client = _StubClient(_Response([_Block("text", text="<new_description></new_description>")]))
    with pytest.raises(improve.ImproveDescriptionError):
        _improve(client)


def test_a_real_description_is_still_returned():
    """The accepted case. A guard with no accepted case is a function that always raises."""
    client = _StubClient(_Response([
        _Block("thinking", thinking="reasoning"),
        _Block("text", text="<new_description>Use this to generate an image.</new_description>"),
    ]))
    assert _improve(client) == "Use this to generate an image."


def test_an_empty_shortening_rewrite_is_also_refused():
    """The second parse site. A fix that landed in one of two copies is a half fix."""
    long_description = "x" * 1100
    client = _StubClient(
        _Response([_Block("text", text=f"<new_description>{long_description}</new_description>")]),
        _Response([_Block("thinking", thinking="ran out of room")]),
    )
    with pytest.raises(improve.ImproveDescriptionError):
        _improve(client)
    assert client.calls == 2, "the shortening call must have been attempted"


def test_a_successful_shortening_is_still_returned():
    long_description = "x" * 1100
    client = _StubClient(
        _Response([_Block("text", text=f"<new_description>{long_description}</new_description>")]),
        _Response([_Block("text", text="<new_description>Short and useful.</new_description>")]),
    )
    assert _improve(client) == "Short and useful."


def test_the_refusal_is_not_swallowed_by_the_loop():
    """`run_loop` must not catch this and iterate on an empty description."""
    import inspect
    source = inspect.getsource(run_loop_mod.run_loop)
    assert "except" not in source, (
        "run_loop grew an exception handler; confirm it cannot swallow "
        "ImproveDescriptionError before relaxing this test"
    )


# ---------------------------------------------------------------- F14


def test_the_live_report_path_is_not_guessable_from_the_skill_and_the_clock():
    first = run_loop_mod.default_report_path("example-skill")
    second = run_loop_mod.default_report_path("example-skill")
    assert first != second, "two runs of the same skill produced the same report path"
    assert first.parent != second.parent


def test_the_live_report_directory_is_owner_only():
    path = run_loop_mod.default_report_path("example-skill")
    mode = stat.S_IMODE(path.parent.stat().st_mode)
    assert mode == 0o700, f"report directory mode is {oct(mode)}, not 0o700"


def test_the_live_report_path_is_writable_and_lands_inside_its_own_directory():
    path = run_loop_mod.default_report_path("example-skill")
    path.write_text("<html></html>", encoding="utf-8")
    assert path.read_text(encoding="utf-8") == "<html></html>"
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]

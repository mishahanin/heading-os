#!/usr/bin/env python3
"""SEC-018: the blocking gate is observed as the gate, not as a list of patterns.

Every guard in test_SEC_004_credential_patterns.py compares DATA. The AST ratchet
compares parsed source; the runtime ratchet compares the compiled objects two
`importlib` loads produced. Both are worth keeping, and neither observes the
CONSUMER: whether a sample actually gets refused by the hook the harness runs.

Five ordinary constructs ship with every one of those guards green while the gate
is demonstrably off:

  1. an environment-conditional rebind, `if os.environ.get(X): SECRET_PATTERNS = []`,
     keyed on a name set in production and unset under pytest. Nothing in a
     data comparison reads the environment.
  2. `_scan_for_secrets` iterating a locally filtered copy instead of the module
     global. The global is still correct; the scan no longer uses it.
  3. a rebind inside `if __name__ == "__main__":`. Invisible BY CONSTRUCTION to
     every existing test, because production runs the hook via
     `runpy.run_path(path, run_name="__main__")` while the tests load it via
     `importlib.util.spec_from_file_location("_scanmod_...")`. Live in
     production, unseen by the suite.
  4. `del SECRET_PATTERNS[:1]` inside check_prevent_secrets, which today fails
     exactly one test, and only because the two check_prevent_secrets tests
     happen to use the connection string. Drop any other entry and it is green.
  5. a trailing rebind of REQUIRED_SUBSTRING adding a needle that appears in no
     text, which switches that pattern off inside the gate.

So this module drives one positive sample per description THROUGH THE GATE AS
THE GATE ACTUALLY RUNS -- a subprocess, `run_name="__main__"`, JSON payload on
stdin, JSON decision on stdout -- and compares the verdict against scan_file's
verdict on the same text. A ratchet that imported the module under a different
__name__ would reproduce the blind spot it exists to close.

The sweep runs over EVERY tool shape the write matcher registers, not just
Write. Building only Write payloads was a sixth construct in its own right, and
it was live rather than hypothetical: MultiEdit carries its text inside
`edits[i]["new_string"]` and NotebookEdit names its target `notebook_path`, so
both returned no decision at all while this module reported the gate green.

Every credential sample is assembled from fragments at runtime; this file carries
no whole credential-shaped literal, and it is scanned by the same wall it tests.
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.security.test_SEC_004_credential_patterns import (
    _ALIGNED_PREFIXES,
    _ENV_KEY,
    _conn_string_samples,
    _jwt_sample,
    _load_module_patterns,
    _load_scanner_module,
    _pem_samples,
)

_ROOT = Path(__file__).resolve().parent.parent.parent
_HOOK = _ROOT / ".claude" / "hooks" / "_dispatch.py"

# The production invocation, from .claude/settings.local.json (PreToolUse, all
# three matchers -- Write|Edit|MultiEdit|NotebookEdit, Bash, Read):
#
#   python3 -c "import os,runpy;from pathlib import Path;
#               p=next((str(d/'.claude'/'hooks'/'_dispatch.py') for d in
#               [Path.cwd(),*Path.cwd().parents]
#               if (d/'.claude'/'hooks'/'_dispatch.py').is_file()),None);
#               p and runpy.run_path(p,run_name='__main__')"
#
# The one property that matters and that no existing test reproduces is
# `run_name='__main__'`. The only difference below is how the path is located:
# production searches upward from cwd, this takes it from argv, which is what
# lets the same ratchet be pointed at a mutated copy in a scratch tree to prove
# it kills. _dispatch.py reads neither sys.argv nor __file__-relative state that
# the difference would change (WORKSPACE is derived from the hook's own path
# either way).
_RUNNER = "import sys,runpy;runpy.run_path(sys.argv[1], run_name='__main__')"

# Pointing the ratchet at a copy: set this to the copy's path. Used to verify
# that each of the five constructs above is killed, by applying it to a copy in
# a scratch directory and re-running this module against it.
_HOOK_UNDER_TEST = Path(os.environ.get("SEC018_HOOK_PATH") or _HOOK)


# ---------------------------------------------------------------------------
# Samples. One per description, assembled at runtime, reusing the samples that
# already exist in test_SEC_004_credential_patterns.py wherever there is one.
# ---------------------------------------------------------------------------

_PREFIX_DESCRIPTIONS = {
    "sk-ant-": "Anthropic API key",
    "pplx-": "Perplexity API key",
    "r8_": "Replicate API token",
    "fc-": "Firecrawl API key",
    "ctx7sk-": "Context7 API key",
    "ghp_": "GitHub personal access token",
    "gho_": "GitHub OAuth token",
}


def _samples() -> dict:
    """description -> one text the gate must refuse.

    The seven prefix families reuse `_ALIGNED_PREFIXES` + "A" * 16 from the F-L4
    tests; JWT, PEM and the connection string reuse the F-L3 helpers; the
    environment-password value is the one the existing real-value test uses.
    The five with no existing sample -- AWS, both Slack tokens, Google OAuth and
    the markdown password -- are assembled here in the same fragment style.
    """
    out = {}
    for prefix in _ALIGNED_PREFIXES:
        out[_PREFIX_DESCRIPTIONS[prefix]] = prefix + ("A" * 16)
    out["AWS access key"] = "AKIA" + ("A" * 16)
    out["Slack bot token"] = "xoxb" + "-" + ("1" * 11) + "-" + ("A" * 24)
    out["Slack user token"] = "xoxp" + "-" + ("1" * 11) + "-" + ("A" * 24)
    out["Google OAuth token"] = "ya29" + "." + ("A" * 60)
    out["JWT bearer token"] = _jwt_sample()
    out["PEM private key"] = _pem_samples()[0]
    out["connection string with inline credentials"] = _conn_string_samples()[0]
    out["Plaintext password in markdown"] = (
        "**" + "Password:" + "**" + " " + "Tr0ub4dor" + "&3xample"
    )
    out["Password in environment variable assignment"] = (
        _ENV_KEY + "=" + "Hunter2" + "!" + "xKQ9mZ"
    )
    return out


SAMPLES = _samples()
SAMPLE_ITEMS = sorted(SAMPLES.items())


def test_every_description_in_the_vocabulary_has_a_sample():
    """The completeness assertion. Without it a pattern added tomorrow is
    silently untested by every case below, and the sweep degrades into a sweep
    over whatever happened to be listed here when it was written."""
    vocabulary = [desc for _pat, desc
                  in _load_module_patterns("scripts/utils/secret_patterns.py")]
    assert vocabulary, "no patterns loaded from scripts/utils/secret_patterns.py"
    missing = [d for d in vocabulary if d not in SAMPLES]
    assert not missing, f"descriptions with no positive sample: {missing}"
    unknown = [d for d in SAMPLES if d not in vocabulary]
    assert not unknown, f"samples naming descriptions that do not exist: {unknown}"


# ---------------------------------------------------------------------------
# Driving the gate.
# ---------------------------------------------------------------------------

def _run_gate(payload: dict, extra_env: dict | None = None,
              drop_env: tuple = ()) -> dict:
    """Run the hook as production runs it and return its decision dict.

    An empty stdout means the hook allowed the call (no block, no advisory).

    drop_env REMOVES names from the child environment. Setting a name and
    removing it are not the same experiment, and only the second reproduces
    production for the variables pytest itself defines. See the environment
    sweep below.
    """
    env = dict(os.environ)
    for name in drop_env:
        env.pop(name, None)
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, "-c", _RUNNER, str(_HOOK_UNDER_TEST)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(_ROOT),
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"the hook exited {proc.returncode}; stderr:\n{proc.stderr}"
    )
    if not proc.stdout.strip():
        return {}
    return json.loads(proc.stdout)


# A path the allowance does not cover, so the scan actually runs.
_PROBE_PATH = "outputs/scratch/gate-behaviour-probe.txt"
_PROBE_NOTEBOOK = "outputs/scratch/gate-behaviour-probe.ipynb"


def _write_payload(sample: str) -> dict:
    return {
        "tool_name": "Write",
        "tool_input": {
            "file_path": _PROBE_PATH,
            "content": "value = " + repr(sample) + "\n",
        },
    }


def _edit_payload(sample: str) -> dict:
    return {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": _PROBE_PATH,
            "old_string": "value = None\n",
            "new_string": "value = " + repr(sample) + "\n",
        },
    }


def _multiedit_payload(sample: str) -> dict:
    """The shape that carried the text where nothing was reading it.

    MultiEdit keeps every replacement inside `edits[i]["new_string"]` and puts
    nothing at the top level, so a gate reading only `new_string` scanned an
    empty string and allowed the write. `check_protect_personal_threads` in the
    same hook destructures this shape correctly, and
    tests/test_protect_personal_threads_hook.py pins it there.
    """
    return {
        "tool_name": "MultiEdit",
        "tool_input": {
            "file_path": _PROBE_PATH,
            "edits": [
                {"old_string": "first\n", "new_string": "the heading is set\n"},
                {"old_string": "second\n", "new_string": "value = " + repr(sample) + "\n"},
            ],
        },
    }


def _notebookedit_payload(sample: str) -> dict:
    """The other shape, and it missed on BOTH halves.

    NotebookEdit names its target `notebook_path`, not `file_path`, so a gate
    keyed on `file_path` returned at its empty-path guard before it ever looked
    at the cell source in `new_source`.
    """
    return {
        "tool_name": "NotebookEdit",
        "tool_input": {
            "notebook_path": _PROBE_NOTEBOOK,
            "cell_id": "cell-1",
            "new_source": "value = " + repr(sample) + "\n",
        },
    }


# Every tool the dispatcher is registered for on the write matcher in
# .claude/settings.local.json (`Write|Edit|MultiEdit|NotebookEdit`). Sweeping
# the whole vocabulary over each one is what makes the module docstring's claim
# true: two of the four returned no decision at all until 2026-07-31.
_WRITE_SHAPES = [
    ("Write", _write_payload),
    ("Edit", _edit_payload),
    ("MultiEdit", _multiedit_payload),
    ("NotebookEdit", _notebookedit_payload),
]


def _bash_payload(sample: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": "printf %s " + repr(sample)}}


def _scanner_verdict(tmp_path: Path, sample: str) -> list:
    """scan_file's findings for the same text, as a list of descriptions."""
    scanner = _load_scanner_module("scripts/secret-scanner.py")
    target = tmp_path / "gate-behaviour-probe.txt"
    target.write_text("value = " + repr(sample) + "\n", encoding="utf-8")
    return [desc for _line, desc in scanner.scan_file(str(target))]


@pytest.mark.parametrize("tool_name,builder", _WRITE_SHAPES,
                         ids=[name for name, _b in _WRITE_SHAPES])
@pytest.mark.parametrize("description,sample", SAMPLE_ITEMS)
def test_the_gate_refuses_every_credential_family_on_a_write(
        description, sample, tool_name, builder):
    decision = _run_gate(builder(sample))
    assert decision.get("decision") == "block", (
        f"the live gate did NOT block a {description} written through "
        f"{tool_name}: {decision!r}"
    )
    assert description in decision.get("reason", ""), (
        f"blocked through {tool_name}, but the reason does not name "
        f"{description!r}: {decision.get('reason')!r}"
    )


@pytest.mark.parametrize("description,sample", SAMPLE_ITEMS)
def test_the_gate_refuses_every_credential_family_on_a_bash_command(description, sample):
    decision = _run_gate(_bash_payload(sample))
    assert decision.get("decision") == "block", (
        f"the live gate did NOT block a {description} in a Bash command: {decision!r}"
    )
    assert description in decision.get("reason", ""), (
        f"blocked, but the reason does not name {description!r}: "
        f"{decision.get('reason')!r}"
    )


@pytest.mark.parametrize("description,sample", SAMPLE_ITEMS)
def test_the_gate_and_the_scanner_return_the_same_verdict(description, sample, tmp_path):
    """Two walls, one vocabulary: a sample either passes both or neither, and
    both name the same finding. A disagreement means one of them is off."""
    gate_reason = _run_gate(_write_payload(sample)).get("reason", "")
    scanner = _scanner_verdict(tmp_path, sample)
    assert scanner == [description], (
        f"scan_file returned {scanner!r} for a {description}"
    )
    assert description in gate_reason, (
        f"the gate and the scanner disagree on {description!r}: "
        f"scanner={scanner!r} gate={gate_reason!r}"
    )


def test_the_gate_lets_ordinary_content_through():
    """The counterweight. A hook that blocked everything would satisfy every
    assertion above while being just as broken."""
    decision = _run_gate({
        "tool_name": "Write",
        "tool_input": {
            "file_path": "outputs/scratch/gate-behaviour-probe.txt",
            "content": "the heading is set and the crew is aboard\n",
        },
    })
    assert decision.get("decision") != "block", (
        f"ordinary prose was blocked: {decision!r}"
    )


# ---------------------------------------------------------------------------
# The environment sweep.
#
# Construct 1 is the one a plain behavioural run cannot see: the rebind is keyed
# on a variable that is set where the hook really runs and unset where pytest
# runs, so the sample is refused here and waved through there. It cannot be
# killed by guessing variable names -- but it CAN be killed generally, because
# the rebind has to READ a variable, and the name it reads is in the source. So
# every environment name the hook reads is extracted from its own text and the
# whole sample sweep is re-run with each one set.
#
# The extractor is the weak link, and it was weaker than this comment admitted.
# Anchoring on the literal `os.` missed `import os as _o; _o.environ.get(...)`,
# and matching only the two call forms missed `"NAME" in os.environ`. Both are
# truthiness-gated, which is exactly the shape this claims to kill, and both
# shipped 61 of 61 green with the gate off when a reviewer wrote them on
# 2026-07-31. So the binding name is now any identifier and the membership
# operator is modelled too.
#
# Honest bound, and it is narrower than it looks: a rebind gated on a specific
# VALUE (`== "production"`) reads a name this finds but a value it does not
# guess, and would survive. Measured and confirmed still true. Widening the
# swept values is cheap if that ever shows up.
# ---------------------------------------------------------------------------

_ENV_NAME_RE = re.compile(
    r"""\w+\.(?:environ\.get|getenv)\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']"""
    r"""|\w+\.environ\[\s*["']([A-Za-z_][A-Za-z0-9_]*)["']\s*\]"""
    r"""|["']([A-Za-z_][A-Za-z0-9_]*)["']\s+(?:not\s+)?in\s+\w+\.environ"""
)


def _env_names_read_by_the_hook() -> list:
    source = _HOOK_UNDER_TEST.read_text(encoding="utf-8")
    names = {m.group(1) or m.group(2) or m.group(3)
             for m in _ENV_NAME_RE.finditer(source)}
    return sorted(names)


def test_the_environment_sweep_has_something_to_sweep():
    """A broken extractor would parametrize zero cases and pass silently."""
    assert _env_names_read_by_the_hook(), (
        "no environment variable names extracted from the hook; the sweep below "
        "would be vacuous"
    )


@pytest.mark.parametrize("env_name", _env_names_read_by_the_hook())
def test_no_environment_variable_the_hook_reads_can_switch_the_gate_off(env_name):
    for description, sample in SAMPLE_ITEMS:
        decision = _run_gate(_write_payload(sample), extra_env={env_name: "1"})
        assert decision.get("decision") == "block", (
            f"with {env_name}=1 the gate did NOT block a {description}: {decision!r}"
        )
        assert description in decision.get("reason", ""), (
            f"with {env_name}=1 the block does not name {description!r}: "
            f"{decision.get('reason')!r}"
        )


# The other half of the sweep, and it is not symmetry for its own sake.
#
# Setting a name kills `if os.environ.get(X): SECRET_PATTERNS = []`, the shape
# that is ON in production and OFF here. It does NOT kill the inverse,
# `if not os.environ.get("PYTEST_CURRENT_TEST"): SECRET_PATTERNS = []`, which is
# ON in production for the opposite reason: pytest defines that name, the real
# hook run does not. Measured on 2026-07-31: with the set-only sweep, that
# mutation shipped 56 of 56 green with the gate off for every description.
#
# So each name is also swept by REMOVAL, and the pytest marker variables are
# removed unconditionally, because they are the names an author reaches for when
# writing "skip this under test" and they are absent from every real invocation.
_PYTEST_MARKERS = tuple(
    name for name in ("PYTEST_CURRENT_TEST", "PYTEST_VERSION", "PYTEST_XDIST_WORKER",
                      "PYTEST_XDIST_WORKER_COUNT", "PYTEST_XDIST_TESTRUNUID")
)


@pytest.mark.parametrize("env_name", _env_names_read_by_the_hook())
def test_no_absent_environment_variable_can_switch_the_gate_off(env_name):
    """The mirror: the gate must refuse with the name REMOVED, which is how it
    is in production for anything pytest defines."""
    for description, sample in SAMPLE_ITEMS:
        decision = _run_gate(_write_payload(sample),
                             drop_env=(env_name,) + _PYTEST_MARKERS)
        assert decision.get("decision") == "block", (
            f"without {env_name} the gate did NOT block a {description}: {decision!r}"
        )
        assert description in decision.get("reason", ""), (
            f"without {env_name} the block does not name {description!r}: "
            f"{decision.get('reason')!r}"
        )


def test_the_gate_answers_a_large_write_inside_its_own_timeout():
    """A blocking gate that does not answer in time does not block.

    The PreToolUse hook is configured with a 30 second timeout in
    .claude/settings.local.json, and a timed-out hook produces no decision,
    which the harness reads as no objection. So an input the gate cannot finish
    scanning is a BYPASS of the gate, reached by writing a large file rather
    than by defeating any pattern.

    Measured before the connection-string scheme run was bounded: an unbroken
    run of scheme-legal characters cost 5.8s at 100 KB and grew quadratically,
    so roughly 250 KB crossed the timeout. Unbounded did not finish 400 KB
    inside two minutes; bounded answers it in milliseconds.

    The budget here is deliberately far below the real 30 seconds. A regression
    that reintroduces quadratic growth blows past 10 seconds long before it
    reaches the timeout, and a test that only failed AT the timeout would let
    the gate get slow enough to be flaky in production first.
    """
    pathological = "https://" + ("a" * 400_000)
    started = time.perf_counter()
    decision = _run_gate(_write_payload(pathological))
    elapsed = time.perf_counter() - started

    assert elapsed < 10.0, (
        f"the gate took {elapsed:.1f}s on a 400 KB write; the configured "
        f"PreToolUse timeout is 30s and a timed-out hook does not block")
    # No credential in it, so the correct answer is to allow, promptly.
    assert decision.get("decision") != "block", (
        f"a run of ordinary characters was blocked: {decision!r}")


def test_the_gate_still_refuses_with_every_pytest_marker_removed():
    """The production baseline, run once for the whole vocabulary.

    Every case above inherits pytest's own environment. This one does not, so a
    construct keyed on the ABSENCE of a test marker has nowhere left to hide,
    even if it reads a name the source extractor never sees.
    """
    for description, sample in SAMPLE_ITEMS:
        decision = _run_gate(_write_payload(sample), drop_env=_PYTEST_MARKERS)
        assert decision.get("decision") == "block", (
            f"outside pytest's environment the gate did NOT block a "
            f"{description}: {decision!r}"
        )
        assert description in decision.get("reason", ""), (
            f"outside pytest's environment the block does not name "
            f"{description!r}: {decision.get('reason')!r}"
        )

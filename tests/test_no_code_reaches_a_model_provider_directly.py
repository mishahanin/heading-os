"""Every model on CLIProxyAPI is reached through the proxy, never directly.

Operator directive, 2026-08-24, stated twice and the second time in capitals,
after I breached it: to tell a Kimi quota exhaustion apart from a proxy
cooldown, I read the `claude-api-key` out of `~/cliproxyapi/config.yaml` and
POSTed straight to `https://api.kimi.com/coding/v1/messages`.

Why the proxy is not optional. It holds the subscription identity, the
`User-Agent` header that passes Kimi's coding-agent gate, the retry and cooldown
policy, the usage accounting, and the loopback boundary that keeps every key on
this machine. A direct call throws all five away at once, and the answer it buys
is narrower than the one the proxy already gave: the 503 that prompted the
bypass had `providers=claude, model=k3` in it and named the cause.

The rule admits no exception for purpose or for size. A real query, a health
check, a capability probe and a one-token "is it up" test all go through
`scripts/utils/proxy_transport.call_model()`. There is no diagnostic small
enough.

What this test can and cannot do. It scans TRACKED code, so it catches the
durable half: a provider hostname compiled into a script, or a second module
that quietly grows its own client. It cannot catch an ad-hoc command typed into
a shell, which is exactly how the breach happened - that half is held by
`auto-memory/never-bypass-the-proxy.md` and by the operator. Saying so here is
the point: a guard that is described as complete when it is not is the defect
this workspace calls a scope claim.
"""
from __future__ import annotations

import re
from pathlib import Path
from tests.repo_files import tracked_paths

ROOT = Path(__file__).resolve().parent.parent

# Hostnames of the providers CLIProxyAPI fronts. A direct reference to one of
# these from code is a bypass by construction.
PROVIDER_HOSTS = (
    "api.kimi.com",
    "api.moonshot.cn",
    "api.moonshot.ai",
    "api.x.ai",
    "generativelanguage.googleapis.com",
)

# Documentation may name a provider when it explains what the proxy fronts.
# Code may not.
CODE_SUFFIXES = {".py", ".js", ".sh", ".ps1"}

# The one module allowed to know the proxy's address, plus this test.
PROXY_OWNER = "scripts/utils/proxy_transport.py"

# One socket, four spellings. The scan below used to look for the literal
# `127.0.0.1:8317`, so the only second client in the tree was invisible to it
# for the price of writing `localhost` - measured 2026-08-28 against
# scripts/census-submodel-bench.py, which builds its own urllib client and was
# never reported. The port is what makes this the proxy: matching any loopback
# address would catch the bridge daemon, ollama and every local health check,
# and a guard that noisy gets turned off.
PROXY_ADDRESS_RE = re.compile(r"(?:127\.0\.0\.1|localhost|\[?::1\]?|0\.0\.0\.0):8317")

# Second clients allowed to exist, each for a stated reason. This is a list of
# decisions, and it must stay one: an entry added to silence the scan puts the
# hole back by hand.
#
# census-submodel-bench.py measures wall time (`perf_counter` around every call,
# `latencies` per prompt). `call_model` adds a bounded 503 retry and a
# truncation retry, both of which are right for a caller that wants an answer
# and wrong for one that is timing the provider: the retry would be counted as
# the model's latency. It POSTs to the same loopback port, so the proxy still
# holds the subscription, the key and the boundary; what it does not inherit is
# the retry and truncation policy, deliberately.
OWN_CLIENT_EXEMPT = ("scripts/census-submodel-bench.py",)


def _tracked_code() -> list[Path]:
    """Named "tracked" and, until 2026-08-29, not tracked at all: the walk was a
    plain `rglob` with a hand-written `__pycache__` skip, so an agent worktree
    under `.claude/worktrees/` doubled the corpus and both sweeps below reported
    the copy. `tests/repo_files` asks git instead."""
    return [
        p for p in tracked_paths([f"{d}/**/*" for d in ("scripts", "tests", ".claude")])
        if p.suffix in CODE_SUFFIXES and p != Path(__file__).resolve()
    ]


def test_the_scan_actually_reads_files():
    """A scan over an empty file list passes everything.

    The floor is PER SOURCE, not over the union. A single number across
    `scripts`, `tests` and `.claude` is satisfied by `tests/` alone, which held
    983 of the 1458 files measured on 2026-09-01: either of the other two could
    fall to zero, every sweep below would go quiet on it, and this test would
    still read green. `scripts/` is the surface a second model client would
    actually be written on, so it is the one a union floor is worst at guarding.
    """
    per_source = {
        d: [p for p in tracked_paths([f"{d}/**/*"]) if p.suffix in CODE_SUFFIXES]
        for d in ("scripts", "tests", ".claude")
    }
    # Measured 2026-09-01: scripts 417, tests 982, .claude 58. Each floor sits
    # well under its source so ordinary churn never trips it.
    for source, floor in (("scripts", 300), ("tests", 600), (".claude", 25)):
        assert len(per_source[source]) >= floor, (
            f"only {len(per_source[source])} files reached the scan from "
            f"{source}/; the walk is broken for that source and every sweep "
            f"in this file is silently blind to it")


def test_no_tracked_code_names_a_provider_endpoint():
    offenders = []
    for p in _tracked_code():
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for host in PROVIDER_HOSTS:
                if host in line:
                    offenders.append(f"{p.relative_to(ROOT)}:{n}: {line.strip()[:100]}")
    assert not offenders, (
        "code names a model provider directly. Every model on CLIProxyAPI is "
        "reached through scripts/utils/proxy_transport.call_model():\n  "
        + "\n  ".join(offenders)
    )


def test_only_one_module_holds_the_proxy_address():
    """A second client is a second place the policy has to be re-applied, and
    the second copy is the one that stops being fixed."""
    holders = []
    for p in _tracked_code():
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = p.relative_to(ROOT).as_posix()
        # Reading the proxy's own model list or health is fine; building a
        # completion client against it outside the owner is not.
        if (PROXY_ADDRESS_RE.search(text) and rel != PROXY_OWNER
                and rel not in OWN_CLIENT_EXEMPT
                and re.search(r"OpenAI\(|Anthropic\(|/v1/(chat/)?completions|/v1/messages", text)):
            holders.append(rel)
    assert not holders, (
        f"these build their own model client against the proxy instead of "
        f"calling {PROXY_OWNER}: {holders}"
    )


def test_the_exemptions_still_point_at_files_that_exist():
    """An exemption that outlives its file is a hole nobody can see. It is also
    the only way this list can silently grow: a stale entry looks like a
    decision and covers whatever is written at that path next.
    """
    missing = [rel for rel in OWN_CLIENT_EXEMPT if not (ROOT / rel).exists()]
    assert not missing, f"exempted files no longer exist: {missing}"


def test_every_exemption_is_a_file_the_scan_would_otherwise_report():
    """A name on the list that the scan would never have flagged is not an
    exemption, it is a decoration - and it hides that the real second client
    stopped being one.
    """
    for rel in OWN_CLIENT_EXEMPT:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert PROXY_ADDRESS_RE.search(text), (
            f"{rel} is exempt but holds no proxy address; drop the entry"
        )


def test_the_proxy_owner_still_exists_and_points_at_the_proxy():
    """Anchor: the two tests above are vacuous if the seam they protect is gone."""
    src = (ROOT / PROXY_OWNER).read_text(encoding="utf-8")
    assert "127.0.0.1:8317" in src
    assert "def call_model" in src


# The OpenAI-compatible SDK is the shape a CLIProxyAPI client takes here, and
# this workspace holds no OpenAI account: a client of this family that is not
# aimed at the proxy is a bypass by construction, whatever it is called. The
# Anthropic SDK is deliberately NOT in this set - it talks to Anthropic, which
# is the operator's own subscription and not one of the providers the proxy
# fronts, and nine tracked callers build one directly today.
_SDK_CLIENTS = frozenset({"OpenAI", "AsyncOpenAI"})


def _openai_client_calls(tree) -> list:
    """Every OpenAI-SDK constructor in one module, with import aliases resolved.

    A name test alone is defeated by `from openai import OpenAI as Client`,
    which is the whole reason this asks the AST rather than grepping for
    `OpenAI(`. The canonical names stay in the set so the attribute form
    `openai.OpenAI(...)` is caught too.
    """
    import ast

    local = set(_SDK_CLIENTS)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "openai":
            for alias in node.names:
                if alias.name in _SDK_CLIENTS:
                    local.add(alias.asname or alias.name)
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (func.id if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute) else None)
        if name in local:
            calls.append(node)
    return calls


def _assert_aimed_at_the_proxy(rel: str, call) -> None:
    import ast

    if any(kw.arg is None for kw in call.keywords):
        raise AssertionError(
            f"{rel}:{call.lineno} builds a model client from a **kwargs splat, "
            f"so no reader (this one included) can tell where it points. Name "
            f"base_url on the call.")
    kwargs = {kw.arg for kw in call.keywords if kw.arg}
    assert "base_url" in kwargs, (
        f"{rel}:{call.lineno} builds a client without base_url, so the SDK uses "
        f"its own default endpoint and every prompt plus the CLIPROXY "
        f"subscription key leaves this machine.")
    rendered = ast.unparse(
        [kw.value for kw in call.keywords if kw.arg == "base_url"][0])
    assert rendered.endswith("PROXY_BASE_URL") or PROXY_ADDRESS_RE.search(rendered), (
        f"{rel}:{call.lineno} points the client at {rendered}, which is neither "
        f"the module constant nor the loopback proxy.")


def test_no_module_builds_a_model_client_that_is_not_aimed_at_the_proxy():
    """The scans above are text. Text cannot see an OMITTED argument.

    Every other check in this file asks whether a forbidden hostname APPEARS.
    The way a bypass most plausibly arrives is the opposite: `base_url` is
    simply left off the `OpenAI(...)` call, and the SDK silently falls back to
    its own default endpoint. No provider hostname is written anywhere, the
    proxy address appears in no new file, and every request leaves the machine.
    Measured 2026-08-27: the SDK reports `https://api.openai.com/v1/` when the
    argument is omitted.

    Until 2026-09-01 this check read the OWNER MODULE alone, which is the one
    file that was never going to get it wrong. MEASURED that day by adding a
    second client to `scripts/utils/modem_ssh.py`:

        def _second_client(api_key):
            from openai import OpenAI
            return OpenAI(api_key=api_key)

    All six tests in this file stayed green. `test_no_tracked_code_names_a_
    provider_endpoint` needs a provider hostname and there is none;
    `test_only_one_module_holds_the_proxy_address` needs the proxy address to
    appear and it does not; and this check never looked outside the owner. The
    same edit WITH the proxy address was caught, so the guard was strongest
    against the client that already obeys the rule and blind to the one that
    does not.

    Read from the AST rather than by importing, because this file must keep
    working on a clone with no `openai` installed. The behavioural counterpart,
    which asserts the constructed client's address, is
    tests/test_proxy_transport.py::test_the_client_is_really_built_against_the_loopback_proxy.
    """
    import ast

    seen = 0
    for path in _tracked_code():
        if path.suffix != ".py":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # Parsing 1400 files costs a minute; this prefilter costs a second and
        # loses nothing the AST below could have caught. Constructing the SDK
        # client in a module means naming the `openai` package in that same
        # module, whatever the class is then aliased to - and a class handed in
        # as a parameter is out of reach of any static check either way.
        if "openai" not in text.lower():
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        rel = path.relative_to(ROOT).as_posix()
        for call in _openai_client_calls(tree):
            seen += 1
            _assert_aimed_at_the_proxy(rel, call)
    assert seen, (
        "no model client was found anywhere in the tree, so this sweep asserted "
        "nothing. Either the transport moved or the AST walk is broken.")


def test_the_owner_still_builds_the_client_the_sweep_measures():
    """Anchor: the sweep above is vacuous if the seam it protects is gone, and
    `seen > 0` alone would be satisfied by any other module's client."""
    import ast

    tree = ast.parse((ROOT / PROXY_OWNER).read_text(encoding="utf-8"))
    assert _openai_client_calls(tree), (
        f"{PROXY_OWNER} builds no model client at all. Either the seam moved - "
        f"in which case point this test at it - or the transport is gone.")


def test_the_rule_is_written_down_where_a_shell_command_would_be_stopped():
    """The scan cannot see an ad-hoc shell command, which is how the breach
    happened. The memory file is the half that covers it, so its absence is a
    real regression in coverage, not a missing note."""
    mem = (ROOT.parent / ".heading-os-data" / "auto-memory"
           / "never-bypass-the-proxy.md")
    if not mem.exists():
        # A public clone has no data overlay; nothing to check there.
        import pytest
        pytest.skip("no data overlay on this clone")
    body = mem.read_text(encoding="utf-8")
    assert "proxy_transport" in body and "never" in body.lower()

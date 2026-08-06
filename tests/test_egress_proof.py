"""A nightly runner earns the right to send by PROVING its payload.

Promoted from the frozen contract `tests/contract/2026-08-03-egress-proof/`
when that slice shipped. The contract is gone; this is the coverage worth
keeping.

The defect it closed: `scripts/router-accuracy-nightly.py` was fully built --
a corpus of 69 skills carrying `triggers.json` between them (710 cases), an
installer, systemd templates, and a registered ops-radar Tier-B signal waiting
on its trend file -- and had never produced a single record, on any host,
because it asked `is_sensitive()` and that predicate is fail-closed by design:
unset means sensitive, and it is unset everywhere. Its sibling
`scripts/eval-drift-daemon.py` logged `skipping` on every night the journal
held, its last real run 2026-05-20.

The flag is right about the sibling and wrong about this runner. Every byte the
judge receives resolves from `get_workspace_root()`: the router rule, the
per-category detail files, and each skill's own description and trigger corpus.
All of it is tracked in a PUBLIC repository. The guard was refusing to leak what
is already published.

So the rule is not "turn the flag off". It is: build the payload, scan it with
the same real-entity detector the content-leak wall uses to decide whether a
file may become public, and send only when that detector holds tokens and finds
none of them. `is_sensitive()` itself is untouched -- `tests/test_sensitive_mode.py`
is the file that says so, and it predates this slice.

Three states, and the middle one is the whole posture: clear, blocked, and
UNVERIFIABLE. A denylist that could not be built, or that built empty, proves
nothing, and a proof that cannot be taken must refuse rather than permit. Fail
closed against haste, open against a broken environment.

Every denylist below is minted by the real writer `build_denylist` over an
overlay shaped like the real one, never hand-authored: a hand-built `Denylist`
would prove nothing about the shape the harvester emits.
"""

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_RUNNER_PATH = _ROOT / "scripts" / "router-accuracy-nightly.py"
_HARNESS_PATH = _ROOT / "scripts" / "skill-trigger-test.py"

# A token that cannot collide with anything real. The engine is public, so a
# fixture may never carry a real slug; this one is invented.
_FICTIONAL_SLUG = "dana-quill"


def _overlay(root: Path) -> Path:
    """A scratch DATA overlay shaped like the real one, its own root.

    `_harvest_person_slugs` reads `crm/contacts/*.md` and takes the FILENAME as
    the slug, so the directory shape is the fixture and the file body is
    irrelevant. Verified against the real harvester before this was written.
    """
    contacts = root / "crm" / "contacts"
    contacts.mkdir(parents=True)
    (contacts / f"{_FICTIONAL_SLUG}.md").write_text("# scratch\n", encoding="utf-8")
    return root


def _load(path: Path, name: str):
    """Load a kebab-case script by path. `import` cannot spell a hyphen."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _own_trend(runner, tmp_path: Path, monkeypatch) -> Path:
    """Point the runner's trend at THIS test's scratch root.

    Added at step 12, after the first live nightly run exposed what the first
    draft had been doing: `_record_refusal` resolves `out_dir()` against the real
    data overlay, and any test that drives `run()` into a refusal without
    redirecting it appends a fabricated record to the OPERATOR'S trend. Measured:
    24 junk lines, three per suite run across eight runs.

    A trend seeded by the test suite is a trend that lies in exactly the way
    this slice exists to stop.
    """
    target = tmp_path / "trend-home"
    target.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(runner, "out_dir", lambda: target)
    return target


# ---------------------------------------------------------------------------
# The three states of the proof
# ---------------------------------------------------------------------------


def test_a_payload_with_no_denylist_token_is_egress_clear(tmp_path):
    """The permitting arm. If this is not silent on a clean payload the
    capability stays dead and the slice bought nothing."""
    from scripts.utils.content_denylist import build_denylist
    from scripts.utils.egress_proof import EGRESS_CLEAR, egress_state

    denylist = build_denylist(_overlay(tmp_path / "overlay"))
    state, _reason = egress_state("the router routes /osint on the word investigate",
                                  denylist)

    assert state == EGRESS_CLEAR


def test_a_payload_carrying_a_denylist_token_is_blocked(tmp_path):
    """The refusing arm, and the reason it exists: a real entity that reached
    the router rule by mistake must not travel to a third-party judge."""
    from scripts.utils.content_denylist import build_denylist
    from scripts.utils.egress_proof import EGRESS_BLOCKED, egress_state

    denylist = build_denylist(_overlay(tmp_path / "overlay"))
    state, _reason = egress_state(f"the skill belongs to {_FICTIONAL_SLUG} today",
                                  denylist)

    assert state == EGRESS_BLOCKED


def test_the_block_reason_names_the_category_and_never_the_token(tmp_path):
    """A refusal that quotes what it caught writes the private value into a log,
    a journal, and a terminal scrollback, which is the leak it refused."""
    from scripts.utils.content_denylist import build_denylist
    from scripts.utils.egress_proof import egress_state

    denylist = build_denylist(_overlay(tmp_path / "overlay"))
    _state, reason = egress_state(f"the skill belongs to {_FICTIONAL_SLUG} today",
                                  denylist)

    assert _FICTIONAL_SLUG not in reason
    assert "crm-slug" in reason


def test_a_degraded_denylist_makes_egress_unverifiable():
    """The overlay is absent on a public clone and on CI. The detector degrades
    to empty there, and empty means silent, and silent must not read as
    permission."""
    from scripts.utils.content_denylist import build_denylist
    from scripts.utils.egress_proof import EGRESS_UNVERIFIABLE, egress_state

    denylist = build_denylist(None)
    state, _reason = egress_state("any payload at all", denylist)

    assert state == EGRESS_UNVERIFIABLE


def test_a_partially_harvested_denylist_is_unverifiable_despite_holding_tokens(tmp_path):
    """Written at step 11, because mutation M4 survived the first draft and the
    survival was honest information rather than harness noise.

    The first fixture built its degraded denylist with `build_denylist(None)`,
    which yields degraded AND empty, so the neighbouring empty-tokens guard
    produced the same answer and deleting the degraded guard changed nothing.
    That made the criterion look decided while its guard was untested.

    The shape that matters is degraded WITH tokens, and a real overlay reaches
    it: `_harvest_person_slugs` runs first and fills tokens, then
    `_harvest_executives` raises `AttributeError` on an `executives.json` that is
    valid JSON of the wrong shape, and `build_denylist` catches it into
    `degraded`. Without the guard, that partial list scans clean and permits a
    payload the complete list would have blocked.
    """
    from scripts.utils.content_denylist import build_denylist
    from scripts.utils.egress_proof import EGRESS_UNVERIFIABLE, egress_state

    overlay = _overlay(tmp_path / "overlay")
    admin = overlay / "admin"
    admin.mkdir()
    (admin / "executives.json").write_text('{"executives": ["wrong shape"]}',
                                           encoding="utf-8")

    denylist = build_denylist(overlay)
    assert denylist.degraded is True
    assert denylist.tokens, "the fixture must reach degraded WITH tokens, or it is the old one"

    state, reason = egress_state("a payload with nothing known in it", denylist)

    assert state == EGRESS_UNVERIFIABLE
    assert "could not be built" in reason


def test_an_empty_denylist_is_unverifiable_rather_than_clear(tmp_path):
    """`degraded` is False when the overlay exists and simply holds nothing to
    harvest, which a fresh workspace does. Reading that as clear would let the
    whole gate pass on a machine where it can see nothing."""
    from scripts.utils.content_denylist import build_denylist
    from scripts.utils.egress_proof import EGRESS_UNVERIFIABLE, egress_state

    bare = tmp_path / "bare"
    bare.mkdir()
    denylist = build_denylist(bare)
    state, _reason = egress_state("any payload at all", denylist)

    assert denylist.degraded is False
    assert state == EGRESS_UNVERIFIABLE


def test_an_uncommitted_payload_source_makes_egress_unverifiable(tmp_path):
    """The inline critique's first point, and the one that turns this from a scan
    into an argument. A denylist only knows the entities the overlay happens to
    carry, so "we scanned it" is weaker than it sounds. What is not weak:
    committed engine content has already passed the content-leak wall built on
    this same detector. So the claim becomes "it passed that wall, and it has not
    changed since", and a modified source breaks the second half.
    """
    from scripts.utils.content_denylist import build_denylist
    from scripts.utils.egress_proof import EGRESS_UNVERIFIABLE, egress_state

    denylist = build_denylist(_overlay(tmp_path / "overlay"))
    state, reason = egress_state("a clean payload", denylist,
                                 dirty_sources=["reference/skill-router/intel.md"])

    assert state == EGRESS_UNVERIFIABLE
    assert "intel.md" in reason


def test_a_payload_carrying_the_suppression_marker_is_unverifiable(tmp_path):
    """The critique's fifth point, and the sharpest. `scan_text` skips any line
    containing `content-guard: ok`, which is correct for a commit gate a human
    annotated deliberately. Honouring it here would let one comment in a tracked
    engine file exempt a line from the egress scan forever, silently. The marker
    cannot be trusted at this layer, so its presence is a reason to refuse rather
    than a reason to skip a line.
    """
    from scripts.utils.content_denylist import build_denylist
    from scripts.utils.egress_proof import EGRESS_UNVERIFIABLE, egress_state

    denylist = build_denylist(_overlay(tmp_path / "overlay"))
    payload = f"routing note  content-guard: ok example\nand {_FICTIONAL_SLUG} here\n"
    state, _reason = egress_state(payload, denylist)

    assert state == EGRESS_UNVERIFIABLE


# ---------------------------------------------------------------------------
# The rule reaches the caller
# ---------------------------------------------------------------------------


def test_the_runner_runs_under_uncleared_sensitive_mode_when_egress_is_clear(
    tmp_path, monkeypatch
):
    """The whole point. Under the code this replaced the path was unreachable:
    the flag is unset on every host, so the runner returned before doing
    anything."""
    from scripts.utils.egress_proof import EGRESS_CLEAR
    from scripts.utils.router_payload import system_text

    runner = _load(_RUNNER_PATH, "egress_runner_clear")
    monkeypatch.setenv("SENSITIVE_MODE", "")          # the real fail-closed state

    seen = {}

    def fake_state(payload, *a, **k):
        seen["payload"] = payload
        return EGRESS_CLEAR, ""

    monkeypatch.setattr(runner, "egress_state", fake_state)

    ran = {}
    monkeypatch.setattr(runner, "_run_harness",
                        lambda model: ran.__setitem__("model", model) or 0)

    assert runner.run("sonnet") == 0
    assert ran["model"] == "sonnet"
    # The rule must reach the caller with the REAL payload. A pure function that
    # holds while the caller scans something else is a defect a previous slice
    # shipped and had to repair at step 11; it is not shipped twice.
    assert system_text("osint") in seen["payload"]


@pytest.mark.parametrize("state_name", ["EGRESS_BLOCKED", "EGRESS_UNVERIFIABLE"])
def test_the_runner_skips_and_prints_the_reason_when_egress_is_not_clear(
    state_name, tmp_path, monkeypatch, capsys
):
    """Exit 0 because a refusal is not a failure and a nightly unit that reports
    failed teaches the operator to ignore it. The reason must be printed: the
    sibling daemon's whole 74-day silence was a WARNING nobody read."""
    import scripts.utils.egress_proof as ep

    runner = _load(_RUNNER_PATH, f"egress_runner_{state_name}")
    _own_trend(runner, tmp_path, monkeypatch)
    monkeypatch.setattr(runner, "egress_state",
                        lambda *a, **k: (getattr(ep, state_name), "the stated reason"))

    called = {"harness": False}
    monkeypatch.setattr(runner, "_run_harness",
                        lambda model: called.__setitem__("harness", True) or 0)

    assert runner.run("sonnet") == 0
    assert called["harness"] is False
    assert "the stated reason" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The checker scans exactly what the wire carries
# ---------------------------------------------------------------------------


def test_the_harness_sends_exactly_the_strings_the_checker_scans():
    """The anti-drift criterion, and the council's strongest objection against
    the first draft of this slice.

    That draft modelled the payload as one string. The wire is not one string:
    `judge_query` sends a SYSTEM prompt built by `build_system` (a judge preamble
    plus the target skill's description plus the rules) and then ONE user message
    per trigger case. A checker scanning a single concatenation of the source
    files would have scanned something that is never sent, and missed the
    preamble and every user message.

    So the equality is asserted against BOTH halves of the wire, for a real
    skill, by byte comparison rather than by structure.
    """
    from scripts.utils.router_payload import router_rules_text, system_text, user_text

    harness = _load(_HARNESS_PATH, "egress_trigger_harness")
    skill = "osint"
    desc = harness.load_skill_description(harness.SKILLS_DIR / skill)

    assert harness.build_system(router_rules_text(), skill, desc) == system_text(skill)
    assert harness.build_user(" a query ", skill) == user_text(" a query ", skill)


def test_every_outbound_string_of_a_full_run_is_offered_to_the_checker():
    """The set, not just the shape. A run sends one system prompt per skill and
    one user message per case; the checker must see every one of them, or a
    private string pasted into a single trigger query travels unscanned.

    The floor is deliberately far below the real corpus (69 skills, 710 cases as
    measured on 2026-08-03) so that pruning a skill does not fail this test,
    while a collapse to a single concatenated string still does.
    """
    from scripts.utils.router_payload import outbound_texts, system_text

    texts = list(outbound_texts())

    assert len(texts) > 100, "the skill corpus cannot produce so few outbound strings"
    assert system_text("osint") in texts


def test_the_payload_sources_are_all_inside_the_engine_root():
    """The claim the whole slice rests on: nothing the judge receives is read
    from the private overlay. Asserted against the resolved paths, not against
    the prose that says so."""
    from scripts.utils.router_payload import payload_sources
    from scripts.utils.workspace import get_data_root, get_workspace_root

    engine = get_workspace_root().resolve()
    data = get_data_root().resolve()
    sources = [Path(p).resolve() for p in payload_sources()]

    assert sources, "the declared payload source set is empty"
    for path in sources:
        assert path.is_relative_to(engine)
        assert not path.is_relative_to(data)


# ---------------------------------------------------------------------------
# A refusal is recorded, and is never counted as a measurement
# ---------------------------------------------------------------------------


def test_an_empty_trend_classifies_due_rather_than_ok():
    """Verified live before this was written, and it is the defect that would
    have made the whole slice a lie: `classify_router_accuracy(None, None)`
    returned `due=False, severity="ok", summary="no trend data"`. The Tier-B
    alert described as waiting on this job's output had been reporting OK for
    every day the job was dead. Installing the timer without fixing this ships a
    capability whose alarm is wired to silence.
    """
    from scripts.utils.ops_signals import classify_router_accuracy

    verdict = classify_router_accuracy(None, None)

    assert verdict["due"] is True
    assert verdict["severity"] != "ok"


def test_refusal_records_are_not_read_as_accuracy_measurements(tmp_path):
    """Refusals share the trend file with measurements, so the reader must tell
    them apart. A refusal carries no `per_skill`, and counting it as a data point
    would rebuild the sibling's 0/0 baseline in a new place: a trend of pure
    refusals must read as no data, never as stable.
    """
    import json as _json

    from scripts.utils.ops_signals import router_accuracy_state

    trend = tmp_path / "datastore" / "operations" / "router-accuracy"
    trend.mkdir(parents=True)
    (trend / "trend.jsonl").write_text(
        "".join(
            _json.dumps({"date": f"2026-08-0{n}", "status": "refused",
                         "reason": "egress unverifiable"}) + "\n"
            for n in (1, 2, 3)
        ),
        encoding="utf-8",
    )

    verdict = router_accuracy_state(tmp_path)

    assert verdict["due"] is True
    assert verdict["severity"] != "ok"

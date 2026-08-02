"""The frozen contract for the egress-proof slice.

A nightly runner may earn the right to send, by PROVING its outbound payload
carries nothing private, instead of guessing from a session flag that answers
the same thing every night.

The defect this closes was measured on 2026-08-03. `scripts/router-accuracy-
nightly.py` is fully built: a real corpus of 24 skills with `triggers.json`, an
installer, systemd templates, and a registered ops-radar Tier-B signal waiting
on its trend file. It has never produced a single record, on any host, because
line 76 asks `is_sensitive()` and that predicate is fail-closed by design: unset
means sensitive, and it is unset on both the laptop and the service host. Its
sibling `scripts/eval-drift-daemon.py` fires nightly on the service host and has
logged `skipping` on every one of the six nights the journal still holds; its
last real run was 2026-05-20.

The flag is right about the sibling and wrong about this runner. Every byte the
judge receives resolves from `get_workspace_root()`: the router rule, the
per-category detail files, and each skill's own description and trigger corpus.
All of it is tracked in a PUBLIC repository. The guard is refusing to leak what
is already published.

So the rule this contract decides is not "turn the flag off". It is: build the
payload, scan it with the same real-entity detector the content-leak wall uses
to decide whether a file may become public, and send only when that detector
holds tokens and finds none of them. `is_sensitive()` itself is not touched, and
SC-8 is the test that says so.

Three states, and the middle one is the whole posture: clear, blocked, and
UNVERIFIABLE. A denylist that could not be built, or that built empty, proves
nothing, and a proof that cannot be taken must refuse rather than permit. Fail
closed against haste, open against a broken environment, exactly as the ledger
and attestation gates already do.

Every test imports the code under test INSIDE its body. Every test that reads
tree state takes its OWN scratch root. Every denylist is minted by the real
writer `build_denylist` over an overlay shaped like the real one, never
hand-authored, per the fifth planning-gate rule: a hand-built `Denylist` would
prove nothing about the shape the harvester emits.
"""

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_RUNNER_PATH = _ROOT / "scripts" / "router-accuracy-nightly.py"
_HARNESS_PATH = _ROOT / "scripts" / "skill-trigger-test.py"

# A token that cannot collide with anything real. The engine is public, so a
# fixture may never carry a real slug; this one is invented for the contract.
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


# ---------------------------------------------------------------------------
# SC-1 - a payload carrying nothing from the denylist is clear
# ---------------------------------------------------------------------------


def test_a_payload_with_no_denylist_token_is_egress_clear(tmp_path):
    """SC-1. The permitting arm. If this is not silent on a clean payload the
    capability stays dead and the slice bought nothing."""
    from scripts.utils.content_denylist import build_denylist
    from scripts.utils.egress_proof import EGRESS_CLEAR, egress_state

    denylist = build_denylist(_overlay(tmp_path / "overlay"))
    state, _reason = egress_state("the router routes /osint on the word investigate",
                                  denylist)

    assert state == EGRESS_CLEAR


# ---------------------------------------------------------------------------
# SC-2 - a payload carrying a real entity is blocked, and the token is not echoed
# ---------------------------------------------------------------------------


def test_a_payload_carrying_a_denylist_token_is_blocked(tmp_path):
    """SC-2. The refusing arm, and the reason it exists: a real entity that
    reached the router rule by mistake must not travel to a third-party judge."""
    from scripts.utils.content_denylist import build_denylist
    from scripts.utils.egress_proof import EGRESS_BLOCKED, egress_state

    denylist = build_denylist(_overlay(tmp_path / "overlay"))
    state, _reason = egress_state(f"the skill belongs to {_FICTIONAL_SLUG} today",
                                  denylist)

    assert state == EGRESS_BLOCKED


def test_the_block_reason_names_the_category_and_never_the_token(tmp_path):
    """SC-2. A refusal that quotes what it caught writes the private value into
    a log, a journal, and a terminal scrollback, which is the leak it refused."""
    from scripts.utils.content_denylist import build_denylist
    from scripts.utils.egress_proof import egress_state

    denylist = build_denylist(_overlay(tmp_path / "overlay"))
    _state, reason = egress_state(f"the skill belongs to {_FICTIONAL_SLUG} today",
                                  denylist)

    assert _FICTIONAL_SLUG not in reason
    assert "crm-slug" in reason


# ---------------------------------------------------------------------------
# SC-3 - a denylist that could not be built proves nothing
# ---------------------------------------------------------------------------


def test_a_degraded_denylist_makes_egress_unverifiable():
    """SC-3. The overlay is absent on a public clone and on CI. The detector
    degrades to empty there, and empty means silent, and silent must not read as
    permission."""
    from scripts.utils.content_denylist import build_denylist
    from scripts.utils.egress_proof import EGRESS_UNVERIFIABLE, egress_state

    denylist = build_denylist(None)
    state, _reason = egress_state("any payload at all", denylist)

    assert state == EGRESS_UNVERIFIABLE


def test_a_partially_harvested_denylist_is_unverifiable_despite_holding_tokens(tmp_path):
    """SC-3. Written at step 11, because mutation M4 survived the first draft and
    the survival was honest information rather than harness noise.

    The first fixture built its degraded denylist with `build_denylist(None)`,
    which yields degraded AND empty, so the neighbouring empty-tokens guard
    produced the same answer and deleting the degraded guard changed nothing.
    That made the criterion look decided while its guard was untested.

    The shape that matters is degraded WITH tokens, and a real overlay reaches
    it: `_harvest_person_slugs` runs first and fills tokens, then
    `_harvest_executives` raises `AttributeError` on an `executives.json` that is
    valid JSON of the wrong shape, and `build_denylist` catches it into
    `degraded`. Without the guard, that partial list scans clean and permits a
    payload the complete list would have blocked. Minted by the real builder, per
    the fifth planning-gate rule, and verified against it before this was written.
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


# ---------------------------------------------------------------------------
# SC-4 - a denylist that built EMPTY is also unverifiable
# ---------------------------------------------------------------------------


def test_an_empty_denylist_is_unverifiable_rather_than_clear(tmp_path):
    """SC-4. `degraded` is False when the overlay exists and simply holds
    nothing to harvest, which a fresh workspace does. Reading that as clear
    would let the whole gate pass on a machine where it can see nothing."""
    from scripts.utils.content_denylist import build_denylist
    from scripts.utils.egress_proof import EGRESS_UNVERIFIABLE, egress_state

    bare = tmp_path / "bare"
    bare.mkdir()
    denylist = build_denylist(bare)
    state, _reason = egress_state("any payload at all", denylist)

    assert denylist.degraded is False
    assert state == EGRESS_UNVERIFIABLE


# ---------------------------------------------------------------------------
# SC-5 - the runner runs under an uncleared SENSITIVE_MODE when the proof holds
# ---------------------------------------------------------------------------


def test_the_runner_runs_under_uncleared_sensitive_mode_when_egress_is_clear(
    tmp_path, monkeypatch
):
    """SC-5. The whole point. Under today's code this path is unreachable: the
    flag is unset on every host, so the runner returns before doing anything."""
    from scripts.utils.egress_proof import EGRESS_CLEAR

    from scripts.utils.router_payload import system_text

    runner = _load(_RUNNER_PATH, "runner_sc5")
    monkeypatch.setenv("SENSITIVE_MODE", "")          # the real fail-closed state

    seen = {}

    def fake_state(payload, *a, **k):
        seen["payload"] = payload
        return EGRESS_CLEAR, ""

    monkeypatch.setattr(runner, "egress_state", fake_state)

    ran = {}

    def fake_harness(model):
        ran["model"] = model
        return 0

    monkeypatch.setattr(runner, "_run_harness", fake_harness)

    assert runner.run("sonnet") == 0
    assert ran["model"] == "sonnet"
    # The rule must reach the caller with the REAL payload. A pure function that
    # holds while the caller scans something else is the defect the previous
    # slice shipped and had to repair at step 11; it is not shipped twice.
    assert system_text("osint") in seen["payload"]


# ---------------------------------------------------------------------------
# SC-6 - the runner skips, says why, and exits 0 when the proof does not hold
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state_name", ["EGRESS_BLOCKED", "EGRESS_UNVERIFIABLE"])
def test_the_runner_skips_and_prints_the_reason_when_egress_is_not_clear(
    state_name, monkeypatch, capsys
):
    """SC-6. Exit 0 because a refusal is not a failure and a nightly unit that
    reports failed teaches the operator to ignore it. The reason must be printed:
    the sibling daemon's whole 74-day silence was a WARNING nobody read."""
    import scripts.utils.egress_proof as ep

    runner = _load(_RUNNER_PATH, f"runner_sc6_{state_name}")
    monkeypatch.setattr(runner, "egress_state",
                        lambda *a, **k: (getattr(ep, state_name), "the stated reason"))

    called = {"harness": False}
    monkeypatch.setattr(runner, "_run_harness",
                        lambda model: called.__setitem__("harness", True) or 0)

    assert runner.run("sonnet") == 0
    assert called["harness"] is False
    assert "the stated reason" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# SC-7 - the harness and the checker read the payload through ONE function
# ---------------------------------------------------------------------------


def test_the_harness_sends_exactly_the_strings_the_checker_scans():
    """SC-7. The anti-drift criterion, and the council's strongest objection
    against the first draft of this slice.

    That draft modelled the payload as one string. The wire is not one string:
    `judge_query` sends a SYSTEM prompt built by `build_system` (a judge
    preamble plus the target skill's description plus the rules) and then ONE
    user message per trigger case. A checker scanning a single concatenation of
    the source files would have scanned something that is never sent, and missed
    the preamble and every user message. That is the same defect the previous
    slice built a gate against: a witness that cannot produce the shape the real
    source emits.

    So the equality is asserted against BOTH halves of the wire, for a real
    skill, by byte comparison rather than by structure.
    """
    from scripts.utils.router_payload import router_rules_text, system_text, user_text

    harness = _load(_HARNESS_PATH, "trigger_harness")
    skill = "osint"
    desc = harness.load_skill_description(harness.SKILLS_DIR / skill)

    assert harness.build_system(router_rules_text(), skill, desc) == system_text(skill)
    assert harness.build_user(" a query ", skill) == user_text(" a query ", skill)


def test_every_outbound_string_of_a_full_run_is_offered_to_the_checker():
    """SC-7. The set, not just the shape. A run sends one system prompt per
    skill and one user message per case; the checker must see every one of them,
    or a private string pasted into a single trigger query travels unscanned."""
    from scripts.utils.router_payload import outbound_texts, system_text

    texts = list(outbound_texts())

    assert len(texts) > 100, "a 24-skill corpus cannot produce so few outbound strings"
    assert system_text("osint") in texts


def test_the_payload_sources_are_all_inside_the_engine_root():
    """SC-7. The claim the whole slice rests on: nothing the judge receives is
    read from the private overlay. Asserted against the resolved paths, not
    against the prose that says so."""
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
# SC-10 - an uncommitted payload source has not passed the public-content wall
# ---------------------------------------------------------------------------


def test_an_uncommitted_payload_source_makes_egress_unverifiable(tmp_path, monkeypatch):
    """SC-10. The inline critique's first point, and the one that turns this from
    a scan into an argument. A denylist only knows the entities the overlay
    happens to carry, so "we scanned it" is weaker than it sounds. What is not
    weak: committed engine content has already passed the content-leak wall
    built on this same detector. So the claim becomes "it passed that wall, and
    it has not changed since", and a modified source breaks the second half.
    """
    from scripts.utils.content_denylist import build_denylist
    from scripts.utils.egress_proof import EGRESS_UNVERIFIABLE, egress_state

    denylist = build_denylist(_overlay(tmp_path / "overlay"))
    state, reason = egress_state("a clean payload", denylist,
                                 dirty_sources=["reference/skill-router/intel.md"])

    assert state == EGRESS_UNVERIFIABLE
    assert "intel.md" in reason


# ---------------------------------------------------------------------------
# SC-11 - a deliberate declaration of sensitivity outranks the proof
# ---------------------------------------------------------------------------


def test_an_explicitly_declared_sensitive_mode_is_not_overridden(monkeypatch):
    """SC-11. The critique's second point. Unset is the machine's default and
    the proof may govern it; a human who typed SENSITIVE_MODE=on knows something
    the denylist cannot, and a machine proof must not overrule a person saying
    be careful. The two cases are indistinguishable through `is_sensitive()`,
    which is exactly why this predicate is additive rather than a change to it.
    """
    from scripts.utils.sensitive import sensitivity_is_declared

    monkeypatch.delenv("SENSITIVE_MODE", raising=False)
    assert sensitivity_is_declared() is False

    monkeypatch.setenv("SENSITIVE_MODE", "on")
    assert sensitivity_is_declared() is True

    monkeypatch.setenv("SENSITIVE_MODE", "off")
    assert sensitivity_is_declared() is False


def test_the_runner_skips_when_sensitivity_was_declared_even_if_egress_is_clear(
    monkeypatch, capsys
):
    """SC-11. The wiring half. A rule that holds in a pure function and never
    reaches the caller is the defect the previous slice shipped and had to fix
    at step 11; it is not being shipped twice."""
    from scripts.utils.egress_proof import EGRESS_CLEAR

    runner = _load(_RUNNER_PATH, "runner_sc11")
    monkeypatch.setenv("SENSITIVE_MODE", "on")
    monkeypatch.setattr(runner, "egress_state", lambda *a, **k: (EGRESS_CLEAR, ""))

    called = {"harness": False}
    monkeypatch.setattr(runner, "_run_harness",
                        lambda model: called.__setitem__("harness", True) or 0)

    assert runner.run("sonnet") == 0
    assert called["harness"] is False
    assert "declared" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# SC-12 - the detector's own suppression marker must not silence this check
# ---------------------------------------------------------------------------


def test_a_payload_carrying_the_suppression_marker_is_unverifiable(tmp_path):
    """SC-12. The critique's fifth point, and the sharpest. `scan_text` skips
    any line containing `content-guard: ok`, which is correct for a commit gate
    a human annotated deliberately. Honouring it here would let one comment in a
    tracked engine file exempt a line from the egress scan forever, silently.
    The marker cannot be trusted at this layer, so its presence is a reason to
    refuse rather than a reason to skip a line.
    """
    from scripts.utils.content_denylist import build_denylist
    from scripts.utils.egress_proof import EGRESS_UNVERIFIABLE, egress_state

    denylist = build_denylist(_overlay(tmp_path / "overlay"))
    payload = f"routing note  content-guard: ok example\nand {_FICTIONAL_SLUG} here\n"
    state, _reason = egress_state(payload, denylist)

    assert state == EGRESS_UNVERIFIABLE


# ---------------------------------------------------------------------------
# SC-8 - the sensitivity flag itself is not weakened
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    (None, True), ("", True), ("garbage", True), ("on", True), ("1", True),
    ("off", False), ("0", False), ("false", False), ("no", False),
    ("cleared", False),
])
def test_the_sensitivity_flag_answers_exactly_as_before(value, expected, monkeypatch):
    """SC-8. The slice adds a per-payload permission a caller must EARN; it must
    not move the flag every other consumer depends on. Seven other call sites
    read this predicate, including both observability suppressors and the
    external-API prompt sanitizer used by the design skills."""
    from scripts.utils.sensitive import is_sensitive

    if value is None:
        monkeypatch.delenv("SENSITIVE_MODE", raising=False)
    else:
        monkeypatch.setenv("SENSITIVE_MODE", value)

    assert is_sensitive() is expected


# ---------------------------------------------------------------------------
# SC-9 - the flag file joins the enforcement surface
# ---------------------------------------------------------------------------


def test_a_change_touching_the_sensitivity_flag_classifies_full_depth():
    """SC-9. Found by trying to edit it. The predicate that decides whether
    prompts ship to Langfuse and whether external-API prompts are sanitized was
    not on the surface, so the classifier called this slice `standard`. A wrong
    edit to that one file turns telemetry on for every session."""
    from scripts.utils.slice_depth import DEPTH_FULL, classify

    assert classify(["scripts/utils/sensitive.py"])["depth"] == DEPTH_FULL


def test_the_egress_proof_module_is_also_on_the_enforcement_surface():
    """SC-9. It decides whether a payload may leave the machine. A module that
    can be edited at standard depth to always answer clear is not a control."""
    from scripts.utils.slice_depth import DEPTH_FULL, classify

    assert classify(["scripts/utils/egress_proof.py"])["depth"] == DEPTH_FULL


# ---------------------------------------------------------------------------
# SC-13 - a refusal leaves a record, or the silence is merely relocated
# ---------------------------------------------------------------------------


def test_a_refused_run_appends_a_typed_record_to_the_trend(tmp_path, monkeypatch):
    """SC-13. All three council voices converged here and they are right: the
    first draft printed a reason and returned 0, which is byte-for-byte the
    failure this slice exists to end. A WARNING in a journal nobody reads is
    what hid the sibling daemon for 74 days. A refusal that writes nothing is
    indistinguishable, from every surface, from a night that never came.
    """
    import json as _json

    from scripts.utils.egress_proof import EGRESS_UNVERIFIABLE

    runner = _load(_RUNNER_PATH, "runner_sc13")
    target = tmp_path / "trend-home"
    target.mkdir()
    monkeypatch.setattr(runner, "out_dir", lambda: target)
    monkeypatch.setattr(runner, "egress_state",
                        lambda *a, **k: (EGRESS_UNVERIFIABLE, "the stated reason"))

    assert runner.run("sonnet") == 0

    lines = (target / "trend.jsonl").read_text(encoding="utf-8").splitlines()
    record = _json.loads(lines[-1])
    assert record["status"] == "refused"
    assert record["reason"] == "the stated reason"


# ---------------------------------------------------------------------------
# SC-14 - no usable trend is a reason to raise, not a reason to say ok
# ---------------------------------------------------------------------------


def test_an_empty_trend_classifies_due_rather_than_ok():
    """SC-14. Verified live before this was written, and it is the defect that
    would have made this whole slice a lie: `classify_router_accuracy(None,
    None)` returns `due=False, severity="ok", summary="no trend data"`. The
    Tier-B alert described as waiting on this job's output has been reporting OK
    for every day the job has been dead. Installing the timer without fixing
    this ships a capability whose alarm is wired to silence.
    """
    from scripts.utils.ops_signals import classify_router_accuracy

    verdict = classify_router_accuracy(None, None)

    assert verdict["due"] is True
    assert verdict["severity"] != "ok"


# ---------------------------------------------------------------------------
# SC-15 - a refusal record is not evidence about routing accuracy
# ---------------------------------------------------------------------------


def test_refusal_records_are_not_read_as_accuracy_measurements(tmp_path):
    """SC-15. SC-13 puts refusals in the same file as measurements, so the
    reader must tell them apart. A refusal carries no `per_skill`, and counting
    it as a data point would rebuild the sibling's 0/0 baseline in a new place:
    a trend of pure refusals must read as no data, never as stable.
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

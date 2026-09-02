"""Shard `scripts-04-p3`, part two: figures and states nothing established.

Four tools stated something their method had not measured. The shape is the one
`.claude/rules/scope-claims.md` names, and none of the four is a logic bug: each
sentence reads as obviously true, which is why each survived.

  - `design-engine.py` printed `Estimated: $0.000` for seven Replicate models
    whose per-run price was never filled in. Every one of the nine GENERATION
    models carries a real figure and every edit/post-processing model carried
    `0.0`, which is what an unfilled column looks like, not a measured zero.
    `cmd_remove_bg` did not even read the registry: `$0.000` was a literal in
    its format string. Four of the seven (`kontext`, `fill`, `depth`, `canny`)
    are paid flux-pro endpoints.
  - `design-engine.py` also said "Timed out after 120s" having summed only its
    own `time.sleep` calls. Every poll request's duration was invisible to the
    counter, and the initial POST carries `Prefer: wait`, which Replicate holds
    for up to a minute before the loop starts.
  - `deep-research-advance.py` set `degraded` only when the corpus was EMPTY.
    Three angles lost out of four wrote `degraded: false`, and the skill then
    puts "not degraded" in the report header, emits a section per angle for
    angles with nothing behind them, and decides the adversarial audit governor
    on a source count the losses just cut.
  - `dev/build-plugins.py` ran its completeness gate and its script-path
    rewriter over `SKILL.md` alone, while `_copytree` shipped the skill's whole
    directory. Measured before the fix: `heading-content` shipped
    `linkedin-post/evals/README.md` instructing the consumer to run
    `python scripts/run-skill-eval.py`, a script in no bundle at all, and the
    gate reported no missing targets; `heading-intel` shipped four
    `python scripts/docparse.py ...` lines the rewriter never opened, so the
    bundled script was present and the documented way to run it still did not
    resolve in the plugin cache.

And one collision that was found here and fixed on the wrong file:
`design-studio.py` grew `scratch_name()` because a one-second timestamp in a
shared directory collides. It was applied to the throwaway HTML and not to the
default OUTPUT name, which has the same shape in the same shared directory.

Fixed 2026-08-24.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ===========================================================================
# design-engine.py — the price, and the clock
# ===========================================================================

@pytest.fixture(scope="module")
def eng():
    return _load("design_engine_mod", "scripts/design-engine.py")


_UNPRICED = ("kontext", "fill", "depth", "canny",
             "crisp-upscale", "esrgan", "eraser")


@pytest.mark.parametrize("alias", _UNPRICED)
def test_an_unrecorded_price_is_not_reported_as_zero(eng, alias, capsys):
    """`$0.000` reads as free. These are paid Replicate endpoints."""
    eng._report_cost(alias, eng.MODELS[alias], 1)
    out = capsys.readouterr().out
    assert "$0.000" not in out, (
        f"'{alias}' has no recorded per-run price and the tool printed a "
        f"dollar figure anyway: {out.strip()!r}"
    )
    assert "no per-run price recorded" in out
    assert alias in out, "the operator has to know WHICH model was not priced"


@pytest.mark.parametrize("alias", _UNPRICED)
def test_the_unpriced_models_carry_none_not_zero(eng, alias):
    """The registry is where the claim originates, so it states the gap."""
    assert eng.MODELS[alias]["cost"] is None


def test_a_recorded_price_is_still_multiplied_out(eng, capsys):
    """Anchor: the honest path must not lose the figures that DO exist."""
    eng._report_cost("recraft-v4", eng.MODELS["recraft-v4"], 3)
    out = capsys.readouterr().out
    assert "$0.120" in out, f"0.04 x 3 should be reported: {out.strip()!r}"
    assert "3 image(s)" in out


def test_every_generation_model_still_has_a_price(eng):
    """The split is edit/post-processing, not arbitrary. If a generation model
    ever loses its figure, that is a regression, not a new honest gap."""
    unpriced = sorted(a for a, m in eng.MODELS.items()
                      if m["type"] == "generate" and m["cost"] is None)
    assert not unpriced, unpriced


def test_the_model_table_prints_a_question_mark_not_a_zero(eng):
    assert eng._cost_cell(eng.MODELS["kontext"]) == "?"
    assert eng._cost_cell(eng.MODELS["flux-schnell"]) == "$0.003"


def test_the_models_command_does_not_crash_on_an_unpriced_row(eng, capsys):
    """`f"${m['cost']:.3f}"` is a TypeError once the value is None, and
    `models` is the command an operator runs to CHECK the prices."""
    class _Args:
        type = None
    eng.cmd_models(_Args())
    out = capsys.readouterr().out
    assert "kontext" in out and "?" in out


def test_the_remove_bg_price_comes_from_the_registry_not_a_literal(eng):
    src = (ROOT / "scripts" / "design-engine.py").read_text(encoding="utf-8")
    body = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    assert 'Estimated: $0.000' not in body, (
        "cmd_remove_bg had the zero hard-coded in its format string, so it "
        "reported a price without consulting anything at all"
    )


# --- the poll clock --------------------------------------------------------

def _fake_clock(monkeypatch, eng, steps):
    """Drive `time.monotonic` from a list and make `time.sleep` free."""
    seq = iter(steps)
    last = [steps[-1]]

    def _mono():
        last[0] = next(seq, last[0])   # past the end, the clock simply stops
        return last[0]

    monkeypatch.setattr(eng.time, "sleep", lambda _s: None)
    monkeypatch.setattr(eng.time, "monotonic", _mono)


def test_the_timeout_measures_real_time_not_its_own_sleeps(eng, monkeypatch,
                                                           capsys):
    """`elapsed += POLL_INTERVAL` ignored every request's own duration, so a
    four-minute wait still announced itself as 120 seconds."""
    calls = {"n": 0}

    def _api(method, url, token, data=None):
        calls["n"] += 1
        return {"id": "pred1", "status": "processing"}

    monkeypatch.setattr(eng, "_api_request", _api)
    # start, then a jump far past the budget on the first poll.
    _fake_clock(monkeypatch, eng, [0.0, 300.0, 300.0])

    with pytest.raises(SystemExit) as exc:
        eng._create_prediction("tok", "owner/name", {})
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "300s" in err, (
        f"the refusal must name the time that actually passed: {err.strip()!r}"
    )
    assert "budget 120s" in err, "and the budget it broke, so both are legible"


def test_a_prediction_that_succeeds_is_still_returned(eng, monkeypatch):
    """Anchor: the clock change must not break the working path."""
    statuses = iter(["processing", "succeeded"])

    def _api(method, url, token, data=None):
        return {"id": "pred1", "status": next(statuses)}

    monkeypatch.setattr(eng, "_api_request", _api)
    _fake_clock(monkeypatch, eng, [0.0, 2.0, 2.0, 4.0])
    assert eng._create_prediction("tok", "o/n", {})["status"] == "succeeded"


def test_a_failed_prediction_still_exits_one(eng, monkeypatch, capsys):
    statuses = iter(["processing", "failed"])
    monkeypatch.setattr(eng, "_api_request",
                        lambda *a, **k: {"id": "p", "status": next(statuses),
                                         "error": "boom"})
    _fake_clock(monkeypatch, eng, [0.0, 2.0, 2.0])
    with pytest.raises(SystemExit):
        eng._create_prediction("tok", "o/n", {})
    assert "boom" in capsys.readouterr().err


# ===========================================================================
# deep-research-advance.py — a quarter of a corpus is still degraded
# ===========================================================================

@pytest.fixture(scope="module")
def dra():
    return _load("dra_shard_mod", "scripts/deep-research-advance.py")


def _wire(dra, monkeypatch, tmp_path, angles, ok_angles):
    monkeypatch.setattr(dra, "get_outputs_dir", lambda: tmp_path)
    monkeypatch.setattr(dra, "probe_proxy", lambda: ["k3"])
    monkeypatch.setattr(dra, "kimi_reason",
                        lambda prompt, **k: json.dumps(angles)
                        if "decompose" in prompt.lower() or len(prompt) < 4000
                        else json.dumps({"summary": "s", "claims": [],
                                         "contradictions": []}))

    def _pplx(angle, **kwargs):
        if angle not in ok_angles:
            raise RuntimeError("perplexity 502")
        return f"text for {angle}", [f"https://example.test/{angle}"]

    monkeypatch.setattr(dra, "pplx_research", _pplx)


def _result(run_dir: Path) -> dict:
    return json.loads((run_dir / "intermediate.json").read_text(encoding="utf-8"))


def test_a_partly_lost_corpus_is_marked_degraded(dra, monkeypatch, tmp_path):
    angles = ["a one", "a two", "a three", "a four"]
    _wire(dra, monkeypatch, tmp_path, angles, ok_angles={"a one"})
    result = _result(dra.run("q", depth=4))

    assert result["degraded"] is True, (
        "three of four angles returned nothing and the file said the run was "
        "not degraded; the skill writes that straight into the report header"
    )
    assert "3 of 4 angle(s)" in result["degraded_reason"]


def test_the_lost_angles_are_named(dra, monkeypatch, tmp_path):
    angles = ["alpha", "beta", "gamma"]
    _wire(dra, monkeypatch, tmp_path, angles, ok_angles={"alpha"})
    reason = _result(dra.run("q2", depth=3))["degraded_reason"]
    assert "beta" in reason and "gamma" in reason
    assert "alpha" not in reason, "an angle that DID return is not a loss"


def test_a_full_corpus_is_not_degraded(dra, monkeypatch, tmp_path):
    """Anchor: the flag must still mean something. If every run is degraded the
    skill's header stops carrying information."""
    angles = ["one", "two"]
    _wire(dra, monkeypatch, tmp_path, angles, ok_angles=set(angles))
    result = _result(dra.run("q3", depth=2))
    assert result["degraded"] is False
    assert result["degraded_reason"] == ""


def test_the_corpus_that_did_arrive_is_still_written(dra, monkeypatch, tmp_path):
    angles = ["keep", "lose"]
    _wire(dra, monkeypatch, tmp_path, angles, ok_angles={"keep"})
    result = _result(dra.run("q4", depth=2))
    assert [c["angle"] for c in result["corpus"]] == ["keep"], (
        "degrading the run must not throw away the angle that succeeded"
    )
    assert len(result["sources"]) == 1


def test_an_acquisition_loss_and_a_reasoning_failure_both_survive(dra,
                                                                  monkeypatch,
                                                                  tmp_path):
    """Three phases write one `degraded_reason`. Phase 2 appends to whatever
    acquisition left, and a writer that ASSIGNS drops the phase before it, so
    the operator is told about one failure and never hears about the other."""
    angles = ["keeps", "loses"]
    calls = {"n": 0}

    def _kimi(prompt, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:          # Phase 0 decompose succeeds
            return json.dumps(angles)
        raise RuntimeError("kimi down")   # Phase 2, both attempts

    monkeypatch.setattr(dra, "get_outputs_dir", lambda: tmp_path)
    monkeypatch.setattr(dra, "probe_proxy", lambda: ["k3"])
    monkeypatch.setattr(dra, "kimi_reason", _kimi)

    def _pplx(angle, **kwargs):
        if angle == "loses":
            raise RuntimeError("perplexity 502")
        return "text", ["https://example.test/x"]

    monkeypatch.setattr(dra, "pplx_research", _pplx)

    reason = _result(dra.run("q5", depth=2))["degraded_reason"]
    assert "1 of 2 angle(s)" in reason, "the acquisition loss must survive"
    assert "kimi reason" in reason, "and so must the reasoning failure"


def test_a_total_loss_still_exits_three(dra, monkeypatch, tmp_path):
    """Anchor: the documented exit code for no corpus at all is unchanged."""
    _wire(dra, monkeypatch, tmp_path, ["x"], ok_angles=set())
    with pytest.raises(SystemExit) as exc:
        dra.run("q6", depth=1)
    assert exc.value.code == 3


# ===========================================================================
# design-studio.py — the collision that was fixed on the wrong file
# ===========================================================================

@pytest.fixture(scope="module")
def studio():
    return _load("studio_shard_mod", "scripts/design-studio.py")


def test_two_default_output_names_in_the_same_second_differ(studio):
    names = {studio.scratch_name("render", ".png") for _ in range(10)}
    assert len(names) == 10


def test_the_default_output_path_no_longer_uses_a_bare_timestamp():
    src = (ROOT / "scripts" / "design-studio.py").read_text(encoding="utf-8")
    body = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    for shape in ('f"render-{timestamp()}.png"', 'f"render-{timestamp()}.pdf"'):
        assert shape not in body, (
            f"{shape} is the one-second name this file already diagnosed as a "
            "collision, left on the artifact the operator keeps"
        )


class _FakePage:
    def __init__(self, seen):
        self.seen = seen

    def goto(self, url, **kwargs):
        self.seen.append(url)

    def screenshot(self, **kwargs):
        Path(kwargs["path"]).write_bytes(b"\x89PNG")

    def pdf(self, **kwargs):
        Path(kwargs["path"]).write_bytes(b"%PDF")


class _FakeBrowser:
    def __init__(self, seen):
        self.seen = seen

    def new_page(self, **kwargs):
        return _FakePage(self.seen)

    def close(self):
        pass


class _FakePlaywright:
    def __init__(self, seen):
        self.seen = seen
        self.chromium = self

    def launch(self, **kwargs):
        return _FakeBrowser(self.seen)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture()
def seen_urls(studio, monkeypatch, tmp_path):
    seen: list[str] = []
    # raising=False is load-bearing here, not habit: design-studio.py binds
    # `sync_playwright` inside a `try: ... except ImportError` at module level,
    # so the name is simply absent on a machine with no playwright installed.
    monkeypatch.setattr(studio, "sync_playwright",
                        lambda: _FakePlaywright(seen), raising=False)
    monkeypatch.setattr(studio, "get_tmp_dir", lambda: tmp_path)
    return seen


def test_a_workspace_path_with_a_space_produces_a_loadable_url(studio, seen_urls,
                                                               tmp_path,
                                                               monkeypatch):
    """`f"file:///{path.as_posix()}"` escaped nothing, so a space went into the
    URL raw. The symptom is a blank PNG, never an error."""
    spaced = tmp_path / "my design dir"
    spaced.mkdir()
    monkeypatch.setattr(studio, "get_tmp_dir", lambda: spaced)
    studio.render_screenshot("<p>x</p>", 100, 100, 1, tmp_path / "out.png")

    url = seen_urls[0]
    assert " " not in url, f"an unescaped space reached the browser: {url}"
    assert "%20" in url
    assert not url.startswith("file:////"), "three slashes plus an absolute path"


def test_the_pdf_path_builds_the_same_kind_of_url(studio, seen_urls, tmp_path,
                                                  monkeypatch):
    spaced = tmp_path / "a b"
    spaced.mkdir()
    monkeypatch.setattr(studio, "get_tmp_dir", lambda: spaced)
    studio.render_pdf("<p>x</p>", tmp_path / "out.pdf")
    assert "%20" in seen_urls[0]


def test_an_ordinary_path_still_renders(studio, seen_urls, tmp_path):
    """Anchor: as_uri must not break the normal case."""
    out = tmp_path / "plain.png"
    studio.render_screenshot("<p>x</p>", 100, 100, 1, out)
    assert seen_urls[0].startswith("file:///")
    assert out.read_bytes() == b"\x89PNG"


def test_the_scratch_file_is_still_removed(studio, seen_urls, tmp_path):
    studio.render_screenshot("<p>x</p>", 100, 100, 1, tmp_path / "o.png")
    assert not list(tmp_path.glob("render-*.html"))


# ===========================================================================
# dev/build-plugins.py — the gate reads what the bundle ships
# ===========================================================================

@pytest.fixture(scope="module")
def bp():
    return _load("bp_shard_mod", "scripts/dev/build-plugins.py")


@pytest.fixture()
def repo(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "utils").mkdir()
    (tmp_path / "scripts" / "bundled.py").write_text("x = 1\n")
    (tmp_path / "scripts" / "orphan.py").write_text("x = 1\n")
    skill = tmp_path / ".claude" / "skills" / "demo"
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: demo\n---\n\nBody.\n",
                                    encoding="utf-8")
    return tmp_path


SPEC = {"scripts": ["bundled.py"], "skills": ["demo"], "hooks": [], "commands": []}


def _ref(repo: Path, body: str, name: str = "guide.md") -> None:
    (repo / ".claude" / "skills" / "demo" / "references" / name).write_text(
        body, encoding="utf-8")


def test_an_invoked_script_in_a_reference_file_is_checked(bp, repo):
    _ref(repo, "Run it:\n\n```bash\npython scripts/orphan.py --now\n```\n")
    missing = bp.completeness_gate(SPEC, repo)
    assert missing, (
        "the gate opened SKILL.md only, while _copytree shipped the whole "
        "skill directory; a reference file telling the consumer to run an "
        "unbundled script passed as 'no missing targets'"
    )
    assert "references/guide.md -> scripts/orphan.py" in missing[0]


def test_an_invoked_bundled_script_in_a_reference_file_passes(bp, repo):
    """Anchor: the widening must not fail a reference that is correct."""
    _ref(repo, "```bash\npython scripts/bundled.py\n```\n")
    assert bp.completeness_gate(SPEC, repo) == []


def test_illustrative_prose_in_a_reference_file_is_not_a_reference(bp, repo):
    """`create-plan/references/plan-template.md` carries
    `**Files affected:** scripts/models/user.py` as an EXAMPLE. Failing the
    build on that is the false failure this file's scanner comment warns of."""
    _ref(repo, "**Files affected:** scripts/models/user.py\n"
               "**Files affected:** scripts/models/product.py\n")
    assert bp.completeness_gate(SPEC, repo) == []


def test_a_bash_invocation_counts_too(bp, repo):
    _ref(repo, "```\nbash scripts/orphan.py\n```\n")
    assert bp.completeness_gate(SPEC, repo)


def test_an_eval_corpus_is_not_scanned_because_it_is_not_shipped(bp, repo):
    """The gate's source set and the bundle's contents are one definition, so
    they cannot drift into checking a file that does not ship, or shipping one
    that is not checked."""
    evals = repo / ".claude" / "skills" / "demo" / "evals"
    evals.mkdir()
    (evals / "README.md").write_text("python scripts/orphan.py\n", encoding="utf-8")
    assert bp.completeness_gate(SPEC, repo) == []
    assert not bp.bundled_skill_prose(repo / ".claude" / "skills" / "demo")


def test_the_skill_ignore_and_the_prose_scan_name_the_same_dirs(bp):
    """One list. A second copy is the one that stops being updated."""
    assert "evals" in bp._SKILL_EXCLUDE_DIRS
    assert bp._SKILL_IGNORE(".", ["evals", "references"]) == {"evals"}


def test_the_rewriter_handles_a_file_with_no_frontmatter(bp):
    """A reference page has no `---` block, so it takes the plain body path."""
    text = "```bash\npython scripts/docparse.py parse --file x\n```\n"
    new, n = bp.rewrite_script_paths(text)
    assert n == 1
    assert '"${CLAUDE_PLUGIN_ROOT}"/scripts/docparse.py' in new


def test_a_built_bundle_rewrites_its_reference_files_too(bp, repo, tmp_path):
    """Proving `rewrite_script_paths` works on the text is not proving the BUILD
    calls it on that file. It did not: `rglob("SKILL.md")` walked past every
    `references/` page, so a bundled script's own documented command shipped
    pointing at a relative path that does not exist in the plugin cache."""
    _ref(repo, "Run it:\n\n```bash\npython scripts/bundled.py --now\n```\n")
    out = tmp_path / "mkt"
    bp.build_bundle("demo-bundle", SPEC, out, repo)

    built = out / "plugins" / "demo-bundle" / "skills" / "demo"
    guide = (built / "references" / "guide.md").read_text(encoding="utf-8")
    assert "${CLAUDE_PLUGIN_ROOT}" in guide, (
        f"the reference page shipped un-rewritten: {guide.strip()!r}"
    )
    assert "python scripts/bundled.py" not in guide


def test_a_built_bundle_still_rewrites_its_skill_md(bp, repo, tmp_path):
    """Anchor: widening the glob must not lose the file it already covered."""
    (repo / ".claude" / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\n---\n\nRun `python scripts/bundled.py`.\n",
        encoding="utf-8")
    out = tmp_path / "mkt2"
    bp.build_bundle("demo-bundle", SPEC, out, repo)
    built = (out / "plugins" / "demo-bundle" / "skills" / "demo" / "SKILL.md")
    assert "${CLAUDE_PLUGIN_ROOT}" in built.read_text(encoding="utf-8")


def test_a_built_bundle_does_not_ship_the_eval_corpus(bp, repo, tmp_path):
    evals = repo / ".claude" / "skills" / "demo" / "evals"
    evals.mkdir()
    (evals / "README.md").write_text("python scripts/orphan.py\n", encoding="utf-8")
    out = tmp_path / "mkt3"
    bp.build_bundle("demo-bundle", SPEC, out, repo)
    assert not list((out / "plugins").rglob("evals"))


def test_the_real_bundles_pass_the_widened_gate(bp):
    """The tightening must not reject a bundle this repo actually ships."""
    manifest = bp.load_manifest(ROOT)
    broken = {n: m for n, s in manifest.items()
              if (m := bp.completeness_gate(s, ROOT))}
    assert not broken, broken


def test_the_blind_spot_still_names_the_narrower_prose_rule(bp):
    """scope-claims obligation 2: a widened check that reads like a complete
    one is the same defect wearing a different hat."""
    src = (ROOT / "scripts" / "dev" / "build-plugins.py").read_text(encoding="utf-8")
    # Comment markers stripped and whitespace normalised: the sentence wraps
    # across comment lines, and a reflow is not a regression.
    head = " ".join(
        " ".join(ln.lstrip().lstrip("#") for ln in
                 src.split("_SCRIPT_REF_RE", 1)[0].splitlines()).split()
    )
    assert "bare non-invoked path inside a reference file" in head


def test_a_commands_only_bundle_is_built_by_all(bp):
    """`commands` became a first-class field and this filter was not updated
    with it, so such a bundle was skipped and nothing said so.

    Asked of the FIELD SET, not of the source text. This was
    `assert 's.get("commands")' in body` until 2026-09-02, over a file whose
    filter was a chain of `s.get(...)` calls. The chain became the
    `CONTENT_FIELDS` tuple that same day, for the reason this test exists (a
    field can be added to the builder and forgotten here), and the assertion
    then failed over a change that FIXED its subject. A test pinned to one
    spelling of a filter cannot survive the filter being made harder to
    forget, which is the wrong way round.
    """
    assert "commands" in bp.CONTENT_FIELDS, (
        "a bundle declaring only `commands:` is skipped by --all and left out "
        f"of marketplace.json, silently: {bp.CONTENT_FIELDS}")

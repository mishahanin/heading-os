"""Unit tests for scripts/apply-wizard-answers.py."""
import json
import subprocess
from pathlib import Path


def test_setup_directory_is_gitignored():
    """The .setup/ directory must be gitignored - it contains PII."""
    result = subprocess.run(
        ["git", "check-ignore", "--quiet", ".setup/answers.json"],
        cwd=Path(__file__).parent.parent,
    )
    assert result.returncode == 0, (
        ".setup/ is not gitignored. Add '.setup/' to .gitignore before shipping."
    )


import yaml
from pathlib import Path

REPO = Path(__file__).parent.parent
QUESTIONS_PATH = REPO / "config" / "wizard-questions.yaml"


def test_question_bank_is_valid_yaml():
    """Question bank must parse as YAML and be a non-empty list."""
    with open(QUESTIONS_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, list), "Question bank must be a YAML list"
    assert len(data) > 0, "Question bank must not be empty"


def test_every_question_has_required_fields():
    """Every entry needs id, audience, type, required, prompt, example, target."""
    with open(QUESTIONS_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    required_fields = {"id", "audience", "type", "required", "prompt", "example", "target"}
    for i, q in enumerate(data):
        missing = required_fields - set(q.keys())
        assert not missing, f"Question {i} ({q.get('id', '?')}) missing: {missing}"


def test_every_question_id_is_unique():
    """IDs must be unique and snake_case."""
    import re
    with open(QUESTIONS_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    ids = [q["id"] for q in data]
    assert len(ids) == len(set(ids)), f"Duplicate IDs: {[x for x in ids if ids.count(x) > 1]}"
    for qid in ids:
        assert re.fullmatch(r"[a-z][a-z0-9_]*", qid), f"Invalid snake_case: {qid}"


def test_every_question_type_is_valid():
    """type must be one of placeholder|rich|secret|list."""
    with open(QUESTIONS_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    valid_types = {"placeholder", "rich", "secret", "list"}
    for q in data:
        assert q["type"] in valid_types, f"Question {q['id']}: invalid type {q['type']!r}"


def test_every_audience_entry_is_valid():
    """audience must be a list containing only 'public' or 'exec'."""
    with open(QUESTIONS_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    for q in data:
        assert isinstance(q["audience"], list), f"Question {q['id']}: audience must be list"
        assert all(a in ("public", "exec") for a in q["audience"]), (
            f"Question {q['id']}: audience must contain only 'public' or 'exec'"
        )
        assert len(q["audience"]) >= 1, f"Question {q['id']}: audience list empty"


def test_all_rich_templates_exist():
    """Every rich question's template file must exist."""
    with open(QUESTIONS_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    for q in data:
        if q["type"] != "rich":
            continue
        template_rel = q["target"]["template"]
        template_abs = REPO / template_rel
        assert template_abs.exists(), (
            f"Rich question {q['id']!r} references missing template: {template_rel}"
        )


import sys


def test_apply_script_has_help():
    """The apply script must expose --help without error."""
    result = subprocess.run(
        [sys.executable, "scripts/apply-wizard-answers.py", "--help"],
        cwd=REPO, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"--help failed: {result.stderr}"
    assert "--question" in result.stdout
    assert "--all" in result.stdout
    assert "--status" in result.stdout
    assert "--reset" in result.stdout
    assert "--check" in result.stdout
    assert "--audience" in result.stdout


import pytest
import shutil


@pytest.fixture
def tmp_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yield tmp_path


def _write_identity(root: Path, type_):
    if type_ is None:
        (root / ".workspace-identity.json").unlink(missing_ok=True)
        return
    (root / ".workspace-identity.json").write_text(
        json.dumps({"type": type_, "slug": "test"})
    )


def _load_apply_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "apply_mod", REPO / "scripts" / "apply-wizard-answers.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_audience_public_when_identity_file_absent(tmp_workspace):
    mod = _load_apply_module()
    assert mod.detect_audience(tmp_workspace) == "public"


def test_audience_exec_when_identity_type_is_exec_workspace(tmp_workspace):
    mod = _load_apply_module()
    _write_identity(tmp_workspace, "exec-workspace")
    assert mod.detect_audience(tmp_workspace) == "exec"


def test_audience_ceo_master_when_identity_type_is_ceo_master(tmp_workspace):
    mod = _load_apply_module()
    _write_identity(tmp_workspace, "ceo-master")
    assert mod.detect_audience(tmp_workspace) == "ceo-master"


def test_audience_raises_on_malformed_identity_file(tmp_workspace):
    mod = _load_apply_module()
    (tmp_workspace / ".workspace-identity.json").write_text("not valid json {{{")
    with pytest.raises(mod.SchemaError):
        mod.detect_audience(tmp_workspace)


def test_audience_raises_on_unknown_type_value(tmp_workspace):
    mod = _load_apply_module()
    _write_identity(tmp_workspace, "robot-overlord")
    with pytest.raises(mod.SchemaError):
        mod.detect_audience(tmp_workspace)


def test_status_on_ceo_master_exits_4(tmp_workspace):
    _write_identity(tmp_workspace, "ceo-master")
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"), "--status"],
        cwd=tmp_workspace, capture_output=True, text=True,
    )
    assert result.returncode == 4
    assert "ceo-master" in (result.stderr + result.stdout).lower()


def test_audience_override_without_force_flag_rejected(tmp_workspace):
    _write_identity(tmp_workspace, "ceo-master")
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"),
         "--status", "--audience", "public"],
        cwd=tmp_workspace, capture_output=True, text=True,
    )
    assert result.returncode == 4


def test_audience_override_with_force_flag_allowed(tmp_workspace):
    _write_identity(tmp_workspace, "ceo-master")
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"),
         "--status", "--audience", "public", "--force-ceo-master"],
        cwd=tmp_workspace, capture_output=True, text=True,
    )
    # Should proceed past the gate (exit code != 4). May still return non-zero
    # later because config is missing, but must not be blocked by ceo-master.
    assert result.returncode != 4


def test_load_questions_returns_list(tmp_workspace):
    mod = _load_apply_module()
    (tmp_workspace / "config").mkdir()
    (tmp_workspace / "config" / "wizard-questions.yaml").write_text(
        "- id: test_q\n"
        "  audience: [public]\n"
        "  type: placeholder\n"
        "  required: true\n"
        "  prompt: \"Test?\"\n"
        "  example: \"example\"\n"
        "  target:\n"
        "    placeholder: \"{TEST}\"\n"
        "    files: [\"*.md\"]\n"
    )
    questions = mod.load_questions(tmp_workspace)
    assert isinstance(questions, list)
    assert len(questions) == 1
    assert questions[0]["id"] == "test_q"


def test_load_questions_raises_on_duplicate_id(tmp_workspace):
    mod = _load_apply_module()
    (tmp_workspace / "config").mkdir()
    (tmp_workspace / "config" / "wizard-questions.yaml").write_text(
        "- id: dupe\n  audience: [public]\n  type: placeholder\n  required: true\n"
        "  prompt: p\n  example: e\n  target: {placeholder: '{X}', files: ['*']}\n"
        "- id: dupe\n  audience: [public]\n  type: placeholder\n  required: true\n"
        "  prompt: p\n  example: e\n  target: {placeholder: '{Y}', files: ['*']}\n"
    )
    with pytest.raises(mod.SchemaError, match="duplicate"):
        mod.load_questions(tmp_workspace)


def test_load_questions_raises_on_unknown_type(tmp_workspace):
    mod = _load_apply_module()
    (tmp_workspace / "config").mkdir()
    (tmp_workspace / "config" / "wizard-questions.yaml").write_text(
        "- id: bad_type\n  audience: [public]\n  type: invalid\n  required: true\n"
        "  prompt: p\n  example: e\n  target: {}\n"
    )
    with pytest.raises(mod.SchemaError, match="type"):
        mod.load_questions(tmp_workspace)


def test_filter_questions_by_audience():
    mod = _load_apply_module()
    bank = [
        {"id": "a", "audience": ["public"]},
        {"id": "b", "audience": ["exec"]},
        {"id": "c", "audience": ["public", "exec"]},
    ]
    assert [q["id"] for q in mod.filter_by_audience(bank, "public")] == ["a", "c"]
    assert [q["id"] for q in mod.filter_by_audience(bank, "exec")] == ["b", "c"]


def test_load_answers_returns_empty_skeleton_when_missing(tmp_workspace):
    mod = _load_apply_module()
    state = mod.load_answers(tmp_workspace)
    assert state["schema_version"] == 1
    assert state["answers"] == {}


def test_save_answers_writes_atomically(tmp_workspace):
    mod = _load_apply_module()
    state = {"schema_version": 1, "audience": "public", "answers": {}}
    mod.save_answers(tmp_workspace, state)
    path = tmp_workspace / ".setup" / "answers.json"
    assert path.exists()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["schema_version"] == 1


def test_save_answers_leaves_no_tmp_file(tmp_workspace):
    mod = _load_apply_module()
    mod.save_answers(tmp_workspace, {"schema_version": 1, "audience": "public", "answers": {}})
    setup_dir = tmp_workspace / ".setup"
    tmp_files = list(setup_dir.glob("answers.json.tmp*"))
    assert tmp_files == [], f"leftover tmp files: {tmp_files}"


def _setup_public_workspace(tmp_workspace):
    """Fixture: copy real question bank + seed one answered question."""
    (tmp_workspace / "config").mkdir()
    shutil.copy(REPO / "config" / "wizard-questions.yaml",
                tmp_workspace / "config" / "wizard-questions.yaml")
    (tmp_workspace / ".setup").mkdir()
    state = {
        "schema_version": 1,
        "audience": "public",
        "started_at": "2026-04-24T09:00:00+04:00",
        "last_updated": "2026-04-24T09:01:00+04:00",
        "applied_at": "2026-04-24T09:01:00+04:00",
        "answers": {
            "company_full_name": {"value": "Acme Corp", "status": "answered",
                                  "answered_at": "2026-04-24T09:01:00+04:00"},
        },
    }
    (tmp_workspace / ".setup" / "answers.json").write_text(json.dumps(state))
    return tmp_workspace


def test_status_returns_schema_matching_spec(tmp_workspace):
    _setup_public_workspace(tmp_workspace)
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"), "--status"],
        cwd=tmp_workspace, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["audience"] == "public"
    assert "completion_pct" in payload
    assert "required" in payload and "answered" in payload["required"]
    assert "optional" in payload
    assert "rows" in payload and isinstance(payload["rows"], list)
    answered_rows = [r for r in payload["rows"] if r["status"] == "answered"]
    assert any(r["id"] == "company_full_name" for r in answered_rows)


def test_status_completion_pct_counts_required_only(tmp_workspace):
    """2 of 10 required answered, all optional unanswered -> 20%."""
    (tmp_workspace / "config").mkdir()
    bank = []
    for i in range(10):
        bank.append({
            "id": f"req_{i}", "audience": ["public"], "type": "placeholder",
            "required": True, "prompt": "?", "example": "e",
            "target": {"placeholder": f"{{R{i}}}", "files": ["*.md"]},
        })
    for i in range(5):
        bank.append({
            "id": f"opt_{i}", "audience": ["public"], "type": "placeholder",
            "required": False, "prompt": "?", "example": "e",
            "target": {"placeholder": f"{{O{i}}}", "files": ["*.md"]},
        })
    (tmp_workspace / "config" / "wizard-questions.yaml").write_text(yaml.safe_dump(bank))
    (tmp_workspace / ".setup").mkdir()
    state = {
        "schema_version": 1, "audience": "public",
        "started_at": None, "last_updated": None, "applied_at": None,
        "answers": {
            "req_0": {"value": "x", "status": "answered", "answered_at": "now"},
            "req_1": {"value": "y", "status": "answered", "answered_at": "now"},
        },
    }
    (tmp_workspace / ".setup" / "answers.json").write_text(json.dumps(state))
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"), "--status"],
        cwd=tmp_workspace, capture_output=True, text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["completion_pct"] == 20
    assert payload["required"]["total"] == 10
    assert payload["required"]["answered"] == 2
    assert payload["optional"]["total"] == 5
    assert payload["optional"]["answered"] == 0


def test_skip_marks_question_skipped(tmp_workspace):
    _setup_public_workspace(tmp_workspace)
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"),
         "--skip", "company_timezone"],
        cwd=tmp_workspace, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    state = json.loads((tmp_workspace / ".setup" / "answers.json").read_text())
    assert state["answers"]["company_timezone"]["status"] == "skipped"
    assert "skipped_at" in state["answers"]["company_timezone"]


def test_skip_unknown_id_returns_exit_5(tmp_workspace):
    """Unknown id is a user-input error (exit 5), NOT a config error (exit 1)."""
    _setup_public_workspace(tmp_workspace)
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"),
         "--skip", "nonexistent_question"],
        cwd=tmp_workspace, capture_output=True, text=True,
    )
    assert result.returncode == 5


def _seed_public_fixture_with_placeholder(tmp_workspace):
    (tmp_workspace / "config").mkdir()
    bank = [{
        "id": "company_full_name", "audience": ["public"], "type": "placeholder",
        "required": True, "prompt": "full name?", "example": "e",
        "target": {"placeholder": "{COMPANY_FULL}", "files": ["**/*.md"]},
    }]
    (tmp_workspace / "config" / "wizard-questions.yaml").write_text(yaml.safe_dump(bank))
    (tmp_workspace / "context").mkdir()
    (tmp_workspace / "context" / "about.md").write_text("# About\n\n{COMPANY_FULL} builds tools.\n")


def test_question_placeholder_substitutes_across_files(tmp_workspace):
    _seed_public_fixture_with_placeholder(tmp_workspace)
    payload = json.dumps({"value": "Acme Corporation"})
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"),
         "--question", "company_full_name", "--value-from-stdin"],
        cwd=tmp_workspace, input=payload, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    content = (tmp_workspace / "context" / "about.md").read_text()
    assert "{COMPANY_FULL}" not in content
    assert "Acme Corporation" in content
    state = json.loads((tmp_workspace / ".setup" / "answers.json").read_text())
    assert state["answers"]["company_full_name"]["value"] == "Acme Corporation"


def test_placeholder_substitution_is_idempotent(tmp_workspace):
    _seed_public_fixture_with_placeholder(tmp_workspace)
    payload = json.dumps({"value": "Acme Corporation"})
    for _ in range(2):
        subprocess.run(
            [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"),
             "--question", "company_full_name", "--value-from-stdin"],
            cwd=tmp_workspace, input=payload, capture_output=True, text=True, check=True,
        )
    content = (tmp_workspace / "context" / "about.md").read_text()
    assert content.count("Acme Corporation") == 1


def test_placeholder_skips_gitignored_and_binary(tmp_workspace):
    _seed_public_fixture_with_placeholder(tmp_workspace)
    (tmp_workspace / ".git").mkdir(exist_ok=True)
    (tmp_workspace / ".git" / "HEAD").write_text("{COMPANY_FULL}\n")
    (tmp_workspace / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n{COMPANY_FULL}")
    payload = json.dumps({"value": "Acme Corporation"})
    subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"),
         "--question", "company_full_name", "--value-from-stdin"],
        cwd=tmp_workspace, input=payload, capture_output=True, text=True, check=True,
    )
    assert "{COMPANY_FULL}" in (tmp_workspace / ".git" / "HEAD").read_text()
    assert b"{COMPANY_FULL}" in (tmp_workspace / "logo.png").read_bytes()


def test_placeholder_skips_non_allowlisted_extensions(tmp_workspace):
    """Extensions outside PROCESSABLE_EXTENSIONS (.toml, .cfg, .ini) must not be rewritten."""
    _seed_public_fixture_with_placeholder(tmp_workspace)
    (tmp_workspace / "pyproject.toml").write_text("name = \"{COMPANY_FULL}\"\n")
    (tmp_workspace / "app.cfg").write_text("company = {COMPANY_FULL}\n")
    (tmp_workspace / "legacy.ini").write_text("[section]\nname={COMPANY_FULL}\n")
    payload = json.dumps({"value": "Acme Corporation"})
    subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"),
         "--question", "company_full_name", "--value-from-stdin"],
        cwd=tmp_workspace, input=payload, capture_output=True, text=True, check=True,
    )
    assert "{COMPANY_FULL}" in (tmp_workspace / "pyproject.toml").read_text()
    assert "{COMPANY_FULL}" in (tmp_workspace / "app.cfg").read_text()
    assert "{COMPANY_FULL}" in (tmp_workspace / "legacy.ini").read_text()


def _seed_list_fixture(tmp_workspace):
    (tmp_workspace / "config").mkdir()
    bank = [{
        "id": "core_values", "audience": ["public"], "type": "list",
        "required": False, "prompt": "values?", "example": "e",
        "target": {
            "placeholders": ["{VALUE_1}", "{VALUE_2}", "{VALUE_3}"],
            "files": ["**/*.md"],
        },
    }]
    (tmp_workspace / "config" / "wizard-questions.yaml").write_text(yaml.safe_dump(bank))
    (tmp_workspace / "about.md").write_text("- {VALUE_1}\n- {VALUE_2}\n- {VALUE_3}\n")


def test_list_maps_array_to_placeholders(tmp_workspace):
    _seed_list_fixture(tmp_workspace)
    payload = json.dumps({"value": ["Trust", "Speed", "Integrity"]})
    subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"),
         "--question", "core_values", "--value-from-stdin"],
        cwd=tmp_workspace, input=payload, capture_output=True, text=True, check=True,
    )
    content = (tmp_workspace / "about.md").read_text()
    assert "Trust" in content and "Speed" in content and "Integrity" in content
    assert "{VALUE_" not in content


def test_list_underflow_blanks_remaining(tmp_workspace):
    _seed_list_fixture(tmp_workspace)
    payload = json.dumps({"value": ["OnlyOne"]})
    subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"),
         "--question", "core_values", "--value-from-stdin"],
        cwd=tmp_workspace, input=payload, capture_output=True, text=True, check=True,
    )
    content = (tmp_workspace / "about.md").read_text()
    assert "OnlyOne" in content
    # Remaining placeholders replaced with empty string -> lines like "- \n"
    lines_with_blank = [line for line in content.splitlines() if line.strip() == "-"]
    assert len(lines_with_blank) >= 2


def test_list_overflow_drops_extras(tmp_workspace):
    _seed_list_fixture(tmp_workspace)
    payload = json.dumps({"value": ["A", "B", "C", "D", "E"]})
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"),
         "--question", "core_values", "--value-from-stdin"],
        cwd=tmp_workspace, input=payload, capture_output=True, text=True,
    )
    assert result.returncode == 0
    out = json.loads(result.stdout)
    warnings = out.get("warnings", [])
    assert any("overflow" in w.lower() or "drop" in w.lower() for w in warnings)


def test_render_template_simple_interpolation():
    mod = _load_apply_module()
    assert mod.render_template("Hello {{ name }}!", {"name": "World"}) == "Hello World!"


def test_render_template_missing_variable_becomes_empty():
    mod = _load_apply_module()
    assert mod.render_template("{{ absent }}-end", {}) == "-end"


def test_render_template_conditional_block_true():
    mod = _load_apply_module()
    assert mod.render_template("{% if shown %}yes{% endif %}", {"shown": "x"}) == "yes"


def test_render_template_conditional_block_false():
    mod = _load_apply_module()
    assert mod.render_template("{% if shown %}yes{% endif %}", {"shown": ""}) == ""


def test_render_template_unsupported_syntax_tolerated():
    mod = _load_apply_module()
    # Unsupported syntax like filters should not crash; returns either empty or pass-through
    result = mod.render_template("{{ x | upper }}", {"x": "hi"})
    assert result in ("", "{{ x | upper }}")


def _seed_rich_fixture(tmp_workspace, audience="public"):
    (tmp_workspace / "config" / "wizard-templates").mkdir(parents=True)
    (tmp_workspace / "config" / "wizard-templates" / "voice.md.tmpl").write_text(
        "# Voice\n\n{{ ceo_voice_draft }}\n\n> {{ ceo_voice }}\n"
    )
    bank = [{
        "id": "ceo_voice", "audience": ["public", "exec"], "type": "rich",
        "required": True, "prompt": "voice?", "example": "e",
        "target": {
            "template": "config/wizard-templates/voice.md.tmpl",
            "output": "reference/ceo-voice.md",
            "output_exec": "personal/reference/voice.md",
        },
    }]
    (tmp_workspace / "config" / "wizard-questions.yaml").write_text(yaml.safe_dump(bank))
    if audience == "exec":
        (tmp_workspace / ".workspace-identity.json").write_text(
            json.dumps({"type": "exec-workspace", "slug": "test"})
        )


def test_rich_write_renders_public_output(tmp_workspace):
    _seed_rich_fixture(tmp_workspace, "public")
    payload = json.dumps({
        "value": "Direct, no fluff.",
        "draft": "Long expanded voice brief about directness.",
        "draft_approved": True,
    })
    subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"),
         "--question", "ceo_voice", "--value-from-stdin"],
        cwd=tmp_workspace, input=payload, capture_output=True, text=True, check=True,
    )
    out = tmp_workspace / "reference" / "ceo-voice.md"
    assert out.exists()
    content = out.read_text()
    assert "Long expanded voice brief about directness." in content
    assert "> Direct, no fluff." in content


def test_rich_write_uses_output_exec_on_exec(tmp_workspace):
    _seed_rich_fixture(tmp_workspace, "exec")
    payload = json.dumps({
        "value": "Analytical.",
        "draft": "Analytical voice brief.",
        "draft_approved": True,
    })
    subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"),
         "--question", "ceo_voice", "--value-from-stdin"],
        cwd=tmp_workspace, input=payload, capture_output=True, text=True, check=True,
    )
    assert (tmp_workspace / "personal" / "reference" / "voice.md").exists()
    assert not (tmp_workspace / "reference" / "ceo-voice.md").exists()


def test_rich_archive_draft_moves_to_previous(tmp_workspace):
    _seed_rich_fixture(tmp_workspace, "public")
    subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"),
         "--question", "ceo_voice", "--value-from-stdin"],
        cwd=tmp_workspace,
        input=json.dumps({"value": "v1", "draft": "draft_v1", "draft_approved": True}),
        capture_output=True, text=True, check=True,
    )
    subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"),
         "--question", "ceo_voice", "--value-from-stdin"],
        cwd=tmp_workspace, input=json.dumps({"archive_draft": True}),
        capture_output=True, text=True, check=True,
    )
    state = json.loads((tmp_workspace / ".setup" / "answers.json").read_text())
    entry = state["answers"]["ceo_voice"]
    assert "draft_previous" in entry
    assert len(entry["draft_previous"]) == 1
    assert entry["draft_previous"][0]["draft"] == "draft_v1"
    # After archive, applied_at cleared; --status reports unapplied:true
    assert state["applied_at"] is None
    status = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"), "--status"],
        cwd=tmp_workspace, capture_output=True, text=True,
    )
    payload = json.loads(status.stdout)
    assert payload["unapplied"] is True


def _seed_secret_fixture(tmp_workspace):
    (tmp_workspace / "config").mkdir(exist_ok=True)
    bank = [{
        "id": "anthropic_api_key", "audience": ["public"], "type": "secret",
        "required": True, "prompt": "key?", "example": "e",
        "target": {"env_var": "ANTHROPIC_API_KEY"},
    }]
    (tmp_workspace / "config" / "wizard-questions.yaml").write_text(yaml.safe_dump(bank))


def test_secret_writes_env_var(tmp_workspace):
    _seed_secret_fixture(tmp_workspace)
    payload = json.dumps({"value": "TEST-FIXTURE-KEY-123"})
    subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"),
         "--question", "anthropic_api_key", "--value-from-stdin"],
        cwd=tmp_workspace, input=payload, capture_output=True, text=True, check=True,
    )
    env = (tmp_workspace / ".env").read_text()
    assert "ANTHROPIC_API_KEY=TEST-FIXTURE-KEY-123" in env


def test_secret_masks_value_in_answers(tmp_workspace):
    _seed_secret_fixture(tmp_workspace)
    payload = json.dumps({"value": "TEST-FIXTURE-LONGSECRETVALUE"})
    subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"),
         "--question", "anthropic_api_key", "--value-from-stdin"],
        cwd=tmp_workspace, input=payload, capture_output=True, text=True, check=True,
    )
    state = json.loads((tmp_workspace / ".setup" / "answers.json").read_text())
    stored = state["answers"]["anthropic_api_key"]["value"]
    assert "LONGSECRETVALUE" not in stored
    assert state["answers"]["anthropic_api_key"]["env_written"] is True


def test_secret_updates_existing_env_line(tmp_workspace):
    _seed_secret_fixture(tmp_workspace)
    (tmp_workspace / ".env").write_text(
        "OTHER_VAR=keep\nANTHROPIC_API_KEY=TEST-FIXTURE-OLD\nTHIRD=3\n"  # pragma: allowlist secret
    )
    payload = json.dumps({"value": "TEST-FIXTURE-NEW"})
    subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"),
         "--question", "anthropic_api_key", "--value-from-stdin"],
        cwd=tmp_workspace, input=payload, capture_output=True, text=True, check=True,
    )
    content = (tmp_workspace / ".env").read_text()
    assert "ANTHROPIC_API_KEY=TEST-FIXTURE-NEW" in content
    assert "ANTHROPIC_API_KEY=TEST-FIXTURE-OLD" not in content
    assert "OTHER_VAR=keep" in content
    assert "THIRD=3" in content


def test_secret_rejects_non_identifier_env_var_name(tmp_workspace):
    """env_var names must be valid identifiers; non-identifier values (even path-like) are rejected."""
    (tmp_workspace / "config").mkdir(exist_ok=True)
    bank = [{
        "id": "hostile", "audience": ["public"], "type": "secret",
        "required": False, "prompt": "?", "example": "e",
        "target": {"env_var": "../escape"},
    }]
    (tmp_workspace / "config" / "wizard-questions.yaml").write_text(yaml.safe_dump(bank))
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"),
         "--question", "hostile", "--value-from-stdin"],
        cwd=tmp_workspace, input=json.dumps({"value": "x"}),
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "invalid env_var" in (result.stderr + result.stdout).lower()


def test_all_applies_every_answered_question(tmp_workspace):
    (tmp_workspace / "config").mkdir()
    bank = [
        {"id": "a", "audience": ["public"], "type": "placeholder", "required": True,
         "prompt": "?", "example": "e",
         "target": {"placeholder": "{A}", "files": ["**/*.md"]}},
        {"id": "b", "audience": ["public"], "type": "placeholder", "required": True,
         "prompt": "?", "example": "e",
         "target": {"placeholder": "{B}", "files": ["**/*.md"]}},
    ]
    (tmp_workspace / "config" / "wizard-questions.yaml").write_text(yaml.safe_dump(bank))
    (tmp_workspace / "doc.md").write_text("{A} and {B}\n")
    (tmp_workspace / ".setup").mkdir()
    state = {
        "schema_version": 1, "audience": "public",
        "started_at": None, "last_updated": None, "applied_at": None,
        "answers": {
            "a": {"value": "Alpha", "status": "answered", "answered_at": "t"},
            "b": {"value": "Beta", "status": "answered", "answered_at": "t"},
        },
    }
    (tmp_workspace / ".setup" / "answers.json").write_text(json.dumps(state))
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"), "--all"],
        cwd=tmp_workspace, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    content = (tmp_workspace / "doc.md").read_text()
    assert "Alpha and Beta" in content
    state_after = json.loads((tmp_workspace / ".setup" / "answers.json").read_text())
    assert state_after["applied_at"] is not None


def test_all_idempotent(tmp_workspace):
    test_all_applies_every_answered_question(tmp_workspace)
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"), "--all"],
        cwd=tmp_workspace, capture_output=True, text=True,
    )
    assert result.returncode == 0
    out = json.loads(result.stdout)
    assert out.get("files_updated", 0) == 0


def test_check_mode_single_question_writes_nothing(tmp_workspace):
    _seed_public_fixture_with_placeholder(tmp_workspace)
    doc = tmp_workspace / "context" / "about.md"
    mtime_before = doc.stat().st_mtime
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"),
         "--question", "company_full_name", "--value-from-stdin", "--check"],
        cwd=tmp_workspace, input=json.dumps({"value": "Acme"}),
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert doc.stat().st_mtime == mtime_before
    assert "{COMPANY_FULL}" in doc.read_text()
    out = json.loads(result.stdout)
    assert out.get("dry_run") is True


def test_reset_reverts_tracked_file_to_git_index(tmp_workspace):
    subprocess.run(["git", "init", "-q"], cwd=tmp_workspace, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.io"], cwd=tmp_workspace, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_workspace, check=True)
    (tmp_workspace / "doc.md").write_text("{COMPANY}\n")
    (tmp_workspace / "config").mkdir()
    bank = [{"id": "c", "audience": ["public"], "type": "placeholder", "required": True,
             "prompt": "?", "example": "e",
             "target": {"placeholder": "{COMPANY}", "files": ["**/*.md"]}}]
    (tmp_workspace / "config" / "wizard-questions.yaml").write_text(yaml.safe_dump(bank))
    subprocess.run(["git", "add", "doc.md", "config/"], cwd=tmp_workspace, check=True)
    subprocess.run(["git", "commit", "-m", "init", "-q"], cwd=tmp_workspace, check=True)
    (tmp_workspace / ".setup").mkdir()
    (tmp_workspace / ".setup" / "answers.json").write_text(json.dumps({
        "schema_version": 1, "audience": "public",
        "started_at": None, "last_updated": None, "applied_at": "t",
        "answers": {"c": {"value": "Acme", "status": "answered", "answered_at": "t"}},
    }))
    (tmp_workspace / "doc.md").write_text("Acme\n")
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"), "--reset", "--force"],
        cwd=tmp_workspace, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_workspace / "doc.md").read_text() == "{COMPANY}\n"
    state = json.loads((tmp_workspace / ".setup" / "answers.json").read_text())
    assert state["answers"]["c"]["status"] == "answered"
    assert state["applied_at"] is None


def test_reset_refuses_on_unrelated_uncommitted_changes(tmp_workspace):
    subprocess.run(["git", "init", "-q"], cwd=tmp_workspace, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.io"], cwd=tmp_workspace, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_workspace, check=True)
    (tmp_workspace / "other.md").write_text("hi\n")
    subprocess.run(["git", "add", "other.md"], cwd=tmp_workspace, check=True)
    subprocess.run(["git", "commit", "-m", "init", "-q"], cwd=tmp_workspace, check=True)
    (tmp_workspace / "other.md").write_text("modified\n")
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"), "--reset"],
        cwd=tmp_workspace, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "uncommitted" in (result.stderr + result.stdout).lower()


def test_depends_on_parent_matches_child_included(tmp_workspace):
    (tmp_workspace / "config").mkdir()
    bank = [
        {"id": "use_telegram", "audience": ["public"], "type": "placeholder", "required": True,
         "prompt": "?", "example": "yes",
         "target": {"placeholder": "{UT}", "files": ["**/*.md"]}},
        {"id": "telegram_phone", "audience": ["public"], "type": "placeholder", "required": False,
         "prompt": "?", "example": "e",
         "target": {"placeholder": "{TP}", "files": ["**/*.md"]},
         "depends_on": {"question": "use_telegram", "equals": "yes"}},
    ]
    (tmp_workspace / "config" / "wizard-questions.yaml").write_text(yaml.safe_dump(bank))
    (tmp_workspace / ".setup").mkdir()
    (tmp_workspace / ".setup" / "answers.json").write_text(json.dumps({
        "schema_version": 1, "audience": "public",
        "started_at": None, "last_updated": None, "applied_at": None,
        "answers": {"use_telegram": {"value": "yes", "status": "answered", "answered_at": "t"}},
    }))
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"), "--status"],
        cwd=tmp_workspace, capture_output=True, text=True,
    )
    payload = json.loads(result.stdout)
    row_ids = [r["id"] for r in payload["rows"]]
    assert "telegram_phone" in row_ids


def test_depends_on_parent_mismatch_child_hidden(tmp_workspace):
    (tmp_workspace / "config").mkdir()
    bank = [
        {"id": "use_telegram", "audience": ["public"], "type": "placeholder", "required": True,
         "prompt": "?", "example": "yes",
         "target": {"placeholder": "{UT}", "files": ["**/*.md"]}},
        {"id": "telegram_phone", "audience": ["public"], "type": "placeholder", "required": False,
         "prompt": "?", "example": "e",
         "target": {"placeholder": "{TP}", "files": ["**/*.md"]},
         "depends_on": {"question": "use_telegram", "equals": "yes"}},
    ]
    (tmp_workspace / "config" / "wizard-questions.yaml").write_text(yaml.safe_dump(bank))
    (tmp_workspace / ".setup").mkdir()
    (tmp_workspace / ".setup" / "answers.json").write_text(json.dumps({
        "schema_version": 1, "audience": "public",
        "started_at": None, "last_updated": None, "applied_at": None,
        "answers": {"use_telegram": {"value": "no", "status": "answered", "answered_at": "t"}},
    }))
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"), "--status"],
        cwd=tmp_workspace, capture_output=True, text=True,
    )
    payload = json.loads(result.stdout)
    row_ids = [r["id"] for r in payload["rows"]]
    assert "telegram_phone" not in row_ids


def test_depends_on_missing_parent_id_errors(tmp_workspace):
    (tmp_workspace / "config").mkdir()
    bank = [
        {"id": "child", "audience": ["public"], "type": "placeholder", "required": True,
         "prompt": "?", "example": "e",
         "target": {"placeholder": "{X}", "files": ["**/*.md"]},
         "depends_on": {"question": "nonexistent_parent", "equals": "yes"}},
    ]
    (tmp_workspace / "config" / "wizard-questions.yaml").write_text(yaml.safe_dump(bank))
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"), "--status"],
        cwd=tmp_workspace, capture_output=True, text=True,
    )
    assert result.returncode == 1


def test_depends_on_malformed_raises(tmp_workspace):
    """Missing 'question' or 'equals' key, or wrong type, must raise SchemaError at load."""
    mod = _load_apply_module()
    (tmp_workspace / "config").mkdir()
    # Missing 'question' key
    (tmp_workspace / "config" / "wizard-questions.yaml").write_text(yaml.safe_dump([
        {"id": "a", "audience": ["public"], "type": "placeholder", "required": True,
         "prompt": "?", "example": "e",
         "target": {"placeholder": "{X}", "files": ["*"]},
         "depends_on": {"equals": "yes"}},
    ]))
    with pytest.raises(mod.SchemaError, match="depends_on"):
        mod.load_questions(tmp_workspace)
    # Non-string 'question'
    (tmp_workspace / "config" / "wizard-questions.yaml").write_text(yaml.safe_dump([
        {"id": "a", "audience": ["public"], "type": "placeholder", "required": True,
         "prompt": "?", "example": "e",
         "target": {"placeholder": "{X}", "files": ["*"]},
         "depends_on": {"question": 42, "equals": "yes"}},
    ]))
    with pytest.raises(mod.SchemaError, match="question must be a string"):
        mod.load_questions(tmp_workspace)
    # depends_on is not a dict
    (tmp_workspace / "config" / "wizard-questions.yaml").write_text(yaml.safe_dump([
        {"id": "a", "audience": ["public"], "type": "placeholder", "required": True,
         "prompt": "?", "example": "e",
         "target": {"placeholder": "{X}", "files": ["*"]},
         "depends_on": "not a dict"},
    ]))
    with pytest.raises(mod.SchemaError, match="depends_on"):
        mod.load_questions(tmp_workspace)


def test_ceo_voice_template_renders_cleanly_without_ceo_name(tmp_workspace):
    """Voice template must not produce ' - Voice Guide' heading when ceo_full_name is unset."""
    # Use the REAL ceo-voice.md.tmpl from this repo
    (tmp_workspace / "config" / "wizard-templates").mkdir(parents=True)
    shutil.copy(REPO / "config" / "wizard-templates" / "ceo-voice.md.tmpl",
                tmp_workspace / "config" / "wizard-templates" / "ceo-voice.md.tmpl")
    bank = [{
        "id": "ceo_voice", "audience": ["public"], "type": "rich",
        "required": True, "prompt": "voice?", "example": "e",
        "target": {
            "template": "config/wizard-templates/ceo-voice.md.tmpl",
            "output": "reference/ceo-voice.md",
        },
    }]
    (tmp_workspace / "config" / "wizard-questions.yaml").write_text(yaml.safe_dump(bank))
    payload = json.dumps({
        "value": "Direct.",
        "draft": "Full voice brief about being direct.",
        "draft_approved": True,
    })
    subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"),
         "--question", "ceo_voice", "--value-from-stdin"],
        cwd=tmp_workspace, input=payload, capture_output=True, text=True, check=True,
    )
    content = (tmp_workspace / "reference" / "ceo-voice.md").read_text()
    first_line = content.splitlines()[0]
    # Without ceo_full_name answered, heading should be "# Voice Guide" (no leading " - ")
    assert first_line == "# Voice Guide", f"unexpected heading: {first_line!r}"
    # Body should render cleanly (no broken Jinja fragments, no double spaces from empty vars)
    assert "{{" not in content and "{%" not in content, "template did not fully render"


def test_question_result_includes_files_skipped(tmp_workspace):
    """Result JSON must include files_skipped count (spec section 8.2 step 10)."""
    _seed_public_fixture_with_placeholder(tmp_workspace)
    (tmp_workspace / "logo.png").write_bytes(b"\x89PNG\r\n{COMPANY_FULL}")
    (tmp_workspace / ".git").mkdir(exist_ok=True)
    (tmp_workspace / ".git" / "HEAD").write_text("{COMPANY_FULL}\n")
    payload = json.dumps({"value": "Acme"})
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"),
         "--question", "company_full_name", "--value-from-stdin"],
        cwd=tmp_workspace, input=payload, capture_output=True, text=True, check=True,
    )
    out = json.loads(result.stdout)
    assert "files_skipped" in out, "files_skipped field required per spec"
    assert isinstance(out["files_skipped"], int)

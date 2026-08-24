"""Four defects around the council pins and the data-repo bootstrap.

Shard `scripts-04-p1` of the 2026-08 engine audit.

  - `create-data-repo.py` carried two branches whose comments describe a re-run
    ("Idempotent: skip init if already a repo", "If origin already exists ...
    skip creation") and could never reach them: step 1 delegates to
    `init-data.py`, which refuses ANY non-empty directory. A `gh repo create`
    that failed on a taken name left a scaffolded tree, a git repo and no
    remote, and the second attempt died before step 2.
  - `council-models.py --get ""` fell past every branch to
    `apply_sets(args.set)` with `args.set` None and raised TypeError, instead of
    the exit 2 the file documents. `if args.get:` cannot tell "not supplied"
    from "supplied empty", which is what a shell writes for an unset variable.
  - `council-record-verdict.py` kept a second, hand-written copy of the six
    choices inside `render_tally`. A seventh choice would have been accepted,
    written and counted in the total, then left out of the breakdown.
  - `council-models-notify.py` promises a transient failure is "logged and
    SWALLOWED (exit 0) so the oneshot systemd unit is never left `failed`", and
    both halves of its state file could raise past every handler.

One finding was checked and REFUTED: the `--public` help text claims the first
push is refused by the remote-identity wall. `scripts/utils/git_push.py` does
carry that refusal ("only the engine may push to a public repository"), so the
claim is true and nothing was changed.
"""
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    """Strip the colour codes that sit between the words of a printed line."""
    return _ANSI.sub("", text)


def _load(stem: str, filename: str):
    spec = importlib.util.spec_from_file_location(stem, ROOT / "scripts" / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# council-models.py: --get distinguishes absent from empty
# ============================================================

@pytest.fixture()
def models():
    return _load("council_models_cli", "council-models.py")


@pytest.mark.parametrize("provider", ["", "   ", "nope"])
def test_an_unusable_provider_exits_two_and_says_so(models, provider, capsys):
    """`--get ""` used to reach `for pair in None` and raise TypeError."""
    assert models.main(["--get", provider]) == 2
    assert "unknown provider" in capsys.readouterr().err


def test_a_real_provider_prints_its_pin(models, capsys):
    assert models.main(["--get", "kimi"]) == 0
    assert capsys.readouterr().out.strip()


def test_the_empty_provider_message_lists_the_real_ones(models, capsys):
    models.main(["--get", ""])
    err = capsys.readouterr().err
    assert "gemini" in err and "grok" in err and "kimi" in err


def test_a_malformed_set_pair_still_exits_two(models, capsys):
    assert models.main(["--set", "grok"]) == 2
    assert "is not provider=model" in capsys.readouterr().err


def test_an_empty_model_id_is_refused(models, capsys):
    assert models.main(["--set", "grok="]) == 2
    assert "empty model id" in capsys.readouterr().err


# ============================================================
# council-record-verdict.py: the parts sum to the whole
# ============================================================

@pytest.fixture()
def verdict():
    return _load("council_record_verdict", "council-record-verdict.py")


def test_the_tally_names_every_choice_argparse_accepts(verdict):
    """The breakdown is derived from VALID_CHOICES, not a second hand-typed tuple."""
    line = verdict.render_tally({"a": {"choice": "kimi"}})
    for choice in verdict.VALID_CHOICES:
        assert f"{choice}=" in line


def test_the_counts_add_up_to_the_total(verdict):
    rows = {"a": {"choice": "kimi"}, "b": {"choice": "kimi"}, "c": {"choice": "mix"}}
    line = verdict.render_tally(rows)
    assert "3 recorded" in line
    counted = sum(int(p.split("=")[1]) for p in line.split(" - ")[1].split(", "))
    assert counted == 3


def test_a_record_outside_the_known_choices_is_shown_not_dropped(verdict):
    """Silent truncation reads as "we counted everything". It did not."""
    rows = {"a": {"choice": "kimi"}, "b": {"choice": "an-unknown-model"}}
    line = verdict.render_tally(rows)
    assert "other=1" in line
    counted = sum(int(p.split("=")[1]) for p in line.split(" - ")[1].split(", "))
    assert counted == 2


def test_a_record_with_no_choice_key_does_not_raise(verdict):
    """`v["choice"]` raised KeyError on a ledger line from an older schema."""
    line = verdict.render_tally({"a": {"verdict_id": "a"}})
    assert "other=1" in line


def test_a_clean_tally_carries_no_other_bucket(verdict):
    line = verdict.render_tally({"a": {"choice": "reject"}})
    assert "other=" not in line


def test_an_empty_ledger_says_zero(verdict):
    assert verdict.render_tally({}) == "tally: 0 recorded"


def test_the_choices_are_ordered_so_the_line_is_stable(verdict):
    assert list(verdict.VALID_CHOICES) == [
        "claude", "gemini", "grok", "kimi", "mix", "reject"]


# ============================================================
# council-models-notify.py: the oneshot unit is never left failed
# ============================================================

@pytest.fixture()
def notify(tmp_path, monkeypatch):
    mod = _load("council_models_notify", "council-models-notify.py")
    monkeypatch.setattr(mod, "_state_path", lambda: tmp_path / "state.json")
    return mod


def test_a_saved_signature_comes_back(notify):
    assert notify._save_signature(["grok:newer:grok-9"]) is True
    assert notify._load_last_signature() == ["grok:newer:grok-9"]


def test_a_missing_state_file_is_no_prior_nudge(notify):
    assert notify._load_last_signature() == []


@pytest.mark.parametrize("body", ["null", "[1, 2]", '"a string"', "42",
                                  '{"signature": "not a list"}',
                                  '{"signature": null}', '{}'])
def test_a_state_file_of_the_wrong_shape_never_raises(notify, body):
    """`json.load(f).get(...)` raised AttributeError past every handler here."""
    notify._state_path().write_text(body, encoding="utf-8")
    assert notify._load_last_signature() == []


def test_unparseable_state_is_still_no_prior_nudge(notify, capsys):
    notify._state_path().write_text("{not json", encoding="utf-8")
    assert notify._load_last_signature() == []
    assert "could not read nudge state" in capsys.readouterr().err


def test_an_unwritable_state_dir_is_reported_not_raised(notify, monkeypatch, capsys):
    """An OSError here used to exit non-zero and leave the oneshot unit failed."""
    def _no(*a, **k):
        raise OSError("read-only file system")
    monkeypatch.setattr(notify.Path, "mkdir", _no)
    assert notify._save_signature(["x"]) is False
    assert "could not save nudge state" in capsys.readouterr().err


def test_a_failed_save_says_the_nudge_may_repeat(notify, monkeypatch, capsys):
    """Over-report, never fall silent: the operator must know why it repeats."""
    def _no(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(notify.json, "dump", _no)
    assert notify._save_signature(["x"]) is False
    assert "may repeat" in capsys.readouterr().err


def test_the_module_still_promises_a_swallowed_failure(notify):
    """Pins the contract the two guards above exist to keep."""
    assert "never left `failed`" in notify.__doc__


# ============================================================
# create-data-repo.py: an interrupted bootstrap can be resumed
# ============================================================

@pytest.fixture()
def bootstrap():
    return _load("create_data_repo", "create-data-repo.py")


def test_a_tree_we_already_stamped_resumes(bootstrap, tmp_path, capsys):
    target = tmp_path / "data"
    target.mkdir()
    (target / ".schema-version").write_text("1\n", encoding="utf-8")
    (target / "leftover").mkdir()
    assert bootstrap.scaffold(target, dry_run=False) == 0
    assert "resuming" in capsys.readouterr().out


def test_somebody_elses_directory_is_still_refused(bootstrap, tmp_path, capsys):
    """The stamp is the discriminator. Without it, hands off."""
    target = tmp_path / "notmine"
    target.mkdir()
    (target / "notes.txt").write_text("mine", encoding="utf-8")
    assert bootstrap.scaffold(target, dry_run=False) != 0
    assert "Scaffold failed" in capsys.readouterr().out


def test_an_empty_target_is_scaffolded_normally(bootstrap, tmp_path):
    target = tmp_path / "fresh"
    assert bootstrap.scaffold(target, dry_run=False) == 0
    assert (target / ".schema-version").exists()


def test_a_dry_run_touches_nothing(bootstrap, tmp_path, capsys):
    target = tmp_path / "fresh"
    assert bootstrap.scaffold(target, dry_run=True) == 0
    assert not target.exists()
    assert "[dry-run]" in capsys.readouterr().out


def test_an_edited_gitignore_survives_a_resume(bootstrap, tmp_path, capsys):
    target = tmp_path / "data"
    target.mkdir()
    (target / ".gitignore").write_text("# mine\nsecret-notes/\n", encoding="utf-8")
    bootstrap.write_repo_files(target, dry_run=False)
    assert "secret-notes/" in (target / ".gitignore").read_text(encoding="utf-8")
    assert "kept existing" in capsys.readouterr().out


def test_missing_repo_files_are_still_written(bootstrap, tmp_path):
    target = tmp_path / "data"
    target.mkdir()
    bootstrap.write_repo_files(target, dry_run=False)
    assert ".memory-index/" in (target / ".gitignore").read_text(encoding="utf-8")
    assert "private data" in (target / "README.md").read_text(encoding="utf-8")


def test_only_the_missing_one_is_written(bootstrap, tmp_path, capsys):
    target = tmp_path / "data"
    target.mkdir()
    (target / "README.md").write_text("# mine", encoding="utf-8")
    bootstrap.write_repo_files(target, dry_run=False)
    lines = _plain(capsys.readouterr().out).splitlines()
    assert "wrote .gitignore" in lines
    assert "kept existing README.md" in lines
    assert (target / "README.md").read_text(encoding="utf-8") == "# mine"


# ============================================================
# The refuted finding: the remote-identity wall is real
# ============================================================

def test_the_public_push_refusal_the_help_text_promises_exists():
    """--public says the first push is refused. Verify, do not assume.

    A false claim about a safety control is worse than a missing control: the
    next reader trusts it. This one is true.
    """
    source = (ROOT / "scripts" / "utils" / "git_push.py").read_text(encoding="utf-8")
    assert "only the engine may push to a public repository" in source


def test_the_end_to_end_bootstrap_is_resumable(tmp_path):
    """The whole point: run it twice, get exit 0 twice."""
    target = tmp_path / "data"
    for _ in range(2):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "create-data-repo.py"),
             "--path", str(target), "--no-remote"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=300,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "Refusing to scaffold" not in proc.stdout
    assert (target / ".schema-version").exists()
    assert (target / "README.md").exists()

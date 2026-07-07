"""F-9.7: data-overlay migration framework.

Covers the runner (status / stamp / apply / demo-exempt), the idempotent apply
of a (fake) pending migration, and the H1 regression: an overlay with no
.schema-version marker reads as "current" via the fallback, so it must be
stamped for a future version bump to be detected by require_writable_data_root().
"""
import importlib.util
from pathlib import Path

import pytest

import scripts.migrations as migrations_pkg
import scripts.utils.paths as paths


def _load_runner():
    """Import the kebab-case scripts/migrate-data.py as a module."""
    path = Path(__file__).resolve().parent.parent / "scripts" / "migrate-data.py"
    spec = importlib.util.spec_from_file_location("migrate_data", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


migrate_data = _load_runner()


@pytest.fixture
def overlay(tmp_path, monkeypatch):
    """A writable tmp overlay wired into both the runner and paths."""
    root = tmp_path / "data"
    root.mkdir()
    # The runner binds these names at import; patch them on the runner module.
    monkeypatch.setattr(migrate_data, "get_data_root", lambda: root)
    monkeypatch.setattr(migrate_data, "data_root_is_demo", lambda: False)
    monkeypatch.setattr(migrate_data, "read_data_schema_version",
                        lambda: paths.read_data_schema_version())
    # paths.read_data_schema_version reads get_data_root()/.schema-version.
    monkeypatch.setattr(paths, "get_data_root", lambda: root)
    monkeypatch.setattr(paths, "data_root_is_demo", lambda: False)
    return root


def test_status_up_to_date(overlay, capsys):
    (overlay / ".schema-version").write_text("1\n", encoding="utf-8")
    assert migrate_data.cmd_status() == 0
    assert "up to date" in capsys.readouterr().out


def test_stamp_writes_marker_when_absent(overlay):
    marker = overlay / ".schema-version"
    assert not marker.exists()
    assert migrate_data.cmd_stamp(dry_run=False) == 0
    assert marker.read_text(encoding="utf-8").strip() == str(paths.DATA_SCHEMA_VERSION)


def test_stamp_is_idempotent(overlay):
    assert migrate_data.cmd_stamp(dry_run=False) == 0
    assert migrate_data.cmd_stamp(dry_run=False) == 0  # second run: no-op
    assert (overlay / ".schema-version").read_text(encoding="utf-8").strip() == "1"


def test_dry_run_stamp_writes_nothing(overlay):
    assert migrate_data.cmd_stamp(dry_run=True) == 0
    assert not (overlay / ".schema-version").exists()


def test_apply_pending_then_idempotent(overlay, monkeypatch):
    """Inject a fake v2 migration: apply advances the marker to 2 and runs up()
    exactly once; a second apply is a no-op."""
    marker = overlay / ".schema-version"
    marker.write_text("1\n", encoding="utf-8")
    calls = []

    class _FakeV2:
        __name__ = "scripts.migrations.0002_fake"
        VERSION = 2

        @staticmethod
        def up(data_root, dry_run=False):
            calls.append(Path(data_root))

    fake = _FakeV2()
    reg = [(1, migrations_pkg.registered_migrations()[0][1]), (2, fake)]
    monkeypatch.setattr(migrate_data, "registered_migrations", lambda: reg)
    monkeypatch.setattr(migrate_data, "max_version", lambda: 2)

    assert migrate_data.cmd_apply(dry_run=False) == 0
    assert marker.read_text(encoding="utf-8").strip() == "2"
    assert len(calls) == 1
    # Second apply: overlay now at v2, nothing pending.
    assert migrate_data.cmd_apply(dry_run=False) == 0
    assert len(calls) == 1


def test_fileless_stamp_then_bump_refusal(overlay, monkeypatch):
    """H1 regression: a fileless overlay reads as current (no refusal). After
    --stamp it carries v1, so a future max_version()=2 is detected as pending and
    require_writable_data_root() raises the exact remediation command."""
    # Fileless: reads as current (DATA_SCHEMA_VERSION), max_version 1 -> no refusal.
    assert not (overlay / ".schema-version").exists()
    assert paths.require_writable_data_root() == overlay

    # Stamp writes the concrete v1 marker.
    assert migrate_data.cmd_stamp(dry_run=False) == 0
    assert (overlay / ".schema-version").read_text(encoding="utf-8").strip() == "1"

    # Simulate a future engine that ships a v2 migration.
    monkeypatch.setattr("scripts.migrations.max_version", lambda: 2)
    with pytest.raises(paths.DataRootError) as exc:
        paths.require_writable_data_root()
    assert "migrate-data.py --apply" in str(exc.value)


def test_pending_refusal_message(tmp_path, monkeypatch):
    """require_writable_data_root() refuses with the exact command when behind."""
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(paths, "get_data_root", lambda: root)
    monkeypatch.setattr(paths, "data_root_is_demo", lambda: False)
    monkeypatch.setattr(paths, "read_data_schema_version", lambda: 0)
    monkeypatch.setattr("scripts.migrations.max_version", lambda: 1)
    with pytest.raises(paths.DataRootError) as exc:
        paths.require_writable_data_root()
    assert "python scripts/migrate-data.py --apply" in str(exc.value)


def test_demo_exempt(overlay, monkeypatch):
    """A demo (read-only examples) overlay refuses apply and stamp."""
    monkeypatch.setattr(migrate_data, "data_root_is_demo", lambda: True)
    assert migrate_data.cmd_apply(dry_run=False) == 1
    assert migrate_data.cmd_stamp(dry_run=False) == 1

#!/usr/bin/env python3
"""Five hand-editable files, five loaders that promised to degrade, and did not.

Each of these loaders documents what it does when its file cannot be read, and
each had a hole. Measured 2026-08-30 against a scratch workspace root:

* `_load_routing_map_cached` says "any read/parse error yields default
  'private'" and caught `(OSError, yaml.YAMLError)`. `UnicodeDecodeError` is a
  `ValueError` and escaped both, so `default: \\xffprivate` in routing-map.yaml
  made `get_routing_destination("crm/x.md")` RAISE. That resolver runs once per
  tracked file in the push wall and the engine-tree-clean pre-commit hook, and
  the whole point of the promise is that a corrupt map cannot take the leak wall
  down.
* `get_workspace_identity` promises a ValueError for a file that "exists but
  cannot be parsed", with no shape check. `[]` parsed, cached, and returned, and
  `is_ceo_workspace()` died with `AttributeError: 'list' object has no attribute
  'get'` far from the file that caused it.
* `_read_registry_or_empty` has a careful absent-versus-corrupt taxonomy with a
  stderr warning, and no room for the SHAPE case: `[]` in
  `admin/executives.json` made `load_fleet()` raise `AttributeError`.
* `load_admin_config` returned `{}` for a corrupt file in silence, so a typo
  invisibly reverted admin gating and org resolution to their defaults.
* `display_path` promises it "degrades to the absolute path rather than raise"
  and caught only `DataRootError`. `get_corporate_root()` resolves through
  `get_workspace_identity()`, which raises `ValueError` by design, so a corrupt
  identity file made a LOGGING helper throw.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils import workspace  # noqa: E402


@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    """A scratch workspace + data root. Nothing here touches the operator's tree."""
    (tmp_path / "config").mkdir()
    (tmp_path / "admin").mkdir()
    monkeypatch.setattr(workspace, "get_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(workspace, "get_data_root", lambda: tmp_path)
    monkeypatch.setattr(workspace, "get_data_config_dir", lambda: tmp_path / "config")
    workspace._load_routing_map_cached.cache_clear()
    workspace._reset_identity_cache()
    yield tmp_path
    workspace._load_routing_map_cached.cache_clear()
    workspace._reset_identity_cache()


# ------------------------------------------------------------- routing map


def test_an_undecodable_routing_map_fails_closed_to_private(fake_root):
    """The measured case: UnicodeDecodeError past a fail-closed handler."""
    (fake_root / "config" / "routing-map.yaml").write_bytes(b"default: \xffprivate\n")
    assert workspace.get_routing_destination("crm/x.md") == "private"
    assert workspace.load_routing_map() == {"default": "private", "rules": {}}


def test_a_readable_routing_map_is_still_honoured(fake_root):
    """The control: failing closed must not become failing always."""
    (fake_root / "config" / "routing-map.yaml").write_text(
        "default: engine\nrules:\n  crm/: private\n", encoding="utf-8")
    assert workspace.get_routing_destination("crm/x.md") == "private"
    assert workspace.get_routing_destination("scripts/x.py") == "engine"


# ---------------------------------------------------------------- identity


@pytest.mark.parametrize("body", ["[]", '"a string"', "42", "null"])
def test_a_wrong_shape_identity_raises_the_documented_valueerror(fake_root, body):
    (fake_root / ".workspace-identity.json").write_text(body, encoding="utf-8")
    workspace._reset_identity_cache()
    with pytest.raises(ValueError, match="Refusing silent fallback"):
        workspace.is_ceo_workspace()


def test_an_unparseable_identity_still_raises_valueerror(fake_root):
    """The pre-existing refusal must survive the shape check beside it."""
    (fake_root / ".workspace-identity.json").write_text("{invalid", encoding="utf-8")
    workspace._reset_identity_cache()
    with pytest.raises(ValueError, match="cannot be parsed"):
        workspace.get_workspace_identity()


@pytest.mark.parametrize("body", [b'{"type": "ceo-\xff-master"}', b"\xff\xfe\x00"])
def test_an_undecodable_identity_raises_the_documented_valueerror(fake_root, body):
    """The `UnicodeDecodeError` arm of the handler, which had no case on it.

    The tuple reads `(json.JSONDecodeError, OSError, UnicodeDecodeError)` and
    only the first two were ever driven. The third is not reachable through
    either: the decode fails inside `read_text()`, before `json.loads` is
    called, so `JSONDecodeError` never applies, and `UnicodeDecodeError`
    subclasses `ValueError`, not `OSError`. MEASURED 2026-09-01 by deleting
    `UnicodeDecodeError` from that tuple: this module and the twenty other test
    files naming these loaders all stayed green, and one non-UTF-8 byte in a
    hand-edited `.workspace-identity.json` raised a raw decode traceback out of
    the resolver every path helper in this module sits on -- instead of the
    explanatory refusal the docstring promises for a file that "exists but
    cannot be parsed".

    Its sibling `load_admin_config` is covered by
    `tests/test_data_config_seam.py`, which names this loader and
    `_read_registry_or_empty` as the two copies it could not reach. These two
    tests are those two.
    """
    (fake_root / ".workspace-identity.json").write_bytes(body)
    workspace._reset_identity_cache()
    with pytest.raises(ValueError, match="cannot be parsed"):
        workspace.get_workspace_identity()


def test_a_well_formed_identity_still_loads(fake_root):
    """The control."""
    (fake_root / ".workspace-identity.json").write_text(
        '{"role": "admin", "slug": "jamesbond", "type": "exec-workspace"}',
        encoding="utf-8")
    workspace._reset_identity_cache()
    assert workspace.is_exec_workspace() is True
    assert workspace.get_exec_slug() == "jamesbond"


# ---------------------------------------------------------------- registry


def test_a_wrong_shape_registry_warns_and_reports_empty(fake_root, capsys):
    """The measured case: `[]` crashed `load_fleet()` with AttributeError."""
    (fake_root / "admin" / "executives.json").write_text("[]", encoding="utf-8")
    assert workspace.load_fleet() == []
    assert "parsed as list" in capsys.readouterr().err


def test_a_corrupt_registry_still_warns_and_reports_empty(fake_root, capsys):
    """The pre-existing corrupt-file path must survive the shape check."""
    (fake_root / "admin" / "executives.json").write_text("{invalid", encoding="utf-8")
    assert workspace.load_fleet() == []
    assert "could not be read" in capsys.readouterr().err


def test_an_undecodable_registry_warns_and_reports_empty(fake_root, capsys):
    """The registry's `UnicodeDecodeError` arm, the second uncovered copy.

    Same handler, same three-way tuple, same reason it escapes the other two
    clauses. MEASURED 2026-09-01 by deleting `UnicodeDecodeError` from
    `_read_registry_or_empty`: twenty-one test files naming these loaders stayed
    green while `load_fleet()` raised a raw decode traceback. Offboarding, CRM
    aggregation and admin-health all sit on this loader, and the whole point of
    the stderr line beside it is that an empty fleet must never read as a
    measured one.
    """
    (fake_root / "admin" / "executives.json").write_bytes(
        b'{"version": 1, "executives": [{"slug": "\xff\xfe"}]}')
    assert workspace.load_fleet() == []
    assert "could not be read" in capsys.readouterr().err


def test_an_absent_registry_is_still_silent(fake_root, capsys):
    """The negative case: absent is NOT an error and must print nothing."""
    assert workspace.load_fleet() == []
    assert capsys.readouterr().err == ""


# ------------------------------------------------------------ admin config


def test_a_corrupt_admin_config_says_so_instead_of_falling_back_in_silence(
        fake_root, capsys):
    (fake_root / "config" / "admin.json").write_text("{invalid", encoding="utf-8")
    assert workspace.load_admin_config() == {}
    assert "could not be read" in capsys.readouterr().err


def test_a_wrong_shape_admin_config_says_so_too(fake_root, capsys):
    (fake_root / "config" / "admin.json").write_text("[]", encoding="utf-8")
    assert workspace.load_admin_config() == {}
    assert "parsed as list" in capsys.readouterr().err


def test_an_absent_admin_config_is_still_silent(fake_root, capsys):
    """The negative case: absent config is normal, not a defect to announce."""
    assert workspace.load_admin_config() == {}
    assert capsys.readouterr().err == ""


def test_a_readable_admin_config_is_returned(fake_root):
    """The control."""
    (fake_root / "config" / "admin.json").write_text(
        '{"github_org": "acme-invalid"}', encoding="utf-8")
    assert workspace.load_admin_config() == {"github_org": "acme-invalid"}


# ------------------------------------------------------------- display_path


def test_display_path_degrades_over_a_corrupt_identity_file(fake_root):
    """The measured case: a LOGGING helper raised ValueError."""
    (fake_root / ".workspace-identity.json").write_text("{invalid", encoding="utf-8")
    workspace._reset_identity_cache()
    assert workspace.display_path("/etc/hostname") == "/etc/hostname"


def test_display_path_still_relativises_a_path_under_a_known_root(fake_root):
    """The control: degrading must not become never resolving."""
    (fake_root / ".workspace-identity.json").write_text("{invalid", encoding="utf-8")
    workspace._reset_identity_cache()
    assert workspace.display_path(fake_root / "crm" / "x.md") == "crm/x.md"

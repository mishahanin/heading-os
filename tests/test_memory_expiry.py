"""The `expires:` retirement machinery, and the gate that keeps it switched off.

Everything above the last section measures the pure logic: what parses as an
expiry, which records a sweep would select, and what a pointer rewrite leaves
behind. That is the MECHANISM. It says nothing about whether the mechanism is
allowed to run, and the operator's standing directive is that auto-memory is
never pruned on a clock (docs/memory-lifecycle.md; the timer was disabled by
hand on 2026-08-07).

The only mechanical thing standing between that directive and a daily deletion
sweep is the refusal at the top of `scripts/install-memory-auto-retire-timer.sh`,
and until 2026-09-01 no test touched it. The last section does.
"""
import datetime
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import memory_expiry

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "scripts" / "install-memory-auto-retire-timer.sh"


def _mem(store: Path, name: str, *, expires=None, body="a fact"):
    store.mkdir(parents=True, exist_ok=True)
    fm = ["---", f"name: {name[:-3]}", "description: test", "metadata:", "  type: project"]
    if expires is not None:
        fm.append(f"  expires: {expires}")
    fm.append("---")
    (store / name).write_text("\n".join(fm) + "\n\n" + body + "\n", encoding="utf-8")


TODAY = datetime.date(2026, 7, 6)


def test_parse_expires_from_metadata(tmp_path):
    _mem(tmp_path, "a.md", expires="2026-08-25")
    assert memory_expiry.parse_expires((tmp_path / "a.md").read_text()) == datetime.date(2026, 8, 25)


def test_parse_expires_top_level(tmp_path):
    # top-level expires: also honored
    (tmp_path / "b.md").write_text(
        "---\nname: b\nexpires: 2026-01-01\nmetadata:\n  type: project\n---\nbody\n",
        encoding="utf-8",
    )
    assert memory_expiry.parse_expires((tmp_path / "b.md").read_text()) == datetime.date(2026, 1, 1)


def test_parse_expires_absent_returns_none(tmp_path):
    _mem(tmp_path, "c.md")
    assert memory_expiry.parse_expires((tmp_path / "c.md").read_text()) is None


def test_parse_expires_malformed_returns_none(tmp_path):
    (tmp_path / "d.md").write_text(
        "---\nname: d\nmetadata:\n  expires: not-a-date\n---\nbody\n", encoding="utf-8"
    )
    assert memory_expiry.parse_expires((tmp_path / "d.md").read_text()) is None


def test_find_expired_selects_strictly_past(tmp_path):
    _mem(tmp_path, "past.md", expires="2026-07-05")      # yesterday -> expired
    _mem(tmp_path, "boundary.md", expires="2026-07-06")  # today -> NOT yet (survives its last day)
    _mem(tmp_path, "future.md", expires="2026-09-01")    # future -> live
    _mem(tmp_path, "noexp.md")                            # no expires -> never touched
    (tmp_path / "MEMORY.md").write_text("# index\n", encoding="utf-8")

    expired = memory_expiry.find_expired(tmp_path, TODAY)
    names = {n for n, _ in expired}
    assert names == {"past.md"}


def test_find_expired_skips_memory_md(tmp_path):
    # MEMORY.md must never be a retire candidate even if it somehow parses
    (tmp_path / "MEMORY.md").write_text(
        "---\nexpires: 2020-01-01\n---\n# index\n", encoding="utf-8"
    )
    assert memory_expiry.find_expired(tmp_path, TODAY) == []


def test_strip_index_pointers_removes_only_named(tmp_path):
    index = (
        "# Memory index\n"
        "- [Keep me](keep.md) — stays.\n"
        "- [Drop me](drop.md) — goes.\n"
        "## Active Threads\n"
        "- [A thread](threads/business/drop.md) — managed, must stay.\n"
    )
    out = memory_expiry.strip_index_pointers(index, ["drop.md"])
    assert "(keep.md)" in out
    assert "- [Drop me](drop.md)" not in out
    # the thread pointer references a path, not the bare top-level file: untouched
    assert "threads/business/drop.md" in out


def test_strip_index_pointers_noop_when_absent(tmp_path):
    index = "# Memory index\n- [Keep](keep.md) — stays.\n"
    assert memory_expiry.strip_index_pointers(index, ["gone.md"]) == index


# ---------------------------------------------------------------------------
# The gate: clock-driven retirement stays OFF
# ---------------------------------------------------------------------------

UNITS = ("memory-auto-retire.service", "memory-auto-retire.timer")


def _run_installer(tmp_path, *, override, argv=()):
    """Run the installer with HOME and PATH pinned inside tmp_path.

    HOME is the installer's only write destination (`DEST_DIR` is
    `$HOME/.config/systemd/user`), so pinning it means a run that gets past the
    gate cannot render a unit onto this machine.

    PATH holds `dirname` and nothing else. `dirname` is the one external the
    script reaches before its `command -v systemctl` check; leaving `systemctl`
    off the PATH makes an overridden run stop at exit 5, having printed the
    override warning and written nothing. That is what lets the positive case
    below be run at all: it proves the gate is not a blanket refusal without
    ever installing a timer that deletes memories.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    dirname = shutil.which("dirname")
    assert dirname, "no dirname on PATH; the probe cannot pin the environment"
    link = bindir / "dirname"
    if not link.exists():
        link.symlink_to(dirname)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {
        "HOME": str(home),
        "PATH": str(bindir),
        "PYTHON": sys.executable,
    }
    if override:
        env["MEMORY_AUTO_RETIRE_OVERRIDE"] = "1"
    return subprocess.run(
        ["/bin/bash", str(INSTALLER), *argv],
        capture_output=True, text=True, timeout=120, env=env, cwd=str(ROOT),
    ), home


def _rendered_units(home: Path):
    dest = home / ".config" / "systemd" / "user"
    return sorted(p.name for p in dest.glob("*") ) if dest.is_dir() else []


def test_the_installer_refuses_and_that_refusal_is_a_stop(tmp_path):
    """Exit status and side effect, not the words.

    A refusal that prints and proceeds is the failure mode this workspace has
    already shipped once (`.githooks/pre-push-data` printed "push blocked" and
    exited 0). So the assertion is on the exit status AND on the absence of the
    two unit files, with the message checked last and only as a courtesy to
    whoever reads the terminal.
    """
    proc, home = _run_installer(tmp_path, override=False)
    assert proc.returncode == 9, (proc.returncode, proc.stdout, proc.stderr)
    assert _rendered_units(home) == [], (
        "the installer wrote a systemd unit despite refusing")
    assert "[refused]" in proc.stderr


def test_the_refusal_is_reached_before_anything_is_resolved(tmp_path):
    """No unit is rendered even when every later precondition is satisfiable.

    The gate is the FIRST statement after `set -euo pipefail`, ahead of the
    workspace-root resolution, the interpreter probe and the template read. A
    gate placed after any of those would still print the same refusal while
    having already touched the machine.
    """
    proc, home = _run_installer(tmp_path, override=False)
    assert proc.returncode == 9
    assert not (home / ".config").exists(), (
        "the installer created its destination tree before refusing")


def test_the_override_flag_gets_past_the_gate(tmp_path):
    """The other direction: a guard that refuses everything measures nothing.

    Stopped at the systemctl probe (exit 5) by the pinned PATH, so the run
    proves the gate opened without ever enabling a timer. The warning line is
    asserted because it is what an operator who typed the override sees.
    """
    proc, home = _run_installer(
        tmp_path, override=False, argv=("--i-am-reversing-the-no-prune-directive",))
    assert proc.returncode != 9, (proc.stdout, proc.stderr)
    assert "DELETES memories" in proc.stderr
    assert _rendered_units(home) == []


def test_the_override_env_var_gets_past_the_gate(tmp_path):
    """The second documented override, which the flag branch does not cover."""
    proc, home = _run_installer(tmp_path, override=True)
    assert proc.returncode != 9, (proc.stdout, proc.stderr)
    assert "DELETES memories" in proc.stderr
    assert _rendered_units(home) == []


def test_a_near_miss_flag_does_not_open_the_gate(tmp_path):
    """The realistic near-miss, not an obviously invalid string.

    A half-remembered flag is how this gate would actually be attacked by
    accident. It must refuse exactly as a bare invocation does.
    """
    proc, home = _run_installer(
        tmp_path, override=False, argv=("--i-am-reversing-the-no-prune",))
    assert proc.returncode == 9, (proc.stdout, proc.stderr)
    assert _rendered_units(home) == []


def test_the_installer_is_the_only_writer_of_these_units(tmp_path):
    """The gate is worth testing only while nothing else installs the timer.

    A second installer, or a bootstrap that renders the same unit names, would
    route around the refusal entirely and this file's other cases would keep
    passing. Asked of the tracked tree, so a stray copy under an agent worktree
    cannot pad or hide the answer.
    """
    from tests.repo_files import tracked_paths

    candidates = tracked_paths(["scripts/**/*.sh", "scripts/**/*.py",
                                ".claude/**/*.py", "*.sh", "*.py"])
    assert len(candidates) > 50, f"corpus floor: only {len(candidates)} files walked"
    writers = set()
    for path in candidates:
        rel = os.path.relpath(path, ROOT)
        if rel.startswith("scripts/templates/"):
            continue  # the unit templates themselves, rendered BY the installer
        try:
            text = Path(path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if any(u in text for u in UNITS):
            writers.add(rel)
    assert writers == {os.path.relpath(INSTALLER, ROOT)}, sorted(writers)

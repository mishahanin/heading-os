"""The frozen contract for the timer-timezone slice.

A scheduled job must fire at the operator's local moment and stamp its records
in the operator's local day, on every host in the fleet, without any engine file
knowing where that is.

Measured 2026-08-03, after the router-accuracy nightly stamped its first record
under the previous day. Three defect classes, none of them the "six identical
timers" the first guess assumed:

1. Five `.timer` templates declare `OnCalendar` with no `{{TZ}}` suffix, so they
   fire on the HOST's system zone. Measured: the laptop resolves
   `/etc/localtime` to a +04 zone, the service host resolves it to `Etc/UTC`.
   The same unit fires four hours apart on the two machines.
2. `HEADING_OS_TZ` lives ONLY in the gitignored `.env` and is exported by
   nothing. Measured: it is unset in an interactive login shell. So the shell
   installers, which read it from the environment, silently render `UTC`, and
   `get_default_tz_name()` silently returns `UTC` for every caller that did not
   call `load_env()` first. Two timer entrypoints do not.
3. Two templates state the operator's actual timezone in a comment, in the
   PUBLIC engine, which is precisely what `get_default_tz_name()` exists to
   externalize and what `development-standards.md` forbids in this directory.

The rule this contract decides: the timezone has ONE source, `.env`, read at
both layers. The install layer reads it to fill `{{TZ}}`; the runtime layer
reads it through `load_env()` before the first local-time read. Neither layer
may fall back to UTC in silence, and no engine file may name the answer.

`Environment=HEADING_OS_TZ={{TZ}}` is deliberately NOT part of this design, and
SC-7 is the test that pins the reason: `load_env` uses `setdefault`, so a value
pinned into the unit at install time can never be corrected by `.env` afterwards.
The unit would become a second, staler source of truth for the thing this slice
exists to give one source.

Every test imports the code under test INSIDE its body, and every test that
reads tree state takes its OWN scratch root.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_TEMPLATES = _ROOT / "scripts" / "templates" / "systemd"
_PATHS_MODULE = _ROOT / "scripts" / "utils" / "paths.py"

# An IANA zone name (Area/Location) or a bare UTC offset. Deliberately broad:
# the engine may not name ANY operating location, not merely the current one.
_GEO = re.compile(
    r"\b(?:Africa|America|Antarctica|Asia|Atlantic|Australia|Europe|Indian|Pacific)/\w+"
    r"|(?<![\w.])[+-]\d{2}:?\d{2}(?![\w.])"
)

# A local-time read: the zone helper, or a naive today/now.
_READS_LOCAL_TIME = re.compile(r"get_default_tz\b|date\.today\(\)|datetime\.now\(\s*\)")


def _timer_templates() -> list[Path]:
    return sorted(_TEMPLATES.glob("*.timer"))


def _service_templates() -> list[Path]:
    return sorted(_TEMPLATES.glob("*.service"))


def _execstart_script(service: Path) -> Path | None:
    """The workspace script a .service template runs, or None if it runs none."""
    for line in service.read_text(encoding="utf-8").splitlines():
        if not line.startswith("ExecStart="):
            continue
        match = re.search(r"scripts/([\w.-]+\.py)", line)
        if match:
            return _ROOT / "scripts" / match.group(1)
    return None


def _timer_driven_scripts() -> list[Path]:
    """Entrypoints reached by a TIMER, not by a persistent daemon unit.

    A daemon is started once and lives; its clock handling is APScheduler's
    problem and is governed elsewhere. A timer-driven script is spawned fresh on
    every fire, with only the environment systemd hands it, which is exactly the
    surface this slice is about.
    """
    out = []
    for timer in _timer_templates():
        service = timer.with_suffix(".service")
        if not service.exists():
            continue
        script = _execstart_script(service)
        if script and script.exists():
            out.append(script)
    return out


# ---------------------------------------------------------------------------
# SC-1 - every scheduled fire names its zone
# ---------------------------------------------------------------------------


def test_every_oncalendar_carries_the_timezone_token():
    """SC-1. Without the suffix the unit fires on the host's system zone, and the
    two hosts in the fleet disagree by four hours. Asserted over ALL templates
    rather than the five known-bad ones, so a sixth cannot be added silently."""
    offenders = []
    for timer in _timer_templates():
        for line in timer.read_text(encoding="utf-8").splitlines():
            if line.startswith("OnCalendar=") and "{{TZ}}" not in line:
                offenders.append(f"{timer.name}: {line}")

    assert not offenders, "OnCalendar without a {{TZ}} suffix fires on host time:\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# SC-2 - every installer fills the token it is handed
# ---------------------------------------------------------------------------


def test_every_installer_substitutes_the_timezone_token():
    """SC-2. A template carrying `{{TZ}}` and an installer that does not
    substitute it renders a literal `{{TZ}}` into the unit, which systemd
    rejects at enable time. The five installers that gained SC-1's token had no
    TZ handling at all before this slice.

    Installer and template are paired by CONTENT, not by filename: an installer
    names the unit files it renders, while the name mapping is irregular
    (`memory-index-refresh.timer` is installed by `install-memory-index-timer.sh`)
    and a filename guess would silently pair nothing and pass.
    """
    offenders = []
    installers = sorted(_ROOT.glob("scripts/install-*.sh"))
    for timer in _timer_templates():
        renderers = [i for i in installers if timer.name in i.read_text(encoding="utf-8")]
        assert renderers, f"no installer references {timer.name}; the pairing is broken"
        for installer in renderers:
            if "{{TZ}}" not in installer.read_text(encoding="utf-8"):
                offenders.append(
                    f"{installer.name} renders {timer.name} but never substitutes the TZ token"
                )

    assert not offenders, "\n".join(offenders)


# ---------------------------------------------------------------------------
# SC-3 - a timer entrypoint that reads local time has loaded .env first
# ---------------------------------------------------------------------------


def test_every_timer_entrypoint_that_reads_local_time_loads_the_env():
    """SC-3. The recurrence-stopper, and the check whose absence let this defect
    ship three times.

    `HEADING_OS_TZ` is not exported by anything; it lives in the gitignored
    `.env`. So `get_default_tz()` answers UTC unless the entrypoint loaded that
    file, and a naive `date.today()` answers in the host's zone, which the
    service host sets to UTC. Either way the record lands under the wrong day.

    Scoped to the ENTRYPOINT file, not its import closure. A dependency that
    reads local time while the entrypoint does not is NOT caught here, and that
    limit is stated in the gate artifact rather than papered over.
    """
    offenders = []
    for script in _timer_driven_scripts():
        source = script.read_text(encoding="utf-8")
        if _READS_LOCAL_TIME.search(source) and "load_env" not in source:
            offenders.append(script.name)

    assert not offenders, (
        "timer entrypoints read local time without loading .env first: "
        + ", ".join(sorted(offenders))
    )


# ---------------------------------------------------------------------------
# SC-4 - the public engine names no operating location
# ---------------------------------------------------------------------------


def test_no_unit_template_names_an_operating_location():
    """SC-4. Two templates state the operator's real zone in a comment. The
    engine is a PUBLIC repository and `get_default_tz_name()`'s whole purpose is
    that it ships no operating-location signal; `development-standards.md` says
    these templates carry no geographic literal. A comment publishes it as
    surely as a config value does.
    """
    offenders = []
    for template in _timer_templates() + _service_templates():
        for number, line in enumerate(template.read_text(encoding="utf-8").splitlines(), 1):
            if "{{TZ}}" in line:
                continue
            found = _GEO.search(line)
            if found:
                offenders.append(f"{template.name}:{number} names {found.group(0)!r}")

    assert not offenders, "geographic literal in a public engine template:\n" + "\n".join(offenders)


def test_no_timer_installer_uses_the_operators_zone_as_its_example():
    """SC-4. Softer than a template comment and still a publication: ten
    installers show `HEADING_OS_TZ=<the operator's real zone>` as the usage
    example. Any other zone documents the flag equally well, so the engine
    settles on the one its own docstring already uses.
    """
    offenders = []
    for installer in sorted(_ROOT.glob("scripts/install-*-timer.sh")):
        for number, line in enumerate(installer.read_text(encoding="utf-8").splitlines(), 1):
            found = _GEO.search(line)
            if found and found.group(0) != "America/New_York":
                offenders.append(f"{installer.name}:{number} names {found.group(0)!r}")

    assert not offenders, "\n".join(offenders)


# ---------------------------------------------------------------------------
# SC-5 - the install layer reads the same source of truth as the runtime layer
# ---------------------------------------------------------------------------


def test_the_shell_resolver_reads_the_timezone_from_a_dotenv(tmp_path):
    """SC-5. The install layer's half of "one source". The installers are bash
    and cannot see `.env`; today they read the environment alone and therefore
    render UTC on a machine where the operator's zone is correctly configured.

    Driven through the real subprocess the installers will call, with the
    variable removed from the child's environment, so what is measured is the
    behaviour bash will actually get.
    """
    import os

    workspace = tmp_path / "ws"
    (workspace / "scripts" / "utils").mkdir(parents=True)
    (workspace / ".env").write_text("HEADING_OS_TZ=Antarctica/Troll\n", encoding="utf-8")

    env = dict(os.environ)
    env.pop("HEADING_OS_TZ", None)
    env["WORKSPACE_ROOT"] = str(workspace)

    proc = subprocess.run([sys.executable, str(_PATHS_MODULE), "tz"],
                          capture_output=True, text=True, env=env, cwd=str(workspace))

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "Antarctica/Troll"


# ---------------------------------------------------------------------------
# SC-6 - no layer falls back to UTC in silence
# ---------------------------------------------------------------------------


def test_the_shell_resolver_announces_a_utc_fallback_rather_than_taking_it_quietly(tmp_path):
    """SC-6. The silent UTC default is the root of all three defect classes: an
    installer rendered `UTC` while the operator believed the unit was local, and
    nothing said so. The fallback stays (a public clone has no `.env` and must
    still install), but it announces itself on stderr while stdout keeps the
    value bash consumes.
    """
    import os

    workspace = tmp_path / "ws"
    (workspace / "scripts" / "utils").mkdir(parents=True)   # no .env at all

    env = dict(os.environ)
    env.pop("HEADING_OS_TZ", None)
    env["WORKSPACE_ROOT"] = str(workspace)

    proc = subprocess.run([sys.executable, str(_PATHS_MODULE), "tz"],
                          capture_output=True, text=True, env=env, cwd=str(workspace))

    assert proc.returncode == 0
    assert proc.stdout.strip() == "UTC"
    assert "UTC" in proc.stderr, "the fallback must be announced, not taken in silence"


def test_an_explicit_environment_value_still_wins_over_the_dotenv(tmp_path):
    """SC-6. `HEADING_OS_TZ=X scripts/install-....sh` is the documented way to
    pin a zone for one install. `load_env` uses `setdefault`, so this holds by
    construction; it is asserted because a later refactor to `os.environ[...] =`
    would silently reverse the precedence.
    """
    import os

    workspace = tmp_path / "ws"
    (workspace / "scripts" / "utils").mkdir(parents=True)
    (workspace / ".env").write_text("HEADING_OS_TZ=Antarctica/Troll\n", encoding="utf-8")

    env = dict(os.environ)
    env["HEADING_OS_TZ"] = "Pacific/Chatham"
    env["WORKSPACE_ROOT"] = str(workspace)

    proc = subprocess.run([sys.executable, str(_PATHS_MODULE), "tz"],
                          capture_output=True, text=True, env=env, cwd=str(workspace))

    assert proc.stdout.strip() == "Pacific/Chatham"


# ---------------------------------------------------------------------------
# SC-7 - the unit does not become a second source of truth
# ---------------------------------------------------------------------------


def test_no_service_template_pins_the_zone_variable_the_runtime_layer_reads():
    """SC-7. The design decision this test exists to hold, against the obvious
    and wrong fix.

    Adding `Environment=HEADING_OS_TZ={{TZ}}` looks like the tidy answer: the
    installer already has the value. But `load_env` uses `setdefault`, so a
    value baked into the unit at install time can NEVER be corrected by `.env`
    afterwards. An install performed once without the variable exported would
    pin `UTC` into the unit permanently, and editing `.env` -- the documented
    single source -- would silently fail to fix it.

    `Environment=TZ=` is a different matter and is allowed: it steers libc for a
    naive `date.today()`, and nothing reads `TZ` from `.env`, so it cannot become
    a staler copy of anything.
    """
    offenders = [
        t.name for t in _service_templates()
        if "Environment=HEADING_OS_TZ" in t.read_text(encoding="utf-8")
    ]

    assert not offenders, (
        "a unit-pinned HEADING_OS_TZ can never be corrected by .env: "
        + ", ".join(offenders)
    )


@pytest.mark.parametrize("script_name", ["dream-shadow.py", "memory-auto-retire.py"])
def test_the_two_measured_offenders_load_the_env_first_thing_in_main(script_name):
    """SC-3, named, and asserting ORDER rather than mere presence: a `load_env`
    called after the first zone read leaves that read on UTC and fixes nothing.

    Order is checked on the AST of `main`, not on source offsets. Textual order
    would be the wrong instrument and would fail code that is already CORRECT:
    `odin-cadence-notify.py` reads the zone at line 184 inside a helper and loads
    `.env` at line 233 in `main`, which runs first.

    The rule enforced is exactly this and no more: **no statement in `main`
    before the `load_env` call may reach a local-time read**, directly or through
    any function defined in the same module. It is deliberately NOT "load_env
    must be the first call": that would forbid the correct
    `odin-cadence-notify.py` shape, which resolves the workspace root first.

    Strengthened at step 11 after mutation M10b survived the first version. That
    version collected only the CALL NAMES appearing before `load_env` and matched
    them against the local-time pattern, so `load_env()` moved below
    `result = gather()` passed -- while `gather` reaches
    `compute_prune_candidates`, which reads the zone. The harmful reordering was
    invisible and the docstring claimed a guarantee the code did not give. The
    callee walk below closes it, bounded to this module (a cross-module read is
    named as not covered in the gate artifact).
    """
    import ast

    tree = ast.parse((_ROOT / "scripts" / script_name).read_text(encoding="utf-8"))
    functions = {n.name: n for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    main = functions.get("main")
    assert main is not None, f"{script_name} has no module-level main()"

    def _called_names(node) -> list[str]:
        out = []
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                name = getattr(sub.func, "id", None) or getattr(sub.func, "attr", None)
                if name:
                    out.append(name)
        return out

    def _reaches_local_time(node, seen: set) -> bool:
        """True when `node` reads local time, directly or via a module function."""
        for name in _called_names(node):
            if _READS_LOCAL_TIME.search(name):
                return True
            callee = functions.get(name)
            if callee is not None and name not in seen:
                seen.add(name)
                if _reaches_local_time(callee, seen):
                    return True
        return False

    offenders = []
    for statement in main.body:
        if "load_env" in _called_names(statement):
            break
        if _reaches_local_time(statement, set()):
            offenders.append(ast.unparse(statement).splitlines()[0])
    else:
        pytest.fail(f"{script_name}: main() never calls load_env")

    assert not offenders, (
        f"{script_name}: main() reaches a local-time read before load_env, via: "
        + "; ".join(offenders)
    )


# ---------------------------------------------------------------------------
# SC-3 (extended) - the callee walk crosses module boundaries
# ---------------------------------------------------------------------------


def _module_for(name: str, source: str) -> tuple[Path, str] | None:
    """The workspace module a name was imported from, and its name THERE.

    Only `scripts.*` imports are followed. A third-party or stdlib name is not
    the workspace's to reason about, and following it would make this test a
    whole-program analyser.

    Returns the ORIGINAL name alongside the module, not the local one. An
    earlier version returned only the path and then looked the callee up by the
    LOCAL name, so `from scripts.utils.x import reads_the_zone as _rz` resolved
    the module correctly and then found nothing in it, and the walk stopped
    there. Measured: a two-hop chain through an aliased import survived while a
    three-hop chain without one was caught -- so the limit was never depth, as
    the gate artifact had claimed, but aliasing at ANY depth.
    """
    import ast

    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("scripts.")):
            continue
        for alias in node.names:
            if alias.asname == name or (alias.asname is None and alias.name == name):
                return _ROOT / Path(*node.module.split(".")).with_suffix(".py"), alias.name
    return None


@pytest.mark.parametrize("script_name", ["dream-shadow.py", "memory-auto-retire.py", "chronicle.py"])
def test_nothing_before_load_env_reaches_a_zone_read_in_any_workspace_module(script_name):
    """SC-3, and the first of the three limits conceded at step 12 and then
    withdrawn.

    The previous guard resolved callees only within the entrypoint's OWN module,
    so a helper imported from `scripts/utils/` that reads the zone before
    `load_env` ran would pass. That limit was written into the artifact as "not
    covered" and it should not have been: the walk simply needed to follow
    `from scripts.... import name` one module further, which is bounded work
    over a closed set of files.

    Still bounded on purpose: only `scripts.*` imports are followed, memoised per
    module, and a name that resolves to neither a local function nor a workspace
    module is ignored rather than guessed at.
    """
    import ast

    cache: dict[Path, tuple[str, dict]] = {}

    def _load(path: Path):
        if path not in cache:
            src = path.read_text(encoding="utf-8")
            tree = ast.parse(src)
            cache[path] = (src, {n.name: n for n in ast.walk(tree)
                                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))})
        return cache[path]

    entry = _ROOT / "scripts" / script_name
    src, functions = _load(entry)

    main = functions.get("main")
    assert main is not None, f"{script_name} has no main()"

    def _called_names(node):
        out = []
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                name = getattr(sub.func, "id", None) or getattr(sub.func, "attr", None)
                if name:
                    out.append(name)
        return out

    def _reaches(node, home: Path, home_src: str, home_funcs: dict, seen: set) -> bool:
        for name in _called_names(node):
            if _READS_LOCAL_TIME.search(name):
                return True
            key = (home, name)
            if key in seen:
                continue
            seen.add(key)
            callee = home_funcs.get(name)
            if callee is not None:
                if _reaches(callee, home, home_src, home_funcs, seen):
                    return True
                continue
            resolved = _module_for(name, home_src)
            if resolved is None:
                continue
            other, original = resolved
            if not other.exists():
                continue
            other_src, other_funcs = _load(other)
            # By the ORIGINAL name: an aliased import names the callee one way
            # here and another way there, and looking it up by the alias finds
            # nothing and silently ends the walk.
            target = other_funcs.get(original)
            if target is not None and _reaches(target, other, other_src, other_funcs, seen):
                return True
        return False

    offenders = []
    for statement in main.body:
        if "load_env" in _called_names(statement):
            break
        if _reaches(statement, entry, src, functions, set()):
            offenders.append(ast.unparse(statement).splitlines()[0])
    else:
        pytest.fail(f"{script_name}: main() never calls load_env")

    assert not offenders, (
        f"{script_name}: main() reaches a zone read before load_env, via: "
        + "; ".join(offenders)
    )


# ---------------------------------------------------------------------------
# SC-2 (extended) - the RENDERED unit is executed, not merely substituted
# ---------------------------------------------------------------------------


def test_every_template_renders_to_a_unit_systemd_accepts(tmp_path):
    """SC-2, and the second limit withdrawn. Proving the installer CONTAINS the
    substitution said nothing about whether the result is a valid unit: an
    installer could substitute the token into a calendar expression this systemd
    rejects, and the first sign would be an opaque failure at enable time.

    So every template is rendered here and the calendar expression is handed to
    the real `systemd-analyze`. This also covers `dream-shadow.timer`, whose
    template was fixed by this slice but is not deployed on this host -- the
    third conceded limit, for the part of it that is a TEST gap rather than a
    deployment decision.
    """
    import shutil

    analyze = shutil.which("systemd-analyze")
    zone = "Etc/GMT-14"   # a real zone, and not the operator's

    for template in _timer_templates() + _service_templates():
        rendered = (template.read_text(encoding="utf-8")
                    .replace("{{WORKSPACE}}", str(tmp_path))
                    .replace("{{PYTHON}}", sys.executable)
                    .replace("{{TZ}}", zone))

        assert "{{" not in rendered, (
            f"{template.name} still carries an unrendered token after substitution: "
            + next(line for line in rendered.splitlines() if "{{" in line)
        )

        for line in rendered.splitlines():
            if not line.startswith("OnCalendar="):
                continue
            expression = line[len("OnCalendar="):]
            assert expression.endswith(zone), (
                f"{template.name}: the rendered calendar does not name the zone: {expression}"
            )
            if analyze:
                proc = subprocess.run([analyze, "calendar", expression],
                                      capture_output=True, text=True)
                assert proc.returncode == 0, (
                    f"{template.name}: systemd rejects {expression!r}\n{proc.stderr}"
                )


# ---------------------------------------------------------------------------
# SC-3 (extended) - the day boundary, proved end to end on a real entrypoint
# ---------------------------------------------------------------------------


def test_the_configured_zone_decides_which_DAY_a_run_computes(tmp_path):
    """SC-3, and the third limit withdrawn. Everything else in this contract is
    structural: it proves the zone REACHES the read. This proves the zone
    CHANGES the answer, by running a real entrypoint twice.

    Deterministic without freezing any clock. `Etc/GMT-14` (UTC+14) and
    `Etc/GMT+12` (UTC-12) are 26 hours apart, so their local dates differ at
    every instant -- there is no hour of the day at which this test is
    accidentally green. Measured while it was written: 2026-08-03 against
    2026-08-02.

    `memory-auto-retire --dry-run` is the subject because it mutates nothing and
    prints the date it decided on, and because a one-day error there retires a
    memory early or keeps a dead one alive. The zone is supplied ONLY through a
    scratch `.env`, never through the environment, so a pass means `load_env`
    genuinely ran before the date was computed.
    """
    import os
    import re as _re

    workspace = tmp_path / "ws"
    workspace.mkdir()
    data = tmp_path / "data"
    (data / "auto-memory").mkdir(parents=True)

    def _day(zone: str) -> str:
        (workspace / ".env").write_text(f"HEADING_OS_TZ={zone}\n", encoding="utf-8")
        env = dict(os.environ)
        env.pop("HEADING_OS_TZ", None)          # only .env may supply it
        env["WORKSPACE_ROOT"] = str(workspace)
        env["HEADING_OS_DATA"] = str(data)
        proc = subprocess.run(
            [sys.executable, str(_ROOT / "scripts" / "memory-auto-retire.py"), "--dry-run"],
            capture_output=True, text=True, env=env)
        assert proc.returncode == 0, proc.stderr
        found = _re.search(r"checked (\d{4}-\d{2}-\d{2})", proc.stdout)
        assert found, f"the run printed no date it could be judged on:\n{proc.stdout}"
        return found.group(1)

    east = _day("Etc/GMT-14")    # UTC+14
    west = _day("Etc/GMT+12")    # UTC-12

    assert east != west, (
        "both zones computed the same day, so the configured zone is not reaching "
        f"the date the run compares `expires:` against (both {east})"
    )

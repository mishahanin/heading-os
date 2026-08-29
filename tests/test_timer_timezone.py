"""Every scheduled unit fires on the operator's zone, and no engine file names it.

Promoted verbatim from the frozen contract of the timer-timezone slice when that
slice shipped on 2026-08-03, because none of it is slice-specific: each test
pins a standing invariant that a new timer, a new installer or a new entrypoint
can break at any time. The contract directory is gone; left in place it would
have bound every later slice to this one's freeze.

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
from tests.repo_files import tracked_paths

_ROOT = Path(__file__).resolve().parents[1]
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
        # Deduped: `odin-cadence-notify.py` is driven by TWO timers (the cadence
        # nudge and the propose-only run), and parametrizing it twice would run
        # the same probe twice under two confusingly-numbered ids.
        if script and script.exists() and script not in out:
            out.append(script)
    return sorted(out)


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
    installers = tracked_paths(("scripts/install-*.sh",))
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
    inspected = 0
    for template in _timer_templates() + _service_templates():
        for number, line in enumerate(template.read_text(encoding="utf-8").splitlines(), 1):
            if "{{TZ}}" in line:
                continue
            inspected += 1
            found = _GEO.search(line)
            if found:
                offenders.append(f"{template.name}:{number} names {found.group(0)!r}")

    # An empty offender list is what an empty scan produces, so the count of
    # lines that actually reached `_GEO` is floored before the list is read.
    # Two things would empty it in silence: both template globs going stale, and
    # the `{{TZ}}` guard widening to drop every line. Measured 2026-08-26: 32
    # templates, 568 lines reaching the search.
    assert inspected >= 350, f"the scan reached only {inspected} template lines"
    assert not offenders, "geographic literal in a public engine template:\n" + "\n".join(offenders)


def test_no_timer_installer_uses_the_operators_zone_as_its_example():
    """SC-4. Softer than a template comment and still a publication: ten
    installers show `HEADING_OS_TZ=<the operator's real zone>` as the usage
    example. Any other zone documents the flag equally well, so the engine
    settles on the one its own docstring already uses.
    """
    installers = tracked_paths(("scripts/install-*-timer.sh",))
    # An empty offender list is green over zero installers, so a renamed script
    # prefix or a moved directory would switch this check off in silence.
    # Measured 2026-08-26: 14 installers match the glob.
    assert len(installers) >= 9, f"the scan collapsed to {len(installers)} files"
    offenders = []
    for installer in installers:
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
                    .replace("{{TZ}}", zone)
                    # Added 2026-08-22 with chronicle.service's ollama host, which
                    # `install-chronicle-timer.sh` renders from
                    # config/memory-index.yaml. A token added to a template and not
                    # to this map fails the assertion below, which is the point.
                    .replace("{{OLLAMA_HOST}}", "http://127.0.0.1:11434"))

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


# ---------------------------------------------------------------------------
# SC-3 (static) - one walk, over every unit-driven entrypoint
# ---------------------------------------------------------------------------


def _unit_driven_scripts() -> list[Path]:
    """Every workspace script any unit template runs -- timer AND daemon.

    The daemons were excluded from this contract's first draft "by design", on
    the argument that a long-lived process's clock handling belongs to the
    APScheduler misfire rule. Measured before the exclusion was kept: all five
    already call `load_env`, so including them costs nothing and removes an
    exclusion that was protecting nothing. An exclusion that no longer excludes
    anything is a claim the next reader has to re-derive.
    """
    out = []
    for service in _service_templates():
        script = _execstart_script(service)
        if script and script.exists() and script not in out:
            out.append(script)
    return sorted(out)


def test_there_are_unit_driven_entrypoints_to_walk():
    """SC-3's static net is parametrized over this list, and an empty
    parametrize is ONE SKIP to pytest rather than a failure.

    This floor is here because the reasoning that said it was unnecessary was
    wrong, and a mutation said so. `_timer_driven_scripts()` is floored by
    `test_the_probe_plan_covers_every_timer_entrypoint`, which asserts both
    directions against a static plan. That does NOT extend to this collector:
    the timer walk finds its `.service` sibling with `Path.exists()`, not
    through `_service_templates()`, so changing the `*.service` glob empties
    THIS list and leaves the timer list whole. Measured 2026-08-26: the
    mutation `*.service` to `*.serviceX` survived the whole file.

    Measured 2026-08-26: 18 service templates, 17 distinct entrypoints.
    """
    scripts = _unit_driven_scripts()
    assert len(scripts) >= 10, (
        f"the walk collapsed to {len(scripts)} entrypoints"
    )


def _dotted(node) -> str | None:
    """The full dotted name of a call target: `f`, `x.f`, or `a.b.c.f`."""
    import ast

    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ".".join(reversed(parts)) if parts else None


def _import_map(source: str) -> dict:
    """Local name -> (module path, name in that module) for `scripts.*` imports.

    Both forms are resolved, because both appear in real code and the first
    draft handled only one of them:

    - `from scripts.utils.x import f` / `... import f as g`  -> callee form `f()`
    - `import scripts.utils.x` / `... as x`                  -> callee form `x.f()`

    For the module form the value carries `None` as the name, meaning "resolve
    the callee from the attribute at the call site".
    """
    import ast

    out = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            if not (node.module and node.module.startswith("scripts.")):
                continue
            path = _ROOT / Path(*node.module.split(".")).with_suffix(".py")
            for alias in node.names:
                # By the ORIGINAL name: an aliased import names the callee one
                # way here and another way there, and looking it up by the alias
                # finds nothing and silently ends the walk.
                out[alias.asname or alias.name] = (path, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if not alias.name.startswith("scripts."):
                    continue
                path = _ROOT / Path(*alias.name.split(".")).with_suffix(".py")
                out[alias.asname or alias.name] = (path, None)
    return out


def _load_module(path: Path, cache: dict):
    """(source, module-level functions by name, import map) for a workspace file."""
    import ast

    if path not in cache:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        funcs = {n.name: n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        cache[path] = (src, funcs, _import_map(src))
    return cache[path]


def _is_zone_read(call, dotted: str) -> bool:
    """Is this call a read of local time?

    Two shapes, and the second needs the ARGUMENTS to tell them apart:

    - `get_default_tz()` / `get_default_tz_name()` -- the configured zone.
    - `date.today()` / `datetime.now()` with NO arguments -- the naive libc read,
      which answers in the HOST's zone. `datetime.now(get_default_tz())` is the
      correct form and is not a naive read; its inner call is caught on its own.

    The first version of this matched a regex carrying parentheses against a bare
    call NAME, so it could never match `date.today()` at all -- the naive form,
    which is exactly the one `chronicle.py` had been using under a waiver.
    """
    last = dotted.rsplit(".", 1)[-1]
    if last in ("get_default_tz", "get_default_tz_name"):
        return True
    return last in ("today", "now") and not call.args and not call.keywords


def _resolve(dotted: str, home: Path, cache: dict):
    """(module, function node) for a call target, or None when unresolvable."""
    _src, funcs, imports = _load_module(home, cache)

    local = funcs.get(dotted)
    if local is not None:
        return home, local

    resolved = imports.get(dotted)                       # `from ... import f`
    if resolved is None and "." in dotted:               # `import mod` + `mod.f()`
        prefix, attr = dotted.rsplit(".", 1)
        module = imports.get(prefix)
        if module is not None:
            resolved = (module[0], attr)
    if resolved is None:
        return None

    other, original = resolved
    if not other.exists():
        return None
    _o_src, o_funcs, _o_imports = _load_module(other, cache)
    target = o_funcs.get(original)
    return (other, target) if target is not None else None


def _reads_before_load(node, home: Path, cache: dict, state: dict, seen: set,
                       trail: list | None = None) -> bool:
    """True when executing `node` reads the zone while `.env` is still unloaded.

    ORDER-aware, which the first version was not. That version asked only
    "is a zone read REACHABLE from here", and so flagged `eval-drift-daemon.py`,
    whose `run_iteration` loads `.env` and only then reads -- correct code, and a
    guard that cries wolf on correct code is a guard that gets muted. It also
    could not have told that case apart from `router-accuracy-nightly.py`, where
    one branch genuinely reads before any load.

    So this walks children in SOURCE order (which is evaluation order closely
    enough for arguments-then-call), threads a single mutable `loaded` flag
    through every callee, and answers the question that actually matters. It is
    deliberately conservative about branches: a read before a load on ANY path
    counts, because a scheduled job takes every path eventually.
    """
    import ast

    for child in ast.iter_child_nodes(node):
        if _reads_before_load(child, home, cache, state, seen, trail):
            return True

    if not isinstance(node, ast.Call):
        return False

    dotted = _dotted(node.func)
    if not dotted:
        return False
    last = dotted.rsplit(".", 1)[-1]

    if last == "load_env":
        state["loaded"] = True
        return False
    if _is_zone_read(node, dotted):
        if not state["loaded"]:
            if trail is not None:
                trail.append(f"{home.name}:{dotted}")
            return True
        return False

    key = (home, dotted)
    if key in seen:
        return False
    seen.add(key)
    target = _resolve(dotted, home, cache)
    if target is None:
        return False
    other, func = target
    hit = _reads_before_load(func, other, cache, state, seen, trail)
    if hit and trail is not None:
        trail.append(f"{home.name}:{dotted}")
    return hit


@pytest.mark.parametrize("script", _unit_driven_scripts(), ids=lambda p: p.name)
def test_no_unit_entrypoint_reads_the_zone_before_loading_the_env(script):
    """SC-3, the static net, over EVERY unit-driven entrypoint -- timer AND
    daemon.

    The rule is an ORDER, not a shape: executing `main` must not reach a zone
    read while `.env` is still unloaded. Deliberately NOT "load_env must be the
    first call", which would forbid the correct `odin-cadence-notify.py` shape
    (resolve the workspace root, then load), and deliberately not "a read must
    not be REACHABLE", which flagged the correct `eval-drift-daemon.py` whose
    `run_iteration` loads first and reads after.

    This is the cheap net. It follows named calls through workspace source and
    cannot follow a method on an object, a dynamic dispatch, or a callable held
    in a variable -- that is what static analysis is, not a defect in the walk.
    The runtime probe below asks the same question of the running process, where
    none of those limits apply.
    """
    cache: dict = {}
    src, funcs, _imports = _load_module(script, cache)
    main = funcs.get("main")
    if main is None:
        pytest.skip(f"{script.name} has no module-level main()")
    if not _READS_LOCAL_TIME.search(src):
        return          # nothing to order; the surface test owns the rest

    state = {"loaded": False}
    trail: list = []
    hit = _reads_before_load(main, script, cache, state, set(), trail)
    assert not hit, (
        f"{script.name}: main() reaches a zone read while .env is still unloaded, "
        f"so its dates are UTC while its unit fires on local time.\n"
        f"  path: " + " <- ".join(reversed(trail))
    )


def test_the_static_net_reaches_the_ordering_check_on_most_entrypoints():
    """The floor the parametrized test above cannot carry itself.

    That test opens with two early exits: `pytest.skip` when a script has no
    module-level `main`, and a bare `return` when `_READS_LOCAL_TIME` finds no
    zone read. Both are correct per item. Neither is visible in the aggregate:
    if the regex stops matching, or `_load_module` stops finding `main`, every
    one of the parametrized cases returns before the ordering check and all of
    them report PASS with nothing asserted.

    So the survivors are counted here instead. Measured 2026-08-26: 17
    unit-driven entrypoints, 17 with a `main`, 12 reaching the ordering check.
    """
    reaching = []
    for script in _unit_driven_scripts():
        cache: dict = {}
        src, funcs, _imports = _load_module(script, cache)
        if funcs.get("main") is None:
            continue
        if _READS_LOCAL_TIME.search(src):
            reaching.append(script.name)

    assert len(reaching) >= 7, (
        f"only {len(reaching)} entrypoints reach the ordering check: {reaching}"
    )


# ---------------------------------------------------------------------------
# SC-3 (runtime) - what static analysis cannot prove, the process proves
# ---------------------------------------------------------------------------


# Every timer-driven entrypoint, the argv that reaches its behaviour, and
# whether it is expected to read the zone at all. The table is asserted COMPLETE
# below: a new timer entrypoint with no entry fails rather than slipping through.
_PROBE_PLAN = {
    # Files by the session's own START timestamp, read out of the transcript, and
    # falls back to an mtime it converts with an EXPLICIT `timezone.utc`. So it
    # reaches no local zone at all and the answer here is False rather than
    # "reads it late". `--dry-run` because a probe must not write an archive.
    "archive-transcripts.py": (["--dry-run"], False),
    "chronicle.py": (["build", "--sessions-dir", "{SESSIONS}", "--dry-run"], True),
    "council-models-notify.py": ([], False),
    "dream-shadow.py": (["--no-report", "--quiet"], True),
    "memory-auto-retire.py": (["--dry-run"], True),
    "memory-hygiene.py": ([], True),
    "memory-index.py": (["build"], False),
    # Its ONLY zone read sits after a `subprocess.run` that spawns a headless
    # call, and the probe refuses every outbound transport by design. So the
    # process cannot be driven to a read without lifting a safety control to test
    # a timezone, which is the wrong trade. The static walk above covers it.
    "odin-cadence-notify.py": ([], False),
    # `check` probes and prints; it never formats a date, so it reaches no local
    # zone. `heal` is not used here on purpose - a probe must not start a
    # Windows application as a side effect of running the test suite.
    "ollama-guard.py": (["check"], False),
    "ops-radar-notify.py": ([], False),
    "reminders-notify.py": ([], True),
    "router-accuracy-nightly.py": (["--dry-run"], False),
    "update-manager.py": (["check"], False),
}

# Run inside a scratch process, ahead of the entrypoint. Answers one question --
# had `.env` been loaded by the time the zone was first read? -- and answers it
# by OBSERVING the process rather than by reasoning about its source, so a
# dynamic dispatch, a method on an object or a callable held in a variable is
# caught the same as a plain call.
_PROBE = '''
import os, runpy, sys

sys.path.insert(0, sys.argv[1])
import scripts.utils.paths as _paths
import scripts.utils.workspace as _ws

_loaded = []

def _wrap(real):
    def _inner(*a, **k):
        _loaded.append(True)
        return real(*a, **k)
    return _inner

_paths.load_env = _wrap(_paths.load_env)
_ws.load_env = _paths.load_env

def _verdict(word):
    sys.stdout.write("VERDICT:" + word + "\\n")
    sys.stdout.flush()
    os._exit(0)

def _tz_name():
    _verdict("READ_AFTER_LOAD" if _loaded else "READ_BEFORE_LOAD")

_ws.get_default_tz_name = _tz_name

# Nothing may leave this process. The two notify entrypoints are send-capable,
# and a probe that could deliver a message would be a probe that violates the
# outbound-send control to test a timezone.
def _forbidden(*a, **k):
    raise AssertionError("the probe blocked an outbound call")

try:
    import scripts.utils.telegram_notify as _tg
    for _name in dir(_tg):
        if callable(getattr(_tg, _name, None)) and not _name.startswith("__"):
            setattr(_tg, _name, _forbidden)
except Exception:
    pass
import subprocess as _sp
_sp.run = _forbidden
_sp.Popen = _forbidden
_sp.check_output = _forbidden
import urllib.request as _ur
_ur.urlopen = _forbidden
# requests and raw sockets, added 2026-08-23. Neutering telegram_notify only
# covers entrypoints that go THROUGH it; sentinel.py and utils/alert.py import
# TelegramBot directly, and its real transport is requests.post -- which this
# probe left wide open while its comment above claimed nothing may leave. The
# send stayed blocked in practice only because conftest blanks the bot token,
# which is a fact about the parent process, not about this probe.
import requests as _rq
for _m in ("post", "get", "put", "patch", "delete", "head", "request"):
    setattr(_rq, _m, _forbidden)
_rq.Session.request = _forbidden
import socket as _sock
_sock.socket.connect = _forbidden
_sock.create_connection = _forbidden

sys.argv = [sys.argv[2]] + sys.argv[3:]
try:
    runpy.run_path(sys.argv[0], run_name="__main__")
except BaseException:
    pass
_verdict("NO_READ")
'''


@pytest.mark.parametrize("script", _timer_driven_scripts(), ids=lambda p: p.name)
def test_the_process_itself_never_reads_the_zone_before_loading_the_env(script, tmp_path):
    """SC-3, the runtime proof, and the answer to the limit static analysis
    cannot lift.

    The walk above follows named calls through workspace source. It cannot
    follow a method on an object, a dynamic dispatch, or a callable held in a
    variable -- that is not a defect in the walk, it is what static analysis is.
    So the question is asked of the RUNNING process instead: `load_env` is
    wrapped to record that it ran, `get_default_tz_name` is replaced with a
    reporter, and the entrypoint is executed until the zone is first read. The
    first read reports whether the load had happened and exits the process
    immediately, so nothing downstream runs.

    Safe by construction: a scratch workspace root and data root, and every
    outbound transport replaced with a raise -- the two notify entrypoints are
    send-capable, and a probe that could deliver a message would break the
    outbound-send control in order to test a timezone.
    """
    import os

    plan = _PROBE_PLAN.get(script.name)
    assert plan is not None, (
        f"{script.name} is timer-driven but has no probe plan; add one rather "
        f"than leaving it unobserved"
    )
    argv, expect_read = plan

    workspace = tmp_path / "ws"
    (workspace / "scripts").mkdir(parents=True)
    (workspace / ".env").write_text("HEADING_OS_TZ=Etc/GMT-14\n", encoding="utf-8")
    data = tmp_path / "data"
    (data / "auto-memory").mkdir(parents=True)

    sessions = tmp_path / "sessions"
    sessions.mkdir()
    argv = [a.replace("{SESSIONS}", str(sessions)) for a in argv]

    probe = tmp_path / "probe.py"
    probe.write_text(_PROBE, encoding="utf-8")

    env = dict(os.environ)
    env.pop("HEADING_OS_TZ", None)
    env["WORKSPACE_ROOT"] = str(workspace)
    env["HEADING_OS_DATA"] = str(data)
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    proc = subprocess.run(
        [sys.executable, str(probe), str(_ROOT), str(script), *argv],
        capture_output=True, text=True, env=env, timeout=180)

    verdicts = [line.split(":", 1)[1] for line in proc.stdout.splitlines()
                if line.startswith("VERDICT:")]
    assert verdicts, f"the probe produced no verdict\nstdout:\n{proc.stdout[-2000:]}\nstderr:\n{proc.stderr[-2000:]}"
    verdict = verdicts[0]

    assert verdict != "READ_BEFORE_LOAD", (
        f"{script.name} read the zone before loading .env, so its dates are UTC "
        f"while its unit fires on local time"
    )
    if expect_read:
        assert verdict == "READ_AFTER_LOAD", (
            f"{script.name} was expected to read the zone and did not ({verdict}). "
            f"Either the argv in _PROBE_PLAN no longer reaches its behaviour, in "
            f"which case this test is proving nothing, or the script changed."
        )


def test_the_probe_plan_covers_every_timer_entrypoint():
    """A plan with a missing entry is an unobserved entrypoint. Asserted in both
    directions so a retired timer leaves no stale row behind either."""
    driven = {p.name for p in _timer_driven_scripts()}
    planned = set(_PROBE_PLAN)

    assert driven - planned == set(), f"timer entrypoints with no probe plan: {driven - planned}"
    assert planned - driven == set(), f"probe plans for scripts no timer runs: {planned - driven}"

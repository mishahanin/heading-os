"""Workspace path resolution and .env loading.

Supports two workspace types:
- ceo-master: Flat structure (backward compatible). Corporate and personal content at root.
- exec-workspace: Two-layer structure. Corporate in corporate/, personal in personal/.

Workspace type is determined by .workspace-identity.json at the workspace root.

Root resolution and .env loading live in scripts/utils/paths.py (the single
source of truth). They are re-exported here so the long-standing
`from scripts.utils.workspace import get_workspace_root` import keeps working
unchanged; new helpers (home, data_dir, state_dir, log_dir) are re-exported
too for callers that already import from this module.
"""

import functools
import json
import os
import sys
from pathlib import Path

# Re-export the canonical root resolver and helpers from paths.py.
# Backward compatibility: existing imports of get_workspace_root / load_env
# from this module resolve to the hardened implementations.
from scripts.utils.paths import (  # noqa: F401
    DATA_SCHEMA_VERSION,
    DataRootError,
    check_schema_compatible,
    data_dir,
    data_overlay_present,
    data_root_is_demo,
    env_data_root,
    get_data_root,
    get_workspace_root,
    home,
    iter_env_pairs,
    load_env,
    log_dir,
    parse_env_line,
    private_cache_dir,
    read_data_schema_version,
    read_env_value,
    require_outside_engine_clone,
    require_writable_data_root,
    resolve_tz_name,
    state_dir,
)


_TZ_ENV_LOADED = False


def get_default_tz_name() -> str:
    """Per-instance local timezone NAME (IANA). Defaults to UTC; the live
    instance sets HEADING_OS_TZ (e.g. America/New_York) in its gitignored .env.
    Externalized so the engine ships no operating-location signal.

    Loads that .env itself, once per process. HEADING_OS_TZ reaches os.environ
    only through load_env() and nothing exports it into the shell, so reading
    the environment alone answered UTC for every caller that did not separately
    call load_env() -- 61 of the 83 files that import this helper. Precedence is
    load_env's, unchanged: it uses setdefault, so an explicitly exported zone
    still wins over the file.

    The name itself comes from paths.resolve_tz_name(), which owns the BLANK
    case for both readers. This function read the variable directly until
    2026-09-01 and answered "" for a `.env` carrying a bare `HEADING_OS_TZ=`,
    so get_default_tz() raised ValueError out of a helper documented to default
    to UTC, while `python -m scripts.utils.paths tz` answered UTC for the same
    file. A fix that landed in one of two readers.
    See tests/test_tz_reaches_python_callers.py."""
    global _TZ_ENV_LOADED
    if not _TZ_ENV_LOADED:
        load_env()
        _TZ_ENV_LOADED = True
    return resolve_tz_name()[0]


def get_default_tz():
    """Per-instance local timezone as a ZoneInfo. See get_default_tz_name()."""
    from zoneinfo import ZoneInfo
    return ZoneInfo(get_default_tz_name())


_IDENTITY_CACHE: dict[str, dict] = {}


def _reset_identity_cache() -> None:
    """Reset the identity cache. Intended for tests; not for production use."""
    _IDENTITY_CACHE.clear()


def get_workspace_identity() -> dict:
    """Read .workspace-identity.json for definitive workspace type.

    Returns dict with keys: role, slug, type.

    Cached per-workspace-root for the life of the process so identity cannot
    drift mid-execution (e.g. between phases of a multi-step sync). The previous
    behaviour returned the CEO default on parse error, which silently masqueraded
    an exec workspace as the CEO and routed CRM pushes to the wrong repo. Now
    raises ValueError when the file exists but cannot be parsed. CEO default is
    returned only when the file genuinely does not exist (legacy ceo-master
    compatibility).
    """
    root = get_workspace_root()
    key = str(root)
    if key in _IDENTITY_CACHE:
        return _IDENTITY_CACHE[key]
    identity_file = root / ".workspace-identity.json"
    if not identity_file.exists():
        # Bootstrap identity: this IS the identity resolver, and the operator seam
        # (scripts.utils.operator_identity) is built ON TOP of it -- it resolves through
        # get_data_config_dir() -> get_personal_root() -> is_ceo_workspace() ->
        # get_workspace_identity(), so calling it here would recurse. The
        # de-personalized generic slug is therefore a plain literal, not a seam
        # call. The live ceo-master ships a real .workspace-identity.json, so this
        # fallback is only hit by a fresh clone (which wants "operator" anyway).
        identity = {"role": "admin", "slug": "operator", "type": "ceo-master"}
    else:
        try:
            identity = json.loads(identity_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
            raise ValueError(
                f".workspace-identity.json at {identity_file} exists but cannot be parsed: {e}. "
                "Refusing silent fallback to CEO identity."
            ) from e
        # Valid JSON of the WRONG SHAPE is a parse failure by the contract this
        # docstring states ("Returns dict with keys: role, slug, type"), and it
        # had no check. Measured 2026-08-30 with `[]` in the file:
        # `is_ceo_workspace()` raised `AttributeError: 'list' object has no
        # attribute 'get'`, and so did every path helper that resolves through
        # this - the opaque crash landing far from the hand-edited file that
        # caused it, instead of the explanatory ValueError designed for exactly
        # that case.
        if not isinstance(identity, dict):
            raise ValueError(
                f".workspace-identity.json at {identity_file} parsed as "
                f"{type(identity).__name__}, not an object with role/slug/type. "
                "Refusing silent fallback to CEO identity."
            )
    _IDENTITY_CACHE[key] = identity
    return identity


def is_ceo_workspace() -> bool:
    """Check if this is the CEO's flat master workspace."""
    return get_workspace_identity().get("type") == "ceo-master"


def is_exec_workspace() -> bool:
    """Check if this is an exec's two-layer workspace."""
    return get_workspace_identity().get("type") == "exec-workspace"


def is_admin() -> bool:
    """Check if current workspace user has admin privileges."""
    return get_workspace_identity().get("role") == "admin"


def get_exec_slug() -> str:
    """Get the current exec's slug identifier."""
    return get_workspace_identity().get("slug", "unknown")


def get_corporate_root() -> Path:
    """Get the root directory for corporate *content* (datastore, shared knowledge,
    business-info/strategy context, crm config/aliases/address-book).

    CEO workspace: the data root (.heading-os-data when present, else legacy
      in-tree). Per Plan 4 D1 (M1), the CEO authors corporate content inside the
      private data overlay and publishes the corporate subset OUT to
      heading-os-corporate via /publish-corporate (unchanged flow). On ceo-main
      today data_root ==
      workspace_root, so this is a no-op.
    Exec workspace: root/.corporate-repo/ — the gitignored clone of
      heading-os-corporate, read in place (no copy). scripts/sync-corporate.py
      clones it on first run and `git pull --ff-only`s it thereafter; /sync keeps
      it fresh. This replaced the legacy in-tree corporate/ copy (2026-06-26):
      corporate content is now read directly from the clone, a single source of
      truth with no stale on-disk duplicate.

    NOTE: reference/ and config/ are ENGINE content, not corporate content. They
    do NOT resolve through here — see get_reference_dir() / get_config_dir(), which
    pin to the engine (workspace) root for the CEO.
    """
    if is_ceo_workspace():
        return get_data_root()
    return get_workspace_root() / ".corporate-repo"


def display_path(path) -> str:
    """Human-readable relative path for display, manifests, and logs.

    After the engine/data split a workspace file may live under the ENGINE root,
    the DATA root, or the corporate root. A bare ``path.relative_to(<one root>)``
    raises ``ValueError`` whenever the file actually lives under a *different*
    root -- the "data-root seam" bug that hit knowledge-health,
    capture-design-exemplars, and odin-skill-proposal. This resolver tries each
    known root in turn (data, engine, corporate) and degrades to the absolute
    path rather than raise. Separators are normalised to '/'.

    Use this anywhere a workspace path is turned into a string for a human or a
    manifest. Do NOT use it where a path must be relative to one specific root
    (those callers should keep their explicit ``relative_to``).
    """
    p = Path(path)
    for getter in (get_data_root, get_workspace_root, get_corporate_root):
        try:
            base = getter()
        except (DataRootError, ValueError):
            # No resolvable data/corporate root in this environment; try next base.
            #
            # ValueError as well as DataRootError: `get_corporate_root()` calls
            # `is_ceo_workspace()` -> `get_workspace_identity()`, which raises
            # ValueError by design on a corrupt `.workspace-identity.json`.
            # Measured 2026-08-30 with `{invalid` in that file:
            # `display_path("/etc/hostname")` propagated the ValueError and the
            # absolute-path fallback was never reached. This helper is what
            # turns paths into strings for humans, manifests and LOGS, and the
            # docstring above promises it "degrades to the absolute path rather
            # than raise"; a logging helper that throws while something is
            # already broken is the worst possible place to find out.
            continue
        try:
            return str(p.relative_to(base)).replace("\\", "/")
        except ValueError:
            continue  # path is not under this base; try the next one
    return str(p).replace("\\", "/")


def get_personal_root() -> Path:
    """Get the root directory for personal (private) content.

    Both CEO and exec follow the same HEADING OS topology: an engine clone plus a
    sibling private-data repo. The CEO's sibling is ``../.heading-os-data``; an
    exec's is ``../.heading-os-data-{slug}`` (created by
    admin/provision/provision_exec.py) or a generically-named
    ``../.heading-os-data`` clone. The exec branch was previously hard-coded to
    the retired two-layer ``engine/personal`` path, which stranded an exec's CRM,
    knowledge, context, outputs, and plans inside the engine clone instead of
    their data repo -- so ``/crm`` read no contacts. It now resolves through the
    data root like the CEO. The forbidden field workaround was a symlink from the
    engine tree into the data repo; the fix belongs in the resolver, not on disk.
    """
    if is_ceo_workspace():
        return get_data_root()
    return get_exec_data_root()


def get_exec_data_root() -> Path:
    """Resolve an exec workspace's private-data root (the sibling data repo).

    First hit wins:
      1. ``HEADING_OS_DATA`` env override, when it points at a real dir.
         Set-but-missing is IGNORED with a warning, never silently (see
         ``env_data_root``).
      2. Slug-named sibling ``../.heading-os-data-{slug}`` (provision_exec.py default).
      3. Generic resolver ``get_data_root()`` -- handles a sibling cloned as plain
         ``../.heading-os-data`` and the read-only demo fallback (with its warning).
    """
    env_root = env_data_root()
    if env_root is not None:
        return env_root
    sibling = get_workspace_root().parent / f".heading-os-data-{get_exec_slug()}"
    if sibling.is_dir():
        return sibling.resolve()
    return get_data_root()


def get_crm_contacts_dir() -> Path:
    """Get the CRM contacts directory."""
    return get_personal_root() / "crm" / "contacts"


def get_crm_config_path() -> Path:
    """Get the CRM config file path."""
    return get_corporate_root() / "crm" / "config.md"


def get_people_file() -> Path:
    """Get the people.md quick-reference file."""
    if is_ceo_workspace():
        return get_data_root() / "context" / "people.md"
    return get_personal_root() / "context" / "people.md"


def get_context_dir() -> Path:
    """Get the corporate context directory (strategy, business-info, etc.)."""
    return get_corporate_root() / "context"


def get_personal_context_dir() -> Path:
    """Get the personal context directory (personal-info.md, people.md)."""
    if is_ceo_workspace():
        return get_data_root() / "context"
    return get_personal_root() / "context"


def get_knowledge_dir() -> Path:
    """Get the personal knowledge directory."""
    if is_ceo_workspace():
        return get_data_root() / "knowledge"
    return get_personal_root() / "knowledge"


def get_shared_knowledge_dir() -> Path:
    """Get the shared (corporate) knowledge directory."""
    return get_corporate_root() / "knowledge" / "shared"


def get_reference_dir() -> Path:
    """Get the reference directory.

    reference/ is ENGINE content -> ships in the engine clone root. For the CEO it
    resolves under the workspace (engine) root, NOT the corporate/data root. Execs
    still read it from their pulled corporate/ layer until exec migration (Plan 7).
    """
    if is_ceo_workspace():
        return get_workspace_root() / "reference"
    return get_corporate_root() / "reference"


def get_datastore_dir() -> Path:
    """Get the datastore directory."""
    return get_corporate_root() / "datastore"


def get_outputs_dir() -> Path:
    """Get the outputs directory."""
    if is_ceo_workspace():
        return get_data_root() / "outputs"
    return get_personal_root() / "outputs"


def get_auto_memory_dir() -> Path:
    """Durable canonical auto-memory fact store in the DATA overlay."""
    return get_data_root() / "auto-memory"


def get_threads_dir() -> Path:
    """Get the threads directory (operational registry — private CEO data).

    Resolves under the personal/data root (.heading-os-data for the CEO), NOT the
    engine root. A THREADS_ROOT env override still wins for tests/tools.
    """
    import os
    if env := os.environ.get("THREADS_ROOT"):
        return Path(env)
    return get_personal_root() / "threads"


def get_plans_dir() -> Path:
    """Get the plans directory (active implementation plans — private CEO data).

    Resolves under the personal/data root (.heading-os-data for the CEO), NOT the
    engine root.
    """
    return get_personal_root() / "plans"


def get_templates_dir() -> Path:
    """Get the templates directory (shared-doc source of truth — private CEO data).

    templates/ routes `private` (config/routing-map.yaml), so it lives under the
    data overlay (.heading-os-data/templates for the CEO), NOT the engine root.
    The sync-docs.py PostToolUse hook copies templates/ -> docs/ for distribution.
    Resolving under the engine root (the pre-data-seam behaviour) made the health
    check report every shared doc as "missing" — these files are on the data side.
    """
    return get_personal_root() / "templates"


def get_config_dir() -> Path:
    """Get the config directory (exec-registry, admin config).

    config/ is ENGINE content -> resolves under the workspace (engine) root for the
    CEO, NOT the corporate/data root. Execs read it from their pulled corporate/
    layer until exec migration (Plan 7).
    """
    if is_ceo_workspace():
        return get_workspace_root() / "config"
    return get_corporate_root() / "config"


def get_data_config_dir() -> Path:
    """Get the config directory for *instance config-DATA* (not engine config).

    A handful of config/ files carry real per-instance data, not shareable engine
    logic: admin.json, exec-registry.json, email-triage-rules.yaml,
    service-manifest.json, x-pulse-accounts.yaml. These resolve under the DATA
    root (.heading-os-data/config for the CEO), NOT the engine root -- so a
    data-less engine clone reads them from the data sibling instead of finding
    them absent. The engine ships generic examples; the real files live in the
    data overlay (routed private).

    Distinct from get_config_dir(), which stays pinned to the engine root for
    genuinely shareable config (routing-map.yaml, schemas/, tool-risk.json,
    wizard-*, llm_fallback.yaml, memory-index.yaml).
    """
    return get_personal_root() / "config"


def resolve_config_with_example(filename: str, example: Path) -> Path:
    """Resolve an instance config-DATA file with an engine-example fallback.

    Returns the real file under the data-config dir when it exists, else the
    engine-shipped example. This lets a data-less engine clone run on bundled
    defaults while a real deployment uses its private config in the data overlay.
    The standard pattern for any "code ships an example, real config is private
    data" file (sentinel, etc.).
    """
    real = get_data_config_dir() / filename
    return real if real.exists() else example


def get_crm_central_path() -> Path:
    """RESOLVE the local path of the RETIRED crm-central repo. Creates nothing.

    Kept, deliberately, rather than deleted with its callers. `31c-crm-central`
    was retired when the fleet seam was hard-cut to per-exec repos: aggregation
    reads the per-exec data repos (`scripts/aggregate-crm.py`), execs push their
    own contacts, and `scripts/setup.py` steps 7 and 9 are no-ops that clone and
    create nothing. One live reader of the same path survives -
    `scripts/bridge_daemon/sources/contacts.py` scans
    `../31c-crm-central/contacts/{owner}/` as a documented last-resort fallback
    behind the per-exec mirror - so the path is not a dead concept and a single
    named resolver is better than a second literal.

    THE CONTRACT, and it is the whole reason this docstring exists. On the CEO
    workspace the returned path is OUTSIDE the workspace, a sibling of the
    engine root. Callers may `.exists()` it, read it, and `git pull` an existing
    clone. Callers may NOT `mkdir()` it, `mkdir(parents=True)` it, or
    `git clone` into it. On 2026-08-30 `emergency-revoke.py` did both: a
    `gh repo clone` in `audit_recent_commits` and an unconditional
    `audit_dir.mkdir(parents=True, exist_ok=True)` in `log_security_event`,
    which had already materialised `<parent>/31c-crm-central/audit/` on the
    operator's disk once, from a stray import, with no network involved. Both
    now refuse and say so. Resolving a retired path is free; rebuilding a
    retired tree outside the workspace is a side effect nothing asked for.

    Regression lock: tests/test_a_wizard_that_cloned_a_retired_repository.py.
    """
    root = get_workspace_root()
    if is_ceo_workspace():
        return root.parent / "31c-crm-central"
    return root / ".crm-central-repo"


def per_exec_overlay_dirname(slug: str) -> str:
    """The directory NAME of an exec's DATA overlay: `.heading-os-data-{slug}`.

    Name only, with no root attached, so a caller that already holds its own
    root can compose the layout without inheriting this module's
    `get_workspace_root()` anchor. `scripts/bridge_daemon/sources/contacts.py`
    is exactly that caller: it takes `workspace_root` as a PARAMETER so its
    tests can sandbox the sibling overlays under `tmp_path`, and calling
    `get_per_exec_contacts_dir` there would make the resolver ignore its own
    argument and read the operator's real siblings during a test run.

    Exists so the layout is spelled once. Until 2026-08-30 the daemon carried
    its own second spelling (`31c-crm-{slug}/contacts`, the model retired on
    2026-08-23) and every executive rendered as zero contacts on the /contacts
    page while their overlays held real files. A third spelling is how that
    drift happened; this helper is what prevents a fourth.

    Validates the same three path shapes `get_per_exec_repo_path` rejects,
    since a directory name is about to be joined to a path.
    """
    if not slug or "/" in slug or "\\" in slug or ".." in slug:
        raise ValueError(f"Invalid slug: {slug!r}")
    return f".heading-os-data-{slug}"


def get_per_exec_repo_path(slug: str) -> Path:
    """Return the local clone path for an exec's DATA overlay.

    ONE topology, and this is it: each exec's full data overlay is cloned as
    `../.heading-os-data-{slug}` (CEO-owned, exec is a collaborator), with CRM
    contacts inside it at `crm/contacts/`. The dotted name matches
    `provision_exec.py` and the data-root seam, so provisioning and aggregation
    share one clone per exec.

    Until 2026-08-23 this returned `31c-crm-{slug}` - the retired model - while
    `scripts/aggregate-crm.py` carried its own correct copy and its docstring
    said the legacy model was retired. Two integration test files pinned the two
    answers and the suite was green on both. The audit of that date caught it.
    Callers that were reading the wrong sibling: `scripts/transfer-contact.py`
    and `scripts/admin-health.py`.
    """
    return get_workspace_root().parent / per_exec_overlay_dirname(slug)


def get_per_exec_contacts_dir(slug: str) -> Path:
    """Where an exec's CRM contact files live: `<their data repo>/crm/contacts/`.

    The overlay is a full data repo, so contacts sit under `crm/`, exactly as
    `get_per_exec_repo_path` describes. Five call sites joined `contacts`
    straight onto the repo root instead, one level too high, and read an empty
    directory as an empty fleet: on 2026-08-23 `admin-health.py` reported the
    whole fleet DEAD with 0 contacts while two live exec overlays held 11 and
    7 files. Two of the five WROTE there, filing contacts into a directory no
    reader ever opens.

    Exists as a helper rather than a path join so the layout is stated once.
    """
    return get_per_exec_repo_path(slug) / "crm" / "contacts"


def get_all_active_exec_slugs() -> list[str]:
    """Return sorted list of active exec slugs from the HEADING OS fleet roster.

    Source is `load_exec_registry()` (`<data-root>/admin/executives.json`), NOT
    `config/exec-registry.json` - the docstring named the latter until
    2026-08-23 while the code already called the former. Excludes admin role
    (CEO) and any non-active status. Used by aggregate-crm.py to know which
    per-exec repos to pull from.
    """
    registry = load_exec_registry()
    slugs = []
    for e in registry.get("executives", []):
        if e.get("status") != "active":
            continue
        # Exclude admin role (CEO); CEO CRM stays in ceo-main/crm/contacts/ and is not pushed to a per-exec repo
        if e.get("role") == "admin":
            continue
        slug = e.get("slug")
        if slug:
            slugs.append(slug)
    return sorted(slugs)


def get_corporate_repo_path() -> Path:
    """Get the local clone path for the corporate repo."""
    root = get_workspace_root()
    if is_ceo_workspace():
        return root.parent / "heading-os-corporate"
    return root / ".corporate-repo"


def load_admin_config() -> dict:
    """Load admin configuration from config/admin.json, or `{}` and SAY why.

    An absent file is genuinely no config. An existing-but-unparseable one used
    to be answered identically and in silence: measured 2026-08-30 with
    `{invalid` in `<data-config>/admin.json`, this returned `{}` with nothing
    printed, so `get_admin_slugs()` fell back to `[operator_slug()]` and
    `load_github_org()` to the operator seam. A typo in a hand-edited file
    invisibly reverted admin gating and org resolution to their defaults.
    `_read_registry_or_empty` in this same file already articulates why that is
    unacceptable for the registries; the same standard applies here.
    """
    config_path = get_data_config_dir() / "admin.json"
    if config_path.exists():
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            print(f"[workspace] admin config at {config_path} exists but could "
                  f"not be read ({type(exc).__name__}: {exc}); falling back to "
                  f"DEFAULT admin gating and org resolution", file=sys.stderr)
            return {}
        if not isinstance(loaded, dict):
            print(f"[workspace] admin config at {config_path} parsed as "
                  f"{type(loaded).__name__}, not an object; falling back to "
                  f"DEFAULT admin gating and org resolution", file=sys.stderr)
            return {}
        return loaded
    return {}


def _read_registry_or_empty(path: Path, what: str) -> dict:
    """Read a registry JSON file, or return an empty one AND say why.

    An ABSENT file is an empty registry and that is not an error: a data-less
    engine clone has no fleet and no org chart. A file that EXISTS and cannot be
    parsed is a different thing entirely, and both loaders used to answer it
    with the same silent `{"executives": []}`. Measured 2026-08-26 against a
    truncated `admin/executives.json` and a broken `config/exec-registry.json`:
    `load_exec_registry`, `get_all_active_exec_slugs`, `load_business_registry`
    and `load_fleet` all reported a fleet of zero, nothing was printed and
    nothing raised.

    That is the exact failure `load_exec_registry`'s own docstring records from
    2026-08-23 - "the exists() guard turned the miss into an empty registry
    rather than an error. Every caller silently saw a fleet of zero" - and the
    corrupt-file path still did it. Callers act on the roster: offboarding,
    CRM aggregation and admin-health all read "nobody" as a real answer.
    Returning empty keeps them running; the stderr line is what stops empty from
    reading as measured.
    """
    if not path.exists():
        return {"version": "1.0", "executives": []}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        print(f"[workspace] {what} at {path} exists but could not be read "
              f"({type(exc).__name__}: {exc}); reporting an EMPTY registry - "
              f"treat every 'no executives' answer this run as unmeasured",
              file=sys.stderr)
        return {"version": "1.0", "executives": []}
    # The failure taxonomy above had no room for the SHAPE case, so a file
    # holding valid JSON of the wrong type sailed past the warning machinery and
    # crashed a caller instead. Measured 2026-08-30 with `[]` in
    # `<data-root>/admin/executives.json`: `load_fleet()` raised
    # `AttributeError: 'list' object has no attribute 'get'` from
    # `load_business_registry().get("executives", [])`. A wrong-shape registry is
    # a registry that could not be read; route it through the same warning.
    if not isinstance(loaded, dict):
        print(f"[workspace] {what} at {path} parsed as {type(loaded).__name__}, "
              f"not an object; reporting an EMPTY registry - treat every "
              f"'no executives' answer this run as unmeasured", file=sys.stderr)
        return {"version": "1.0", "executives": []}
    return loaded


def load_exec_registry() -> dict:
    """Load the HEADING OS fleet roster from `<data-root>/admin/executives.json`.

    This answers "who is provisioned as a HEADING OS user". Its single writer is
    `../.heading-os-data/admin/provision/registry.py`.

    It is NOT `<data-root>/config/exec-registry.json`, which answers a different
    question: who is an executive at 31C (title, email, business role). That one
    is hand-maintained and loads through `load_business_registry()`. Prefer
    `load_fleet()` over either: it joins them on `slug` and labels which side
    each fact came from.

    Until 2026-08-23 this loader read the `config/exec-registry.json` path while
    intending the fleet roster, so it resolved the wrong file under the wrong
    root and the `exists()` guard turned the miss into an empty registry rather
    than an error. Every caller silently saw a fleet of zero.
    `scripts/aggregate-crm.py` reads the roster through its own
    `load_fleet_registry`, which is how the fleet kept working while
    `admin-health.py` and `transfer-contact.py` saw nobody.

    An absent file still yields an empty registry: a data-less engine clone has
    no fleet, and that is not an error.
    """
    registry_path = get_data_root() / "admin" / "executives.json"
    return _read_registry_or_empty(registry_path, "the HEADING OS fleet roster")


def load_business_registry() -> dict:
    """Load `<data-root>/config/exec-registry.json` — the 31C ORG CHART.

    Answers "who is an executive in the business": name, title, email, business
    role, platform, employment status. Hand-maintained, not written by
    provisioning.

    Its sibling is `load_exec_registry()` (`admin/executives.json`), the HEADING
    OS fleet roster. Prefer `load_fleet()` over either: it joins them and says
    which side each fact came from.

    An absent file yields an empty registry, same as the roster: a data-less
    engine clone has no org chart either.
    """
    path = get_data_config_dir() / "exec-registry.json"
    return _read_registry_or_empty(path, "the 31C org chart")


# Which registry owns which fact. Split on 2026-08-23; the reasoning, and the
# stale `aios: removed` defect that forced it, are in
# tests/test_fleet_registry_split.py.
_BUSINESS_FIELDS = {"name": "name", "title": "title", "email": "email",
                    "role": "business_role", "platform": "platform",
                    "status": "employment_status"}
_SYSTEM_FIELDS = {"name": "name", "github_user": "github_user",
                  "data_repo": "data_repo", "status": "provisioning_status"}


def repo_name_for(slug: str) -> str:
    """The GitHub repo name for an exec's data overlay, from the fleet roster.

    Falls back to the `heading-os-data-{slug}` convention when the roster row
    omits `data_repo`, which is what a hand-added row usually does.

    It lives here, not in a caller, because it had a caller that did not use
    it. `scripts/admin-health.py` resolved the name through the roster while
    `scripts/aggregate-crm.py` hardcoded the convention, so an exec whose row
    named a different repo had their overlay cloned correctly by the health
    dashboard and 404'd by the CRM aggregation -- which then contributed zero
    of their contacts and exited 0. Two fleet tools cannot drift on a repo name
    they both read from one function.
    """
    for row in load_fleet():
        if row.get("slug") == slug and row.get("data_repo"):
            return row["data_repo"]
    return f"heading-os-data-{slug}"


def load_fleet() -> list[dict]:
    """Join the org chart and the fleet roster on `slug`. Sorted by slug.

    Returns one record per person appearing in EITHER file, carrying:

      slug, is_business_exec, is_heading_os_user,
      name, title, email, business_role, platform, employment_status,   (chart)
      github_user, data_repo, provisioning_status                       (roster)

    The two flags are the point. Merging the files was rejected because the
    fleet already holds people who are one and not the other: an executive with
    no HEADING OS install, and an install belonging to nobody on the org chart.
    Read the flag rather than inferring membership from a `status` string —
    BOTH files have a field called `status` and they mean different things,
    which is why the join renames them apart.

    Absent facts are None, never "" and never a guess: a caller must be able to
    tell "this person has no roster row" from "their roster row says nothing".

    `provisioning_status` runs provisioning -> provisioned -> active ->
    offboarded | revoked. Only `active` counts as fleet membership for
    aggregation and sync; `provisioned` means setup finished but the operator
    has not started using the install.
    """
    merged: dict[str, dict] = {}

    def _slot(slug: str) -> dict:
        return merged.setdefault(slug, {
            "slug": slug, "is_business_exec": False, "is_heading_os_user": False,
            **dict.fromkeys(set(_BUSINESS_FIELDS.values()) | set(_SYSTEM_FIELDS.values())),
        })

    for row in load_business_registry().get("executives", []):
        slug = row.get("slug")
        if not slug:
            continue
        rec = _slot(slug)
        rec["is_business_exec"] = True
        for src, out in _BUSINESS_FIELDS.items():
            if row.get(src) is not None:
                rec[out] = row[src]

    for row in load_exec_registry().get("executives", []):
        slug = row.get("slug")
        if not slug:
            continue
        rec = _slot(slug)
        rec["is_heading_os_user"] = True
        for src, out in _SYSTEM_FIELDS.items():
            if row.get(src) is not None:
                rec[out] = row[src]

    return [merged[s] for s in sorted(merged)]


@functools.lru_cache(maxsize=4)
def _load_routing_map_cached(path: str, mtime_ns: int, size: int) -> dict:
    """Parse one routing map. Keyed on file IDENTITY (path + mtime + size), never bare.

    A bare ``lru_cache`` on ``load_routing_map()`` would make a long-running daemon
    (bridge-daemon, sentinel) blind to an edit of routing-map.yaml — and that file is
    the classifier deciding what counts as private data, so blindness there is a leak
    path, not a staleness annoyance. Keying on mtime_ns + size means an edited map is
    a cache MISS and gets re-parsed on the next call.

    Fails closed exactly as the uncached loader did: any read/parse error yields
    default 'private'.
    """
    import yaml

    from scripts.utils import yamlio

    try:
        with open(path, encoding="utf-8") as fh:
            data = yamlio.safe_load(fh) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        # UnicodeDecodeError is a ValueError, NOT an OSError and not a
        # YAMLError, so one invalid byte in the map escaped both clauses and
        # crashed the classifier instead of failing closed. Measured 2026-08-30
        # with `default: \xffprivate` in routing-map.yaml:
        # `get_routing_destination("crm/x.md")` raised UnicodeDecodeError. This
        # resolver is called once per tracked file by the push wall and the
        # engine-tree-clean pre-commit hook, and the docstring above promises
        # "any read/parse error yields default 'private'" precisely so a
        # corrupted map cannot take the leak wall down.
        return {"default": "private", "rules": {}}
    # Valid YAML of the WRONG SHAPE is a parse error by any reading the
    # docstring above supports, and it used to raise instead. A `rules:` block
    # written as a list (a stray `-`, the commonest YAML slip) reached
    # `rules.items()` and threw AttributeError out of a resolver that every
    # classification call sits on. Found 2026-08-26 while testing the coercion
    # below; the crash predates it. Fail closed, like every other bad read here.
    if not isinstance(data, dict):
        return {"default": "private", "rules": {}}
    default = data.get("default", "private")
    rules = data.get("rules") or {}
    if not isinstance(rules, dict):
        print(f"[workspace] routing-map `rules:` is a {type(rules).__name__}, "
              f"not a mapping; failing closed to 'private' for every path",
              file=sys.stderr)
        return {"default": "private", "rules": {}}
    legal = {"engine", "private", "corporate"}
    # A destination LEAF has to be a string before it can be asked about. Both
    # membership tests below hash the candidate against `legal`, and a list- or
    # dict-valued leaf is unhashable, so it raised TypeError straight out of
    # this loader. Measured 2026-08-31: `rules: {crm/: [private]}` and
    # `default: [private]` each raised `TypeError: unhashable type: 'list'`,
    # and `get_routing_destination` has no handler for it while its own
    # docstring promises "Fails closed: load_routing_map() already defaults to
    # 'private' on error". Same YAML slip as the `rules:`-written-as-a-list case
    # guarded fifteen lines up, one level further down the tree and missed
    # there. This resolver is called once per tracked file by the push wall and
    # the engine-tree-clean hook, so it must answer rather than raise.
    if not isinstance(default, str):
        print(f"[workspace] routing-map `default:` is a {type(default).__name__}, "
              f"not a destination name; failing closed to 'private'",
              file=sys.stderr)
        return {"default": "private", "rules": {}}
    if default not in legal:
        default = "private"

    # An illegal destination on a RULE now fails CLOSED to 'private'. It used to
    # be dropped from the map entirely, and a dropped rule is not a neutral act:
    # the path it governed falls through to `default`, and this workspace's real
    # `config/routing-map.yaml` reads `default: engine` — the PUBLIC repository.
    #
    # Reproduced 2026-08-26 against a scratch map holding `default: engine` and
    # rules {"outputs/": "privat", "crm/": "private"}, one character wrong on the
    # first: `load_routing_map()` returned only the crm rule,
    # `get_routing_destination("outputs/secret.md")` answered 'engine', and
    # nothing was printed. So a typo in one value silently reclassified a whole
    # CEO-data subtree as shareable, and the loader's own docstring promises the
    # opposite: "any read/parse error yields default 'private'". A misspelled
    # value is a parse-level defect and this is the direction it must fail in.
    coerced = {}
    for key, value in rules.items():
        if not isinstance(value, str):
            print(f"[workspace] routing-map rule {key!r} has a "
                  f"{type(value).__name__} where a destination name belongs; "
                  f"treating {key!r} as 'private' rather than letting it fall "
                  f"through to the '{default}' default", file=sys.stderr)
            coerced[key] = "private"
            continue
        if value in legal:
            coerced[key] = value
            continue
        print(f"[workspace] routing-map rule {key!r} has destination {value!r}, "
              f"which is not one of {sorted(legal)}; treating it as 'private' "
              f"rather than letting {key!r} fall through to the '{default}' "
              f"default", file=sys.stderr)
        coerced[key] = "private"
    return {"default": default, "rules": coerced}


def load_routing_map() -> dict:
    """Load config/routing-map.yaml. Returns {default, rules} with legal destinations.

    Fails closed: on any error, returns a map whose default is 'private' so an
    unresolvable path is treated as data (never accidentally 'engine'/shareable).

    Cached on file identity (2026-08-20). Before the cache this re-parsed the YAML on
    EVERY get_routing_destination() call — measured 6.4 ms per call, so the 1535-file
    engine-tree-clean scan paid 9.63 s of pure parsing; with the map parsed once it is
    0.19 s. Returns a fresh copy per call so a caller mutating `rules` cannot corrupt
    the shared cached map for the rest of the process.
    """
    path = get_workspace_root() / "config" / "routing-map.yaml"
    try:
        st = path.stat()
        mtime_ns, size = st.st_mtime_ns, st.st_size
    except OSError:
        # No file / unreadable stat: fail closed, and do not poison the cache.
        return {"default": "private", "rules": {}}
    m = _load_routing_map_cached(str(path), mtime_ns, size)
    return {"default": m["default"], "rules": dict(m["rules"])}


def matched_routing_rule(file_path: str) -> str | None:
    """The routing-map key that governs `file_path`, or None if none does.

    Split out of `get_routing_destination` on 2026-08-24 so a caller can tell
    "this path has an explicit rule" from "this path fell through to the map
    default". `classification-health.py --unclassified` needs exactly that
    distinction and had no way to ask for it, so its third bucket did not exist
    and an unclassified file was silently counted as CEO-only.

    Most-specific (longest matching) key wins. A key ending in '/' matches the
    path as a directory prefix; a key without a trailing '/' matches either that
    exact file or that path as a prefix.
    """
    rules = load_routing_map()["rules"]
    # normalize: strip leading slash, convert backslashes, collapse to posix
    norm = file_path.replace("\\", "/").lstrip("/")
    best_key = None
    for key in rules:
        k = key.rstrip("/")
        if norm == k or norm.startswith(k + "/"):
            if best_key is None or len(key) > len(best_key):
                best_key = key
    return best_key


def get_routing_destination(file_path: str) -> str:
    """Resolve a workspace-relative path to 'engine' | 'private' | 'corporate'.

    Most-specific (longest matching) rule key wins. A key ending in '/' matches
    the path as a directory prefix; a key without a trailing '/' matches either
    that exact file or that path as a prefix. Unmatched -> map default.

    Fails closed: load_routing_map() already defaults to 'private' on error.
    """
    m = load_routing_map()
    best_key = matched_routing_rule(file_path)
    if best_key is None:
        return m["default"]
    return m["rules"][best_key]


def get_classification(file_path: str) -> str:
    """Resolve the two-value classification for a workspace-relative file path.

    HEADING OS step 7: this is now a thin collapse of the three-value routing map
    (`config/routing-map.yaml`), the single classification input. The two-value
    question is "is this CEO-private?":

      routing 'private'   -> 'ceo-only'   (CEO data overlay, never shared)
      routing 'corporate' -> 'corporate'  (shared down to execs)
      routing 'engine'    -> 'corporate'  (engine code is not private — it is the
                                           most-shared thing: public + every exec
                                           via the engine clone)

    Default direction: the routing map's default is 'engine' (-> 'corporate'), so an
    unmatched path resolves shareable, NOT ceo-only. This is the routing-map design:
    every DATA directory (crm/, knowledge/, outputs/, threads/, context/, plans/, ...)
    carries an explicit 'private' rule so real data fail-closes; only code-ish paths
    fall through to the engine default. The hard fail-closed case is a *broken*
    routing-map.yaml: load_routing_map() then forces default 'private' (-> 'ceo-only'),
    so an unreadable map treats everything as CEO data.
    """
    dest = get_routing_destination(file_path)
    return "ceo-only" if dest == "private" else "corporate"


def is_corporate(file_path: str) -> bool:
    """Check if a file is classified as corporate (shared with all executives)."""
    return get_classification(file_path) == "corporate"


def get_ceo_only_scripts() -> set:
    """Return the set of script basenames that are CEO-private (routed 'private').

    Derived from the explicit `scripts/*.py` private keys in `config/routing-map.yaml`
    (HEADING OS step 7). Single Source of Truth for the admin-only script list,
    mirroring the old file_overrides approach against the routing map's rule keys.
    """
    rules = load_routing_map()["rules"]
    return {
        Path(key).name
        for key, dest in rules.items()
        if dest == "private" and key.startswith("scripts/") and key.endswith(".py")
    }


def get_ceo_only_references() -> set:
    """Return the set of reference file basenames that are CEO-private.

    Derived from the explicit `reference/*` private keys in `config/routing-map.yaml`
    (HEADING OS step 7).
    """
    rules = load_routing_map()["rules"]
    return {
        Path(key).name
        for key, dest in rules.items()
        if dest == "private" and key.startswith("reference/") and not key.endswith("/")
    }


ADMIN_SLUGS = None

def get_admin_slugs() -> list:
    """Get list of admin slugs from config."""
    global ADMIN_SLUGS
    if ADMIN_SLUGS is None:
        config = load_admin_config()
        if "admin_slugs" in config:
            ADMIN_SLUGS = config["admin_slugs"]
        else:
            # Fleet admins is a distinct concept (plural); the singular operator
            # slug is the sensible one-instance default when admin.json is absent.
            from scripts.utils.operator_identity import operator_slug
            ADMIN_SLUGS = [operator_slug()]
    return ADMIN_SLUGS


_ORG_OVERLAY_FALLBACK_ANNOUNCED = False


def load_github_org() -> str:
    """Load the GitHub org: the operator seam (operator.yaml/env) first, then
    admin.json's github_org, else the seam's value ('' on a fresh clone).

    Never raises, and returns '' when nothing could answer. `load_admin_config()`
    resolves through `get_data_config_dir()` and so through `get_data_root()`,
    which raises `DataRootError` when `HEADING_OS_DATA` names a path that is not
    a directory (and `ValueError` on a corrupt `.workspace-identity.json`, via
    `is_ceo_workspace()`). Three engine scripts bind this at MODULE scope --
    `admin-health.py:43`, `offboard-exec.py:42`, `provision-exec.py:55` -- so on
    2026-08-30 the exception arrived during import, before argparse ran, and
    `--help` died with a traceback and exit 1 on any machine whose overlay is
    missing. That is the same machine state a fleet incident produces, which is
    when these three are wanted most.

    Absorbing it here is the call `scripts/utils/operator_identity._resolve_file`
    already makes one tier up, for the same stated reason. The `DataRootError`
    guard exists to stop a WRITE landing on the live overlay by accident, and
    nothing on this path writes: it is a READ whose resolution order already
    documents a lower tier to read instead, the operator seam, whose documented
    answer on an unresolvable instance is the empty string. Falling to it costs
    nothing and unblocks all three callers at once.

    Deliberately narrow, and that boundary is the justification. This absorbs the
    refusal for THIS read only. It does not widen `get_data_root()`, it does not
    touch `require_writable_data_root()`, and it leaves every write path refusing
    exactly as before. A caller that needs a REAL org must test for the empty
    string -- `operator_org()` already documents that, and it is now true one
    layer down as well: no exception arriving is not evidence that an org exists.

    The fall is announced once per process on stderr, never taken in silence.
    """
    global _ORG_OVERLAY_FALLBACK_ANNOUNCED
    from scripts.utils.operator_identity import operator_org
    org = operator_org()
    if org:
        return org
    try:
        config = load_admin_config()
    except (DataRootError, ValueError, OSError) as exc:
        if not _ORG_OVERLAY_FALLBACK_ANNOUNCED:
            _ORG_OVERLAY_FALLBACK_ANNOUNCED = True
            print(f"[workspace] the private data overlay could not be resolved "
                  f"({type(exc).__name__}: {exc}); admin.json was NOT read, so "
                  f"the GitHub org falls back to the operator seam and resolves "
                  f"to {org!r}. Any command that needs a real org must refuse.",
                  file=sys.stderr)
        return org
    if config.get("github_org"):
        return config["github_org"]
    return org


def validate_admin() -> bool:
    """Validate that current workspace is admin. Exit if not."""
    if not is_admin():
        import sys
        print("ERROR: This operation requires admin privileges.", file=sys.stderr)
        print(f"Current workspace: {get_exec_slug()} ({get_workspace_identity().get('type')})", file=sys.stderr)
        sys.exit(1)
    slug = get_exec_slug()
    if slug not in get_admin_slugs():
        import sys
        print(f"ERROR: {slug} is not in the admin list.", file=sys.stderr)
        sys.exit(1)
    return True

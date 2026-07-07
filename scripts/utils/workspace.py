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

import json
import os
import warnings
from pathlib import Path

# Re-export the canonical root resolver and helpers from paths.py.
# Backward compatibility: existing imports of get_workspace_root / load_env
# from this module resolve to the hardened implementations.
from scripts.utils.paths import (  # noqa: F401
    DATA_SCHEMA_VERSION,
    DataRootError,
    check_schema_compatible,
    data_dir,
    data_root_is_demo,
    get_data_root,
    get_workspace_root,
    home,
    load_env,
    log_dir,
    read_data_schema_version,
    require_writable_data_root,
    state_dir,
)


def get_default_tz_name() -> str:
    """Per-instance local timezone NAME (IANA). Defaults to UTC; the live
    instance sets HEADING_OS_TZ (e.g. America/New_York) in its gitignored .env.
    Externalized so the engine ships no operating-location signal."""
    return os.environ.get("HEADING_OS_TZ", "UTC")


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
        # (scripts.utils.operator) is built ON TOP of it -- it resolves through
        # get_data_config_dir() -> get_personal_root() -> is_ceo_workspace() ->
        # get_workspace_identity(), so calling it here would recurse. The
        # de-personalized generic slug is therefore a plain literal, not a seam
        # call. The live ceo-master ships a real .workspace-identity.json, so this
        # fallback is only hit by a fresh clone (which wants "operator" anyway).
        identity = {"role": "admin", "slug": "operator", "type": "ceo-master"}
    else:
        try:
            identity = json.loads(identity_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            raise ValueError(
                f".workspace-identity.json at {identity_file} exists but cannot be parsed: {e}. "
                "Refusing silent fallback to CEO identity."
            ) from e
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
        except DataRootError:
            # No resolvable data/corporate root in this environment; try next base.
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
      1. ``HEADING_OS_DATA`` env override (when it points at a real dir).
      2. Slug-named sibling ``../.heading-os-data-{slug}`` (provision_exec.py default).
      3. Generic resolver ``get_data_root()`` -- handles a sibling cloned as plain
         ``../.heading-os-data`` and the read-only demo fallback (with its warning).
    """
    env = os.environ.get("HEADING_OS_DATA")
    if env:
        cand = Path(env).expanduser()
        if cand.is_dir():
            return cand.resolve()
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
    """Get the local clone path for crm-central repo (CEO only)."""
    root = get_workspace_root()
    if is_ceo_workspace():
        return root.parent / "31c-crm-central"
    return root / ".crm-central-repo"


def get_per_exec_repo_path(slug: str) -> Path:
    """Return the local clone path for a per-exec CRM repo.

    Per-exec repos are sibling directories of the workspace root, named
    `31c-crm-{slug}`. Used by both CEO (clones all execs') and execs (clones own).
    """
    if not slug or "/" in slug or "\\" in slug or ".." in slug:
        raise ValueError(f"Invalid slug: {slug!r}")
    return get_workspace_root().parent / f"31c-crm-{slug}"


def get_all_active_exec_slugs() -> list[str]:
    """Return sorted list of active exec slugs from config/exec-registry.json.

    Excludes admin role (CEO) and any non-active status. Used by aggregate-crm.py
    to know which per-exec repos to pull from.
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
    """Load admin configuration from config/admin.json."""
    config_path = get_data_config_dir() / "admin.json"
    if config_path.exists():
        try:
            return json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def load_exec_registry() -> dict:
    """Load exec registry from config/exec-registry.json."""
    config_path = get_data_config_dir() / "exec-registry.json"
    if config_path.exists():
        try:
            return json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"version": "1.0", "executives": []}


def load_routing_map() -> dict:
    """Load config/routing-map.yaml. Returns {default, rules} with legal destinations.

    Fails closed: on any error, returns a map whose default is 'private' so an
    unresolvable path is treated as data (never accidentally 'engine'/shareable).
    """
    import yaml

    root = get_workspace_root()
    path = root / "config" / "routing-map.yaml"
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return {"default": "private", "rules": {}}
    default = data.get("default", "private")
    rules = data.get("rules", {}) or {}
    legal = {"engine", "private", "corporate"}
    if default not in legal:
        default = "private"
    rules = {k: v for k, v in rules.items() if v in legal}
    return {"default": default, "rules": rules}


def get_routing_destination(file_path: str) -> str:
    """Resolve a workspace-relative path to 'engine' | 'private' | 'corporate'.

    Most-specific (longest matching) rule key wins. A key ending in '/' matches
    the path as a directory prefix; a key without a trailing '/' matches either
    that exact file or that path as a prefix. Unmatched -> map default.

    Fails closed: load_routing_map() already defaults to 'private' on error.
    """
    m = load_routing_map()
    rules = m["rules"]
    # normalize: strip leading slash, convert backslashes, collapse to posix
    norm = file_path.replace("\\", "/").lstrip("/")
    best_key = None
    for key in rules:
        k = key.rstrip("/")
        if norm == k or norm.startswith(k + "/"):
            if best_key is None or len(key) > len(best_key):
                best_key = key
    if best_key is None:
        return m["default"]
    return rules[best_key]


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

_SHIM_WARNED: set = set()


def _is_established_instance() -> bool:
    """True when this instance already carries real per-instance identity data
    (config/admin.json in the data overlay). Distinguishes the live, pre-migration
    workspace from a fresh public clone, so the operator-identity compat shim
    restores historical defaults on the former and resolves generic on the latter.
    Cannot be called from get_workspace_identity() (it routes through the data
    config dir, which routes back through identity)."""
    return (get_data_config_dir() / "admin.json").exists()


def operator_identity_default(field: str, legacy: str) -> str:
    """Resolve a load-bearing identity DEFAULT through the operator seam.

    Returns the configured operator value when operator.yaml / env set the
    instance identity. Otherwise, on an established instance (pre-migration),
    returns the historical `legacy` literal with a one-time DeprecationWarning so
    the live workspace stays byte-identical until an operator.yaml is written; on
    a fresh clone it returns the generic operator value. Scheduled for removal in
    v0.5.0. Safe here (not the bootstrap identity resolver)."""
    from scripts.utils.operator import get_operator, operator_is_default
    if not operator_is_default():
        return get_operator()[field]
    if _is_established_instance():
        if field not in _SHIM_WARNED:
            warnings.warn(
                f"operator identity default for '{field}' fell back to the legacy "
                f"literal '{legacy}'. Write config/operator.yaml (see "
                f"scripts/operator.example.yaml) to set it explicitly; this "
                f"compatibility shim is removed in v0.5.0.",
                DeprecationWarning, stacklevel=2,
            )
            _SHIM_WARNED.add(field)
        return legacy
    return get_operator()[field]


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
            ADMIN_SLUGS = [operator_identity_default("slug", "misha-hanin")]
    return ADMIN_SLUGS


def load_github_org() -> str:
    """Load the GitHub org: operator.yaml/env, then admin.json, then legacy shim."""
    from scripts.utils.operator import operator_org
    org = operator_org()
    if org:
        return org
    config = load_admin_config()
    if config.get("github_org"):
        return config["github_org"]
    return operator_identity_default("github_org", "mishahanin")


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

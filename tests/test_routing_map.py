import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.workspace import load_routing_map


def test_routing_map_loads_with_required_shape():
    m = load_routing_map()
    assert m["default"] == "engine"
    assert isinstance(m["rules"], dict)
    # every destination is one of the three legal values
    legal = {"engine", "private", "corporate"}
    assert set(m["rules"].values()) <= legal
    assert m["default"] in legal


from scripts.utils.workspace import get_routing_destination


def test_default_is_engine():
    assert get_routing_destination("scripts/foo.py") == "engine"
    assert get_routing_destination(".claude/skills/osint/SKILL.md") == "engine"


def test_private_content_dirs():
    assert get_routing_destination("crm/contacts/jane-doe.md") == "private"
    assert get_routing_destination("outputs/intel/osint/x.md") == "private"
    assert get_routing_destination("threads/business/deal.md") == "private"


def test_corporate_content():
    assert get_routing_destination("datastore/brand/logo.svg") == "corporate"
    assert get_routing_destination("context/business-info.md") == "corporate"


def test_most_specific_wins():
    # knowledge/ is private, but knowledge/shared/ is corporate (longer key wins)
    assert get_routing_destination("knowledge/odin-brain/p.md") == "private"
    assert get_routing_destination("knowledge/shared/ai/n.md") == "corporate"
    # datastore/ is corporate, but a nested private subtree wins
    assert get_routing_destination("datastore/operations/tribe/fireside-state/opt-ins.json") == "private"


def test_exact_file_override_beats_dir():
    # context/ has no dir rule; specific files route explicitly
    assert get_routing_destination("context/pipeline.md") == "private"
    assert get_routing_destination("context/strategy.md") == "corporate"


def test_leading_slash_and_backslash_normalized():
    assert get_routing_destination("/crm/contacts/x.md") == "private"
    assert get_routing_destination("crm\\contacts\\x.md") == "private"


def test_code_dirs_that_were_corporate_are_now_engine():
    # OLD model: these were 'corporate' (shared to execs). NEW model: code = engine.
    assert get_routing_destination("scripts/email-intelligence.py") == "engine"
    assert get_routing_destination(".claude/hooks/memory-inject.py") == "engine"
    assert get_routing_destination("reference/corporate-style-guide.md") == "engine"
    assert get_routing_destination("docs/QUICKSTART.md") == "engine"


def test_known_private_and_corporate_anchors():
    assert get_routing_destination("datastore/books/x.pdf") == "private"
    assert get_routing_destination("crm/address-book/list.md") == "corporate"


def test_ceo_only_sensitive_files_route_private_not_engine():
    # Dangerous-misroute lock: these would default to 'engine' (ship in a public
    # clone) without an explicit rule. They are per-instance config or CEO-only
    # sensitive content and must route 'private' (fail-closed, review Task 7).
    assert get_routing_destination(".claude/settings.local.json") == "private"
    assert get_routing_destination("config/x-pulse-accounts.yaml") == "private"
    assert get_routing_destination("docs/CEO-ADMIN-GUIDE.md") == "private"
    assert get_routing_destination("docs/security/REMEDIATION-REPORT.md") == "private"
    assert get_routing_destination("datastore/INDEX.md") == "private"
    assert get_routing_destination("reference/workspace-overview.md") == "private"


def test_2026_06_13_ceo_audit_sensitive_routes_private():
    # CEO audit (2026-06-13): real identity/contacts/strategy/voice IP that the
    # routing default (docs/ + reference/ + config/ -> engine) would have shipped
    # to a public clone. All fail-closed to private.
    assert get_routing_destination("config/exec-registry.json") == "private"
    assert get_routing_destination("config/admin.json") == "private"
    assert get_routing_destination("config/email-triage-rules.yaml") == "private"
    assert get_routing_destination("config/service-manifest.json") == "private"
    assert get_routing_destination("config/sentinel_config.yaml") == "private"
    assert get_routing_destination("config/fireside-schedule.json") == "private"
    assert get_routing_destination("config/bootcamp-org-chart.json") == "private"
    assert get_routing_destination("config/exec-meeting-attendees.json") == "private"
    assert get_routing_destination("config/modem.json") == "private"
    # The engine-shipped examples are generic scaffolding -> engine (default).
    assert get_routing_destination("scripts/sentinel_config.example.yaml") == "engine"
    assert get_routing_destination("scripts/fireside-schedule.example.json") == "engine"
    assert get_routing_destination("scripts/bootcamp-org-chart.example.json") == "engine"
    assert get_routing_destination("scripts/exec-meeting-attendees.example.json") == "engine"
    assert get_routing_destination("docs/superpowers/specs/2026-04-05-secure-vault-design.md") == "private"
    assert get_routing_destination("docs/security/findings-registry.md") == "private"
    assert get_routing_destination("reference/billion-growth-playbook.md") == "private"
    assert get_routing_destination("reference/dpi-market-intelligence.md") == "private"
    assert get_routing_destination("reference/geopolitical-landscape.md") == "private"
    assert get_routing_destination("reference/misha-voice.md") == "private"
    assert get_routing_destination("reference/exec-voice.md") == "private"
    # docs/USAGE-GUIDE.md retired 2026-06-27 (merged into the Executive Handbook);
    # the CEO-only admin guide is the standing private-docs assertion in its place.
    assert get_routing_destination("docs/CEO-ADMIN-GUIDE.md") == "private"
    assert get_routing_destination("reference/search-domains.md") == "private"
    # managed service-host VM topology (data-config); engine ships the example instead
    assert get_routing_destination("config/service-host.json") == "private"
    # service-host deployment infra (whole deploy/ tree) — never public
    assert get_routing_destination("deploy/service-host/install.sh") == "private"
    assert get_routing_destination("deploy/systemd/some-unit.service") == "private"
    # Claude Code per-machine runtime state: session transcripts + CEO auto-memory.
    # Would default to 'engine' (.claude/ is code) and ship personal facts in a
    # public clone. Fail-closed to private wholesale (2026-06-13 CEO audit).
    assert get_routing_destination(
        ".claude/projects/-home-x-ceo-main/memory/example-friend.md"
    ) == "private"
    assert get_routing_destination(
        ".claude/projects/-home-x-ceo-main/memory/MEMORY.md"
    ) == "private"
    assert get_routing_destination(
        ".claude/projects/-home-x-ceo-main/some-session.jsonl"
    ) == "private"


def test_modem_tune_code_is_engine_only_device_identity_private():
    # CEO decision 2026-06-14 (NO EXCEPTIONS): the modem-tune CODE ships as engine
    # like all other code; ONLY the device identity is private DATA. An earlier
    # routing-map mistakenly pinned the code private — this locks the correction.
    assert get_routing_destination("scripts/modem-tune.py") == "engine"
    assert get_routing_destination(".claude/skills/modem-tune/SKILL.md") == "engine"
    assert get_routing_destination("scripts/modem.example.json") == "engine"
    # the real device identity (TAC + factory IMEI) stays private data
    assert get_routing_destination("config/modem.json") == "private"


def test_decontaminated_code_now_engine():
    # HEADING OS 2026-06-14 (NO EXCEPTIONS): once a script/test had its real
    # identities, rosters, topology literals, and codenames moved OUT into the
    # private data overlay (config/*.json data-config + synthetic fixtures), the
    # code is generic and ships as engine via the default. These previously held
    # private rules; the decontamination removed the need.
    for path in (
        "scripts/bootcamp-roster.py",
        "scripts/md-to-docx-competitive.py",
        "scripts/md-to-docx-charter.py",
        "scripts/gen-exec-meeting-docx.py",
        "scripts/crm_migrate_to_entity_model.py",
        "tests/test_crm_migration.py",
        "tests/test_viraid_counterpart.py",
        "scripts/publish-service.py",
        "scripts/pull-service-state.py",
        "scripts/inbox-pulse-report.py",
        "tests/inbox_pulse/test_report.py",
        "scripts/fireside-pulse.py",
        "scripts/fireside-bot.py",
        "scripts/fireside-bot-daemon.py",
        "scripts/fireside_webhook.py",
        "scripts/setup-fireside-healthchecks.py",
        "scripts/requirements-fireside.txt",
        "scripts/templates/systemd/fireside-bot-daemon.service",
        "tests/test_fireside_bot_auth.py",
        "tests/test_fireside_daemon.py",
        "scripts/archive/old-tool.py",
        ".claude/skills/archive/2026-04-24-export-update/SKILL.md",
        "scripts/service-host.example.json",
    ):
        assert get_routing_destination(path) == "engine", path
    # but the SEPARATE service-host VM's own deployment software stays private,
    # and the instance topology values stay private data
    assert get_routing_destination("deploy/service-host/install.sh") == "private"
    assert get_routing_destination("config/service-host.json") == "private"

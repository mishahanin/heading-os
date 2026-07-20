#!/usr/bin/env python3
"""Update-manager registry: load and validate config/update-registry.yaml.

Each component is described declaratively through source adapters. The one hard
invariant: an `observed` component may not carry an executable `apply` (the
manager cannot update a component that owns its own updater). Mirrors the
send_capable -> gated invariant in .claude/rules/tiered-risk.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

VALID_TIERS = {"auto", "notify", "observed"}


class RegistryError(Exception):
    """Raised on any structural or invariant violation in the registry."""


@dataclass
class Component:
    name: str
    tier: str
    current: dict[str, Any]
    latest: dict[str, Any]
    display: str = ""
    apply: dict[str, Any] | None = None
    health: dict[str, Any] | None = None
    hold: bool = False
    pin: str | None = None


def load_registry(path: Path) -> list[Component]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise RegistryError(f"cannot read registry {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise RegistryError(f"registry {path} must be a mapping at the top level")

    components = raw.get("components")
    if not isinstance(components, dict):
        raise RegistryError("registry must have a top-level `components:` mapping")

    out: list[Component] = []
    for name, body in components.items():
        if not isinstance(body, dict):
            raise RegistryError(f"component {name!r} must be a mapping")
        tier = body.get("tier")
        if tier not in VALID_TIERS:
            raise RegistryError(
                f"component {name!r}: tier {tier!r} not in {sorted(VALID_TIERS)}"
            )
        apply_block = body.get("apply")
        if tier == "observed" and apply_block:
            raise RegistryError(
                f"component {name!r}: observed entries may not carry an `apply` "
                "(the manager does not own updates for observed components)"
            )
        # A present apply block must name how to apply -- `cmd` or `script`.
        if isinstance(apply_block, dict) and not ({"cmd", "script"} & set(apply_block)):
            raise RegistryError(
                f"component {name!r}: an `apply` block must contain `cmd` or `script`"
            )
        if isinstance(apply_block, dict) and "cmd" in apply_block and "script" in apply_block:
            raise RegistryError(
                f"component {name!r}: an `apply` block cannot define both `cmd` "
                "and `script` (choose one)"
            )
        # Never-broken invariant: any `cmd` apply must define `rollback_cmd` so a
        # failed apply or health check can restore the prior version. A `script`
        # apply is exempt -- the script owns its snapshot + rollback internally.
        if isinstance(apply_block, dict) \
                and "cmd" in apply_block and "rollback_cmd" not in apply_block:
            raise RegistryError(
                f"component {name!r}: a `cmd` apply must define `rollback_cmd` "
                "(never-broken invariant)"
            )
        for required in ("current", "latest"):
            if not isinstance(body.get(required), dict):
                raise RegistryError(f"component {name!r}: missing `{required}:` block")
        out.append(
            Component(
                name=name,
                tier=tier,
                current=body["current"],
                latest=body["latest"],
                display=body.get("display", name),
                apply=apply_block,
                health=body.get("health"),
                hold=bool(body.get("hold", False)),
                pin=body.get("pin"),
            )
        )
    return out

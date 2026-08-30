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
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        # UnicodeDecodeError is a ValueError, so a byte-level corruption of the
        # file escaped an OSError/YAMLError pair whose plain intent is "any read
        # failure becomes RegistryError". Measured 2026-08-30 with b"\xff\xfe"
        # in the registry: a raw UnicodeDecodeError, past every caller that
        # catches RegistryError to report a config problem.
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
        # Every `apply` check below is guarded by `isinstance(apply_block, dict)`,
        # so before this clause existed a non-mapping value passed ALL of them by
        # not firing any: `apply: "echo hi"` is truthy, contains neither "cmd"
        # nor "script" as a key, and satisfies the rollback_cmd and health
        # invariants vacuously. It was then stored into `Component.apply`, which
        # is annotated `dict[str, Any] | None`, and `_default_applier` raised
        # `ValueError: apply block has neither cmd nor script` at APPLY time,
        # far from the config line that caused it. Same for the other three
        # fields, which are stored into typed slots with no check at all: an
        # unquoted `pin: 1.5` arrives from YAML as a float.
        if apply_block is not None and not isinstance(apply_block, dict):
            raise RegistryError(
                f"component {name!r}: `apply` must be a mapping, got "
                f"{type(apply_block).__name__}"
            )
        for field, want, label in (
            ("health", dict, "a mapping"),
            ("display", str, "a string"),
            ("pin", str, "a string"),
        ):
            value = body.get(field)
            if value is not None and not isinstance(value, want):
                raise RegistryError(
                    f"component {name!r}: `{field}` must be {label}, got "
                    f"{type(value).__name__}"
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
        # ... and a `health` block, for the same reason. `run_health` returns
        # True when `comp.health` is absent, and for a `cmd` apply that health
        # gate is the ONLY verification between "the command exited 0" and
        # "applied". So a `cmd` entry with no health block reports applied with
        # nothing checked, which is the half of the invariant `rollback_cmd`
        # does not cover: the rollback exists but nothing ever decides to call
        # it. Every entry in `config/update-registry.yaml` already carries one
        # (checked 2026-08-26: 4 components, the 2 with an apply block both have
        # health), so this closes a trap for the next entry rather than changing
        # today's behaviour. A `script` apply is exempt for the same reason it is
        # exempt from `rollback_cmd`: it self-verifies and self-restores.
        if isinstance(apply_block, dict) and "cmd" in apply_block \
                and not isinstance(body.get("health"), dict):
            raise RegistryError(
                f"component {name!r}: a `cmd` apply must define a `health:` "
                "block (it is the only gate before reporting 'applied')"
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

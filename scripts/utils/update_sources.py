#!/usr/bin/env python3
"""Source adapters for the update manager: resolve the latest version of a
component from GitHub releases, PyPI, or the npm registry. Network reads only.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any
from urllib.error import HTTPError, URLError


class SourceError(Exception):
    """Raised when a source cannot be reached or parsed."""


def _get_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "heading-os-update-manager"})  # noqa: S310 - https literal
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - https literal
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        raise SourceError(f"HTTP {exc.code} for {url}") from exc
    except URLError as exc:
        raise SourceError(f"network error for {url}: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise SourceError(f"bad JSON from {url}: {exc}") from exc
    except (TimeoutError, OSError) as exc:
        # A read timeout is NOT a URLError. Since Python 3.10 `socket.timeout`
        # IS `TimeoutError`, an OSError sibling, so `urlopen(..., timeout=20)`
        # raises it straight past both clauses above. Measured 2026-08-26
        # against a loopback socket that accepts the connection and then never
        # answers: "UNHANDLED TimeoutError: timed out", with no SourceError. The
        # update manager catches SourceError to mark a component unresolved; an
        # unwrapped TimeoutError takes down the whole check instead of one row.
        # OSError last, and only here, so the two specific clauses above keep
        # their own messages.
        raise SourceError(f"network error for {url}: {exc}") from exc


def _strip_v(tag: str) -> str:
    return tag[1:] if tag.startswith("v") else tag


def latest_version(spec: dict[str, Any]) -> str:
    via = spec.get("via")
    if via == "github_release":
        data = _get_json(f"https://api.github.com/repos/{spec['repo']}/releases/latest")
        return _strip_v(data.get("tag_name", ""))
    if via == "pypi":
        data = _get_json(f"https://pypi.org/pypi/{spec['package']}/json")
        return data.get("info", {}).get("version", "")
    if via == "npm":
        data = _get_json(f"https://registry.npmjs.org/{spec['package']}/latest")
        return data.get("version", "")
    raise SourceError(f"unknown source via={via!r}")


def github_asset_url(spec: dict[str, Any], arch: str = "amd64") -> str | None:
    """URL of the latest-release linux/<arch> plugin tarball, or None."""
    data = _get_json(f"https://api.github.com/repos/{spec['repo']}/releases/latest")
    for asset in data.get("assets", []):
        name = asset["name"].lower()
        if "linux" in name and arch in name and name.endswith(".tar.gz") \
                and "no-plugin" not in name:
            return asset["browser_download_url"]
    return None

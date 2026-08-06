#!/usr/bin/env python3
"""Tests for the CI substrate every later Canopus clause depends on.

Two properties, both asserted structurally rather than textually. A test that
greps ci.yml for the string "fetch-depth: 0" passes against a commented-out
line and against the wrong job, so these parse the YAML and walk to the
`guards` job. The genesis test likewise resolves the sha against the real
repository instead of trusting the file to name a commit that exists.
"""

import json
import pathlib
import subprocess

import yaml


def test_the_guards_job_clones_full_history():
    ci = yaml.safe_load(
        (
            pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"
        ).read_text(encoding="utf-8")
    )
    checkout = next(
        s
        for s in ci["jobs"]["guards"]["steps"]
        if str(s.get("uses", "")).startswith("actions/checkout")
    )
    assert checkout.get("with", {}).get("fetch-depth") == 0, (
        "the guards job clones at depth 1, so every clause that walks history "
        "either errors or passes vacuously"
    )


def test_the_genesis_commit_resolves_unambiguously_and_is_an_ancestor_of_head():
    """The epoch names a commit that exists, and HEAD descends from it.

    The ref is deliberately ABBREVIATED rather than a full 40-character sha.
    A full sha reads to detect-secrets as a hex high-entropy string, and every
    way to silence that (a baseline entry, an allow-list entry, a pragma) is
    forbidden here without exception, because those are how a real secret
    eventually gets through. Git resolves an abbreviated ref natively and
    ERRORS on ambiguity rather than guessing, so nothing is weakened to buy
    the quiet: `rev-parse` failing IS the ambiguity check.
    """
    root = pathlib.Path(__file__).resolve().parents[1]
    cfg = json.loads(
        (root / "config/canopus-genesis.json").read_text(encoding="utf-8")
    )
    ref = cfg["genesis_ref"]
    assert ref and cfg.get("reason"), "genesis needs a ref and a stated reason"

    resolved = subprocess.run(
        ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
        cwd=root, capture_output=True, text=True, check=False,
    )
    assert resolved.returncode == 0, (
        f"the genesis ref {ref!r} does not resolve to exactly one commit: "
        f"{resolved.stderr.strip()}"
    )
    sha = resolved.stdout.strip()
    assert len(sha) == 40

    assert (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", sha, "HEAD"], cwd=root, check=False
        ).returncode
        == 0
    ), "HEAD does not descend from the genesis commit"

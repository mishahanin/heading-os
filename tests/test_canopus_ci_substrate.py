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


def test_the_genesis_commit_is_named_and_is_an_ancestor_of_head():
    root = pathlib.Path(__file__).resolve().parents[1]
    cfg = json.loads(
        (root / "config/canopus-genesis.json").read_text(encoding="utf-8")
    )
    sha = cfg["genesis_sha"]
    assert len(sha) == 40 and cfg.get("reason"), (
        "genesis needs a full sha and a stated reason"
    )
    assert (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", sha, "HEAD"], cwd=root, check=False
        ).returncode
        == 0
    )

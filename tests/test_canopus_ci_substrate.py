#!/usr/bin/env python3
"""Tests for the CI substrate every later Canopus clause depends on.

One property, asserted structurally rather than textually. A test that greps
ci.yml for the string "fetch-depth: 0" passes against a commented-out line and
against the wrong job, so this one parses the YAML and walks to the `guards`
job.

A second test stood here until 2026-08-07, asserting that
`config/canopus-genesis.json` named a commit that resolved and that HEAD
descended from it. The epoch it guarded stopped being needed when the check
moved from walking commits to reading committed notes, and the file had no
production consumer left, only this test asserting a file was well formed
for the benefit of nobody. Both are gone. `fetch-depth: 0` is NOT: C1, C2 and
C3 each reach the approval commit, which depth 1 does not fetch.
"""

import pathlib

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

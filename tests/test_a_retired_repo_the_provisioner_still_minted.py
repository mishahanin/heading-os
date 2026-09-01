#!/usr/bin/env python3
"""The exec provisioner minted a repository its own docstring called retired.

`31c-crm-central` and every per-exec `31c-crm-{slug}` were retired on 2026-08-30.
An executive's CRM records now live in THEIR OWN data overlay at
`../.heading-os-data-{slug}/crm/contacts/`, created by the admin-layer tool
`.heading-os-data/admin/provision/provision_exec.py`.

`scripts/provision-exec.py` kept `create_crm_repo` as step 7 of 11 in its
pipeline until 2026-09-01. It ran `gh repo create 31c-crm-{slug}` and seeded a
ROOT-LEVEL `contacts/` directory, which is not the layout any current reader
looks for. So even read as "the repo is still wanted", it was seeded wrong.

Line 7 of that file's own module docstring names the `31c-crm-{slug}` repo among
the things "the hard-cut migration retires". The file documented its own
retirement and did it anyway, which is the shape this whole audit keeps finding:
a decision recorded in prose that no code enforces.

## Why this was not urgent, and why it still had to close

`main()` hard-refuses unless `HEADING_OS_ALLOW_LEGACY_PROVISION=1`, so the step
was not reachable by accident. It was reachable DELIBERATELY, by anyone setting
that variable to get at the rest of the pipeline. The escape hatch exists for the
retired WORKSPACE layout; it was never a licence to mint a retired repo.

## What this file binds, and what it deliberately does not

It binds the PIPELINE. `create_crm_repo` the function is left in place, and
`tests/test_a_guard_set_that_never_left_the_admin_machine.py` still pins its
seed-failure semantics. Removing it from the pipeline is the decision the
operator took on 2026-09-01; deleting the code is a separate decision nobody has
taken, and this file does not pre-empt it.

Asked of the SOURCE by AST rather than by running `main()`, because `main()`
parses arguments, refuses, and exits before the step list is built. The step list
is a literal in the source, so the source is where the question can be asked.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PROVISIONER = ROOT / "scripts" / "provision-exec.py"

# Repository names retired by the 2026-08-30 hard cut. A provisioner that mints
# one of these is provisioning into a topology that no longer has readers.
RETIRED_REPO_TOKENS = ("31c-crm-central", "31c-crm-")


def _steps() -> list[str]:
    """Every step name in `main`'s pipeline list, in order.

    The list is `[("name", lambda: ...), ...]`, so each step name is the first
    element of a Tuple inside a List. Derived from the AST rather than matched by
    regex: a comment naming a step, or a step name appearing in a docstring,
    must not count. Six separate findings in this campaign were assertions
    satisfied by the comment explaining the thing they guarded against.
    """
    tree = ast.parse(PROVISIONER.read_text(encoding="utf-8"), filename=str(PROVISIONER))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "main":
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.List):
                continue
            names = [
                elt.elts[0].value
                for elt in inner.elts
                if isinstance(elt, ast.Tuple)
                and len(elt.elts) == 2
                and isinstance(elt.elts[0], ast.Constant)
                and isinstance(elt.elts[0].value, str)
                and isinstance(elt.elts[1], ast.Lambda)
            ]
            if len(names) >= 5:
                return names
    return []


def test_the_pipeline_is_found_at_all():
    """The anti-vacuity jaw, and it earns its place.

    Every other test here asks whether something is ABSENT from the pipeline. An
    empty list satisfies all of them, so a rename, a refactor into a helper, or a
    change of the `(name, lambda)` shape would turn this file into a guard that
    passes over nothing. The floor is measured, not guessed: the pipeline held 11
    steps before the removal and holds 10 after.
    """
    steps = _steps()
    assert len(steps) >= 8, (
        f"only {len(steps)} pipeline step(s) found in main(); the AST walk no "
        f"longer recognises the step list, so every assertion in this file is "
        f"passing over an empty corpus. Found: {steps}")
    # A step known to be genuinely present, so the walk cannot be satisfied by
    # some other list of tuples that happens to be long enough.
    assert "create_github_repo" in steps, steps


def test_the_provisioner_does_not_mint_the_retired_crm_repo():
    """The headline. Step 7 of 11 created `31c-crm-{slug}` until 2026-09-01."""
    steps = _steps()
    assert "create_crm_repo" not in steps, (
        "scripts/provision-exec.py would create a per-exec `31c-crm-{slug}` "
        "repository, retired on 2026-08-30, seeded with a root-level "
        "`contacts/` directory that no current reader looks for. An executive's "
        "CRM records live in their own data overlay at "
        "`../.heading-os-data-{slug}/crm/contacts/`. If this step is being "
        "restored deliberately, change this test in the same commit and say why.")


def test_no_pipeline_step_name_still_speaks_of_the_retired_repo():
    """A rename is not a removal.

    `create_crm_repo` could come back as `seed_exec_crm` or `create_contacts_repo`
    and the test above would not notice. This asks the weaker but rename-proof
    question of every step's own name.
    """
    offenders = [s for s in _steps() if "crm" in s.lower() and "registry" not in s.lower()]
    assert not offenders, (
        f"pipeline step(s) {offenders} name a CRM repository. Per-exec CRM "
        f"repositories were retired on 2026-08-30; CRM lives in the exec's own "
        f"data overlay.")


@pytest.mark.parametrize("token", RETIRED_REPO_TOKENS)
def test_the_admin_layer_tool_provisions_the_current_topology(token):
    """The other half: the tool that IS used must not mint a retired repo either.

    Skipped rather than failed on a public clone, where the admin layer lives in
    the private data overlay and is legitimately absent. The skip states its own
    condition so it cannot decay into silence: a skip reason that stopped being
    true removes a test without anyone noticing.
    """
    tool = ROOT.parent / ".heading-os-data" / "admin" / "provision" / "provision_exec.py"
    if not tool.is_file():
        pytest.skip(
            f"{tool} is absent, which is the normal state of a public engine "
            f"clone with no private data overlay. Nothing to check here.")

    source = tool.read_text(encoding="utf-8")
    assert token not in source, (
        f"the admin-layer provisioner names the retired repository token "
        f"{token!r}. The current topology is a per-exec data overlay, "
        f"`heading-os-data-{{slug}}`.")


def test_the_admin_layer_tool_is_the_one_that_creates_the_overlay():
    """The positive anchor for the test above.

    Without it, `test_the_admin_layer_tool_provisions_the_current_topology` is
    satisfied by a tool that provisions NOTHING, or by a file that stopped being
    the provisioner. It must actually name the current repository shape.
    """
    tool = ROOT.parent / ".heading-os-data" / "admin" / "provision" / "provision_exec.py"
    if not tool.is_file():
        pytest.skip(
            f"{tool} is absent, which is the normal state of a public engine "
            f"clone with no private data overlay.")

    source = tool.read_text(encoding="utf-8")
    assert "heading-os-data-" in source, (
        "the admin-layer provisioner no longer names `heading-os-data-{slug}`, "
        "so it is either not the provisioner any more or it stopped creating "
        "the exec overlay. Either way the test above now proves nothing.")

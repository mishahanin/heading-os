# Engine ⟂ Data Segregation Contract

Last Updated: 2026-06-22
Last Verified: 2026-06-22

The load-bearing invariant of the HEADING OS two-part topology (see `CLAUDE.md`):
the **engine** clone (`.heading-os`) is code only — shareable, eventually public —
and carries NO real data, secrets, PII, or third-party entities. All data lives in
the sibling **data** overlay (`.heading-os-data`), reached at runtime through the
data-root seam (`get_data_root()` / `get_*_dir()` in `scripts/utils/workspace.py` +
`paths.py`). Routing per file is decided by `config/routing-map.yaml` and resolved by
`get_routing_destination()` → `engine | private | corporate`.

This document is the single contract for how that invariant is enforced: the six
layers, what each covers, where each stops, and how the guarantee is *proven* rather
than merely asserted. It exists because a single static check is not enough — finding
\#3 (2026-06-16) showed a regex guard silently missing an entire misroute class (five
document generators writing artifacts into the engine clone) for an extended period;
the 2026-06-22 `docs/superpowers/` leak (post-mortem below) showed that catching the
*outcome* only at **bypassable** layers is also not enough.

## Why six layers, not one

The threat is a data artifact landing inside the engine clone. It can arrive three
ways: (a) code that joins an engine root to a data dir, (b) a SKILL handing a bare
data path to a Bash-invoked script, (c) any process — including a third-party plugin —
writing into the engine tree. No single check covers all three. The layers compose
along **two** axes: *cause vs outcome* (static checks catch the cause early and
cheaply; the runtime tree check catches the outcome no matter the cause) and
*bypassable vs unbypassable* (commit/pre-push gates are skippable with `--no-verify`
or a deleted hook, so the outcome check must ALSO exist as pure code on the sanctioned
push path, where no flag can skip it — layer 6).

## The six layers

| # | Layer | Mechanism | Catches | Stops at |
|---|---|---|---|---|
| 1 | Static bypass guard | `tests/test_data_root_no_bypass.py` (pre-commit `data-root-bypass-guard` + run-tests) | Code in `scripts/`/`.claude/` that joins an engine-root token (incl. the `Path(__file__).parent.parent` / `os.path.dirname(os.path.dirname(...))` idiom) to a data dir, incl. the `Path(VAR) / "datadir"` wrapper | Regex over source text only; cannot see runtime writes or Bash strings |
| 2 | Leak guard | `scripts/leak-guard.py` (pre-commit `leak-guard-paths` / `leak-guard-staged`) | Hardcoded data paths + private/corporate content staged into the engine repo. **Auto-active in split topology** (data-root seam: `get_data_root() != workspace root`) — no longer relies on a hand-set `HEADING_OS_ENGINE_REPO` marker, which is exactly why it sat inert during the 2026-06-22 leak | Commit-time; bypassable with `--no-verify` |
| 3 | Data-path redirect | `.claude/hooks/data-path-redirect.py` (PreToolUse) | Rewrites `@outputs/...`-style references to the data root for Read/Write/Edit/Grep/Glob tool ops | Does **NOT** cover Bash — a script invoked via Bash with a bare data path is not redirected |
| 4 | Build partition | `scripts/build_engine_repo.py` `_suspicious_engine()` | Build-time post-condition: refuses if any non-engine-routed file lands in the engine partition when materialising the public repo | Only runs at engine-build time, not during daily work |
| 5 | Runtime tree-clean | `tests/test_engine_tree_clean.py` (pre-commit `engine-tree-clean` + run-tests), detector in `scripts/utils/engine_guard.py` | **The outcome:** any file in the engine clone (tracked or untracked-not-ignored) whose routing destination is private/corporate — regardless of how it was written (script, SKILL Bash, or plugin) | Routing-filtered, so engine carve-outs (e.g. `datastore/brand/` if it ever appears) are not flagged. Bypassable with `--no-verify` / un-armed pre-push hook → layer 6 is the belt to this |
| 6 | **Unbypassable push wall** | `scripts/push-all.py` `engine_clean_scan()` (shares the layer-5 detector in `scripts/utils/engine_guard.py`) | The SAME outcome as layer 5 — any private/corporate-routed file in the engine clone — but enforced in **pure code on the sanctioned push path** (`/backup` → `push-all`), with **no skip flag**. A `--no-verify` commit and an un-armed pre-push hook still cannot ship a data artifact out of the engine | Only the engine repo (the DATA repo legitimately carries private files). Someone hand-running `git push` outside `push-all` bypasses it — the sanctioned path + GitHub-side controls are the answer there, same model as the secret content_scan |

### Advisory companion (not a guarantee layer)

`scripts/audit-skill-bash-paths.py` (pre-commit `skill-bash-paths`,
`tests/test_skill_bash_paths.py`) is a baseline-ratchet over SKILL.md bash blocks:
it fails only when a skill gains a *new* bare-data-path bash line beyond the frozen
baseline (the current hits are illustrative template paths, not live misroutes). It is
the early, narrow signal for the gap layer 3 leaves open (Bash). It is **advisory** —
the authoritative guarantee for that gap is layer 5, which catches the outcome.

## Boundaries (honest)

- **Plugins** are third-party; their writes cannot be intercepted. Layer 5 catches a
  plugin artifact in the engine tree *after the fact* — the contract for plugins is:
  they are driven from the engine clone, but their artifacts must not settle there.
- **The commit hook is bypassable** (`--no-verify`, or a deleted hook), and so is the
  pre-push `run-tests` gate (`--no-verify` skips it; the hook can be un-armed or
  deleted). The unbypassable wall for secrets is `push-all.py content_scan`; the
  unbypassable wall for the tree-clean invariant is `push-all.py engine_clean_scan`
  (layer 6) — both pure code on the sanctioned push path with no skip flag. The earlier
  claim that "the `run-tests` gate is the unbypassable wall for the tree-clean
  invariant" was wrong (run-tests IS bypassable) and was the latent gap the 2026-06-22
  leak exposed; layer 6 closes it.
- **Engine clone is clean today:** the engine clone `.heading-os` carries no
  private/corporate-routed file (verified 2026-06-22: a full tracked+untracked routing
  scan returns zero). Layers 5 and 6 both pass on the live tree; the routing filter
  keeps them robust if a carve-out is ever added.

## Post-mortem: the 2026-06-22 `docs/superpowers/` leak

Four private design specs (`docs/superpowers/...`, route `private`) were tracked in the
engine repo. Every layer that *should* have caught it failed for a distinct reason, and
the combination is the lesson:

1. **Layer 2 (leak guard) sat inert.** `check-staged` only fired when the hand-set env
   var `HEADING_OS_ENGINE_REPO=1` was present. It was not, so the guard no-opped on
   the commit. *Fix:* auto-activate from the data-root seam (split topology ⇒ engine);
   the env var is now an override, not the sole trigger.
2. **Layer 5 had an unsound narrowing.** The detector gated on a fixed top-level
   allowlist (`outputs/crm/knowledge/...`); `docs/` was not in it, so a private file
   under `docs/` was never routing-checked. *Fix:* filter by routing destination only —
   no allowlist (the `docs/superpowers/` regression test pins this).
3. **No unbypassable outcome check existed.** Even with 1 and 2 fixed, both are
   bypassable. The push path scanned secrets but not routing. *Fix:* layer 6.

The shape of the failure — "a check that never fires is indistinguishable from a clean
result" — is the same lesson as finding \#3. Every guarantee layer is now bidirectional
(proves it can fail) and at least one is unbypassable.

## How the guarantee is proven (not just asserted)

Layer 5 is **bidirectional**: it passes on the clean engine tree (negative branch) and
positively proves the detector fires on a private-routed path (positive branch), so a
no-op detector cannot masquerade as a passing guarantee. This is the lesson of finding
\#3 — a check that never fires is indistinguishable from a clean result until you prove
it can fail.

## When adding new code

- Reach a data path only through `get_*_dir()` — never join a root token to `outputs/`,
  `crm/`, `knowledge/`, `threads/`, `plans/`, `context/`, `datastore/`, `auto-memory/`.
- In a SKILL bash block, resolve output paths via `$(... get_outputs_dir ...)` /
  `$OUTPUTS_DIR`, not a bare `outputs/...` literal.
- If a new write capability lands data in the engine clone, layers 5 and 6 fail the
  gate — fix the route, do not whitelist.

## Change control

Changes to this contract or any weakening of the six layers require Misha's explicit
approval. Classification: engine (this is public-shippable documentation of the public
mechanism; it lives at `docs/` root, not `docs/security/` which routes private).

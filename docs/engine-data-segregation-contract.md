# Engine ⟂ Data Segregation Contract

Last Updated: 2026-08-20
Last Verified: 2026-08-20

Consumed by: `.claude/rules/classification.md`. That always-on rule keeps the two
classification decisions the model makes, and points here for everything else. Read
this file too before you add code that writes files or touches the data seam.

The load-bearing invariant of the HEADING OS two-part topology (see `CLAUDE.md`):
the **engine** clone (`.heading-os`) is code only — shareable, eventually public —
and carries NO real data, secrets, PII, or third-party entities. All data lives in
the sibling **data** overlay (`.heading-os-data`), reached at runtime through the
data-root seam (`get_data_root()` / `get_*_dir()` in `scripts/utils/workspace.py` +
`paths.py`). Routing per file is decided by `config/routing-map.yaml` and resolved by
`get_routing_destination()` → `engine | private | corporate`.

This document is the single contract for how that invariant is enforced: the seven
layers, what each covers, where each stops, and how the guarantee is *proven* rather
than merely asserted. It exists because a single static check is not enough — finding
\#3 (2026-06-16) showed a regex guard silently missing an entire misroute class (five
document generators writing artifacts into the engine clone) for an extended period;
the 2026-06-22 `docs/superpowers/` leak (post-mortem below) showed that catching the
*outcome* only at **bypassable** layers is also not enough.

## Why seven layers, not one

The threat is a data artifact landing inside the engine clone. It can arrive three
ways: (a) code that joins an engine root to a data dir, (b) a SKILL handing a bare
data path to a Bash-invoked script, (c) any process — including a third-party plugin —
writing into the engine tree. No single check covers all three. The layers compose
along **two** axes: *cause vs outcome* (static checks catch the cause early and
cheaply; the runtime tree check catches the outcome no matter the cause) and
*bypassable vs unbypassable* (commit/pre-push gates are skippable with `--no-verify`
or a deleted hook, so the outcome check must ALSO exist as pure code on the sanctioned
push path, where no flag can skip it — layer 6).

There is a third axis, and it took a real miss to find it: *where the file goes* vs
*what is inside it*. Layers 1-6 all answer the routing question. A file whose routing
destination is correct passes every one of them, however private its CONTENTS are —
an engine-routed script that embeds a real person's name and e-mail is, to layers 1-6,
a clean engine file. Layer 7 is the only one that opens the file and reads it, and it
is the layer that caught a category of private content the routing map had missed.

## The seven layers

| # | Layer | Mechanism | Catches | Stops at |
|---|---|---|---|---|
| 1 | Static bypass guard | `tests/test_data_root_no_bypass.py` (pre-commit `data-root-bypass-guard` + run-tests) | Code in `scripts/`/`.claude/` that joins an engine-root token (incl. the `Path(__file__).parent.parent` / `os.path.dirname(os.path.dirname(...))` idiom) to a data dir, incl. the `Path(VAR) / "datadir"` wrapper | Regex over source text only; cannot see runtime writes or Bash strings |
| 2 | Leak guard | `scripts/leak-guard.py` (pre-commit `leak-guard-paths` / `leak-guard-staged`) | Hardcoded data paths + private/corporate content staged into the engine repo. **Auto-active in split topology** (data-root seam: `get_data_root() != workspace root`) — no longer relies on a hand-set `HEADING_OS_ENGINE_REPO` marker, which is exactly why it sat inert during the 2026-06-22 leak | Commit-time; bypassable with `--no-verify` |
| 3 | Data-path redirect | `.claude/hooks/data-path-redirect.py` (PreToolUse) | Rewrites `@outputs/...`-style references to the data root for Read/Write/Edit/Grep/Glob tool ops | Does **NOT** cover Bash — a script invoked via Bash with a bare data path is not redirected |
| 4 | Build partition | `scripts/build_engine_repo.py` `_suspicious_engine()` | Build-time post-condition: refuses if any non-engine-routed file lands in the engine partition when materialising the public repo | Only runs at engine-build time, not during daily work |
| 5 | Runtime tree-clean | `tests/test_engine_tree_clean.py` (pre-commit `engine-tree-clean` + run-tests), detector in `scripts/utils/engine_guard.py` | **The outcome:** any file in the engine clone (tracked or untracked-not-ignored) whose routing destination is private/corporate — regardless of how it was written (script, SKILL Bash, or plugin) | Routing-filtered, so engine carve-outs (e.g. `datastore/brand/` if it ever appears) are not flagged. Bypassable with `--no-verify` / un-armed pre-push hook → layer 6 is the belt to this |
| 6 | **Unbypassable push wall** | `scripts/push-all.py` `engine_clean_scan()` (shares the layer-5 detector in `scripts/utils/engine_guard.py`) | The SAME outcome as layer 5 — any private/corporate-routed file in the engine clone — but enforced in **pure code on the sanctioned push path** (`/backup` → `push-all`), with **no skip flag**. A `--no-verify` commit and an un-armed pre-push hook still cannot ship a data artifact out of the engine | Only the engine repo (the DATA repo legitimately carries private files). Someone hand-running `git push` outside `push-all` bypasses it — the sanctioned path + GitHub-side controls are the answer there, same model as the secret content_scan |
| 7 | **Content guard** | `scripts/content-guard.py` (pre-commit `content-guard-31c`) **and** `scripts/push-all.py` `engine_content_scan()`, at step 0 of `push_repo` | **WHAT is inside an engine-routed file**, not where the file goes: a real person slug or name, a handle, an e-mail, a Telegram ID, or a curated company / event / codename. The denylist is harvested from the private DATA overlay by `scripts/utils/content_denylist.py`. Layers 1-6 all pass such a file, because its routing destination is correct | **Inert where the DATA overlay is absent** (a public clone, CI): the denylist has no tokens and the gate returns 0. Only the operator's machine both authors and pushes engine content, so that is the machine on which it must hold. The pre-commit half is bypassable with `--no-verify`; the `engine_content_scan()` half is pure code on the sanctioned push path with no skip flag, like layer 6. A file it cannot read counts as a leak, not as clean. Since 2026-09-05 the CLI half REUSES a per-file verdict from `scripts/utils/content_scan_cache.py` rather than re-scanning an unchanged file: a row is keyed on the file's content digest AND on a digest of the scanning code plus the resolved denylist, only CLEAN verdicts are stored, and every doubt (no row, an unreadable store, a key that could not be computed) scans. `--no-cache` turns it off. `engine_content_scan()` on the push path is untouched |

### Why layer 7 is a guarantee layer and not an advisory companion

Three properties put it in the table rather than in the section below.

1. **It is unbypassable on the sanctioned push path.** `engine_content_scan()` runs at
   step 0 of `push_repo` in `scripts/push-all.py`, in pure code with no skip flag. That
   is the same criterion that makes layer 6 a guarantee rather than a warning.
2. **It answers a question no other layer answers.** Every routing layer waved through
   the content it caught, and it earned its place by catching that content.
3. **It fails closed.** An engine-routed file it could not open exits 1 exactly as a
   leak does. Unverified is not clean, which is the property the advisory companion
   below deliberately does not have.

Its honest bound is the one stated in the table: no DATA overlay means no denylist and
no verdict. That is a scope, not a hole, and it is the same shape as layer 6's bound
(someone hand-running `git push` outside `push-all` is outside the wall).

A second bound, added 2026-09-04, and stated because it narrows what the layer sees.
An organisation whose name reduces to one ORDINARY ENGLISH word donates only its
multi-word phrase, never the bare word: `config/ordinary-english.txt` (22,862 words,
generated by `scripts/dev/build-ordinary-english.py` from a public frequency list
intersected with a public-domain dictionary) is the vocabulary that decides, and
`_apply_ordinary_english_floor` withholds the bare form only when a longer token still
covers the same entity. Without it two CRM rows arriving on 2026-09-04 put 128 lines of
ordinary engine prose in front of the wall and blocked every push on the machine.
An entity whose ONLY form is an ordinary English word keeps its bare token and the gate
stays noisy over it, which is the direction chosen deliberately. What the floor
withheld is printed on the gate's clean line rather than left to be inferred, and the
vocabulary file is the one path in `CONTENT_SCAN_EXEMPT`, its digest pinned by
`tests/test_a_denylist_that_harvested_ordinary_english.py`.

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

## Record classification

Moved here verbatim from `.claude/rules/classification.md` on 2026-08-20, which keeps
only the two decisions the model itself makes ("When Creating New Files", "After
Classification"). What follows describes what `get_routing_destination()` computes and
what `/publish-corporate` ships — reference, not a directive.

### The three destinations, and the older two-value label

Every workspace record resolves to one of three **routing destinations**: **engine**
(code, shareable to everyone, eventually public — `.heading-os`), **private** (CEO data,
never shared — `.heading-os-data`), and **corporate** (content shared down to executives
via `heading-os-corporate`).

The older two-value label still used by exec-sync tooling — **corporate** (shared with
execs) vs **ceo-only** (CEO-private) — is now a thin collapse of the three:
`private → ceo-only`; `corporate → corporate`; `engine → corporate` (engine code is the
most-shared thing, so it is not "CEO-private").

Single classification input: `config/routing-map.yaml` (HEADING OS step 7 — replaced
`config/classification.json`, removed 2026-06-14).
Shared resolver: `get_routing_destination()` / `get_classification()` in `scripts/utils/workspace.py`.
Health check: `scripts/classification-health.py`.

### Resolution order

When a path could match multiple rules in `routing-map.yaml`, the **most-specific
(longest matching) rule key wins**; otherwise the map `default` applies.

1. **Exact / longest-prefix rule key** in `routing-map.yaml` `rules:`. A key ending in
   `/` matches as a directory prefix; a key without a trailing `/` matches that exact
   file or that path as a prefix.
2. **Map default** — `engine`. Unmatched paths resolve shareable, NOT private.

This default direction is deliberate: every DATA directory (`crm/`, `knowledge/`,
`outputs/`, `threads/`, `context/`, `plans/`, `templates/`, `_archive/`, …) carries an
explicit `private` rule so real data fail-closes; only code-ish paths fall through to
the engine default. The hard fail-closed case is a *broken* `routing-map.yaml`:
`load_routing_map()` then forces default `private` so an unreadable map treats everything
as CEO data.

Example: `knowledge/shared/ai/notes.md` → `corporate` because `knowledge/shared/`
(longer) beats the broader `knowledge/` → `private`. `knowledge/ai/notes.md` →
`private` (→ ceo-only) per the `knowledge/` rule.

Adding a rule: append the path under `rules:` in `config/routing-map.yaml` with its
destination and run `scripts/classification-health.py` to verify resolution.

### Push updates

When the CEO invokes `/push-updates`, files whose **routing destination is
`corporate`** that changed since the last build are published to `heading-os-corporate`; a
BUILD.json manifest tracks the build number and execs pull on their hourly sync.

> **Narrowed at cutover (step 8, 2026-06-14):** publish-corporate ships routing
> `corporate` ONLY — content, not code (datastore, knowledge/shared, the two context
> carve-outs, crm config/aliases/address-book, corporate/ daemon config). Engine code
> is NOT published here; execs receive it by cloning the engine repo (`.heading-os`).
> This replaced the prior pre-separation collapse (`corporate` ∪ `engine`). The
> two-value `get_classification` still exists for memory-index/health; publish uses
> the three-value `get_routing_destination` directly.

### Note: pre-creation guards for on-demand directories

Some `rules:` keys map a directory to `private` before that directory exists on disk
(an on-demand path created only when a feature first runs). This is intentional and the
safe direction — the guard ensures the first write lands `private` rather than falling
through to the engine default. A rule key with no current on-disk directory is expected,
not a defect; do not re-flag it as a broken reference.

## Change control

Changes to this contract or any weakening of the seven layers require Misha's explicit
approval. Classification: engine (this is public-shippable documentation of the public
mechanism; it lives at `docs/` root, not `docs/security/` which routes private).

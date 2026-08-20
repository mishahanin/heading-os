<!-- version: 4.0.0 | last-updated: 2026-08-21 -->
# HEADING OS v0.12

**Switches you can reach, and four gates that were never armed.**

An engine that runs long sessions has one setting that decides the quality of everything else: how full the context window is allowed to get before the session compacts. Until this release that number lived in an environment variable, which meant changing it meant restarting - and restarting is the exact thing a long piece of work cannot afford. It is now a per-session switch the operator sets mid-flight, in the running window, and it takes effect at the next pause.

The other half of this release is the closing sweep that shipped with it. Four mechanisms were found that existed, had passed review, and were connected to nothing: a push hook that had silently stopped uploading Git LFS content for 53 days, a private data overlay whose own tests ran in no gate at all, a plugin bundle shipping a skill without the two commands that skill's own text tells you to run, and a frontmatter rewrite that has emitted YAML no parser accepts since the day the generator first shipped. None of them announced itself. Each was found by reading the code against what it does.

| | |
|---|---|
| **7** | commits since v0.11 |
| **6,017** | automated tests across 403 test files |
| **39** | tests holding the new threshold switch |
| **53** | days the engine's push hook had not uploaded a Git LFS object |
| **4** | gates that existed and had never been armed |
| **0** | messages sent without a human click |

Released 21 August 2026. Every figure on this page comes from the engine's own records, measurements, and test suite. None are estimates. The full commit-level record is in [CHANGELOG.md](https://github.com/mishahanin/heading-os/blob/main/CHANGELOG.md).

---

## 1. The compaction threshold is now a switch, not a restart

*The headline change, described in full*

Long reasoning degrades as the context window fills. That is not a controversial claim and it is not a subtle effect; the useful working band sits somewhere around 30 to 40 percent used, and past it the quality of a chain of thought falls off well before anything breaks. So the number that decides when a session compacts is an operational choice, made per piece of work, by the person doing the work.

Until v0.12 that number was `CLAUDE_HANDOFF_HARD_THRESHOLD`, an environment variable read at launch. To change it you restarted the session. Restarting a session to protect the quality of a long session is a circular cost, and it is the reason the setting was almost never touched.

### What ships

```
/compact-at 35            # this session compacts at 35% used
/compact-at status        # what is set, and who set it
/compact-at off           # back to the workspace default
```

The same thing from a terminal, with no chat in the loop:

```
python scripts/checkpoint-paths.py --compact-at 35
python scripts/checkpoint-paths.py --compact-at status
python scripts/checkpoint-paths.py --compact-at off
```

### It takes effect without a restart, and that is the whole point

Nothing reloads and nothing is signalled. The two components that read the threshold - the Stop hook and the status line - are fresh processes spawned per event, and both re-read the session's own state file every time they run. Writing the number to that file therefore puts it in force at the next pause. There is no daemon to notify and no cache to invalidate.

### One number, not two

The soft reminder is always exactly five points below the hard threshold. It is derived, never stored, and never a second setting. `/compact-at 35` gives a reminder at 30 and a compaction at 35. That relationship is fixed because two independently settable thresholds are two things to get wrong, and the pair only ever moves together in practice.

### The bounds are 15 to 90, and both ends have a reason

Under 15, the derived soft threshold lands below 10 percent used. At that depth the trigger sits at or under the always-loaded context floor - the rules, the memory index, the session preamble that load before any work begins - so it fires on a window that is already full of things nobody can remove, and it fires again immediately after compacting. That cascade is not hypothetical; it is the confirmed cause of an incident on 19 August 2026.

Over 90, there is no window left to write the handoff. The compaction is not the first step: a handoff document has to be written first, or the next session starts with nothing. A threshold that leaves no room for it produces a clean compaction into an empty room.

### It refuses a number that would fire immediately

If the session's last status-line render read 41 percent used, `/compact-at 35` is refused. The refusal says so, and it names the reading honestly:

> This session read 41.0% used at its last status-line render, so a hard threshold of 35 would fire at the very next pause. The reading is one render old and the window only grows. Pick a number above 41.0, or run `--compact-at off`.

That phrasing is deliberate. Only the status line writes that figure, and only on a render that actually measured, so inside one long turn the true fill has already outrun it. Calling it "the current fill" would be a claim the mechanism cannot support.

### It refuses to raise switches you did not ask for

Setting a threshold does not turn on autonomous compaction. With `auto` and `unattended` both off, the hook ASKS at the threshold and compacts nothing, and the command says exactly that rather than quietly flipping a switch on the operator's behalf:

> auto and unattended are both off, so the hook will ask at 35% and compact nothing by itself.

### Where it is stored, and why that detail mattered

The value goes to `session_hard_threshold`, never to `hard_threshold`. The status line rewrites `hard_threshold` on every render as its own echo of the resolved configuration, so an operator's choice recorded there would have survived roughly one turn before being overwritten by the thing that was supposed to read it. The same trap had already been walked into once with `auto`, which is why `--auto` writes `session_auto`.

The number dies with the session. The state file is keyed by session and pruned with it, so a choice made about one piece of work does not leak into the next one. No cleanup path was needed and none was written.

### The status line names the number

The autonomy segment now carries the resolved threshold in every state that can actually fire a compaction, and omits it in the one that cannot:

```
⏵ unattended 35%          driven compaction will fire at 35%
⏵ auto 35%                driven compaction will fire at 35%
⏸  unattended paused 35%  stretch ended, switch still up, still fires
⏸  manual                 the hook only asks; no number, because nothing fires
```

It is shown whether the number came from the session or from the environment. A figure that appeared only once overridden would make "not set" and "not working" look identical, which is the precise ambiguity the segment was added to remove.

!!! note "The change that was not cosmetic"
    The status line computed the threshold level from module-scope constants four lines before it read the state file. It is also the sole producer of `needs_compact_offer`, the flag that eventually stamps `last_offer_at`, which is the floor `_request_compaction` hands to `_handoff_since`. Left in that order, the session's own number would have been read by the Stop hook while the status line kept queueing offers against the workspace default. The threshold would have looked set, the status line would have printed it, and no offer would ever have been queued at it. Reordering the read is four lines and it is the difference between the feature working and the feature appearing to work.

39 tests hold this in `tests/test_session_compaction_threshold.py`. The design went through `/scrutinize` before any code was written: one HIGH, four MEDIUM, three LOW and three NIT findings, all applied to the plan rather than to the implementation.

---

## 2. A push hook that had not uploaded a binary in 53 days

`.githooks/pre-push` occupies the slot git-lfs installs its own hook into. It ended with `exec run-tests.py` and never delegated onward, while `.gitattributes` routes ten binary extensions through LFS. Since 29 June 2026, any `.png` or `.pdf` added to this repository would have been pushed as a pointer file with no object behind it: green for the person pushing, broken for the next person cloning.

It never bit. All nine existing LFS objects were added on the day the hook first replaced the stock one, so nothing new had gone through the broken path. The absence of a symptom was mistaken for coverage for seven weeks.

The sharp part of this finding is where it came from. The review that diagnosed this exact hazard for the private data overlay, and wrote the delegation into `.githooks/pre-push-data`, looked straight past the engine hook carrying the identical defect. Two guards now hold it: the delegation is asserted present, and it is asserted to run AFTER the suite, because exec-ing git-lfs first would satisfy a presence check while skipping every test.

---

## 3. Tests that ran in no gate at all

The private data overlay has its own `tests/` directory. Nothing ran it. The engine's pre-push hook runs the ENGINE suite, and `push-all` invoked the data push with `test_gate` unset, so those tests executed only when somebody typed the command by hand. The first run of the new gate found two that had been failing unnoticed.

The fix reuses the mechanism rather than inventing a second one: a versioned hook in `.githooks/`, installed machine-locally, with `push-all` refusing to push a repository whose gate is not armed. The two gates carry distinct markers so the data repository can never borrow the engine's and demand the engine suite on a tree that holds no engine.

Two constraints shaped it, and both have tests. A data overlay genuinely tracks LFS objects, so the hook hands off to `git lfs pre-push` at the end - asserted, not assumed, for the reason section 2 exists. And an executive's managed overlay carries no `tests/` directory at all, so absence passes rather than failing closed.

The gate also resolved the engine by guessing "the sibling directory named `.heading-os`", a name this project nowhere promises - a public clone is `heading-os` - and then fell through to a bare `python3`, which on this machine carries pytest 9.0.3. A wrong guess ran the overlay's tests green under none of the pinned dependencies. The installer already knew the real path and was throwing it away; it now stamps it in, and a relocation shows red instead of quietly passing.

---

## 4. A skill that shipped without the commands it tells you to run

`build-plugins.py` had no field for `.claude/commands/`. The `heading-core` bundle therefore shipped the `/checkpoint` skill and neither `/unattended` nor `/compact-at` - the two commands that skill's own body instructs the operator to use. Anyone installing the bundle got documentation for controls that were not in the box.

Finding that exposed something older. The `${CLAUDE_PLUGIN_ROOT}` path rewrite substituted into the double-quoted `allowed-tools` frontmatter scalar without escaping, injecting a bare `"` into it. Every `SKILL.md` the generator has ever built has shipped frontmatter that `yaml.safe_load` refuses - since the generator first shipped. The rewrite now splits frontmatter from body and escapes inside the quoted scalar, keeping the quotes in the parsed value where they protect a cache path containing a space. The guard parses what was written rather than trusting the substitution, and the old form was falsified by reproduction before the new guard was accepted.

Replayed across all 96 in-repo skills, the original defect breaks exactly two, `checkpoint` and `queue-draft`, and only `checkpoint` ships in a bundle. The real blast radius was `heading-core` alone.

!!! note "One bundle is not the corpus"
    The guard that caught this rode a fixture building `heading-core` by itself, while four other bundles ship eleven more skills through the same rewrite. A broken shape only they carried would have passed every test in the file. It now builds `--all`, parses every `SKILL.md` and every bundled command, and asserts that at least one file was actually rewritten - a guard running over untouched files proves nothing about the rewrite it exists to check. Measured while widening it: 5 bundles, 14 files, 10 rewritten, 0 bad.

---

## 5. A done marker that did not survive its own turn

The unattended mode lets a long run continue at a pause instead of halting. `scripts/checkpoint-paths.py --done` is how the assistant declares the plan finished so the run stops.

`unattended_turn` cleared the entire window whenever the Stop hook's `prompt_id` differed from the recorded `unattended_turn_id`, four lines before it read the done marker. That comparison tests turn IDENTITY and never age, so it could not distinguish last night's stale marker from one written seconds earlier inside the turn now ending. The operator's own turn is the common case here, being the first pause after any instruction he gives. So `--done` printed `done recorded`, and the hook continued the stretch anyway. It worked only from the second consecutive continuation onward.

Measured across two consecutive turns of a live session on 20 August: the marker reached the state file and was gone by the time the hook looked for it. `unattended_paused_at` now separates the two cases, because it is stamped when the hook ACTS on a marker - carrying it means the marker has had its effect, lacking it means the hook has never seen it.

---

## 6. CodeGraph: an index of the code, on trial

*A tool, not a feature. It ships nothing into this repository.*

This release is the first built with a structural index of the engine's own source. It is worth describing honestly, because it changed how the work in sections 2 through 5 was done, and because it is not part of the product.

### What it is

CodeGraph is a third-party, MIT-licensed indexer with a Rust kernel that parses a repository into a SQLite knowledge graph of symbols and the edges between them: what calls what, what references what, what instantiates what. Over this engine that is 872 files, 17,748 symbols and 46,498 edges - 8,870 functions, 184 classes, 358 methods, 2,614 variables - built in 2.3 seconds and stored in a 53 MB local cache. Queries return in about a third of a second.

### What it actually does for the work

The engine carries 829 tracked Python files and roughly 208,000 lines. Nothing indexed their structure. The workspace's existing memory index covers only Markdown - skill and rule text - and `ast-grep` matches patterns without an index, so neither could answer the two questions that dominate careful editing:

- **Who calls this?** Not "which files contain this string", which is what grep answers and which is wrong in both directions - it misses dynamic dispatch and it counts comments, docstrings and unrelated same-named symbols.
- **What breaks if I change it?** The blast radius, with file and line, before the edit rather than after the test run.

One query returns the verbatim source of the relevant symbols, the call paths among them, and that blast radius. In practice this replaces a loop of a dozen greps and reads with a single call, and it is more accurate than the loop it replaces.

Three checks were run against grep before trusting it: the blast radius of `get_data_root` (476 affected symbols, with locations), the callers of `send_card` (exactly two, which is correct and which grep over-reported), and the action-queue approval flow end to end. All three were right.

### Why it suits this codebase in particular

This engine is unusually edge-dense for its size. A single seam function like `get_data_root` is reached from 476 places; the security guarantees are enforced by six separate layers that must all still hold after any change to the routing map; and the rule against silently breaking a gate is the entire subject of sections 2 through 5 above. A tool whose native output is "here is everything that depends on what you are about to touch" is aimed directly at the failure mode this project keeps finding in itself.

### Three caveats its README does not mention

Stated because a tool trusted past its accuracy is worse than no tool.

1. **The "no covering tests found" annotation is unreliable here and should not be believed.** Its edge resolution is blind to `importlib.util.spec_from_file_location`, which is the idiom this suite uses to import scripts with hyphenated filenames. 172 of 360 scripts have hyphenated names and 155 of 413 test files use the dynamic loader, so the annotation reports zero tests for functions that are demonstrably tested. Measured directly: `get_routing_destination`, a normal import, resolved nine test callers correctly; `approve_card`, reached through a dynamically loaded module, reported none while a test calls it at a known line. Caller lists and blast radius stay accurate - only that one annotation is wrong.
2. **`codegraph affected` must always be given `--filter "tests/**/*.py"`.** Its default test glob misses this layout and returns nothing, so a bare run prints "No test files affected" - which reads as "nothing to re-run" when the true answer is 75 files. A silent empty result is worse than an error, so the filter is not optional here.
3. **Its installer adds a `UserPromptSubmit` hook** that injects an index excerpt into every prompt. Measured: about 16 KB on a code-shaped prompt, 556 bytes on prose, nothing on conversation, at 0.13 to 0.22 seconds. It self-gates sensibly, but on a code-heavy session it pulls the context fill forward - which interacts directly with section 1, and is one reason a per-session threshold is worth having.

### What it does NOT change about this repository

The engine ships no part of CodeGraph. There is no dependency, no configuration, no code path, and nothing to install to run HEADING OS. The single trace it leaves is one entry in `.gitignore` keeping its cache out of the tree. The index is a rebuildable per-machine artifact and is never a source of truth. Adopt-or-remove is decided on 4 September 2026, and if the answer is remove, two commands take it out and the `.gitignore` line becomes the only thing to revert.

---

## 7. What this means for you

- **The context threshold is yours, per session, without a restart.** Set it at the start of important work with `/compact-at 35`. The status line shows the number wherever it can actually fire.
- **Setting it changes nothing you did not ask for.** It will not enable autonomous compaction, and it tells you so when autonomous compaction is off.
- **Binaries push correctly again.** If you added an image or a PDF to this repository between 29 June and 21 August, verify it - the hook that should have uploaded it was not delegating.
- **If you run a private data overlay, its tests now gate its push**, and you need `install-git-hooks.py` run once per clone to arm it. An overlay with no `tests/` directory passes, which is the normal case on a managed workspace.
- **If you installed the `heading-core` plugin bundle, reinstall it.** The previous build shipped a skill whose commands were missing and frontmatter no YAML parser accepts.
- **Nothing about what the engine publishes has loosened.** The engine still carries no private data, and the push-time wall still has no override flag.

---

## 8. Honest limits

*What this release does not do, stated here rather than discovered later*

- **The threshold is a number, not a judgement.** It fires on percentage used. It does not know whether the current turn is a good place to stop, and a compaction at a bad moment is still a compaction at a bad moment.
- **The refusal check reads a figure one render old.** Inside a long turn the true fill has already outrun it, so the guard is a floor and not a guarantee. It is described that way in the message rather than dressed as a live reading.
- **Four unarmed gates were found; that is not a count of how many exist.** The sweep read the areas it reached. Areas it did not reach are not thereby clean, and nothing on this page should be read as coverage of the whole tree.
- **The Git LFS defect went 53 days undetected because nothing exercised it.** The two new guards assert the delegation is present and correctly ordered. Neither pushes an actual binary object through a real remote, so the end-to-end path remains proven by reasoning rather than by execution.
- **CodeGraph is on trial and may be removed.** Nothing in the engine depends on it, and no measurement on this page needs it to be true. Its "no covering tests" annotation is known wrong here and is not used for any decision.
- **This page is not inside the style gate.** That gate covers the twelve pages a reader executes plus the skill instruction bodies, and a release narrative is neither. Sections 1 to 8 were measured with the same checker anyway. The notice below is approved legal wording and stays verbatim, long sentences included.

---

## 9. Notice on names and examples


Official. Applies to this document and to everything else the project publishes. The full notice is [DISCLAIMER.md](https://github.com/mishahanin/heading-os/blob/main/DISCLAIMER.md) in the repository root.

**Every name, company, contact detail, figure, and scenario used as an example in this document, in the HEADING OS engine repository, in its documentation, and in any artifact generated from it is invented.** Each exists for one purpose only: to show how the software behaves. No example is a record of a real person, a real organisation, or a real transaction, and no example is derived from, based on, or modelled after any real party.

Placeholder names are drawn deliberately from conventional stand-in vocabulary, so that it is plain on sight that no real party is being described: `Acme`, `ExampleTelco`, `ExampleCorp`, `Northwind`, `Contoso`, `Globex`, `Initech`, `Jane Doe`, `John Smith`, and similar.

**This declaration holds without exception, including where a name turns out to match something real.** A placeholder is chosen to be invented; the supply of words is finite, and a chosen word may coincide with the name of an actual party somewhere in the world. That coincidence changes nothing about what the example is. **Presence in this project is never evidence of a relationship, a dealing, an interest, or a fact concerning anyone.** And if real content ever appears here notwithstanding the controls below, it is present in error, it is not published as a statement about anyone, and it does not become a claim by virtue of having appeared.

**Any resemblance** between a name, detail, or figure used here and an actual person, living or deceased, or an actual company, organisation, product, or event, **is unintended and coincidental.** No example is a statement of fact about any real party, and nothing in an example should be read as an allegation, an assessment, an endorsement, or a disclosure concerning anyone.

!!! note "One deliberate exception, stated plainly, because precision protects better than a sweeping claim"
    **The author's own identity is real and published on purpose:** Misha Hanin, reachable at misha.hanin@odinix.com. Statements about his own role, work, and affiliations are his own.

    **Project ownership:** HEADING OS is a personal project by Misha Hanin. 31 Concept is not involved in it, does not maintain, sponsor, or endorse it, and bears no responsibility or liability for it. Vocabulary resembling 31C's appears as illustrative example vocabulary only; those marks belong to 31 Concept.

Real operator data is never present in the published repository. It lives in a separate private store that is never published, and the boundary is enforced by code on the publishing path rather than by convention: a routing map declares what may be shared, write-time guards refuse private material entering the shareable tree, and a content scan with no override flag refuses the push outright on a match. An example that resembles something real is therefore a coincidence of naming, not a disclosure of records.

!!! note "If you believe an example resembles you or your organisation"
    Write to **misha.hanin@odinix.com** with the subject line **"HEADING OS naming"**, naming the file or page and the detail concerned. You do not need to demonstrate harm, assert a legal right, instruct a lawyer, or explain why it matters to you. A request is enough.

    Acknowledgement follows within five business days. The material and its occurrences elsewhere in the project are reviewed, and where confirmed, the name or detail is changed or removed in the next release, with nothing left in place pending an argument about whether the resemblance was close enough to count. Change or removal is not an admission that any resemblance was intended or that anything was wrongly done; it is a courtesy extended on request. Reports involving real personal data, credentials, or confidential information are handled as a security matter at the highest priority.

Nothing in this project or its documentation is legal, financial, tax, security, or other professional advice. The software is licensed under the Apache License, Version 2.0, and is provided on an "AS IS" basis, without warranties or conditions of any kind; the disclaimer of warranty and limitation of liability in Sections 7 and 8 of that licence apply in full. Where anything published by this project could be read as describing a real party, this notice governs and that reading is disclaimed. Full notice, version 1.0, effective 8 August 2026: [DISCLAIMER.md](https://github.com/mishahanin/heading-os/blob/main/DISCLAIMER.md) in the repository root.

<!-- version: 3.1.0 | last-updated: 2026-08-20 -->
# HEADING OS v0.11

**Green lights nobody had wired.**

A workspace tells you it is healthy through its own instruments. Tests pass. Gates run. A hook reports that it did the thing. Three days of reading the code against what it actually does found eight places where the instrument was reporting on nothing at all: tests that could not fail, a validator that had never once validated, a compaction that recorded success twice while executing nothing, a detector whose branch had never run in the workspace's entire recorded history. None of this was a broken feature. Every one of them was a green light wired to no circuit, and the suite, the gates and the review had all passed over them.

| | |
|---|---|
| **43** | commits since v0.10 |
| **5,941** | automated tests across 400 test files |
| **25** | tests that had never been able to fail |
| **182 of 326** | CRM records corrupted by the workspace's own merge tool |
| **0** | messages sent without a human click |

Released 20 August 2026. Every figure on this page comes from the engine's own records, measurements, and test suite. None are estimates. The full commit-level record is in [CHANGELOG.md](https://github.com/mishahanin/heading-os/blob/main/CHANGELOG.md).

---

## 1. Twenty-five tests that could not fail

*The most transferable finding in this release*

Three test files were written in the same shape. A helper, `_check(name, condition)`, returned a boolean. Each test called it a dozen times, collected the results in a variable named `ok`, and ended with `return ok`.

Under pytest, a test that RETURNS a value passes. The return produces a warning. It never produces a failure.

So 25 test functions, holding 78 conditions across the operational signals engine, the ops radar, and the action queue's synchronous send, had been green since the day each was written. Some of the conditions were false. Nobody could have known, because the only channel through which they could have said so was closed.

The fix is small and the guard matters more than the fix. `_check` now asserts. The accumulators and the hand-rolled `main()` runners are gone. And `pyproject.toml` promotes `PytestReturnNotNoneWarning` to an error, so the shape cannot come back quietly the next time someone reaches for it.

!!! note "Why review does not catch this"
    The shape reads correctly. `_check` looks like an assertion helper, `ok` looks like a result, and `return ok` looks like a test reporting its verdict. Every one of those readings is what the author intended. The defect lives in the contract between the file and the runner, which is not visible anywhere in the file.

---

## 2. The gate that had never run

*A validator, a hook, and a flag that cancelled both*

The CRM records carry a schema. A script validates them against it. A commit hook runs the script. All three existed, all three had existed for months, and in all that time the chain of them had never once executed from end to end.

`validate-crm-schema.py` skipped when the `jsonschema` library was absent — reasonable — and printed a line saying so. But `--quiet`, the flag the hook passes, suppressed exactly that line. The hook printed nothing and exited 0, which is indistinguishable from a clean pass.

Behind it, the hook itself was dead by construction. Its `files:` pattern was `^crm/contacts/.*\.md$`, and CRM records live in the private data overlay, so no commit in the engine repository can ever match it. It could not have fired.

Three changes: the dependency is pinned in the core set, the skip line prints on every path including `--json` and `--quiet`, and the hook is `always_run: true`. First run: 326 of 326 valid. One record needed correcting — `status: inactive`, where the schema says `dormant`.

The same sweep found the merge tool that writes those records was corrupting 182 of the 326 on every run — block lists flattened to empty strings, quoted scalars silently unquoted so `"2026-08-17"` became a date object. The parser now carries the shape through and round-trips all 326 byte-for-byte.

---

## 3. A compaction that reported success while doing nothing

*Measured on a live overnight session, not reconstructed*

The engine cannot compact a session from inside. No hook can; that was verified against the harness rather than assumed. What it can do is submit the text `/compact` to the pane hosting the session, through the terminal manager, and let the harness read it as if it had been typed.

That submission is a QUEUE operation. The harness runs a queued prompt when the current turn ends, and not one moment before.

The hook submitted the text and then printed a block decision, which is the one thing in the whole protocol that stops a turn from ending, so the queue it had written to a moment earlier could never be reached. It had guaranteed its own request would never run. On the next threshold it queued a second one behind the first. Both were recorded as successful.

The evidence is on disk and it is unambiguous. Two requests, 07:41:02 and 08:07:10, neither carrying an error. A compaction history whose newest entry was still the previous day. Two `enqueue` records in the transcript with no matching `remove`, while every real operator message in the same file cleared within seconds.

The second half of the same defect is worse. The harness records that queueing as an ordinary queue operation, which is the signal the hook uses to notice the operator typing mid-turn. With no removal ever arriving, that check returned true permanently: **the hook was reading its own request as a message waiting from the operator.** It could not tell its own voice from his.

Both ends are fixed and both are held by tests that fail on the mutation. A submitted compaction now ends the turn, and both readers ignore the literal the hook itself submits. Confirmed end to end the following morning: request stamped at 08:26:06, boundary executed at 08:28:24, recorded as operator-driven.

!!! note "The accepted cost, stated rather than hidden"
    The suppression keys on the literal text, so an operator who types `/compact` himself mid-turn is also ignored, and loses one grace period. The terminal manager gives the hook no marker of its own, and the alternative costs the mechanism its only path to a boundary.

---

## 4. What else was claiming more than it knew

*Four tools, one shape*

- **A hook refused to let anything READ six documentation files.** The check gated on one tool name and let every other tool fall through to a path test, so an ordinary Read returned a block, which the harness renders as a permission denial. The denial log records a real one on 11 August, eight days before anyone noticed.
- **A repeat detector had never fired in the workspace's whole recorded history.** It keyed on Python's builtin `hash`, which is randomised per process, and every hook invocation is a fresh process. The live state held 344 tool-history entries and 344 distinct signatures. Now a stable digest — and the guard is a subprocess test, because the in-process version passes against the broken implementation too.
- **The harness audit called a disabled plugin "running in this session".** It read what had been fetched and nothing read whether the loader starts it.
- **The compaction watcher written to investigate section 3 watched a key nobody writes**, and reported "no request" through a run in which one had fired. An instrument that names a missing key reads exactly like an instrument reporting that nothing happened. Its key list is now held against the writer by a test.

Three per-OS install templates carried an empty environment block, so every machine built from one ran the stock compaction window instead of the tuned one, and registered the hidden-character and injection scans on two of the four write tools.

---

## 5. What was removed

*Deletion is a release note item*

- **Specs, plans and architecture decision records left the public repository.** The engine is published; how it was built is not. They moved to the private overlay, 84 back-pointers in engine code were repointed, and a test now resolves every one of them.
- **Three dead functions and nine dead constants**, after a sweep of 1,428 files. One was proven unreachable at its own birth commit rather than merely unused today. Three constants were kept, each with the reason written into the code, and one gained the test its comment had promised.
- **A context-monitoring hook and its four registrations**, superseded and duplicating a measurement the status line already owns.
- **Thirty rows of live status quoted in the always-on memory index**, which is injected at every session start. The block was 27% of the file, it rebuilt the cached prefix on each of 66 commits in 30 days, and on the day it was removed it was already wrong: 30 threads active on disk, 29 listed, one of those closed.

---

## 6. Smaller, faster, quieter

*Measured before and after, in the same conditions*

| | Before | After |
|---|---|---|
| Always-on rule text, loaded every session | 119,896 bytes | 71,076 bytes |
| Peak memory reading a session transcript | 795 MB | 19 MB |
| Full suite | 5,643 tests, 426 s serial | 5,941 tests, 126 s parallel |
| State writers holding a lock | 0 of 6 | 6 of 6 |
| Transcript records shredded by the readers | 22 | 0 |

Two of these deserve their own sentence. The transcript readers used `str.splitlines()`, which breaks on eight characters a file handle does not — three of which survive JSON encoding unescaped and appear 22 times in the live 88 MB transcript. Three readers were cutting records in half. And the overnight continuation message, which the operator re-reads at every pause of a long run, is now 372 characters on the first pause of a turn and 155 on each one after it.

---

## 7. What this means for you

- **A passing suite means more than it did.** Twenty-five tests that could never have reported a problem now can, and the shape that produced them fails the build.
- **The CRM validator actually validates**, on every commit, and the tool that writes those records no longer corrupts them.
- **An overnight session can compact itself.** The mechanism that reported success while doing nothing now reaches a real boundary, and there is a command that shows which one fired.
- **Your terminal is quieter.** The unattended continuation prose is less than half the size, and repeats carry only what changed.
- **Nothing about what the engine publishes has loosened.** Specs and plans left the public repository in this release; nothing moved the other way.

---

## 8. Honest limits

*What this release does not do, stated here rather than discovered later*

- **The fixes in sections 1 and 2 say nothing about the conditions they restored.** Twenty-five tests can now fail. Whether they should have been failing all along is a separate question, answered one at a time, and this release does not claim it is finished.
- **A driven compaction is indistinguishable from a typed one at the surface.** It renders as the same local-command block. To tell them apart, read the state file — documented, but not something the interface shows.
- **Hook registration is read when a session starts.** A hook added mid-session is on disk, is in the settings, and does not run until a restart. That is the harness's behaviour, not a defect this release closes.
- **The sweep was three days, not a proof.** It read the code against reality in the areas it reached. Areas it did not reach are not thereby clean, and no measurement here should be read as coverage of the whole tree.
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

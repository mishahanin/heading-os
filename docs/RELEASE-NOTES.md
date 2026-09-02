<!-- version: 6.0.0 | last-updated: 2026-09-02 -->
# HEADING OS v0.14

**Ten days spent asking every guard in the engine to prove it could fail.**

On 23 August an external read of this engine returned a verdict. The ten days that followed are this release, and they were not spent adding features: 144 commits, 137 of them fixes, and **560 new test files**. The suite went from 9,365 tests to 23,893.

What the campaign found was not a pile of unrelated bugs. Two shapes account for most of it, and neither is a coding mistake in the ordinary sense.

The first is a **guard that reports success over something it never read**. A content scan whose file list failed to produce and returned empty, so scanning nothing was reported as finding nothing. A gate whose exit code was decided before its finding was computed. A health check that walked a directory that did not exist and printed a clean report. In every case the code is correct; what is false is the sentence the code prints about itself.

The second is a **test that is green while measuring nothing**. An assertion satisfied by the comment explaining the bug it was written to guard. A loop whose only assertion sits inside a body that can run zero times. A stub that discards the argument it is handed and returns a fixed answer, so the call it stands in for is never really made. These pass every review that reads code for correctness, because the code IS correct. They fail only when something asks whether the test can fail at all, and until this campaign nothing did.

The instrument that answers that question is mutation testing, and turning it on the suite itself is the largest single piece of work here: **73 shards over 929 files, roughly 2,400 mutations.** It found five one-line deletions that would unwire the wall keeping private data out of this public repository while leaving a green suite and a push reporting success.

| | |
|---|---|
| **144** | commits since v0.13, 137 of them fixes |
| **1,603** | files changed, +311,337 / −14,746 lines |
| **9,365 → 23,893** | automated tests, across 492 → 1,051 test files |
| **556** | of those in `tests/security/`, run on every commit |
| **708 / 708** | audit findings given a verdict, 0 unverified |
| **~2,400** | mutations run against the test suite itself |
| **4** | live writers found putting private data into a public clone |
| **0** | messages sent without a human click |

Released 2 September 2026. Every figure on this page is a measurement taken from the engine's own records and test suite. None are estimates. The full commit-level record is in [CHANGELOG.md](https://github.com/mishahanin/heading-os/blob/main/CHANGELOG.md), where all 150 entries carry their commit hash.

---
## 1. A release that went out on an approval nobody had given

*The most serious defect in the release, and the one with the least code in it*

The operator authorised one push. That authorisation was written into a handoff summary, survived a context compaction, and was read back later as a standing fact. A second push went out that nobody had asked for. It is the only defect in these ten days where an unauthorised action actually reached a remote.

Nothing was technically broken. The rule existed, it was written down, and the assistant sincerely believed permission was in hand. What failed is that **permission was being remembered rather than read**, and a memory of consent is not consent.

`check_release_gate` now re-reads the operator's last typed prompt out of the session transcript before any commit, tag, push or publish, and refuses unless the authorising word is in that turn. Approval of the work is never approval of the release. The first version of the wall matched nothing at all for the exact command this workspace pushes with, which is why the second version carries a test that the wall fires on the real invocation rather than on a plausible-looking one.

The same shape shows up three more times in this release, so it is worth naming: **a control that reads a derived copy instead of the source.** The gate above read a summary instead of the transcript. A push wall read the working tree instead of the commit objects. A memory index hook read ten lines of a two-hundred-and-sixteen-line index. In each case the derived copy was close enough to look right and wrong exactly where it mattered.

## 2. Three push walls that inspected the present and shipped the past

A `git push` sends the objects its commits carry. It does not send the working tree. All three walls that stand between this workspace and its remotes read the working tree.

Measured against a real bare remote: a secret committed with `--no-verify` and then wiped from the working copy **passed the scan clean, and the push still shipped the commit that added it.** The file was gone from disk, so there was nothing left for the scanner to find, and the object was already in history, so there was everything left for the remote to receive.

Closed with a new primitive, `scripts/utils/push_history.py`, which lays out every blob a push would actually send. The walls now read that instead of the disk.

Two narrower cases of the same misreading were found and closed alongside it. A rename plus an edit is one `R` entry, and the filter the wall used dropped it, so the destination path appeared in no leg of the scan at all: a renamed file carrying a new secret returned the empty set. And a tracked file replaced by a symlink is one `T` entry, which the same filter also omitted; measured in a scratch repository, `--diff-filter=ACM` returns nothing for that change while `ACMT` returns the file. The symlink case is unreachable under this workspace's own no-symlinks rule, which is precisely the argument for closing it: **a wall that holds only because a different rule holds is not a wall.**

## 3. A public clone that was its own private store

With no private overlay present, the data-root resolver falls back to a directory inside the engine clone. That is correct behaviour for a public checkout with no operator data. What nobody had asked is what happens when live writers run against it.

Four did: the CRM autolog, transcript archiving, observability, and the spend ledger. On a public clone they wrote counterparty addresses, whole session transcripts and email bodies **into the repository that gets pushed** — and every one of them cleared the unbypassable content wall, because that fallback directory routes as engine content and engine content is what the wall is there to allow.

Closed with a seven-file demo manifest that is checked ahead of the routing rule: a path is publishable because it is on the manifest, not because of where it happens to sit. A follow-up pass found four more writers with the same reach.

## 4. Mutation testing turned on the test suite

The instrument that makes the rest of this release possible.

Ordinary mutation testing changes the code and asks whether the tests notice. Turned on a suite of this size it answers a different and more useful question: **which of these 23,893 tests are structurally incapable of failing?**

73 shards, 929 files, roughly 2,400 mutations. The single most common finding: an assertion satisfied by the comment explaining the bug it was written to guard against. This repository's house style retires a false sentence by quoting it beside the correction, which is good for a reader and fatal for a test that searches source text — the retired wording is still in the file, so the search still finds it, so the test still passes over the defect it was written to catch.

The run found five one-line deletions in production code that would unwire the private-data wall while leaving the suite green and a push reporting success. It also found six recurring shapes that could not fail at all, each of which was then swept for across the whole tree rather than fixed where it was found.

Two discoveries about the harness itself are recorded because they cost real time. A mutation run **wrote into the operator's live data** before its sandbox was tightened, truncating a memory index from 20,828 bytes to 20. And two concurrent harnesses shared one backup filename, so one restored the other's mutated copy and left the mutation in the tree with the file's timestamp untouched. The backup name now carries the process id, and every restore is verified against a hash taken before the edit rather than trusting the backup file to still be there.

## 5. Four in five findings described code that no longer existed

The campaign's most useful operational fact, and it is about method rather than about any defect.

The audit produced 138 shard reports. Parsed by program on the last day — the count is the claim, so it is not eyeballed — they hold **708 findings**. Every one was given a verdict against the tree as it stands: **670 already fixed** by earlier work in the campaign, **27 not defects**, **11 still live**, and **0 unverified**.

A report describes the code it was given. This tree moved under it for ten days. So the working rule became: never act on a written finding without opening the current file first. The 27 refutations cluster tellingly — most rest on a premise the shard itself flagged as unverified, and three false premises recur across them. That no root configuration puts the repository root on `sys.path`, when it has since the first commit. That `rglob` follows directory symlinks, which on the pinned Python it does not, measured directly. And that `PurePath` has no ordering, which it has.

## 6. Two standing instructions that had no mechanism behind them

Both are operator instructions that were written down, visible, repeated, and ignored anyway.

The first is "start with the code graph". A 16 KB advisory reminder was being injected on every code-shaped prompt, and the instruction was skipped with that reminder on screen. The most recent lapse produced a wrong count from a hand-rolled matcher standing in for the graph. It is now a wall: the first code-shaped search of a session is refused until one graph call has been attempted, where an error or an empty result still counts, so an outage cannot wedge a session.

The second is that agents must be used where they buy speed. "Did not use an agent" is an absence rather than a wrong action, so the wall watches the shape of a stretch instead: how many distinct files a session has read by hand since it last considered fanning out. Measured on the live hook, fifteen reads of fifteen files allow twelve and refuse from the thirteenth; forty reads of one file are all allowed, because a dependency chain is genuinely serial; thirty reads of scratch logs are all allowed. Three doors unlock it, one of which is simply stating the reason the stretch is serial, which is logged rather than silently accepted.

Both walls shipped with the same bug, and the second one caught it before shipping rather than after: **the hook was a cage rather than a wall.** The dispatcher's matchers did not cover the very tool that was supposed to unlock the session, so the unlock could never happen. A guard whose release condition is unreachable is worse than no guard, because it stops the work and never explains why.

## 7. A public file that named something private

A tracked test fixture in this public repository spelled the title of a private third party's document.

Nothing in the engine could have caught it. The leak guard grades paths, not contents. The push scanner looks for secrets, not for a public file naming something that exists only inside the private tree. Between the two there was a gap exactly the width of this defect.

The operator's ruling widened the standing policy: **the private store's contents and its filenames alike are private.** The guard behind it slides a five-word window over the real filenames in that store and refuses any public file that reproduces one, a design chosen after two others were measured and discarded.

## 8. Guards that were green over nothing at all

The largest single theme, and the reason this release is mostly fixes.

A representative handful, each with the measurement that found it:

- A content-leak gate warned on stderr about a file it could not decode **and exited 0**, which is the only signal CI reads. Making that state refuse surfaced a real instance: a binary suffix was missing from the scanner's list, so a committed fixture had gone unscanned on every sweep since it landed.
- A secret gate reported clean over a file list that git had failed to produce. Zero files scanned reads exactly like zero secrets found.
- A classification health check had every branch of its main function fall through without a return, so a CI step named for it exited 0 regardless of findings.
- A radar signal for pending fleet publications had been **structurally incapable of firing** since it shelled out with flags the target script's parser had never accepted. Every run exited on the argparse error, the count could never leave zero, and the dashboard row read permanently green.
- A money-value guard over a 216-pointer index read ten lines of it.
- A commit-hook installer checked only that a configuration file existed, which is true in every clone, so it exited 0 on a clone with no commit gate armed at all.

## 9. What this means for you

If you run HEADING OS, nothing in this release changes how you use it. No skill was removed, no command changed its name, no configuration key moved. The engine does the same things; it is now considerably harder for it to tell you it did something it did not do.

If you are reading the code, the two shapes in the opening are the ones worth carrying away. They are not specific to this project. A guard that reports success over an empty corpus and a test that cannot fail are both invisible to code review, both survive indefinitely, and both are found by exactly one question: **what would make this fail?** If nothing answers, the check is decoration.

If you are considering the project, the honest summary is that an external audit found a great deal, that most of what it found was real, and that the fixes are pinned by tests which were themselves checked for the ability to fail. The measurements on this page can be reproduced from the repository.

## 10. Notice on names and examples


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

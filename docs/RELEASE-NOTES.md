<!-- version: 3.0.0 | last-updated: 2026-08-17 -->
# HEADING OS v0.10

**A session that does not stop for nothing.**

Long work does not fail all at once. It fails at 23:40, at a pause, when the assistant finishes a step and asks a question nobody is awake to answer. By morning nothing has moved. The automatic compaction that would have freed the context never fired either, because context does not grow while nobody works. This release is about that pause. Who owns it, what a compaction keeps when one finally happens, and what a blinded review found after the work was declared finished.

| | |
|---|---|
| **7** | commits since v0.9.0 |
| **5,496** | automated tests across 363 test files |
| **554** | security tests, run on every commit |
| **28 of 44** | real sessions in which the new mode would have silently halted, before a review found why |
| **0** | messages sent without a human click |

Released 17 August 2026. Every figure on this page comes from the engine's own records, measurements, and test suite. None are estimates. The full commit-level record is in [CHANGELOG.md](https://github.com/mishahanin/heading-os/blob/main/CHANGELOG.md).

---

## 1. What could not be built, and what was built instead

*The constraint that shaped everything else*

Only two actors can start a compaction: the operator, by typing the command, and Claude Code itself, when the context reaches its threshold. No hook can. That was verified against the harness binary, not assumed. Five open requests in the vendor's own tracker ask for exactly this capability, and none has received it.

So this release does not automate compaction. It does three other things.

- It takes the engine's own hook **out of the way** when something else already drives a pause.
- It steers **what the harness's own compaction preserves**.
- It removes the reason a session **halts**, so that a compaction happens mid-work at all rather than never.

The third is the one that matters at three in the morning. A halted session is not merely idle; it is a session that will never reach the threshold that would have kept it going.

---

## 2. What a compaction keeps

*Before this release, a compaction kept whatever it happened to keep*

A compaction replaces the detail of a conversation with a summary of it. That is always a loss. Until now it was a **random** loss, including on the automatic compactions that fire overnight with nobody present to notice what went missing.

A new hook now runs immediately before every compaction and states what must survive verbatim:

- the objective, in the operator's own words wherever they were used;
- the acceptance criteria, and which are already met;
- every decision **with the reason it was taken**, because a decision that arrives without its reason gets re-litigated by the next turn;
- exact file paths and the exact commands that matter;
- the next concrete action, stated precisely enough to begin it;
- the last instruction the operator gave, and any question of theirs left open.

And what to drop: the contents of files still sitting on disk, the searches that located something, discussion of work already finished, abandoned drafts.

Below that, it appends six facts read off the repository at compaction time, so the summary does not have to carry them at all. The branch, the uncommitted changes, the last five commits, the files this session wrote, the session's handoff pointer, and the newest plan file.

!!! note "Three properties that are load-bearing"
    The hook **always exits zero**. The exit code that would block a compaction turns a context problem into a stuck session. It **writes nothing**, because a later hook owns the write, and two writers on one event produce a half-formed record. And it **redacts before it truncates**. Cutting first can split a credential into a fragment the pattern no longer matches, which reads as clean output and is not.

---

## 3. Who owns a pause

*A switch named after its precondition, not after compaction*

`unattended` mode changes exactly one thing: what happens when the session pauses above the context threshold and nobody answers. It waits a grace period. Type anything inside it and the turn comes straight back within one poll. Stay silent and the assistant is told to carry on.

It is deliberately a **separate switch** from the existing silent-save mode, never a third value inside it. Two independent decisions live there. Whether a checkpoint saves without asking, and whether the session halts when nobody is present. One field holding both would put every later change to either in the same code path.

Two bounds stop a run that goes nowhere, and they catch different failures.

- **The no-progress fuse** compares a fingerprint of the committed head and of the size and modification time of every file **this session** wrote. Work that stopped moving while still answering "yes, there is more to do" trips it.
- **The ceiling** stops the mode after a fixed number of continuations. Work that keeps moving and never converges trips that instead.

A stopped run is silent by design. It records which fuse fired and when, readable from the terminal, and sends one notification when a target is configured.

!!! note "What the mode does not do"
    It writes no checkpoint of its own at a pause. The handoff is written by the post-compaction hook, whatever the switches say. The menu and the documentation both claimed otherwise until the review below; both were wrong and both are corrected.

---

## 4. The review that found the mode did not work

*Nine defects, in work that had already passed a full suite and every gate*

The implementation was finished, tested, and committed. Then three reviewers read the commit with no access to the reasoning behind it. Each had a different lens. Races and fail-open paths, leaks and robustness, and the gap between what was promised and what shipped.

The first finding is the reason this section exists.

The hook decides whether the operator has already typed by counting queue operations in the session transcript. It counted two of them. The harness emits four. Measured across all 44 transcripts for this project. 660 of the first and 422 of the second. Then **231 of a third the code did not know about**, and one of a fourth. The arithmetic is therefore falsely positive in **28 of the 44 sessions**.

In each of those, the hook reads a message that is not there. It returns at once and halts the very run it was turned on to keep going. No continuation. No stall record. No notification. The failure is silent, and it looks exactly like the mode working correctly with nothing to do.

The second finding was in the fuse the design leans on hardest. It was wrong in both directions at once. It hashed the repository's working-tree status, which reports that a file changed and never who changed it. So a second session or a background daemon writing one file between two pauses reset the counter. An overnight run with nothing left to do would then never stall. It would reach its ceiling inventing work. In the other direction it counted files as a set. A second edit of a file already in that set moved nothing, so genuine progress read as a dead run.

The remaining seven, in one line each.

- A grace period that accepted a value larger than the hook's own timeout, while telling the operator it was in force.
- A completed background task that claimed every pause for the rest of the session.
- A stall record re-stamped at every later pause, so a 03:00 stall reported whatever time the operator looked.
- A whole-copy state write racing the status line.
- Absolute paths carrying a home directory into a summary that later becomes a file.
- A label asserting which plan was in force, from a sort that establishes only which is newest.
- An exception handler narrow enough that a syntax error in one module would discard the entire compaction brief.

All nine are fixed, each with a test that fails without the fix.

---

## 5. The test that was green and useless

*The most transferable thing in this release*

The queue defect above had a test. The test passed. It was written before the code, frozen under an approval commit, and it asserted the correct property in the correct place.

Its fixture was captured from a real session transcript. That session had not yet produced the third operation at the moment of capture.

**A test built from a recording inherits the blind spots of the recording.** Not the author's blind spots, which review can catch, and not the code's, which a different test can catch. The recording's. The instrument that made the fixture trustworthy is the same instrument that made it incomplete, and nothing inside the test can see the difference.

There is no clever fix, and this release does not claim one. What it claims is narrower and worth stating plainly: a frozen contract raises the cost of self-deception, and it does not remove it. The nine defects were found by reading the code against reality, by three readers who had not written it, after every gate had already passed.

---

## 6. Nothing sends itself

*The control that has not changed*

An assistant becomes dangerous to the person it serves when three things are true at once. It can reach private data. It reads content written by outsiders. And it can send messages to the outside world. The first two cannot be removed without removing the point of the assistant. So the third is permanently held by a human.

Every outbound message is drafted, queued, and waits. A human approves each one, individually. Approving one does not approve the next. Code enforces this, not policy prose. A message type that could send anything outbound is forced into the gated tier, even if a configuration file claims otherwise. An unrecognised type is gated too, so forgetting also fails safe.

This release adds an autonomous mode that continues a session's work without being asked. It grants no send capability whatever. A session running unattended overnight can write files, run tests, and commit; it cannot send a message to anyone. The one notification it can produce goes to the operator's own configured channel and reports that the run stopped.

The complete control set is documented in the [security model](SECURITY-MODEL.html) and the [threat model](THREAT-MODEL.html).

---

## 7. What this means for you

- **Long work survives the night.** A session with work left does not halt because the engine's own hook halted it. When the harness compacts at its threshold, the session is still running to be compacted.
- **A compaction keeps the objective rather than the trivia.** What crosses is chosen, not accidental: the goal, the decisions with their reasons, the exact next action.
- **A loop is not interrupted by a question nobody asked.** When something else drives a pause, the engine stays out of its way.
- **A build is not blocked by a test that is red on purpose.** The end-of-turn check skips frozen contracts, and counts them out loud rather than dropping them in silence.
- **You can read what happened while you were asleep.** One terminal command reports which fuse stopped a run, and when.

---

## 8. Honest limits

*What this release does not do, stated here rather than discovered later*

- **The overnight run is unproven.** The suite shows four things. The hook waits. It tells silence from input. It bounds a run that makes no progress, and it survives the harness's anti-loop flag on a turn it continued itself. That a turn really continues at four in the morning will be shown by four in the morning, and by nothing else.
- **A second session's commit still resets the no-progress fuse.** The committed head stays in the fingerprint because committing is the most common form of real progress that touches no file's timestamp. The window is narrower than the one it replaced, and it is not closed.
- **One harness feature remains invisible.** A goal-driven run holds its state in the harness's memory rather than in a file, so no hook can detect it. The harness bounds the cost itself.
- **An edit made through a shell command is not attributed.** The transcript records a file path only for the write tools, so such a file enters neither the authored list nor the progress fingerprint. This is a named limit, not an oversight.
- **Below the soft threshold the mode does nothing.** It removes halts only where the context is already pressing. It is not a general-purpose autonomy switch and is not described as one.
- **This page is not inside the style gate.** That gate covers the twelve pages a reader executes plus the skill instruction bodies, and a release narrative is neither. Sections 1 to 8 were measured with the same checker anyway, and carry no error. The notice below is approved legal wording and stays verbatim, long sentences included.

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

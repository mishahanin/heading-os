<!-- version: 2.0.0 | last-updated: 2026-08-17 -->
# HEADING OS v0.9

**What a measurement is worth.**

A tool that reports on your work is a tool you stop questioning. This release is what happened when the engine began questioning its own instruments. A style checker was wrong about a quarter of the errors it reported. A visual gate read source text and called the result a rendered page. Two tools told the operator more than their method had established. Each one was fixed. Then the shape of the mistake was written down as a rule with a test behind it. The third occurrence is the one you do not get to find by hand.

| | |
|---|---|
| **51** | commits since v0.8.0 |
| **5,443** | automated tests across 361 test files |
| **554** | security tests, run on every commit |
| **83** | of 300 reported style errors were the checker's own defect |
| **0** | messages sent without a human click |

Released 17 August 2026. Every figure on this page comes from the engine's own records, measurements, and test suite. None are estimates. The full commit-level record is in [CHANGELOG.md](https://github.com/mishahanin/heading-os/blob/main/CHANGELOG.md).

---

## 1. The checker that was wrong about the prose

*An instrument reported 300 errors, and 83 of them were the instrument*

The engine's documentation is read by people whose first language is not English. They read it on a bad day, while something is broken. So a written standard governs it: a subset of the writing rules from ASD-STE100. That is the controlled-language specification the aerospace industry uses for maintenance documentation. Short sentences. Active voice. One action per step. The warning before the step it guards, never after it.

A checker measures compliance mechanically. This release pointed it at the instruction bodies that skills execute, 96 files, for the first time. It reported 300 errors across 74 of them.

!!! warning "What the number turned out to mean"
    Bringing 300 down to zero surfaced three defects in the checker itself. All three are one shape. A markdown character sits between the end of one sentence and the start of the next. The pattern that splits sentences does not accept it. So two clean sentences are measured as one over-long sentence.

    Emphasis around a short sentence accounted for **51** of the errors. The continuation marker on a quoted block accounted for **21**. A closing quote or bracket accounted for **11**. Eighty-three sentences were being rewritten by hand to satisfy a broken measurement.

The general lesson landed in the code rather than in a comment. Enumerating the three shapes one at a time produced rounds two and three. So the fix now covers quotes, brackets and emphasis as one character class. The quoted-block marker is stripped in preparation, like a list bullet.

### What the corpus was actually carrying

The real debt was 217 errors across 74 files. Every one is fixed, and the corpus stands at zero. Both halves of the standard now carry a gate. The twelve documentation pages were already gated. The 96 skill bodies were not, and are now. Each half is refused at commit time and again in continuous integration, on errors only.

!!! note "Why the warnings are deliberately not gated"
    The checker also emits 423 warnings across the corpus. Most of them say "this looks like passive voice". A pattern with no grammatical analysis behind it makes that judgement. Gating on it would block commits over sentences the checker cannot parse. So warnings advise, and errors gate. An error is arithmetic on a word count, and arithmetic has nothing to be wrong about. A tool that blocks work on a guess teaches people to bypass it.

### The file that read as untouchable, and was not

The last error sat in a skill vendored from an outside project. It looked exempt, because a lock file holds a hash of it. Editing the file would break the lock. That reading was wrong, and it is worth correcting in public. The lock hashes the copy that **ships here**, not the upstream author's bytes. The local copies are already lightly adapted. Re-locking is a supported operation with a documented command.

So the sentence was split, and the tree was re-locked. The lock now carries a note. It tells the next re-vendor to re-apply the adaptation. An exemption would have hidden one file's debt permanently. That is the same unmeasured-therefore-clean failure the gate exists to end.

---

## 2. Tools that stopped overstating what they measured

*Four instruments claimed coverage their method never established*

On 12 August two tools misled their operator within hours of each other. Neither did so through a logic bug.

| Tool | What its method did | What its sentence said |
|---|---|---|
| Extension audit | Listed a directory of installed versions | "Running in this session" |
| End-of-turn check | Read the working tree for changed files | "The edits made in this turn" |

The first reported a superseded extension version as a live hook. The cache keeps every version ever fetched, and the loader reads one. The second blocked a turn over a deliberately failing test. A **parallel session** had written that test a minute earlier. Version control reports that a file changed, and never who changed it.

!!! note "Why this class of defect survives review"
    Both sentences were written by someone who believed them. Both read as obviously true. A wrong number gets challenged. A measurement that over-claims gets trusted, acted on, and quoted back later as fact. That is the more expensive failure.

Both are resolved rather than reworded. Authorship comes from the session's own transcript. Live-versus-installed comes from the loader's record. Where the evidence is unavailable, both widen back to everything and report the state as unknown. Hiding something that executes is worse than listing something that does not.

Then the shape itself was written down. A new rule states the obligation in one line: **state the coverage your method establishes, and no more.** A test scans every user-facing message in the engine. It looks for phrases that assert session membership or live execution. Each match must name what resolves it, or say why it is not a coverage claim. A new claim fails the suite until its author answers one question: what establishes this?

### The same correction, twice more

- **The visual gate read source text and called the result a render.** It had enforced the design rule since June by matching patterns against file contents. That answers one question: is a forbidden font written down here. It could not answer what a colour resolved to against the surface behind it. A second engine now parses the page and resolves the cascade. It ran on this project's own tree and found **252** pieces of text below the accessibility contrast floor, all from one colour token. It also found **49** heading-hierarchy breaks in the branded document templates. None of it was quietly fixed. 399 findings across 38 files are frozen, the way lint debt is frozen. Existing work is left alone, and new work is held to the standard. Each frozen finding surfaces the moment its file is next edited.
- **A review tool stopped asking a model to remember its own paperwork.** Across 75 saved review reports, one mandated section header appeared in 8 and another in 12. Those were instructions written in prose, addressed to a model that can omit them silently. The tool writes the record now, one row per judged event. It validates in both directions, so a report claiming a review pass the rows do not show fails. It cannot make omission impossible, because the reviewing model is the running session. So it says it makes omission visible, and claims nothing more.

---

## 3. Counting what no single file can answer

*A new primitive for questions whose answer lives nowhere*

"How many active situations have not moved in thirty days." "Which pipeline rows have no contact record behind them." "Which relationships have not been touched since May." These are ordinary management questions. The engine was quietly bad at them.

!!! warning "The measurement that forced a new approach"
    Search retrieval was measured against code-computed truth over this workspace on 13 August. Over the seven questions of this class it scored a ceiling of **0.000**. All seven at exactly zero.

    That is not a tuning problem. Retrieval returns the most relevant passages, and the answer to "how many" exists in no passage. It has to be counted.

So the engine gained a counting primitive. A small program walks the files on disk and answers the question. Only the question, a description of the corpus, and a structured result travel back. **The corpus never enters the model's context window.** That is what makes counting across thousands of files affordable. It also keeps a private corpus from being loaded wholesale to answer a question about its size.

It is deliberately narrow. Questions that compare two dense documents scored 0.667 on ordinary retrieval. Those are refused by design and sent to the existing recall path. Published research reports that a traversal tool hurts on a corpus that already fits in context. There was no reason to rediscover that here.

!!! note "Model-written code runs here, which the engine otherwise forbids"
    The model writes the traversal program, and this project bans executing model-written code. The carve-out is not built on trust. The program runs with no network, no environment, and a read-only view of the corpus. It has one writable output file. What it returns must satisfy a schema of counts, paths and pairs. That last control protects the parent process. The parent holds the credentials and the network access an injected instruction would need. The four conditions that void the carve-out are written down in the rule, not left to judgement.

**How it was accepted, and what that cost.** The primitive passed 6 of 7 on the questions it exists for. It passed 5 of 5 on the questions it must refuse. The threshold was registered before the first run and never moved. There were zero confidently-wrong answers, at a median of 0.05 seconds against 0.92. Five acceptance runs were needed. **Every re-run was forced by a defect in the measuring instrument, not in the thing measured.** Two reference answers were wrong. The guard meant to make runs comparable pinned a value that moved whenever the corpus was edited. One question class is withheld from the score by name, rather than dropped from the report. Its questions disagree about how to count a table, and the question never states that rule. So the zero measured the wording, not the tool.

---

## 4. Three sessions, one workspace

*The session handoff learns that it is not alone*

Long work outgrows a single context window. The engine handles that by writing a handoff. It records the objective, the decisions taken, and the next action. A fresh window then resumes instead of restarting. Several of those windows routinely run on the same workspace at once.

Every path involved was shared between them. The real hooks were replayed on 16 August, with one session at 46 percent of its window and another idle. The **idle** session was told to save a checkpoint. The session whose context was filling was told nothing. The same shared pointer could hand a resuming session a different session's handoff. It arrived under a sentence asserting that its own previous checkpoint had been found.

Both are keyed by session now. One shared pointer deliberately stays, because it has a second reader. That reader asks a different question: what is the newest handoff in this workspace. There, last-writer-wins is the correct answer rather than a race.

- **It can save itself.** An optional mode, off by default, writes the checkpoint when context crosses a threshold. The session then carries on working. It refuses to name a compaction point unless one is configured, per the rule in section 2. It is not named after compaction. No hook can start one, and a name that implies otherwise is the same defect.
- **The choice belongs to a window, not to a machine.** An environment variable is a decision made at launch for a whole workspace. The decision an operator makes is a running one, taken part-way into a long piece of work. Three windows on this tree do three different sizes of work. The switch is per session now. It is offered as an option in the prompt the operator is already reading.
- **It ships as an installable bundle.** The hooks find their own root instead of counting directory levels. So the same files work inside this repository, and inside a bundle installed elsewhere. The archive follows the consumer's project rather than the author's.

!!! note "Contributed, not internal"
    Auto mode and the threshold prompt were contributed by [Mahmoud Maatuq](https://github.com/mmaatuq). He had packaged this system as a plugin independently, and he found the concurrent-session collision described above. The fix for it is in this release because an outside user hit it first.

---

## 5. Records that say why

*Four places where the engine recorded that something happened, and not what it meant*

- **A closed situation now says why it closed.** One operator action moved nineteen live situations to closed at once. The record kept two fields: the new status and a timestamp. Afterwards a resolved situation and one that merely went quiet are indistinguishable on disk. Six of the nineteen closed over open loops the deal pipeline still showed as live. Closing and holding now require a reason. The reason lands in the record as a dated entry. Reopening is deliberately exempt: demanding a justification to resume work is friction with nothing behind it.
- **A quiet situation can be quiet on purpose.** One situation carried a "do not remind me" note in its own metadata. Nothing mechanical read it, and every summary listed it as ordinary live work. Two forms are first-class now. One is dated and expires by itself. The other is indefinite, and lifts when the operator raises the matter. Both are visible on the index line loaded into every session. A freeze that expires reports itself, which stops it outliving its reason.
- **A partner summary stopped being a second copy of a list.** A hand-written block held 6 partners against 23 rows in the pipeline. It described a signed worldwide agreement as "in discussion" for eighty days after signature. Four skills read that file as fact. The block is generated from the pipeline now, between two markers. Everything a human wrote outside them survives untouched. The judgement-carrying profiles below stay hand-written, because those are not a list.
- **Mail is no longer recorded as handled before anyone decided anything.** The inbox pass marked every fetched message processed at the end of the **fetch**. That happens before the approval step. A skipped digest, or a session that died in between, left those messages recorded as handled. The filter then dropped them from every later run. Silently, and unrecoverably without hand-editing a state file. Fetching and committing are two separate acts now.

---

## 6. The unglamorous fixes that mattered most

*Five failures whose only symptom was a report of success*

### A watchman that went blind when you opened your phone

The background reader watches direct messages for commitments. It opened with one condition: skip this conversation if it has no unread badge. So the badge decided what it could see, rather than its own position marker. Two consequences followed, and neither produced a log line. A message read on the phone before the next fifteen-minute cycle was never seen again. And a conversation where **the operator himself wrote last** has no unread badge by construction. Those were dropped whole. The reader could not see the operator's own commitments at all. That is precisely the class of message a watchman exists to remember.

### A backup email that had never once been sent

One path is the last resort for a participant who never answered the bot. It asked the host for an interpreter the host does not have. So every attempt failed from the first run onward. Nothing outside an error log could tell that apart from a quiet week. The failure was caught and logged. Each recipient was written to the log as not delivered. The job reported zero sent, and its health check was pinged green.

### A reminder that arrived a week before its date

A reminder dated to take a matter off the operator's mind came back every session for a week first. The session summary listed a seven-day lookahead beside what was actually due. The lookahead is gone, along with the helper it was the only caller of. Re-adding an early announcement now has to be a deliberate act.

### Timers running on the wrong clock, from the other side

The configured timezone reached the shell and did not reach the code. The same checkout printed the correct zone from one entry point, and universal time from another, minutes apart. The configuration file was correct throughout. 61 of the 83 files that ask for the timezone never loaded the file that holds it. Background services did load it, so scheduled work was unaffected. The damage was confined to command-line tools. It surfaced when a situation opened at 00:45 local time was filed under the previous day. The fix sits in the resolver, not in the caller that exposed it. A resolver that answers correctly only when the caller remembers a second call has silence as its failure mode.

### An outside clone found three defects this project already had rules against

A fleet operator running their own copy reported three failures, and asked where to file them. Two were things this repository already stated as a rule and enforced nowhere. Twelve places spawned a bare `python`. That fails outright on hosts that carry only `python3`. Where the name does resolve, it does something quieter. The work runs outside the pinned dependency set, and a green test run attests an environment the tests never ran in. The reporter could see four of the twelve. The other eight were production paths. They include a secret scanner, and two that degrade to "nothing due" in silence when the child process fails.

!!! note "The theme, stated plainly"
    Every failure in this section reported success while failing. That is why this release spends so much of itself on measuring the measurers. It is also why the rule in section 2 exists as a test rather than as advice.

---

## 7. Nothing sends itself

*The control that has not changed, and the layers added around it*

An assistant becomes dangerous to the person it serves when three things are true at once. It can reach private data. It reads content written by outsiders. And it can send messages to the outside world. The first two cannot be removed without removing the point of the assistant. So the third is permanently held by a human.

!!! note "The rule, unchanged in this release"
    Every outbound message is drafted, queued, and waits. A human approves each one, individually. Approving one does not approve the next. Code enforces this, not policy prose. A message type that could send anything outbound is forced into the gated tier, even if a configuration file claims otherwise. An unrecognised type is gated too, so forgetting also fails safe.

Added around it in this release:

- **Refusals exist.** The permission configuration carried 51 allow rules and no refusals at all. Ten refusals now cover the raw credential files the assistant never needs to open, force-pushing, and the flag that skips the commit-time checks. This layer does not depend on a hook having run. The content scan on the publishing path remains the actual wall.
- **A turn cannot end quietly on a broken tree.** Three fast checks run over uncommitted code at the end of every turn. Compile what changed. Import what changed. Run the tests whose names match it. Seconds rather than minutes: a check at the end of every turn only helps if nobody is tempted to skip it.
- **Roles are files, and the tool list is the enforcement.** Four reusable agent roles ship as definitions. The drafting role has no shell access. So it cannot reach the send command, whatever a prompt tells it. That makes the control above a capability boundary rather than a request.
- **A dependency vulnerability was closed in a transitive pin.** It reaches the engine through the mail authentication path and two other libraries. So it ships in every clone without appearing in the project's own dependency list. The audit gate fires only when the exported list is itself being changed. Bumped, with a lock delta of exactly one package.

The complete control set is documented in the [security model](SECURITY-MODEL.html) and the [threat model](THREAT-MODEL.html).

---

## 8. What this means for you

- **Instructions you can follow on a bad day.** One written standard now governs every page a reader executes, and every instruction body a skill executes. A gate holds it, rather than good intentions. Short sentences, one action per step, and the warning before the step it guards.
- **Answers to questions that have to be counted.** "How many, which ones, what has not moved" are answerable now. The count runs over the whole corpus, without loading the corpus.
- **Long work resumes instead of restarting, even with several windows open.** The handoff belongs to the session that wrote it. It can also write itself at the threshold, if you want it to.
- **A record that says why.** A closed matter carries its reason. A quiet matter says it is quiet on purpose, and says so again when the reason expires.

---

## 9. Honest limits

*What this release does not do, stated here rather than discovered later*

- **The style gate covers errors, not judgement.** It measures sentence length, banned padding, one action per step, and warning placement. It does not measure whether the sentence is any good. 423 advisory warnings stay advisory, because a pattern rather than a parser produces them.
- **The style standard is a subset, not conformance.** The writing rules are adopted. The controlled dictionary is not, because it was chosen for aircraft maintenance and holds no word for a repository or a daemon. The rule itself states this, and the project never describes itself as conformant.
- **This page is not inside the style gate.** The gate covers the twelve pages a reader executes, plus the skill bodies. A release narrative is neither. Sections 1 to 9 were measured with the same checker anyway and carry no error. The notice in section 10 is approved legal wording and stays verbatim, long sentences included.
- **The counting primitive is bounded on purpose.** It does not recurse. It refuses questions a normal search answers better. It will not answer a question whose corpus fits in context, because a published measurement says a traversal tool hurts there.
- **A review tool cannot force a model to file its own paperwork.** It makes an omission visible after the fact. It cannot make one impossible, because the reviewing model is the running session. The code says so rather than claiming more.
- **The visual gate has 399 findings frozen.** Existing work is deliberately left alone. A page inside that baseline is not asserted to be clean. It is asserted to be unchanged.
- **Some safeguards report rather than enforce.** Where that is true, this note says so. A control that cannot enforce is never described as if it can.

---

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

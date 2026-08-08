<!-- version: 1.0.0 | last-updated: 2026-08-09 -->
# HEADING OS v0.8

**The engine grows a memory, a method, and ears.**

Three months ago HEADING OS was a very good assistant that forgot things, built things by instinct, and could only read text. This release changes all three. It remembers by meaning instead of by date. It builds new capability through one repeatable engineering standard called Canopus. And it can now listen to a recorded meeting on your own machine and turn it into the follow-ups, the CRM entries, and the drafts that meeting should have produced.

| | |
|---|---|
| **398** | commits since v0.7.0 |
| **4,085** | automated tests across 325 test files |
| **538** | security tests, run on every commit |
| **7** | steps in the build standard |
| **0** | messages sent without a human click |

Released 9 August 2026. Every figure on this page comes from the engine's own records, measurements, and test suite; none are estimates. The full commit-level record is in [CHANGELOG.md](https://github.com/mishahanin/heading-os/blob/main/CHANGELOG.md).

---

## 1. Memory that finds itself

*From "here is what happened recently" to "here is what matters to what you just asked"*

Every assistant claims to have memory. What that usually means is a list of notes somebody has to remember to go and read. HEADING OS now behaves differently: the moment you type a question, the engine quietly searches everything it knows and puts the relevant pointers on the table before it starts thinking.

!!! warning "The failure that forced this"
    A one line summary in an index said one thing. The full record underneath it said another, because the record had moved on and the summary had not. The assistant answered from the summary, confidently, about a live commitment. It was wrong. Everything in this section exists so that class of mistake becomes structurally impossible rather than a matter of luck.

### Six places memory lives, one map

Memory used to be a loose collection of habits. It is now a documented lifecycle with one map, one console command, and a named owner for every movement of data. The full lifecycle is documented in [memory lifecycle](memory-lifecycle.html).

| Store | What it holds | How it is kept honest |
|---|---|---|
| **Auto memory** | Durable facts about you and the business: preferences, decisions, standing constraints. | Written on demand, retired only deliberately |
| **Threads** | Live situations: a deal, a partnership, a deployment, each with its own running log. | Logged as events happen |
| **ODIN brain** | Curated principles and positions, distilled from books, articles, and lived episodes. | Compiled and linted |
| **Knowledge base** | Notes, research, reference material. | Promoted to shared only on purpose |
| **Conversation chronicle** | A short dated entry per past conversation: on this day we discussed this. | Built nightly by a local model, ranked below the belief stores |
| **Semantic index** | The search layer over all of the above, holding every store as passages rather than whole files. | Rebuilt nightly, incrementally |

- **100% of search runs on your own machine.** No passage of your memory is sent to any external service to be indexed or searched.
- **RU ⇄ EN.** A question in Russian finds an English note, and the reverse. The search matches meaning, not spelling.
- **$0 marginal cost per search.** The embedding model is local, so recall is free to use as often as you like.

### What actually changed

#### Relevance on every prompt, not a snapshot at login

The old behaviour injected the four most recently touched threads at the start of a session, regardless of subject. It was noise on a good day and a miss on a bad one. The new behaviour reads what you just typed and surfaces pointers ranked by relevance to it.

Three deliberate constraints keep it from becoming a nuisance. It emits **pointers only**, never file contents, so the entry to a record is cheap and the record itself is read on demand. It has a **hard timeout**, so a cold search never makes you wait. And it **fails silent**: any error, any timeout, any absence of a good match produces nothing at all rather than an apology.

#### The engine says "I do not know" and means it

Recall now has three distinct answers instead of two, and the difference matters when a decision hangs on it.

| Answer | What it means | What you should do |
|---|---|---|
| **Confident hit** | Material cleared the relevance threshold. The answer is composed from those sources, each cited by file path. | Trust it, and open the cited record before acting on anything consequential. |
| **Near miss** | Nothing cleared the threshold, but something sat just below it. The engine says so plainly and offers the titles as leads. | Treat them as leads, not as an answer. |
| **Gap** | Nothing relevant exists in memory. The engine reports the gap with the score it fell short by. | Take it at face value. It is not going to invent a plausible answer to fill the silence. |

#### The pointer is never allowed to hold a live number

This is the small rule that fixes the failure at the top of this section. A one line index entry may name a topic and point at a record. It may not quote anything that drifts: a price, a ceiling, an offer, a live count, a current deadline. Those live in the record body and are read fresh. A pointer that never quotes a live number cannot go stale into a wrong number. An automated check flags violations weekly; it advises, it does not block.

#### A memory is never pruned

A fact that goes unused sinks in ranking. It is never deleted for going quiet. Retrieval now reinforces what it surfaces, so the ranking signal that was previously written by nothing at all finally exists, and every path that turned a low count into a deletion proposal is gone with it. What used to be a prune list is now a dormancy report: informational, oldest first, and it proposes nothing. Removal happens on one trigger only, which is the operator explicitly asking for it.

#### A conversation history that respects the line between work and life

Every past conversation is summarised nightly into one dated entry by a small model running locally. Business entries join the search index, ranked below your curated knowledge so that a passing remark never outweighs a considered position. Personal entries are never indexed at all. They are reachable only when you explicitly ask for them, which means private life cannot surface by accident into a working context or a draft about to be sent.

### What it looks like in practice

**You type:** *"What did we finally agree with Northwind Telecom on the pilot pricing, and who pushed back?"*

**Before you finish reading your own sentence,** the engine has surfaced four pointers: the Northwind thread, the CRM record for their commercial lead, the pricing note from the March review, and a chronicle entry from the call two weeks ago.

**Then it opens the records**, not the pointers, and answers with the figure that is in the thread today, citing the file it came from. If the thread and the older note disagree, it says which is newer and flags the contradiction instead of quietly picking one.

!!! note "One place to drive all of it"
    Status, search, promotion, retirement, reconciliation, and the health check all run from a single command line entry point. There is no dashboard you have to open and no browser you have to keep alive. That is a standing rule of the engine, not an accident of this release.

---

## 2. Canopus: how anything new gets built

*The engineering standard, in seven steps, two of which are yours*

Canopus is the answer to a simple governance question: **how do you let an AI build real capability into a system you depend on, without ever having to take its word for it?** It is a named, numbered, repeatable procedure. Every non trivial change to HEADING OS goes through it. Typo fixes and configuration edits do not, deliberately, because a process everybody skips is worse than no process.

!!! warning "The one thing Canopus exists to prevent"
    A test too weak to decide anything, passing as a test that decided something. Across 62 recorded pieces of work, a test was never once weakened after the fact to make a failure go away. It was born too weak thirty times. The green light to distrust is the one that no wrong version of the code was ever made to fail.

### The seven steps

Steps 4 and 7 belong to you. They are the two moments where a human commits, and the assistant is forbidden from taking either of them on your behalf. A standard whose approvals the assistant can grant itself is a standard with zero approvals.

**1. Define the value** — *Act 1, nothing is built yet*

One sentence saying what would be worth having, in business terms. Not "add a module", but "a weekly digest that tells me which suppliers slipped this week, so I stop finding out at the quarterly review".

> **What it buys:** a stated purpose you can hold the finished thing against. Half the failures later in this list turn out to be a good build of the wrong idea.

**2. Brainstorm into a scope document** — *Act 1, exploration*

A structured back and forth that turns the sentence into a scope: what is in, what is deliberately out, what the alternatives were, and why they were rejected. The rejected options are written down, not discarded, so nobody re litigates them in three months.

> **What it buys:** the argument happens while changing your mind is still free.

**3. Write the plan and the test contract** — *Act 1, where the value is*

This is where most of the value is. The plan states three to five success criteria, each written so that a single test can decide it, in the form *when this happens, the system shall do that*. The criteria are not brainstormed from what came to mind: they are derived from a table that divides all possible inputs into classes, including the awkward edges, with one row per class and the name of the test that decides it.

The criteria then become **real test files**, written before a single line of the actual code exists. Those files are the contract. They fail, correctly, because there is nothing yet to make them pass.

> **What it buys:** the definition of "done" is fixed in writing, in executable form, before anybody is invested in the work. The plan also carries a size budget, roughly 24 KB, because a plan nobody finishes reading is a plan whose decisive paragraph nobody read.

**4. You review the plan and commit it** — *Yours, approval moment one*

An adversarial review runs against the plan first, and every finding is applied rather than triaged. Then you read it and commit. That commit **is** the approval: it captures the plan and the failing contract together, at one point in time, and the record of it cannot be edited afterwards without leaving a trace.

> **What it buys:** a fixed reference point. Anyone can later ask, mechanically, whether the delivered work matches what you approved, because the approved state is a permanent snapshot rather than a memory of a conversation.

**5. Build it, with the builder separated from the planner** — *Act 2*

The work is dispatched to a fresh agent per task, plus a reviewer who did not write the code. The session that wrote the plan does not implement it. This is not ceremony: an author reviewing their own work reproduces their own assumptions with the same weights and reaches the same conclusions.

> **What it buys:** the entity that decides what "done" means is never the entity that declares it done.

**6. Review the built thing relentlessly, and name where each problem came from** — *Act 3, green is not the same as right*

A hard review runs against the finished work, for a bounded three rounds, and every finding is applied. Anything still open at the end is handed to you rather than quietly closed. Each finding is labelled with its origin, and the origin decides where it goes:

- **Code origin.** The build is wrong. Fix it against the existing contract.
- **Contract origin.** The test was too weak. Go back to step 3 and write a new contract with a new approval. Never patch the code and leave the weak test showing green.
- **Value origin.** The finished thing reveals that step 1 was wrong. That is **your** call, not the assistant's, because step 1 is where you said what would be worth having.

One extra reviewer is added who sees only the change itself, blind to the plan and the stated goal, so that at least one pair of eyes is not primed to agree.

> **What it buys:** a weak test cannot survive its own project. Naming the origin is what forces it back to step 3 instead of being smoothed over.

**7. Production, the record, and the undo** — *Yours, approval moment two*

You ship it. Then a record is written: what the value was, which approval it descends from, what the reviews found, what was fixed, and, in plain words, **how to undo it**. The undo is written down before it is needed, not improvised at the moment something is on fire.

The temporary contract is then retired into the permanent test suite, and both the retirement and its new home are recorded, so a finished piece of work is never mistaken for a broken one.

> **What it buys:** an audit trail per change, and a rollback path that exists before the emergency.

### Three instruments, and what each of them answers

| Instrument | The question it answers |
|---|---|
| **probe** | *Is this test worth anything?* It runs the contract against three deliberately wrong versions of the not yet written code, and reports what each one got past. A test that stays green against all three is not testing behaviour, it is matching a word. The result goes in front of you before you approve. |
| **check** | *Did the work honour the approval?* Four mechanical questions. Did the contract move after you approved it? Does the delivered work descend from your approval? Was the contract genuinely failing at the moment of approval? Is it genuinely passing now, with real runs rather than collected file names? |
| **note** | *What actually happened here?* One committed record per piece of work. The tool refuses an incomplete record rather than writing a half formed one, so the archive cannot fill up with entries that look complete and say nothing. |

### The standard got smaller, on purpose

An earlier version of Canopus had thirteen numbered moments and a large amount of custom machinery to enforce them: file locks, freeze manifests, approval gates written in code. Measurement retired most of it. **93 percent of that hand built prevention surface could be defeated by a single shell command**, which meant it stopped the careless hand and not the determined one, while charging every honest change a heavy toll.

Thirteen moments became seven. The enforcement is now built out of tools that already exist and are already trusted: version control for the freeze, a separate reviewer for the separation, and the ordinary test suite for the verdict. Less code to maintain, less ceremony per change, and the guarantees that were real are still there.

!!! note "Four rules the measurements paid for"
    **1. Order comes from lineage, never from clocks.** A timestamp is an environment variable anyone can set. In a live demonstration, two variables put a piece of work nine hours before the approval it descends from; lineage got it right and the clock did not.

    **2. The verdict is the suite actually run, not a shortcut.** A deletion experiment produced 586 failing tests across 28 files while all three fast shortcuts reported success.

    **3. Every check is broken on purpose before it is trusted.** An uncalibrated check is worse than no check, because it manufactures confidence.

    **4. Prove a safeguard is alive, never infer it from silence.** 24 broad error handlers were found wrapped around calls into this system, 10 of them invisibly. Silence is not evidence.

### A worked example, end to end

**The ask:** "I want a Monday morning digest of which suppliers slipped last week."

**Step 1.** Value: *a weekly supplier slippage digest, delivered before the Monday leadership call, so a slip is discussed in the week it happens rather than at quarter end.*

**Step 2.** Scope: reads the supplier records and the delivery log. Sends to one channel. Explicitly out of scope: chasing the supplier, changing any record, forecasting.

**Step 3.** Criteria, one test each. When a delivery date passes with no receipt logged, the digest shall name that supplier. When nothing slipped, the digest shall say so in one line rather than staying silent. When the delivery log cannot be read, the digest shall report the failure and shall not send an empty all clear. The input table covers the awkward cases: a supplier with no deliveries at all, a delivery logged late but on time, a date exactly on the boundary.

**Step 4.** You read the plan, see that the third criterion is the one that saves you from a false all clear, and commit.

**Step 5.** A separate builder implements it. A separate reviewer checks it.

**Step 6.** The review finds that an unreadable log produced an empty digest that read as good news. That is a code origin finding against a criterion that already existed, so it is fixed, and the test that always covered it now genuinely bites.

**Step 7.** Ships. The record names the undo in one line: turn off the schedule, restore the previous version, run the test suite.

### Why an executive should care

| Canopus property | What it means for the business |
|---|---|
| Two human approvals, never delegated | Nothing enters the system you depend on without you agreeing twice, once to the intent and once to the result. |
| Definition of done fixed in advance | No moving goalposts, in either direction. You cannot be sold a smaller success than the one you approved. |
| Builder separated from planner | The self assessment problem is removed by construction rather than by good intentions. |
| Test strength measured, not assumed | A green light means something. That is the entire point of the standard. |
| Undo written before it is needed | Recovery is a one line instruction on file, not an improvisation under pressure. |
| One record per change | A complete, readable audit trail of every meaningful change, with the reasoning attached. |

---

## 3. Hearing recordings, reading documents

*A recorded call becomes the follow ups it should have produced*

The most expensive thing about a good meeting is what happens after it. Someone has to watch it back, or nobody does, and the commitments made in minute 47 quietly evaporate. HEADING OS can now take a recording, hear it, and drive the work that should follow from it.

### Hearing: speech to text that never leaves the building

- **Nothing is uploaded.** The audio, the model, and the transcript all stay on your machine. There is no cloud transcription service in this path, which means a confidential negotiation recording never becomes a third party's training data or a third party's breach.
- **Video files, directly.** Meeting recordings in the usual formats are read as they are, with no conversion step. Audio only recordings, voice notes, and dictated memos work the same way.
- **Languages auto detected, including Russian.** The language is detected automatically and reported with a confidence figure, or you can force it. A Russian call and an English call go through the same command.
- **Plain text, subtitles, or structured data.** Readable prose for a summary, timestamped subtitles for review, or structured output with per segment and per word timings when something needs to be quoted precisely.

!!! note "Built by measurement, not by picking the biggest model"
    The default is deliberately not the largest available model. Over two minutes of real speech, the largest model returned the transcript with **zero commas, periods, or capital letters**, and ran slower. The mid sized model punctuated it properly and was faster. An unreadable wall of lowercase is not the higher quality answer.

    Short recordings take the slower, higher quality path and long ones take the fast path, because the quality difference costs seconds on a voice note and hours on a two hour workshop. And one tempting shortcut was tested and rejected outright: feeding the model a sample sentence to teach it punctuation caused it, on a longer file, to **echo the sample back in place of real speech and delete about 40 percent of the recording**. Losing content is far worse than losing commas, so that option is off by default and carries a warning when switched on.

### Reading: documents, screens, and where exactly something was said

Alongside hearing, the engine reads what it is shown. Contracts, decks, spreadsheets, and scanned documents are parsed with their spatial layout intact, so an answer can point at the exact location on the exact page where a claim comes from, rather than paraphrasing and hoping. A screenshot on your clipboard is read directly. A web page that only renders in a real browser is captured and read the same way.

The practical effect: when the engine tells you a supplier's liability cap is a specific figure, it can show you the box on the page it read that from.

### From a recording to real business action

Transcription on its own is a commodity. The value is what the rest of the engine does with the words, because it already holds the deal history, the CRM, the thread for that relationship, and your voice.

1. **Drop the file.** You hand the engine the recording of the call.
2. **Hear it.** A local transcript with timings, in the language spoken.
3. **Place it.** Matched against the relationship it belongs to and everything already known about it.
4. **Extract.** Decisions, commitments, dates, owners, and open questions, each traceable to a moment in the recording.
5. **Draft.** Thread log, CRM entries, and outbound follow ups, queued for your approval.

**Worked example.** A 62 minute technical workshop with Northwind Telecom is recorded. You hand the file to the engine and say: log it and prepare the follow ups.

**What comes back, in one pass:** a summary of what was agreed; three commitments with owners and dates, each with the timestamp in the recording where it was said, so a disputed detail is settled by playback rather than by memory; two open technical questions nobody answered; an updated entry on the Northwind thread; a CRM interaction logged against the two people who attended; and a follow up email drafted in your voice, restating the commitments and asking the two open questions.

**What does not happen:** the email is not sent. It waits in the approval queue, where you read it, edit it if you want, and approve it. That is a hard rule and section 4 explains why it will not be softened.

!!! warning "Being precise about \"seeing\""
    The engine hears recordings and reads documents, screenshots, and web pages. It does not watch the moving picture of a video: a slide shown on screen and never spoken aloud is not captured by the audio path. If that slide matters, hand over the deck and it will be read properly, with citations to the page. Ingestion is also something you start: you hand over the file. There is no automatic connector that reaches into a meeting platform and pulls recordings by itself, which is a deliberate boundary rather than a missing feature.

---

## 4. Nothing sends itself

*The one control everything else inherits, and the new habit of measuring whether the controls work*

An AI assistant becomes dangerous to the person it serves when three things are true at once: it can reach private data, it reads content written by outsiders, and it can send messages to the outside world. The first two cannot be removed without removing the point of the assistant. So the third is permanently held by a human.

!!! note "The rule, stated plainly"
    Every outbound message is drafted, queued, and waits. A human approves each one, individually. Approving one does not approve the next. This is enforced in code, not in policy prose: a message type that could send anything outbound is forced into the gated tier even if a configuration file claims otherwise, and an unrecognised message type is gated too, so forgetting also fails safe.

### New in this release: we now measure whether our own guardrails do anything

Until recently, a guardrail that was quietly catching real mistakes and a guardrail that had never fired once in its life looked identical from the outside. Both were simply present. Now every refusal is counted, and a report answers a question nobody could previously answer:

- **Catching**: this guard has refused real things recently, and here is what they were.
- **Holding**: this guard has been watched long enough to say it is not firing, which may be fine.
- **Too early**: this guard has not been observed long enough to judge. The report says so rather than implying a clean bill of health.

That last distinction is the point. "We have seen nothing" and "there is nothing" are different statements, and a control that cannot tell them apart is a control that flatters itself. The refused content itself is never recorded, only the fact of the refusal, so the audit log cannot become the leak.

### Other hardening in this release

- **We now audit what we install, not only what we write.** Every guard used to watch what this system writes. None watched what it installs and then runs. A new audit enumerates third party extensions and their hooks, compares the installed surface against a reviewed baseline so an upgrade reads as a diff, and scans that content for injected instructions. It reports rather than blocks, on purpose, until its first real measurement earns it the right to block.
- **A wall on where code can be pushed.** The engine is public and the data is private, permanently. Pushing now refuses a misconfigured destination before anything leaves the machine, refuses a repository aimed at the wrong remote, and scans the actual content of everything about to leave. That scan sits on the only sanctioned path and has no override flag.
- **Secrets redacted at the moment of writing.** Session summaries are scrubbed of credential shaped text before being written to disk, so a session that merely discusses a password pattern cannot produce a file that blocks its own backup. Several ways past the write time scanner were found and closed, including one where a large enough file simply walked past the check.
- **Personal life stays out of working context.** Personal threads and personal conversation history are held behind a hard boundary that the search index will not cross. They surface only when explicitly summoned. The boundary is enforced in several independent layers rather than by one flag.

The complete control set is documented in the [security model](SECURITY-MODEL.html) and the [threat model](THREAT-MODEL.html).

---

## 5. The unglamorous fixes that mattered most

*Four problems that were invisible precisely because everything reported healthy*

### Scheduled work was being silently thrown away

The scheduler inside the always on service discarded any job that was more than one second late, leaving a line in a log nobody reads. Measured over 24 hours on one machine: a one minute heartbeat **lost 1,059 of its 1,440 runs**, and the two hourly mail and calendar sync **ran twice instead of twelve times**. Every health surface reported the service as running normally, because the heartbeat that would have accused it was itself three quarters missing.

Fixed by making late jobs run late instead of vanishing, set once where every current and future job inherits it rather than on each job individually. An automated guard now refuses any new scheduler built without that setting. The visible consequence: that sync now completes twelve times a day rather than twice, which is a sixfold increase in real work on a path that had been quietly failing.

### Timers were running on the wrong clock

Scheduled work had no single source of truth for your timezone and defaulted to universal time in silence, which put jobs hours away from where their own schedule said they should be. One resolver now answers the question for the whole fleet, per machine, and a job that would silently drift is caught by a test instead.

### Backup was declining to back up the irreplaceable half

The backup command treated one repository's refusal as a reason to stop entirely. Because the code side sits on a working branch during every piece of work by construction, the process died there and **the private data was never pushed at all**, for the whole duration of every project. Now the data goes first, because it is the half that cannot be rebuilt; a repository that genuinely cannot be pushed is skipped, committed locally, and named with its reason; and a partial backup reports itself as partial instead of as success. The refusals that genuinely should stop everything, such as a secret found in content, still stop everything.

### A daemon that could not produce anything, deleted

One background service, a thousand lines of it, was found to be structurally incapable of producing the output it existed to produce. It was removed rather than repaired, along with its schedule and its monitoring entry. Subtraction is a legitimate release note item, and this release also retired a large amount of the older engineering machinery described in section 2.

!!! note "A theme worth naming"
    All four of these were invisible for the same reason: the thing that would have raised the alarm was the thing that was broken. That is why this release invests so heavily in measuring the measurers, from probing test strength, to counting guard refusals, to proving that a safeguard is alive rather than inferring it from silence.

---

## 6. What this means for you

*Three changes in how the system behaves day to day*

- **It brings the file to you.** You stop being the index. Ask a question and the relevant history is already on the table, ranked by relevance to what you actually asked, with the record opened rather than the summary quoted.
- **The follow ups write themselves, and still wait for you.** A recording becomes a logged thread, updated relationship records, and drafted follow ups in one pass, entirely on your own machine. Nothing goes out until you approve it, one message at a time.
- **New capability arrives through one known door.** Every meaningful change carries a stated purpose, a fixed definition of done, an independent review, a written undo, and a record. You approve twice and can audit afterwards.

---

## 7. Honest limits

*What this release does not do, stated here rather than discovered later*

- **No automatic meeting connector.** Recordings are handed over by you. Nothing reaches into a meeting platform and collects them on its own.
- **The visual track of a video is not analysed.** The engine hears a recording and reads documents. Content that appears on screen and is never spoken is not captured from the recording; give it the deck instead.
- **Recall reflects what has been written down.** A decision made verbally and never logged is not in memory, and the engine will honestly report the gap rather than reconstruct it.
- **The build standard is measured on itself, and it is not finished.** One of its newer instruments is explicitly uncalibrated and therefore reports rather than blocks. It stays that way until it earns more.
- **Some engine safeguards report rather than enforce.** Where that is true, this note says so. A control that cannot enforce is never described as if it can.

---

## 8. Notice on names and examples

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

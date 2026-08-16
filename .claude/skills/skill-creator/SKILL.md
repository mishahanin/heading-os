---
name: skill-creator
disable-model-invocation: true
description: Create new skills, modify and improve existing skills, and measure skill performance. Use when users want to create a skill from scratch, update or optimize an existing skill, run evals to test a skill, benchmark skill performance with variance analysis, or optimize a skill's description for better triggering accuracy. EXPLICIT INVOCATION ONLY - mutates workspace skill infrastructure.
argument-hint: "[create|improve|eval|optimize]"
allowed-tools: "Read, Write, Edit, Bash(python3:*), Glob, Grep"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.2"
x-heading-orchestration:
  parallel_safe: false
  shared_state: []
  triggers:
    - create a skill
    - improve this skill
    - eval this skill
x-heading-capability:
  what: >
    Creates new skills, improves and optimizes existing ones, and benchmarks
    skill performance with evals and description-trigger tuning. CEO-only -
    mutates workspace skill infrastructure.
  how: >
    Explicit invocation only - run /skill-creator [create|improve|eval|
    optimize]. Drafts SKILL.md, runs test cases, and iterates via the eval
    viewer; never auto-triggers.
  when: >
    Use to author or refine a skill directly. Executives who want a new skill
    instead use /request-skill to email the request to the CEO.
x-heading-routing:
  category: Operations
  triggers:
    - NEVER auto-trigger. Explicit `/skill-creator` only.
  exclusions:
    - N/A
  compound: 'No'
  router: manual
---
# Skill Creator

A skill for creating new skills and iteratively improving them.

At a high level, the process of creating a skill goes like this:

- Decide what you want the skill to do and roughly how it should do it
- Write a draft of the skill
- Create a few test prompts and run claude-with-access-to-the-skill on them
- Help the user evaluate the results both qualitatively and quantitatively
  - While the runs happen in the background, draft some quantitative evals if there are none. Use any that exist as they are, or modify them where something needs to change. Then explain them to the user.
  - Run `eval-viewer/generate_review.py` to show the user the results and the quantitative metrics
- Rewrite the skill from the user's evaluation of the results. Also fix any glaring flaw that the quantitative benchmarks make apparent.
- Repeat until you're satisfied
- Expand the test set and try again at larger scale

Find out where the user stands in this process, then help them through the remaining stages. Say they open with "I want to make a skill for X". Narrow down what they mean, write a draft, write the test cases, agree how to evaluate, run the prompts, and repeat.

On the other hand, maybe they already have a draft of the skill. In this case you can go straight to the eval/iterate part of the loop.

Stay flexible. If the user says "I don't need a bunch of evaluations, just vibe with me", do that instead.

Once the skill is done, and the order here is flexible too, run the skill description improver. A separate script owns it, and it optimizes how the skill triggers.

Cool? Cool.

<!-- ste-skip-start -->
<!-- Explanatory: register calibration, where the reasoning is the value. Per
     .claude/rules/documentation-style.md, flattening this destroys the point. -->

## Communicating with the user

The skill creator is liable to be used by people across a wide range of familiarity with coding jargon. If you haven't heard (and how could you, it's only very recently that it started), there's a trend now where the power of Claude is inspiring plumbers to open up their terminals, parents and grandparents to google "how to install npm". On the other hand, the bulk of users are probably fairly computer-literate.

So please pay attention to context cues to understand how to phrase your communication! In the default case, just to give you some idea:

- "evaluation" and "benchmark" are borderline, but OK
- for "JSON" and "assertion" you want to see serious cues from the user that they know what those things are before using them without explaining them

It's OK to briefly explain terms if you're in doubt, and feel free to clarify terms with a short definition if you're unsure if the user will get it.
<!-- ste-skip-end -->

---

## Creating a skill

### Capture Intent

Start by understanding the user's intent. The current conversation might already contain a workflow the user wants to capture (e.g., they say "turn this into a skill"). If so, extract answers from the conversation history first -- the tools used, the sequence of steps, corrections the user made, input/output formats observed. The user may need to fill the gaps, and should confirm before proceeding to the next step.

1. What should this skill enable Claude to do?
2. When should this skill trigger? (what user phrases/contexts)
3. What's the expected output format?
4. Should we set up test cases to verify the skill works? Skills with objectively verifiable outputs (file transforms, data extraction, code generation, fixed workflow steps) benefit from test cases. Skills with subjective outputs (writing style, art) often don't need them. Suggest the appropriate default based on the skill type, but let the user decide.

### Interview and Research

Proactively ask questions about edge cases, input/output formats, example files, success criteria, and dependencies. Wait to write test prompts until you've got this part ironed out.

Check available MCPs - if useful for research (searching docs, finding similar skills, looking up best practices), research in parallel via subagents if available, otherwise inline. Come prepared with context to reduce burden on the user.

### Write the SKILL.md

Read `references/anthropic-skill-spec.md` for the full SKILL.md spec. It covers:

- the frontmatter field by field, including the namespaced `x-heading-orchestration:` workspace extension
- skill anatomy: `skill-name/SKILL.md` plus `scripts/`, `references/`, and `assets/`
- progressive disclosure: three-level loading, under 500 lines, domain-organized references
- the Principle of Lack of Surprise
- writing patterns and style, including output-format templates

Apply that spec when you fill in the draft.

The two most important fields:

- **description**: The primary triggering mechanism. Include both what the skill does AND specific when-to-use contexts. Be a little "pushy" here, to counter Claude's tendency to undertrigger. For example:

  ```
  Make sure to use this skill whenever the user mentions dashboards, data
  visualization, internal metrics, or wants to display any kind of company
  data, even if they don't explicitly ask for a "dashboard".
  ```

- **`x-heading-orchestration:`**: Namespaced workspace extension carrying `parallel_safe`, `shared_state`, `triggers`. See the reference for the full contract. When in doubt on `parallel_safe`, use `false`.

### Test Cases

After writing the skill draft, come up with 2-3 realistic test prompts -- the kind of thing a real user would actually say. Share them with the user: [you don't have to use this exact language] "Here are a few test cases I'd like to try. Do these look right, or do you want to add more?" Then run them.

Save test cases to `evals/evals.json`. Don't write assertions yet -- just the prompts. You'll draft assertions in the next step while the runs are in progress.

```json
{
  "skill_name": "example-skill",
  "evals": [
    {
      "id": 1,
      "prompt": "User's task prompt",
      "expected_output": "Description of expected result",
      "files": []
    }
  ]
}
```

See `references/schemas.md` for the full schema (including the `assertions` field, which you'll add later).

## Running and evaluating test cases

Read `references/running-evals.md` for the full sequence. This section is one
continuous flow. Do not stop partway through it. The reference covers the
workspace layout (`<skill-name>-workspace/iteration-N/eval-ID/`) and five steps:

1. Spawn the with-skill AND baseline runs in the same turn. See the reference for the `eval_metadata.json` shape.
2. Draft assertions while the runs are in progress.
3. Capture the timing data from the task notifications at once. Nothing else persists it.
4. Grade with `agents/grader.md`, aggregate via `python -m scripts.aggregate_benchmark`, run the analyst pass per `agents/analyzer.md`, then launch the viewer with `eval-viewer/generate_review.py`. Cowork needs the headless `--static` mode.
5. Read `feedback.json`, then kill the viewer.

Do NOT use `/skill-test` or any other testing skill. Always use `generate_review.py` rather than writing custom HTML. The grading.json expectations array must use the exact fields `text`, `passed`, `evidence` - the viewer depends on these names.

### Router corpus (triggers.json), REQUIRED for auto-routable skills

A new **auto-routable** skill (`x-heading-routing.router: auto` AND NOT `disable-model-invocation: true`) is not finished until it ships a valid `triggers.json`. A `router: manual` or `disable-model-invocation: true` skill is EXEMPT (it never auto-routes). This is a mechanical gate: `scripts/skill-metadata-check.py` exits 1 (CI + the `skill-size-budget` pre-commit hook) on any auto-routable skill lacking a valid corpus that is not grandfathered.

A valid corpus is a JSON array of at least 6 `{ "query": "...", "should_trigger": true|false }` cases. It is a different file from `evals/evals.json`. It needs:

- at least 4 positives, drawn from the skill's `x-heading-routing.triggers`
- at least 2 hard negatives, drawn from its `x-heading-routing.exclusions`. Each negative names the neighbor skill it should route to instead.

Use illustrative placeholders only. Run `python scripts/skill-metadata-check.py` and confirm exit 0 before you declare the skill done. New skills are never grandfathered, so do not add one to `config/triggers-coverage-baseline.json`. `--write-baseline` is shrink-only and refuses.

---

## Improving the skill

This is the heart of the loop. You've run the test cases, the user has reviewed the results, and now you need to make the skill better based on their feedback.

### How to think about improvements

<!-- ste-skip-start -->
<!-- Explanatory: this section IS the reasoning it asks the model to adopt, and
     it argues against rigid instruction shapes in its own third point. Per
     .claude/rules/documentation-style.md, flattening it destroys the point. -->

1. **Generalize from the feedback.** The big picture thing that's happening here is that we're trying to create skills that can be used a million times (maybe literally, maybe even more who knows) across many different prompts. Here you and the user are iterating on only a few examples over and over again because it helps move faster. The user knows these examples in and out and it's quick for them to assess new outputs. But if the skill you and the user are codeveloping works only for those examples, it's useless. Rather than put in fiddly overfitty changes, or oppressively constrictive MUSTs, if there's some stubborn issue, you might try branching out and using different metaphors, or recommending different patterns of working. It's relatively cheap to try and maybe you'll land on something great.

2. **Keep the prompt lean.** Remove things that aren't pulling their weight. Make sure to read the transcripts, not just the final outputs -- if it looks like the skill is making the model waste a bunch of time doing things that are unproductive, you can try getting rid of the parts of the skill that are making it do that and seeing what happens.

3. **Explain the why.** Try hard to explain the **why** behind everything you're asking the model to do. Today's LLMs are *smart*. They have good theory of mind and when given a good harness can go beyond rote instructions and really make things happen. Even if the feedback from the user is terse or frustrated, try to actually understand the task and why the user is writing what they wrote, and what they actually wrote, and then transmit this understanding into the instructions. If you find yourself writing ALWAYS or NEVER in all caps, or using super rigid structures, that's a yellow flag -- if possible, reframe and explain the reasoning so that the model understands why the thing you're asking for is important. That's a more humane, powerful, and effective approach.

4. **Look for repeated work across test cases.** Read the transcripts from the test runs and notice if the subagents all independently wrote similar helper scripts or took the same multi-step approach to something. If all 3 test cases resulted in the subagent writing a `create_docx.py` or a `build_chart.py`, that's a strong signal the skill should bundle that script. Write it once, put it in `scripts/`, and tell the skill to use it. This saves every future invocation from reinventing the wheel.

This task is pretty important (we are trying to create billions a year in economic value here!) and your thinking time is not the blocker; take your time and really mull things over. I'd suggest writing a draft revision and then looking at it anew and making improvements. Really do your best to get into the head of the user and understand what they want and need.
<!-- ste-skip-end -->

### The iteration loop

After improving the skill:

1. Apply your improvements to the skill
2. Rerun all test cases into a new `iteration-<N+1>/` directory, baseline runs included. For a new skill the baseline is always `without_skill`, and it stays the same across iterations. For an existing skill, pick the baseline yourself: either the version the user arrived with, or the previous iteration.
3. Launch the reviewer with `--previous-workspace` pointing at the previous iteration
4. Wait for the user to review and tell you they're done
5. Read the new feedback, improve again, repeat

Keep going until:
- The user says they're happy
- The feedback is all empty (everything looks good)
- You're not making meaningful progress

---

## Advanced: Blind comparison

A blind comparison system exists for a rigorous comparison between two versions of a skill. Reach for it when the user asks "is the new version actually better?". Read `agents/comparator.md` and `agents/analyzer.md` for the details. The basic idea: give two outputs to an independent agent without telling it which is which, and let it judge quality. Then analyze why the winner won.

This is optional, requires subagents, and most users won't need it. The human review loop is usually sufficient.

---

## Description Optimization

Once a skill is otherwise stable, offer to optimize the `description` frontmatter field. Claude uses that field to decide whether to invoke a skill at all. Read `references/description-optimization.md` for the full sequence:

1. Generate 20 realistic should-trigger and should-not-trigger eval queries, with near-miss negatives.
2. Review them with the user through `assets/eval_review.html`.
3. Run `python -m scripts.run_loop` in the background. It iterates with extended thinking on a 60/40 train/test split.
4. Apply `best_description` from the loop's JSON output.

The reference also explains how skill triggering works under the hood. Use that to design eval queries a skill would genuinely help Claude answer.

---

### Package and Present (only if `present_files` tool is available)

Check whether you have access to the `present_files` tool. If you don't, skip this step. If you do, package the skill and present the .skill file to the user:

```bash
python -m scripts.package_skill <path/to/skill-folder>
```

After packaging, direct the user to the resulting `.skill` file path so they can install it.

---

## Platform-specific instructions

On Claude.ai (no subagents) or Cowork (subagents but no browser), read `references/platform-specific.md` for the full adaptations. The key points:

- Claude.ai runs test cases inline, one at a time. It skips baselines, benchmarking, description optimization, and blind comparison.
- Cowork keeps the full parallel workflow. Pass `--static <output_path>` to the eval viewer, and ALWAYS generate the viewer before you evaluate any output yourself.

In Claude Code the default workflow applies and needs no adaptation.

---

## Reference files

The agents/ directory contains instructions for specialized subagents. Read them when you need to spawn the relevant subagent.

- `agents/grader.md` -- How to evaluate assertions against outputs
- `agents/comparator.md` -- How to do blind A/B comparison between two outputs
- `agents/analyzer.md` -- How to analyze why one version beat another

The references/ directory has additional documentation:

- `references/schemas.md` -- JSON structures for evals.json, grading.json, etc.
- `references/anthropic-skill-spec.md` -- Anthropic SKILL.md frontmatter spec, skill anatomy, progressive disclosure, writing patterns and style
- `references/running-evals.md` -- Full sequence for running test cases (Steps 1-5: spawn runs, draft assertions, capture timing, grade/aggregate/launch viewer, read feedback)
- `references/description-optimization.md` -- 20-query trigger eval generation, optimization loop via `run_loop`, how skill triggering actually works
- `references/platform-specific.md` -- Adaptations for Claude.ai (no subagents) and Cowork (subagents but no browser)

---

Repeating one more time the core loop here for emphasis:

- Figure out what the skill is about
- Draft or edit the skill
- Run claude-with-access-to-the-skill on test prompts
- With the user, evaluate the outputs:
  - Create benchmark.json and run `eval-viewer/generate_review.py` to help the user review them
  - Run quantitative evals
- Repeat until you and the user are satisfied
- Package the final skill and return it to the user.

Please add these steps to your TodoList, if you have such a thing, so you don't forget. In Cowork, add this one item verbatim:

```
Create evals JSON and run eval-viewer/generate_review.py so human can review test cases
```

Good luck!

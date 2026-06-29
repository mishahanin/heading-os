# Align - Question Templates and Generation Rules

Consumed by: `.claude/skills/align/SKILL.md` Phase 2 Block B when producing the N numbered questions. Consult before generating any question to pick the right form (lettered vs open) and to enforce the committed-recommendation contract.

Last Updated: 2026-06-10

## Question form catalogue

For each question 1..N, pick one of two forms.

### Lettered form (use when the choice changes downstream work substantially)

```
## Question {i}: {short topic line, sentence case}

a) {option, one line}
b) {option, one line}
c) {option, one line}
d) {option, one line}

{voice_mode_label}: {letter} - {one to three lines of reasoning,
why this choice over the others}
```

### Open form (use when the answer space is genuinely open)

```
## Question {i}: {short topic line}

{Open question, one line. No options.}

{voice_mode_label}: {one to three lines of reasoning, naming the
committed default if user skips this question}
```

Open form fits deadlines, named people, specific URLs, free-text constraints, exact word counts, anything where forcing A/B/C would be friction theatre. Default to open when none of the obvious answers are wrong; default to lettered when the choice changes downstream work substantially.

## Rules for question generation

- **Option count.** Options are 2-4 per lettered question, not always 4. Binary decisions get two options. Five+ defensible options force a discrimination (rolled into option d as "other - specify").
- **Mix forms.** Not every question needs lettered options. Use the open form when the answer space is genuinely open.
- **One committed letter.** Recommendation names exactly one letter (or, for open questions, exactly one committed default value), never "a or b" or "depends." If the choice genuinely depends on missing information, that information IS the question; ask for it instead.
- **Committed reasoning prose.** No "on one hand / on the other." Reasoning lands a position.
- **Impact ordering.** Questions are ordered by impact - the question whose answer most reshapes downstream work goes first. Detail questions last.
- **No re-asking.** Do not ask what the request already says.

## Recommendation prose contract

The line under each question (`{voice_mode_label}: ...`) is the single committed recommendation. It carries the asymmetric closer that lands a position, not a balanced summary. Two anti-patterns to refuse:

- "Either a or b works depending on..." - if it depends on something, that something IS the next question.
- "I'd lean toward b but a is also defensible" - pick one. The user can override with a custom answer if they disagree.

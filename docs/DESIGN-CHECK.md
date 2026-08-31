<!-- version: 1.1.0 | last-updated: 2026-08-31 -->
# Design check

Two engines decide whether an artifact this engine produces reads as designed or
as generated. This page explains what each can see, what the calibration
deliberately silences, and why the gate freezes what already exists instead of
demanding it be rebuilt.

The prose rule these engines serve is `.claude/rules/visual-design-discipline.md`.
Its sibling for text is [the humanisation rule](RULES-REFERENCE.html); the shape
is the same, the medium is different.

---

## The two engines

```bash
python scripts/visual-discipline-check.py <file-or-dir>          # regex only
python scripts/visual-discipline-check.py --deep <file-or-dir>   # both engines
```

<!-- audit-skip-start -->
**The regex engine** runs always. It matches patterns against file contents:
forbidden fonts, the purple-to-pink hero gradient, oversized Tailwind radii,
Lucide and Heroicons defaults, the banned hero colours, plus advisory layout and
copy heuristics. It reads HTML, SVG and PPTX. **It sees what is written down.**
<!-- audit-skip-end -->

**The deep engine** runs on `--deep`. It is the
[impeccable](https://github.com/pbakaus/impeccable) CLI (Apache 2.0), pinned in
`scripts/.impeccable-version`. It parses the HTML, resolves the CSS cascade, and
computes real values: text contrast against the surface actually behind it,
heading hierarchy, accent stripes on rounded corners, type floors, kickers,
buzzwords and cadence tells in copy. It reads HTML and SVG. **It sees what
renders.**

<!-- audit-skip-start -->
The difference is not academic. The regex engine can be told `font-family: Inter`
and see it.
<!-- audit-skip-end -->
It cannot be told that `#79839a` on `#ffffff` is a 3.8:1 contrast
ratio against a 4.5:1 accessibility floor, because that fact does not exist in
any single line of the file. Every deep finding carries an `impeccable:` type
prefix, so in a merged report you can always tell which engine made a claim.

---

## Calibration

Three filters sit between the deep engine and a finding anyone acts on. All three
are declared in `config/visual-check-profiles.json`, each entry carrying the
reason it exists, because a silent suppression is indistinguishable from a
missing rule.

### Profiles

A profile is assigned by path, longest matching glob winning.

| Profile | Applies to | Silences |
|---|---|---|
| `screen` | the documentation site, dashboards, briefing HTML | nothing |
| `print` | A4 documents read as PDF | the type floors, line measure, container-inset checks |
| `doctype` | the five locked corporate templates and their renders | the print set, plus the approved kicker, section numbering, tracked uppercase |

The type floors are the largest single group. Impeccable enforces an 11px
minimum, which is a screen floor: a CSS pixel on an A4 page is not a screen
pixel, and 9px sets a normal print caption. On one measured document family, the
print profile removed 1,184 findings that said nothing true about the artifact.

The doctype profile encodes a rule of precedence rather than a technical fact:
**where a detector rule and a locked corporate template disagree, the template
wins.** The kicker above a heading is an approved element of the xPager layout,
and changing an approved template is a decision for its owner, not for a
detector.

### Plausibility bounds

The parser emits physically impossible readings on some CSS: an `h1` measured at
2856px, a `line-height` of 0.11x. These are filtered on **value**, not by
disabling the rule that produced them, so a genuine 96px oversized headline still
lands.

### Scope

Minified and vendored bundles are excluded. A regex inside a minified Mermaid
build produced a `broken-image` finding on the first run; nobody designed that
file.

---

## The baseline is a ratchet

```bash
python scripts/visual-discipline-check.py baseline record --deep docs/
python scripts/visual-discipline-check.py baseline check --deep docs/
python scripts/visual-discipline-check.py baseline stats
```

`.visual-baseline.json` records a count per file and per rule, the same shape as
`.lint-baseline.json` does for lint debt. `record` freezes what exists. `check`
fails only on findings **above** those counts, and never rewrites the file.

This is deliberate and it is the whole reason the integration was possible
without a rewrite. On first run the documentation site carried hundreds of
findings and the branded document family carried thousands. For the current size
of the freeze run `baseline stats`, which derives it from the file; the count
used to be typed into this page, the CI step comment and the rule, and all three
had drifted. Demanding zero would
have meant either rebuilding every existing artifact before the tool could be
used at all, or turning the gate off. Freezing means existing work is left alone
while new work is held to the standard, and each frozen finding surfaces the
moment its file is next edited.

A file **absent** from the baseline is a new file: nothing is suppressed for it.
That asymmetry is the point.

The baseline covers both engines. Freezing only the deep findings left
pre-existing regex debt failing forever, and a gate that is always red is a gate
nobody reads.

---

## Where it runs

| Surface | Behaviour |
|---|---|
| CI (`guards` job) | `baseline check --deep docs/` fails the build on a regression |
| `regenerate-docs-html.py --all` | prints the verdict; never blocks a regeneration |
| `render-doctype.py` | checks the rendered HTML under the `doctype` profile; reports |
| `marp_render.py` | checks the rendered deck under the `screen` profile; reports |

Only CI gates. A renderer that refused to write a file because a heading level
was skipped would block the very edit that fixes it.

---

## Honest limits

Four, stated plainly so a clean result is not over-read.

**PDF and DOCX are covered by neither engine.** The deep engine reads HTML and
SVG. The regex walk visits the suffixes in `SCAN_EXTENSIONS`, which is `.html`,
`.htm`, `.svg` and `.pptx`, so PPTX decks stay on the regex path but PDF and
DOCX are read by nothing at all. This page previously said the three "are not
covered by the deep engine", which implied a regex path for all three; only
PPTX has one. The gap lands hardest on the five locked corporate doctypes, whose
only renders are PDF and DOCX. This is the largest remaining gap.

**An unused CSS rule is not a finding.** The cascade is resolved against real
elements, so a declaration no element uses is correctly invisible. A clean result
is not proof that a stylesheet is clean.

**`npx --yes` fetches and executes third-party code at call time.** The exact
version pin is the only mitigation claimed here. It is the same exposure this
engine already accepts for `marp-cli`, and it is weaker than a hash-verified
install. Saying otherwise would be dressing a convention up as a control.

**The CI step passes when the CLI cannot be fetched.** A design gate that goes red
because a package registry was slow teaches people to ignore it. It reports the
degradation in one line and lets the build proceed on the regex verdict. What it
will never do is pass a real regression quietly.

And one thing no detector decides: the first three fundamentals of the design
rule are specificity density, committed stance, and hierarchy by intent. Those
stay human.

---

## An upstream defect worth knowing about

The impeccable CLI exits without waiting for its asynchronous stdout to drain.
Written to a pipe, Node buffers 64 KiB and the process dies with the rest
unflushed: a directory scan returned exactly 65,536 bytes of a 168,409-byte
document, so the JSON failed to parse and every finding was lost. Redirected to
a file it returns everything, because Node writes to a regular file
synchronously.

`scripts/utils/impeccable_engine.py` writes to a temporary file rather than
reading a pipe. The failure is worth naming because of its shape: the parse error
was caught and reported as "deep design checks skipped", the same message a
missing Node produces, so every directory-sized scan would have silently degraded
to nothing while looking like an environment problem.

---

*HEADING OS · Design check · see also [Extending the engine](EXTENDING.html) and
[Architecture](ARCHITECTURE.html).*

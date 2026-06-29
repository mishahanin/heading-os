# Humanisation — Banned Vocabulary Catalog

> Last Updated: 2026-05-11
> Source: `.claude/rules/humanization.md` (full vocabulary catalog extracted on 2026-05-11)

Full per-category banned word and phrase lists. Consult before any outbound prose voice pass (LinkedIn, email, proposal, letter, Tribe message, knowledge note). Task 10 will trim the active rule to point readers here for the full catalog; until then, this file IS the source-of-truth for the long-tail vocabulary list.

The 5 fundamentals and Step 0 calibration gate stay in the rule itself;
this catalog informs item #4 (kill AI vocabulary and phrase fingerprints)
of the fundamentals.

---

**Important framing.** Some banned items are absolute (no legitimate use in current LLM register). Others are conditional on context - figurative use is banned, literal use is fine. The lists below mark conditional cases.

<!-- audit-skip-start -->
#### Banned transitional and emphasis words

Never start sentences with "Additionally." Avoid these entirely or use sparingly (max once per document):
- `Additionally`, `Moreover`, `Furthermore`, `Subsequently`, `Meanwhile` (transitional)
- `Crucial`, `Pivotal`, `Vital`, `Significant`, `Key` (as adjective filler)

Connect ideas naturally without explicit transitional words. When a transition is genuinely needed, prefer plain ones (`then`, `but`, `so`).

#### Banned abstract nouns (vague filler)

- `Landscape` (figurative: "the evolving landscape of X")
- `Tapestry` (figurative: "a rich tapestry of Y")
- `Testament` ("stands as a testament to")
- `Realm` (figurative: "in the realm of")
- `Interplay`, `Intricacies`, `Paradigm`
- `Insights` (when vague: "key insights," "valuable insights")
- `Synergies`, `Synergy`, `Synergistic`
- `Ecosystem` (figurative)

Be concrete about what you actually mean.

#### Banned verbs

- `Delve`, `Dive into` (figurative)
- `Navigate` (figurative: "navigate the landscape," "navigate complexity")
- `Underscore`, `Highlight` (as verb: "this highlights"), `Showcase`, `Boasts` (meaning "has")
- `Garner` ("garnered attention")
- `Foster`, `Fostering`, `Cultivating` (figurative)
- `Bolster`, `Bolstered`, `Enhance`
- `Align with`, `Resonate with`
- `Elevate`, `Revolutionize`, `Reimagine`
- `Leverage`, `Unleash`, `Harness`, `Unpack`
- `Shed light on`, `Pave the way`
- `Utilize`, `Utilise` (use `use`)

Use straightforward verbs that directly describe the action.

#### Banned adjectives (promotional)

- `Vibrant`, `Rich` (figurative), `Profound`, `Renowned`
- `Groundbreaking` (used loosely), `Transformative`, `Game-changing`, `Cutting-edge`, `Innovative`
- `Robust`, `Comprehensive`, `Seamless`, `Holistic`, `Multifaceted`
- `Meticulous`, `Meticulously`
- `Enduring`, `Diverse array`, `Intricate`, `Nuanced`

Use specific descriptors or let facts speak for themselves.

#### Banned business jargon

- `Game changer`, `Deep dive`, `Think outside the box`
- `Cross-functional`, `Enablement`, `Touch point`, `Touchpoint`
- `There's no denying`, `Across Different`
- `Human oversight`
- `To bridge` (figurative)
- `Hustle and bustle`

Use plain language that directly communicates your point.

#### Banned dramatic / theatrical language

- `Whispering`, `Whisper` (used metaphorically: "the data whispers")
- `It's like having` (followed by analogy)
- Reaching for figurative comparisons to journeys, tapestries, landscapes, ecosystems

Describe things directly without unnecessary drama.

#### Banned phrases

- "In today's [fast-paced / rapidly evolving / digital / dynamic / modern] world..."
- "It's important to note that..." / "It's worth noting that..."
- "When it comes to..."
- "At the end of the day..."
- "exciting times lie ahead"
- "I hope this helps" / "I hope this finds you well"
- "Certainly!" / "Great question!" / "I'd be happy to..." / "I'd love to..."
- "In conclusion..." / "To summarise..." (just write the conclusion; don't announce it)
- "Going forward..." / "Moving forward..."
- "There's no denying"

#### Banned over-emphasis phrases (do not tell readers what's important)

Do not tell the reader something is important. Let facts demonstrate it. Never use:

- "stands as" / "serves as" (when used to inflate, e.g., "stands as a testament")
- "is a testament to" / "is a reminder of"
- "plays a vital/significant/crucial/pivotal/key role"
- "underscores its importance/significance"
- "reflects broader trends"
- "symbolising its ongoing/enduring/lasting impact"
- "contributing to the evolution of"
- "setting the stage for"
- "marking/shaping the future"
- "represents a shift"
- "key turning point"
- "evolving landscape"
- "focal point"
- "indelible mark"
- "deeply rooted"

#### Banned -ing tail analysis phrases

Do not tack on present-participle phrases at the end of sentences. These create false analytical depth and are a strong LLM tic. Never use:

- "...highlighting its importance"
- "...underscoring the significance"
- "...emphasising the need for"
- "...ensuring continued growth"
- "...reflecting broader trends"
- "...symbolising progress"
- "...contributing to development"
- "...fostering innovation"
- "...encompassing multiple aspects"
- "...cultivating community"

Make direct statements. Develop analysis fully in separate sentences rather than appending shallow observations.

#### Banned promotional language (advertisement register)

- `boasts a` (use `has`)
- `nestled in`, `in the heart of`
- `natural beauty`
- `showcasing excellence`, `exemplifies quality`
- `commitment to`
- `featuring a diverse array`
- `enhancing the experience`

Use neutral, factual language. Describe what exists without embellishment.

#### Banned vague attributions

Do not attribute claims to unnamed authorities. Avoid:

- "Industry reports suggest"
- "Observers have cited"
- "Experts argue" / "Some critics argue" / "Researchers believe"
- "Several sources indicate" (when only one or two exist)
- "Such as" (before exhaustive lists implying more examples exist)

Name specific sources, or omit attribution if the statement is uncontroversial.

#### Banned generic ecosystem / heritage language

- `rich heritage`, `cultural significance` (without specifics)
- "ecosystem importance" without naming actual ecological relationships

Be specific about actual practices or relationships.

#### Banned section structures

Do not segregate content into formulaic sections:

- "Despite its [positives], [subject] faces several challenges. Despite these challenges, [optimistic conclusion]." (challenges-and-future formula)
- Section headings: "Future Outlook", "Challenges and Legacy", "Why It Matters", "Why This Is Important"
- Rigid outline structures with predictable section headings on every article

Integrate challenges and significance naturally; remove explicit "Why It Matters" sections.
<!-- audit-skip-end -->

#### Empty structural patterns (CONDITIONAL on vacuous Y)

The PDF's framing resolves the apparent contradiction in our empirical data. The following patterns are AI tells **only when the X-Y contrast is vacuous** (Y adds nothing meaningful, the rhetoric is empty scaffolding). They are legitimate rhetoric **when the X-Y contrast carries real semantic weight**.

Patterns:

- "Not only X, but also Y"
- "Just X, but also Y"
- "From X to Y" (e.g., "from small businesses to large corporations")
- "That's not X. It's Y." / "It's not X, it's Y." / "This isn't about X. It's about Y."
- "don't/aren't/isn't just"

Test for vacuousness: if you can swap Y for a synonym or remove it without changing the substantive claim, the pattern is empty - rewrite. If Y carries information that X does not, the pattern is rhetoric - leave it.

The empirical anchor: the 10.8% Tribe text uses "You don't choose your tribe for their skills. You choose them for what they do when the skills aren't enough." The Y ("what they do when skills aren't enough") carries content X ("their skills") does not. Substantive contrast. Detector did not flag it.

#### The one structural pattern that is always banned

- **Title Case For All Headings.** Use sentence case. Title Case is print-magazine register that RLHF picked up. (This survives every datapoint because it's a typographical convention.)

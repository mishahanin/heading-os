# Visual Design Discipline - Reference

Companion reference to `.claude/rules/visual-design-discipline.md`. Empirical basis, exemplar shelf with captured screenshots, anti-pattern catalog, vocabulary expansion, and audit checklists per artifact type.

Last Updated: 2026-05-25
Last Verified: 2026-05-25
Consumed by: `.claude/rules/visual-design-discipline.md` (always-active workspace rule); all design-producing skills (`/design`, `/pptx-generator`, `/keynote-deck`, `/dashboard`, `/marp`, `/xpager`, `/intel-briefing-newsletter`, `/market-brief`, `/proposal`, `/corporate-letter`, `/partnership-doc`, `/official-doc`, `/investor-pitch`, `/data-room`, `/docparse`).

---

## 1. Empirical basis

Five research passes converged on one diagnosis: AI-default design is the statistical median of the training distribution made visible. Five passes:

- **Pass 1** - four parallel research agents producing 12,460 words across AI-fingerprint discourse, the exemplar catalog (28 entries), presentation-design specifically, and dashboard exemplars cross-referenced against 20 Odin brain principles.
- **Pass 2** - council (Gemini and Grok in independent mode) reasoning fresh on what makes contemporary visual design read as human-crafted vs AI-generated. Verdict logged as `mix` because the three views (Gemini identity, Grok parametric, Claude hierarchy + cadence) are complementary.
- **Pass 3** - Odin brain audit. 20 principles directly relevant, none contradicting a visual design rule. Strongest precedents: `specificity-density-beats-structural-patterns`, `personal-anecdote-is-structural-defeat-of-ai-detection`, `escape-competition-through-authenticity`, `simplicity-signals-mastery`, `headline-first-communication`.
- **Pass 4** - visual capture. 30 screenshots at 1440x900 via Playwright on Proton VPN. 24 successful, 3 enterprise sites returned Akamai "Access Denied" (the refusal is itself evidence), 1 Cloudflare challenge (Gamma), 1 login redirect (Linear status), 1 geo-localized to German (Stripe).
- **Pass 5** - synthesis with hard approval gate. CEO decisions locked the rule structure: parametric leads with identity supplying specifics; stock people-photography banned with custom illustration permitted under 2-bit or commissioned-style constraint; Heading OS v8 referenced as canonical 31C dashboard implementation; rule sits always-active.

Full audit trail: `outputs/research/2026-05-25_design_visual-design-discipline-research.md` (Pass 5 synthesis), `outputs/research/_drafts/2026-05-25_visual-design-discipline_pass1_*.md` (four Pass 1 briefs), `outputs/research/_drafts/2026-05-25_visual-design-discipline_pass4_visual-analysis.md` (visual analysis), `outputs/operations/council/2026-05-25_council_161558_ai-vs-human-design-tells.md` (council transcript).

---

## 2. The exemplar shelf

Three tiers. Product tier commits to a near-monochrome spine with one accent. Studio tier inverts to typography-led and work-as-content. Status-page tier shows that distinctive operational surfaces are an under-exploited brand opportunity. Screenshots captured 2026-05-25 at 1440x900 viewport.

### 2.1 Product tier

#### Linear - linear.app


Dark canvas (`~#08090C`), white sans headline ("The product development system for teams and agents") at approximately 64px in Inter Display with tight tracking. Sub-paragraph blurred behind glassmorphic layer until reveal. App frame below the fold with realistic operator data: issue `ENG-2703`, labels `Performance` and `iOS`, inline `vehicle_state` monospace chip. Letter-spacing tuned to `-0.22px` for display and `-0.11px` for body, deliberately tight to feel "precise" rather than spacious.

What it commits to: dark-first surface mode; single accent (chartreuse callout `~#D7FF3F`); product authenticity over marketing illustration; narrative flow (1.0 Intake -> 2.0 Plan -> 3.0 Build -> 4.0 Diffs -> 5.0 Monitor) rather than feature-card grid.

What it avoids: rounded-2xl cards on the marketing site; gradient hero text overlays; marketing illustration of people; large stock photography; five-CTA hero. One declared brand voice.

#### Plausible Analytics (live demo) - plausible.io/plausible.io


The load-bearing reference for the dashboard half of this rule. Pure white canvas, no chrome beyond logo and two nav links. Six-column metric strip at top: `300k`, `510k`, `1.8M`, `3.71`, `42%`, `7m 27s`. Single indigo area chart (`~#5D4FE7` line, light tint fill) dominates. Two tables (Sources, Top Pages) below. Zero gratuitous decoration. Every datum on the page is real. Every label is the actual metric definition. The URLs in the Top Pages table are the actual top pages of the actual Plausible site.

What it commits to: light-first surface mode; single-page-no-tabs layout; one indigo accent and nothing else saturated; specificity density at every cell of the view.

What it avoids: tabs, menus, drop-shadows, gradients, animation, stock illustration, drawer overlays, hidden detail behind expand-collapse.

#### Vercel - vercel.com


Cream canvas (`~#FAFAF7`), centered headline "Build and deploy on the AI Cloud." in Geist at approximately 56px. Two pill CTAs (black filled, ghost). Below the hero: chromed tetrahedron 3D render on orange-pink-cream radial (`~#FFD89C` to `~#FFF`). Logo wall on scroll. Geist was commissioned with Basement Studio in 2023, open-sourced under SIL OFL, explicitly designed for screens.

What it commits to: commissioned typeface (Geist Sans, Mono, Pixel) as identity signature; monochrome-plus-brand-gradient register; the grid as a "huge part of the new Vercel aesthetic" per their own design system docs.

What it avoids: third-party display fonts; stock developer photography; gradient blob hero; color outside the brand.

#### Resend - resend.com


True black canvas (`~#000000` or close). White Geist-family headline "Email for developers" at approximately 64px. To the right: a black metal cube hero render with subtle highlights and photographic specular reflections. Single CTA "Get started" as white-on-black pill. Logo wall below the fold (Linear and others).

What it commits to: monochrome register; product-shot-as-hero; Geist-family typography; one accent.

What it avoids: mascots; illustration; stock photography; multi-color gradient washes; "We use AI" hero copy.

#### Stripe - stripe.com


Note: capture geo-localized to German via the Proton VPN exit. The brand-gradient ribbon (purple-magenta-coral) sweeping diagonally from upper-right is intact and is the signature visual element. Logo wall shows Amazon, NVIDIA, Ford, Coinbase, Shopify, Mindbody, MetLife - proof-by-association pattern. Sans-serif medium-weight headline at approximately 52px.

What it commits to: commissioned gradient ribbon as brand asset (not a CSS template); navy with abundant white space; bento-grid for product offerings; emphasis on tabular numbers; commissioned photography where "the window frame forms Stripe's parallelogram logo."

What it avoids: rounded card chrome dominating the layout; stock photography; off-the-shelf icon set; third-party gradient generator output.

#### PostHog - posthog.com


Counter-example to the dark-monochrome assumption. Cream-yellow canvas (`~#F6E9C9`). Retro-isometric illustration of a wooden cabin on a grassy hill, characters mining and working. Inside the cabin frame: a fake browser window showing a `posthog.com` URL. Heavy break from the dark-product norm. Hand-illustrated by Lottie Coxon (hired as employee five, graphic designer, two full-time illustrators today dedicated to hedgehogs).

What it commits to: extensive hand-drawn hedgehog universe; open-source company handbook published publicly; pricing visible on the pricing page; "rogue, sarcastic, meme-y, unhinged, weird" brand voice in defiance of every blue-minimal analytics competitor.

What it avoids: blue; minimal corporate aesthetic; founders-in-suits photography; stock illustration; pricing-page obfuscation.

#### Notion Calendar - notion.so/product/calendar


White canvas. Headline "It's time." in Inter at approximately 64px. Surrounded by floating sticker-style icons (laptop, cat tag, bicycle, coffee mug, calendar) in playful asymmetric arrangement. Laptop screenshot showing actual Notion Calendar UI from January 2024 below the hero.

What it commits to: asymmetric sticker-cluster hero (signals audience: stickers = creative/individual); product authenticity (real screenshot of real UI); keyboard-first interaction philosophy.

What it avoids: every-feature-on-the-marketing-page; decorative chrome; color-coding as decoration (strictly semantic); stock business-meeting photography.

#### Figma - figma.com


White canvas. Hero is a collage of designer moodboard cards (a "Rhythm Dance" black card, a workshop photo card, a "FOOD WORKS" magenta-yellow editorial card). Headline "Make anything possible, all in Figma" sans bold at approximately 44px in dark gray. The Figma site is itself a canvas for users' work, not for Figma's own palette. Color register changes seasonally as featured user work changes.

What it commits to: the gallery of real user creations as the primary illustration; product screenshots and user work dominate; Config gets a distinct visual identity every year by an outside studio.

What it avoids: custom mascot; fixed brand illustration set; stock photography; stock developer faces.

### 2.2 Status-page tier

Distinctive status pages are an under-exploited brand surface. Generic Atlassian Statuspage clones blend into the noise. Opinionated ones function as quiet brand reinforcement.

#### Stripe Status - status.stripe.com


Deep navy canvas (`~#0A2540`). Brand-green "STATUS" wordmark next to "stripe" logo. Single uptime bar with 90-day chart at approximately 99.999% uptime, one orange degradation tick visible. Five status cards below in a 1+1 column layout ("System status" left, "Active incidents" right). The cleanest status-page reference of the four captured.

#### Notion Status - status.notion.so


White canvas. Mint-green "We're fully operational" banner at top. Below: 90-day per-service uptime bars in green with yellow/orange tick marks where degradation occurred. Eight services listed (Core, AI, Apps, Calendar, Mail, Sites, Marketplace, API). Sans-serif throughout. Mint accent `~#2EBD85`. The segmentation by product surface (Apps vs Calendar vs Mail vs Sites) reflects that Notion is now a multi-product portfolio; the status page is a window into the company's operational shape.

### 2.3 Studio tier

#### Pentagram - pentagram.com


Full-page capture shown to reveal the grid rhythm. Tan-mushroom canvas (`~#998877`) full-bleed at top. Black serif "Pentagram" wordmark top-left. Rotating-banner card center: "We design Brand Identity for Arts and Culture" with an interactive pill picker. Below: black band with massive serif "Our Future is the Ultimate Project." Full reveals the project grid as muted color-swatch tiles per project (mauve, ochre, cobalt, magenta, stone).

What it commits to: variable typography across 25+ independent partners under one banner; flexible modular grid; large-format project imagery as the argument with copy as footnote.

What it avoids: a single house style; a central "Pentagram look"; decorative motion; studio-as-celebrity photography on the homepage.

#### Studio Sutherl& - studio-sutherland.co.uk


Tan-mushroom canvas matching Pentagram's exact register (`~#A89788`), but where Pentagram uses image tiles, Sutherl& uses a type-only list of client names in serif: "Sutherl&", "Szczęsny", "i.Detroit", "Prostate Cancer UK", "The Arts Society", "Bikedot", "Salvation Army HQ", "Sinfonia Smith Square", "Somos Brasil", "Nat-ional-Re-con-struct-ion", "Start-rite Shoes". The ampersand and the hyphenated breaks are intentional typographic play.

What it commits to: ampersand-as-letter (`Sutherl&`) as the visual pun running through everything; 50+ Royal Mail stamps and 82 D&AD projects as the implied authority; type as content.

What it avoids: web flourish; animated case study reveals; industry-buzzword copy.

---

## 3. The anti-pattern shelf

### Material 3 - m3.material.io


Light pink-lavender hero. "Material Design" headline in light gray-purple sans-serif at very thin weight. To the right: a collage of three phone mockups in different pastel shades showing Material 3 apps (a tracker, a Spotify-like player, a "Serafina" lockscreen). The Google sans is unmistakable. Below: "Material at Google I/O 2026" banner.

Why it is an anti-pattern: Material 3's tonal palette produces a near-uniform primary blue across implementations because most adopters default to the seed colour without committing to a custom palette. The Material elevation/shadow system is so distinctive that any dashboard using it reads instantly as "this was made with MD3 defaults," <!-- audit-skip-start --> the visual equivalent of "delve into the landscape." <!-- audit-skip-end --> The Material FAB (floating action button) bottom-right is a tell so consistent that designers use it as a one-glance MD3 identifier.

### Tabler - tabler.io


Light gray-purple gradient hero. Sans bold black headline "Build your next admin panel in minutes, not weeks." Below: dashboard mockup with multiple stat cards, charts, tables. Cookie/promo modal lower-left. Top banner "SPECIAL OFFER - Get all Tabler's products for just $89. Save $47!" with countdown.

Why it is an anti-pattern: deliberately generic brand personality (Tabler is a utility template, not a designed product). Component-coverage philosophy with 6,146 icons rather than curated set. Treats all devices and modes equally with light/dark parity as equal parents. Promotional density above the fold: countdown banner, primary CTA, secondary CTA, docs link, newsletter modal, and a logo strip - six elements where Linear or Vercel show two or three. Pastel violet-pink-lavender at hero density confirmed (`~#F5EEF8` to `~#EBE6F2`).

### Salesforce - salesforce.com


Access Denied page. Single line `Reference #18.4df90a17.1779712249.3abd93e`. Times New Roman default browser font.

Why it is included: the refusal IS the anti-pattern evidence. Enterprise SaaS marketing operates behind Akamai bot-detection; the marketing site is a gated funnel, not a public face. Sovereign-tech that mimics this defensive funnel posture inherits the defensive register. Salesforce, SAP, and ServiceNow all returned identical Akamai refusal pages on captured headless visits.

---

<!-- audit-skip-start -->
## 4. Vocabulary expansion

Per-tell explanations with concrete redirects. Use when a draft fails the audit and the rule's summary table is not enough.

### 4.1 Color tells

**Purple-to-pink hero gradient.** The single most-cited tell. Anthropic's own cookbook bans it explicitly as "clichéd color schemes (particularly purple gradients on white backgrounds)." Redirect: commissioned brand gradient where the gradient itself is an asset (Stripe ribbon, Vercel orange radial), or single saturated accent on a near-monochrome spine.

**Indigo-violet primary accents.** Tailwind's `indigo-500`/`violet-600`/`blue-500` as the action color. Redirect: 31C orange or 31C blue per brand authority; if neither fits the surface, pick one bold accent and commit (Linear chartreuse, Plausible indigo `~#5D4FE7`, PostHog hedgehog-yellow, Mercury indigo `~#5266EB`).

**Legacy ChatGPT emerald `#10A37F`.** Still associated by readers with "this is an AI product." Redirect: nothing in the green band as primary unless the surface is genuinely OpenAI-affiliated (which 31C surfaces never are).

**The Tailwind neutral stack.** Light backgrounds in `slate-50` (`#F8FAFC`) or `gray-50`. Dark backgrounds in `zinc-900` (`#18181B`) or `gray-950` (`#030712`). Recognized in one second. Redirect: tune the neutrals toward the brand hue (Mercury's `#171721` is indigo-tinted near-black, not pure `#000`). Add a fractional saturation: greys at HSL S=4-8% rather than S=0%.

### 4.2 Typography tells

**Inter and Geist as the SaaS duopoly.** Inter is "the new Arial" in the SaaS register. Geist is the new statistical median for developer-aimed sites. Both are legitimate when used with full awareness; both are tells when used because they are the default.

Redirect for 31C corporate doctypes: GT Standard per `reference/corporate-style-guide.md`. Redirect for public web or dashboards: choose a font that signals 31C specifically. Geist is permitted with explicit commit to Vercel-adjacent register. IBM Plex family is the open-source fallback. Inter is forbidden as primary in any external-facing 31C artifact.

**Roboto, Open Sans, Lato, Poppins, Montserrat.** Budget-tier defaults. Anthropic's cookbook bans these specifically. Redirect: see above.

**Space Grotesk as the "I tried" upgrade.** Anthropic notes that the model still converges on common choices "(Space Grotesk, for example) across generations." Redirect: do not use Space Grotesk as a default substitute for Inter unless there is a defensible 31C-specific reason.

**Mid-weight monoculture.** `font-medium` headings (500) with `font-normal` body (400). Anthropic prescribes the inverse: weight extremes 100/200 vs 800/900, size jumps of 3x+. Redirect: pick two weights from opposite ends of the typeface family. Display 200 or 300 against body 700 or 800.

### 4.3 Layout tells

**Centered-hero stack.** Eyebrow + 64-pt centered headline + subhead + two CTAs centered below. The single most-replicated landing structure of 2024-2026. Redirect: asymmetric composition. Image left, sentence right. Sentence left, product visual right. Rule of thirds, not center alignment.

**Three-up icon-and-description feature cards.** Three identical-width `rounded-2xl` cards with small icon top-left, title, two lines of description. Redirect: heterogeneous cards. One large feature card at 2x the others. Or vertical features list, no cards at all. Or single hero feature with sub-features in supporting text.

**`rounded-2xl` (16px) on every card and button.** The shadcn default radius. Redirect: vary the radius. 4px on inputs. 8px on cards. 12px on hero panels. Or pill (~999px) on CTAs only, sharp corners elsewhere.

**Equal-size bento tiles.** "If all tiles are the same size, you have a card layout with rounded corners not bento grid." Redirect: at least one tile at 2x area of the smallest tile per bento view.

**Sidebar-left, table-center, button-group-top-right dashboard.** The shadcn dashboard archetype, v0's "Dashboard 01" template. Redirect: study Heading OS v8. Or Plausible's single-page no-tabs layout. Or Linear's collapsible-categories sidebar with no breadcrumbs.

### 4.4 Component tells

**The shadcn card composition.** Avatar (left) + title + one-line description + optional top-right action. Recognized in one second. Redirect: vary the composition per card class. Different layouts for testimonials vs team members vs list items.

**Lucide outline icons at 24px / 1.5px stroke / rounded caps.** PkgPulse: "many products end up looking visually identical." Redirect: custom icon system or curated subset of an alternative library at non-default weight (Phosphor heavy or Phosphor light, not regular).

**Pricing toggle (monthly/annual) + three-tier cards with "Recommended" outline.** Universal SaaS marketing furniture. Redirect: 31C does not sell self-serve; if pricing appears, it appears as a single transparent grid (PostHog precedent) or not at all.

### 4.5 Motion tells

**Framer Motion `type: "spring"` with default `bounce`.** "AI-generated UIs all share the same identifiable bouncy 'feel.'" Redirect: motion capped at 120ms linear hover-only (Grok's parametric); reserve longer motion only for the one signature interaction per surface.

**Stagger-reveal on page load.** Each card fades up by 0.05s offset. Redirect: no stagger. Either reveal all at once or animate a single signature element.

### 4.6 Asset tells

**AI-generated decorative illustrations tonally matched to the palette.** The Gamma giveaway. Redirect: per the asset rule. Stock people-photography banned; custom-generated illustration under 2-bit-monochrome or commissioned-style constraint. Real product screenshots, real Tribe photography, real customer-installation imagery permitted.

**Isometric 3D illustration return.** Midjourney-cheap. Redirect: do not.

**Default Unsplash stock with shallow DOF + cool grading.** Redirect: real photography or no photography.

### 4.7 Copy register tells (cross-references humanization.md)

**"Build the future" / "AI-powered" / "Reimagine X" taglines.** Center the technology, not the buyer. Redirect: name the buyer's specific problem. "ODUN.ONE classifies encrypted traffic at line rate without decryption" beats "AI-powered network intelligence."

**"Ship X. Build Y." imperative pairs.** Evil Martians study: "Each block starts with an incentive: 'Build faster', 'Run anywhere', 'Ship in seconds'." Easy to write, light on persuasion. Redirect: replace the imperative with the actual outcome named in operator vocabulary.

**Generic CTAs.** "Get started", "Learn more", "Try it free." Redirect: specific CTAs that name what the next step actually is. "Book the architecture review" not "Get started."

**Title Case For Every Heading.** Always banned. Use sentence case.
<!-- audit-skip-end -->

---

## 5. Audit checklists per artifact type

Each checklist supplements (does not replace) the fundamentals. Apply the fundamentals first, then the type-specific audit.

### 5.1 Investor or keynote deck

- Opener slide: states the claim in one sentence on a clean field. No centered-logo title slide. No "Thank You" closer; closer is the call to action in one line.
- Every slide title is an assertion specific to 31C on that day, not a chapter label.
- Money slide: one chart or number, full bleed, no decoration.
- At least one named specific (customer, region, partner, dated milestone, numeric value) per content slide.
- Real product screenshots, not stylised illustrations. If the product is not ready to show, the slide is typographic.
- Brand voice carries through visually: cover, divider slides, data treatment, closer all draw from the same authored system.
- No stock photography of generic offices, hands at keyboards, lightbulbs, gears, or three-people-pointing-at-a-laptop.
- Slide rhythm alternates: text-heavy slide followed by image-heavy or single-number slide.
- Precise numbers, never rounded to vanity.
- Brand authority: `datastore/products/odun-one/presentations/31C - ODUN.ONE Product Presentation (Master, 12-Apr-2026).pptx`.

### 5.2 Product or sales deck

All of the above, plus:

- The product screenshot is the hero of the relevant slide, not a decorative illustration.
- Feature claims paired with the underlying capability, not the marketing tagline.
- ODUN.ONE positioning observed: deep packet intelligence, not deep packet inspection. Tribe (never team, family, crew). DPI+ (next-generation, not legacy DPI). Five Core Principles named correctly.
- Competitor comparison (if present): grounded in actual capability per `datastore/intelligence/competitors/`, not assumed positioning.

### 5.3 Dashboard

Canonical implementation reference: Heading OS v8 per `reference_heading_os_v8_design` and `reference_heading_os_v8_pulse` memories. Specifically:

- OKLCH tokens for the color system (not RGB or HSL).
- Geist Sans + Geist Mono primary typography stack.
- Light and dark variants, dark canonical, light derived.
- Pulse / Day / Inbox / Conversations page taxonomy.
- Greeting H1 + Next focal card + Approvals queue + KPI strip per the Pulse reference.

Beyond v8 compliance, every dashboard surface must satisfy:

- At least one panel above the fold contains real operator data with real labels (Plausible reference test).
- Heterogeneous card weight: at least one panel at 2x the area of the smallest panel.
- One signature interaction declared per surface.
- No shadcn sidebar + 4-stat-card row + chart card + data table + `+ New` composite (v0 "Dashboard 01" template).
- Color register: one accent, delta-E ≥12 between non-adjacent elements.

### 5.4 Briefing HTML or PDF

Includes intel-briefing-newsletter, market-brief, ceo-intel, dashboard outputs, weekly-review, dossier briefs.

- GT Standard typography (per `feedback_corporate_fonts_always`).
- Dual-mode (light + dark) where the artifact lives in a viewer that supports mode toggle. Single-mode where it ships as a fixed PDF.
- 31C orange corner block in the page header per `feedback_design_standards`.
- Self-contained single file per `self-contained-single-file-artifacts` brain principle. Inline CSS, inline SVG, inline images, no external CDN.
- Validated layout, typography, spacing per `feedback_pdf_document_design` memory.
- No purple-to-pink gradients. No pastel violet-pink-lavender at hero density.
- Cover page: 31C identity unambiguous in the first second.

### 5.5 Status page

Reference exemplars: status.stripe.com (navy canvas, brand-green wordmark, single 90-day uptime bar), status.notion.so (mint-green operational banner, per-product-surface uptime grid).

- Confident declarative language for overall state ("We're fully operational"), not colour-coded pill badges.
- Numeric precision visible at all times. 99.65% uptime, not "operational."
- Forward-looking maintenance schedule if scheduled maintenance exists.
- Geographic or service granularity that matches 31C's actual operational shape, not a compressed "edge network" pseudo-state.
- Historical incident view: heat-map (Linear precedent) or per-service timeline, not just current state.

### 5.6 Customer-facing UI (ODUN.ONE, ODIN AI, TrustONE)

- Brand authority: ODUN.ONE Product Presentation governs visual identity. Dashboards and customer-facing UIs are the consumer-facing implementation of the brand authority.
- Real operator data populates real operator labels in every demo or screenshot used to market the product. No "Sarah M., Marketing Director" testimonials.
- One signature interaction per surface. Power-user signals (keyboard shortcut overlay, monospace IDs visible in row metadata, signal-strength indicators on real captures) are permitted and encouraged.
- Density commits to a single tier (operator-fluent dense like Datadog, or operator-novice sparse like Plausible). Mixed density is the anti-pattern.

### 5.7 Public web / marketing site

- 31C brand identity visible above the fold without needing the logo to identify the company. If the site without the logo could be from any sovereign-tech vendor in the category, the visual register has failed.
- One hero element per page. Maximum 4 discrete elements above the fold (Pass 4 empirical: Linear, Vercel, Stripe, Plausible all stay under 4).
- "Trusted by" logo strip is permitted but it must be real customers with cleared distribution. No fake logos.
- Footer construction: 31C-specific, not the universal four-column-link-grid pattern.

---

## 6. Workspace integration

The rule (`.claude/rules/visual-design-discipline.md`) is always-active. It loads in every session and applies to every visual artifact in the in-scope tier per the carve-out.

When invoked by a producing skill, the skill is responsible for the audit before declaring done. The producing skill must include the validation line in its completion message:

> Visual register: [register name]. AI tells found: clean / N findings (and a one-line summary of redirects applied). Specificity density: X named specifics per artifact.

For artifacts produced outside a skill (direct CEO request, manual production by Claude in conversation), Claude is responsible for applying the audit before presenting.

Companion script status: `scripts/visual-discipline-check.py` is a planned follow-up. Until it lands, the audit is manual against the checklists in §5.

---

## 7. Sources

Pass 1 research briefs (consolidated source list):

- Bhuwan Garbuja, "Why every AI-generated website looks exactly the same" - bhuwan-garbuja.com/blog/why-all-websites-look-the-same/
- Trilogy AI, "Fixing Visual AI Slop" - trilogyai.substack.com/p/fixing-visual-ai-slop
- The Adpharm, "Claude Design produces AI slop unless you tell it not to" - theadpharm.com/insights/claude-design-without-the-ai-slop-look
- Anthropic, "Prompting for frontend aesthetics" - platform.claude.com/cookbook/coding-prompting-for-frontend-aesthetics
- MindStudio, "How to Avoid AI Slop When Using Claude Design" - mindstudio.ai/blog/claude-design-avoid-ai-slop-design-system
- LogRocket, "Linear design: The SaaS design trend that's boring and bettering UI" - blog.logrocket.com/ux-design/linear-design/
- Evil Martians, "We studied 100 dev tool landing pages" - evilmartians.com/chronicles/we-studied-100-devtool-landing-pages-here-is-what-actually-works-in-2025
- PkgPulse, "Lucide vs Heroicons vs Phosphor 2026" - pkgpulse.com/guides/lucide-vs-heroicons-vs-phosphor-react-icon-libraries-2026
- MadeGoodDesigns, "Inter Font Review" - madegooddesigns.com/inter-font/
- Vercel, "Geist Font" - vercel.com/font
- Magic UI, "Framer Motion guide" - magicui.design/blog/framer-motion-react
- StrategyU, "Stripe Sessions 2026 keynote analysis" - strategyu.co/stripe-sessions-2026-keynote-communication/
- Refactoring UI - refactoringui.com/
- Presentation Zen - presentationzen.com/
- Tom Critchlow, "Good slides reduce complexity" - newsletter.seomba.com/p/good-slides-reduce-complexity

Pass 4 visual capture manifest: `outputs/research/_drafts/exemplars/manifest.json` (30 targets, 24 useful captures + 6 access-failure evidence captures).

Pass 5 synthesis with full source enumeration: `outputs/research/2026-05-25_design_visual-design-discipline-research.md`.

Council transcript: `outputs/operations/council/2026-05-25_council_161558_ai-vs-human-design-tells.md`.

Odin brain principles cited in Pass 3: `specificity-density-beats-structural-patterns`, `personal-anecdote-is-structural-defeat-of-ai-detection`, `escape-competition-through-authenticity`, `ai-rhythm-is-the-tell`, `linkedin-register-detector-blind-spot`, `simplicity-signals-mastery`, `headline-first-communication`, `winstons-star-makes-ideas-stick`, `peak-end-rule-overrides-experience`, `one-language-processor-constraint`, `compress-before-consuming`, `noise-is-toxic-distilled-knowledge-endures`, `match-output-format-to-content-shape`, `self-contained-single-file-artifacts`, `agentic-output-is-content-shape`, `20260318140400-navigation-principle-states-not-goals`, `harbors-not-horizons`, `ceo-communication-force-multiplier`, `presentation-anatomy-winston-protocol`, `specificity-and-stance-defeat-ai-detection`, `narrative-craft-emotional-throughline`, `multi-voice-content-architecture`.

# OSINT Research Streams - MODE: TECHNOLOGY

Consumed by: `.claude/skills/osint/SKILL.md` Phase 1 when target mode resolves to `technology`.

Last Updated: 2026-06-10

**Use resolved plan from Phase 0.5** - substitute `canonical.name`, `canonical.aliases`, `canonical.category`, `vendors[].name`, `vendors[].github_org`, and `communities[].identifier` into the queries below where `[technology]` appears. If a field is unresolved, fall back to the literal target string.

7 streams. Execute in parallel.

**Stream 1 - Product Architecture & Capabilities**
- WebSearch: "[technology] architecture features capabilities specifications datasheet"
- Perplexity: `python scripts/perplexity-research.py "[technology] architecture capabilities technical analysis"`

**Stream 2 - Patent & IP Landscape**
- WebSearch: "[technology] patent application filed site:patents.google.com"
- WebSearch: "[technology] intellectual property innovation R&D"

**Stream 3 - Technical Community Signals**
- WebSearch: "[technology] site:github.com OR site:stackoverflow.com OR site:reddit.com"
- WebSearch: "[technology] developer adoption community feedback review"

**Stream 4 - Hiring & Talent Signals**
- WebSearch: "[technology vendor] hiring engineer developer job posting"
- Analyze job postings for architecture and technology stack clues

**Stream 5 - Standards & Protocols**
- WebSearch: "[technology] standard RFC 3GPP ETSI specification"
- WebSearch: "[technology] interoperability integration API"

**Stream 6 - Comparison & Alternatives**
- WebSearch: "[technology] vs comparison alternative benchmark"
- WebSearch: "[technology] review evaluation analysis"

**Stream 7 - Market Adoption**
- WebSearch with Telecom & DPI domains: "[technology] deployment adoption customer use case"
- WebSearch: "[technology] case study implementation production"

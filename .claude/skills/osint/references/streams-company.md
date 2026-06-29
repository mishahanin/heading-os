# OSINT Research Streams - MODE: COMPANY

Consumed by: `.claude/skills/osint/SKILL.md` Phase 1 when target mode resolves to `company`.

Last Updated: 2026-06-10

**Use resolved plan from Phase 0.5** - substitute `canonical.name`, `canonical.aliases`, `canonical.parent`, `social.x_handle`, `people[].name`, and `competitors[].name` into the queries below where `[company]` appears. If a field is unresolved, fall back to the literal target string.

10 streams. Execute in parallel.

**Stream 1 - Corporate Identity & Ownership**
- WebSearch: "[company] founded headquarters ownership structure subsidiary"
- WebSearch: "[company] CEO board directors leadership management team"
- If possibly public: WebSearch with Markets & Finance domains: "[company] SEC filing annual report shareholders"

**Stream 2 - Financial Intelligence**
- WebSearch with Markets & Finance domains: "[company] revenue earnings financial results funding valuation"
- Perplexity: `python scripts/perplexity-research.py --domains "bloomberg.com,ft.com,reuters.com,wsj.com,cnbc.com,crunchbase.com,pitchbook.com" "[company] financial performance revenue funding valuation"`
- If public: WebSearch: "[company] earnings call transcript quarterly results"

**Stream 3 - Technology Stack & Product**
- WebSearch: "[company] product technology platform architecture"
- WebSearch: "[company] site:github.com OR engineering blog"
- WebSearch: "[company] job posting engineer developer" (tech stack signals from hiring)
- WebSearch: "[company] patent application filed" (innovation signals)

**Stream 4 - Leadership & Key People**
- WebSearch: "[CEO name] [company] interview keynote conference"
- WebSearch: "[company] executive hire departure leadership change"
- Cross-reference names against `crm/contacts/` and `context/people.md`

**Stream 5 - Legal, Regulatory & Compliance**
- WebSearch: "[company] lawsuit litigation regulatory fine sanction"
- WebSearch: "[company] data breach security incident"
- If relevant: WebSearch with Geopolitics & Defense domains: "[company] government contract sanctions"

**Stream 6 - Digital Footprint & Technical Indicators**
- WebSearch: "[company] technology stack infrastructure cloud provider"
- WebSearch: "[company] DNS SSL certificate Cloudflare AWS Azure"
- WebSearch: "[company] site:builtwith.com OR site:wappalyzer.com"

**Stream 7 - News & Press Intelligence**
- WebSearch with General Tech domains: "[company] news announcement launch"
- WebSearch with relevant sector domains (Cybersecurity, Telecom & DPI): "[company] product launch partnership"

**Stream 8 - Competitive Positioning & Market**
- Perplexity: `python scripts/perplexity-research.py "[company] competitors market position market share strengths weaknesses"`
- WebSearch: "[company] vs comparison alternative review"

**Stream 9 - Partnership Ecosystem**
- WebSearch: "[company] partnership alliance integration reseller distributor"
- WebSearch: "[company] customer case study deployment"
- Check if company appears in `context/pipeline.md` or `context/people.md`

**Stream 10 - Market Sentiment & Analyst Coverage**
- WebSearch: "[company] analyst report market outlook forecast"
- WebSearch: "[company] glassdoor review reputation" (internal culture signals)
- If public: WebSearch with Markets & Finance domains: "[company] price target analyst upgrade downgrade"

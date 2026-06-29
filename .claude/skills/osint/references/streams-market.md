# OSINT Research Streams - MODE: MARKET

Consumed by: `.claude/skills/osint/SKILL.md` Phase 1 when target mode resolves to `market`.

Last Updated: 2026-06-10

**Use resolved plan from Phase 0.5** - substitute `canonical.name`, `canonical.region`, `canonical.key_terms`, and `key_players[].name` into the queries below where `[market/region]` appears. If a field is unresolved, fall back to the literal target string.

8 streams. Execute in parallel.

**Stream 1 - Market Size & Dynamics**
- WebSearch with Markets & Finance domains: "[market/region] market size growth forecast CAGR"
- Perplexity: `python scripts/perplexity-research.py --domains "bloomberg.com,ft.com,reuters.com,tradingeconomics.com" "[market/region] economic outlook investment trends"`

**Stream 2 - Regulatory Landscape**
- WebSearch with Geopolitics & Defense domains: "[market/region] regulation policy cybersecurity telecom data sovereignty"
- WebSearch: "[market/region] government procurement tender ICT"

**Stream 3 - Competitive Dynamics**
- WebSearch with relevant sector domains: "[market/region] DPI deep packet inspection telecom cybersecurity vendors"
- Cross-reference against `reference/dpi-market-intelligence.md`

**Stream 4 - Digital Infrastructure**
- WebSearch with Telecom & DPI domains: "[market/region] 5G deployment broadband internet penetration"
- WebSearch: "[market/region] data center cloud infrastructure sovereign"

**Stream 5 - Investment & Funding Flows**
- WebSearch with Markets & Finance domains: "[market/region] investment technology venture capital funding"
- WebSearch: "[market/region] FDI foreign direct investment technology"

**Stream 6 - Key Decision Makers**
- WebSearch: "[market/region] ministry telecom communication digital transformation"
- WebSearch: "[market/region] telecom operator tier 1 carrier ISP"
- Cross-reference against `context/people.md` and `crm/contacts/`

**Stream 7 - Geopolitical Context**
- WebSearch with Geopolitics & Defense domains: "[market/region] geopolitics alliance sovereignty"
- Use region-specific domain groups from `reference/search-domains.md` (GCC, CIS, Africa)
- Read `reference/geopolitical-landscape.md` for baseline context

**Stream 8 - 31C Opportunity Assessment**
- WebSearch: "[market/region] the legacy incumbent DPI vendor replacement"
- Cross-reference `context/pipeline.md` for existing deals
- Cross-reference `context/strategy.md` for GTM phasing

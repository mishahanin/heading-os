# OSINT Research Streams - MODE: PERSON

Consumed by: `.claude/skills/osint/SKILL.md` Phase 1 when target mode resolves to `person`.

Last Updated: 2026-06-10

**Use resolved plan from Phase 0.5** - substitute `canonical.name`, `canonical.aliases`, `canonical.current_org`, `social.x_handle`, `social.github_username`, and `affiliations[].organization` into the queries below where `[person]` appears. If a field is unresolved, fall back to the literal target string.

8 streams. Execute in parallel.

**Stream 1 - Professional Identity**
- WebSearch: "[person] [known company] title role biography"
- WebSearch: "[person] LinkedIn profile" (blocked_domains only)
- Check `crm/contacts/` for existing CRM file

**Stream 2 - Career History & Trajectory**
- WebSearch: "[person] previously worked former role career"
- Perplexity: `python scripts/perplexity-research.py "[person] career history professional background companies roles"`

**Stream 3 - Public Presence & Thought Leadership**
- WebSearch: "[person] conference speaker keynote presentation panel"
- WebSearch: "[person] interview podcast published article opinion"
- WebSearch: "[person] patent inventor application"

**Stream 4 - Board Seats & Advisory Roles**
- WebSearch: "[person] board director advisory trustee"
- WebSearch: "[person] investor angel venture fund portfolio"

**Stream 5 - Publications & Academic**
- WebSearch: "[person] site:scholar.google.com OR site:researchgate.net OR published paper"
- WebSearch: "[person] book author"

**Stream 6 - Digital Footprint**
- WebSearch: "[person] twitter github blog personal website"
- WebSearch: "[person] [company] email contact" (only if relevant to business engagement)

**Stream 7 - News & Media Mentions**
- WebSearch: "[person] news interview quote mentioned"
- WebSearch: "[person] award recognition honor"

**Stream 8 - Network & Relationships**
- WebSearch: "[person] co-founded partner colleague collaboration"
- Cross-reference against `context/people.md` and `context/pipeline.md`

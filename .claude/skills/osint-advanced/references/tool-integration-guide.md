# OSINT-Advanced Tool Integration Guide

Per-tool query patterns, response parsing, confidence scoring, rate limits, and fallback chains. Organized by stream for quick reference during investigation execution.

Consumed by: `.claude/skills/osint-advanced/SKILL.md` (Phase 1 stream execution)
Last Updated: 2026-03-21

---

## Sanctions & Compliance Stream

### OpenSanctions (Web Search Page)
- **Query:** `WebFetch https://www.opensanctions.org/search/?q={url_encoded_query}`
- **Response:** HTML page with entity cards
- **Key data in HTML:** Entity name, type (Person/Company/Organization), dataset badges (OFAC, EU FSF, etc.), country associations
- **Confidence scoring:** Multiple dataset matches = HIGH, single dataset = MEDIUM, partial name match = LOW
- **Rate limit:** No documented limit for web search
- **Failure mode:** Page loads but shows "No results" text
- **Note:** The API endpoint (`api.opensanctions.org`) returns 401 without an API key. Use the web search page instead -- it returns rich HTML that Claude can parse.

### OCCRP Aleph
- **Query:** `WebSearch "{target}" site:aleph.occrp.org`
- **Note:** API v2 endpoint (`aleph.occrp.org/api/2/search`) returned 404 during live validation. The web interface is a SPA (empty via WebFetch). Use WebSearch with `site:` operator as the only reliable access method.
- **Extract from WebSearch results:** Entity names, document types, collection sources, jurisdiction data

### ICIJ Offshore Leak Database
- **Query:** `WebSearch "{target}" site:offshoreleaks.icij.org`
- **Note:** The web interface is JS-rendered. WebFetch returns empty content.
- **Extract from WebSearch results:** Entity names, country associations, intermediary connections
- **Coverage:** Panama Papers, Paradise Papers, Pandora Papers, Offshore Leaks

---

## Corporate Registry Stream

### OpenCorporates
- **Query:** `WebSearch "{company}" site:opencorporates.com`
- **Note:** WebFetch returns CAPTCHA wall. Must use WebSearch fallback.
- **Extract:** Company number, jurisdiction, status (Active/Inactive/Dissolved), registered address, officer names
- **Coverage:** 140+ jurisdictions worldwide

### EDGAR (SEC)
- **Query:** `WebSearch "{company}" site:sec.gov 10-K OR 10-Q OR 8-K`
- **Note:** The EFTS API (`efts.sec.gov`) returns 403 to automated requests. Use WebSearch.
- **Alternative:** `WebFetch https://www.sec.gov/cgi-bin/browse-edgar?company={company}&CIK=&type=10-K&dateb=&owner=include&count=10&search_text=&action=getcompany` (legacy CGI -- may return HTML listing)
- **Extract:** Filing type, filing date, company CIK, direct links to filings

### CrunchBase
- **Query:** `WebSearch "{company}" site:crunchbase.com`
- **Extract:** Total funding raised, latest round, lead investors, founding date, employee count, key people

### AI HIT
- **Query:** `WebSearch "{company}" site:aihitdata.com`
- **Note:** 12.5M+ company profiles. Free access with registration.
- **Extract:** Company profile, executive changes, business relationships

---

## Email Intelligence Stream

### EmailRep
- **Query:** `WebSearch "{email}" site:emailrep.io` (API returns 429 without key)
- **Note:** The direct API endpoint (`emailrep.io/{email}`) returns 429 rate-limit errors without an API key header. Use WebSearch with `site:` as fallback.
- **Extract from WebSearch:** Reputation mentions, breach associations, suspicious indicators
- **If API key available:** Add header `Key: {api_key}` to WebFetch request for JSON response

### Hunter.io (API -- key available)
- **Query:** `curl -s "https://api.hunter.io/v2/domain-search?domain={domain}&api_key=$(load_api_key('HUNTER_API_KEY'))"`
- **Response:** JSON with `data.emails[]` array
- **Key fields:** `data.emails[].value` (email), `data.emails[].type` (personal/generic), `data.emails[].confidence` (0-100), `data.emails[].sources[].domain`
- **Additional endpoints:** `/v2/email-finder` (find specific person), `/v2/email-verifier` (verify email), `/v2/email-count` (no auth)
- **Rate limit:** 25 domain searches/month, 50 verifications/month (free tier)
- **Failure mode:** `{"errors":[{"details":"..."}]}` for invalid domain or quota exceeded
- **Note:** API key in `.env` as `HUNTER_API_KEY`. Returns organization data, email patterns, and individual addresses with confidence scores.

### HIBP (API -- key available)
- **Query:** `curl -s -H "hibp-api-key: $(python3 -c \"import os,sys;sys.path.insert(0,'.');from scripts.utils.api import load_api_key;print(load_api_key('HIBP_API_KEY'))\")" -H "user-agent: 31C-OSINT" "https://haveibeenpwned.com/api/v3/breachedaccount/{email}?truncateResponse=false"`
- **Response:** JSON array of breach objects
- **Key fields:** `[].Name`, `[].Domain`, `[].BreachDate`, `[].PwnCount`, `[].DataClasses[]`, `[].IsVerified`
- **Additional endpoints:** `/pasteaccount/{email}` (pastes), `/breacheddomain/{domain}` (domain breaches), `/stealerlogsbyemail/{email}` (stealer logs, Pwned 5+ only)
- **Rate limit:** Subscription-based, 429 with retry-after header
- **Failure mode:** 404 = account not breached (this is a GOOD result, not an error)
- **Note:** API key in `.env` as `HIBP_API_KEY`. Always include `user-agent: 31C-OSINT` header or get 403.

### VoilaNorbert
- **Query:** `WebSearch "{person}" "{company}" email site:voilanorbert.com`
- **Note:** API requires signup. Free tier: 50 leads. SDKs for Node.js, PHP, Python, Ruby.
- **Extract:** Verified email addresses, email format patterns

### Email Checker
- **Query:** `WebSearch "{email}" site:email-checker.net`
- **Note:** Completely free single email validation at email-checker.net/validate. No signup needed.
- **Extract:** Email validity status, mail server response

---

## Infrastructure Recon Stream

### crt.sh
- **Query:** `WebFetch https://crt.sh/?q={domain}&output=json`
- **Response:** JSON array of certificate objects
- **Key fields:** `[].issuer_ca_id`, `[].issuer_name`, `[].common_name`, `[].name_value` (domains covered), `[].not_before`, `[].not_after`
- **Rate limit:** None documented
- **Failure mode:** Returns empty array `[]` for domains with no certificates
- **Note:** Best for subdomain discovery -- `name_value` often reveals subdomains not found by DNS

### urlscan.io
- **Query:** `WebFetch https://urlscan.io/api/v1/search/?q=domain:{domain}`
- **Response:** JSON with `results[]` array
- **Key fields:** `results[].page.url`, `results[].page.domain`, `results[].page.ip`, `results[].page.server`, `results[].stats.` (resource counts)
- **Rate limit:** Free API, no auth required
- **Failure mode:** Returns `{"results": [], "total": 0}` for unscanned domains
- **Note:** Shows technologies, outbound connections, trackers, screenshots

### Shodan
- **Query:** `WebSearch "{domain}" site:shodan.io`
- **Note:** API requires key ($49+ one-time). WebSearch shows partial results.
- **Extract:** Open ports, running services, hosting provider, SSL info, potential vulnerabilities

### BuiltWith
- **Query:** `WebSearch "{domain}" site:builtwith.com`
- **Extract:** CMS, frameworks, analytics tools, CDN, hosting provider, JavaScript libraries

### DNSlytics
- **Query:** `WebSearch "{domain}" site:dnslytics.com`
- **Note:** Freemium. API available for paid tier. 25M+ IPs, 330M+ domains.
- **Extract:** Reverse IP results, DNS records, hosting provider, CIDR allocation

### VirusTotal (API -- key available)
- **Query:** `curl -s -H "x-apikey: $(load_api_key('VIRUSTOTAL_API_KEY'))" "https://www.virustotal.com/api/v3/domains/{domain}"`
- **Response:** JSON with `data.attributes`
- **Key fields:** `data.attributes.last_dns_records[]`, `data.attributes.whois`, `data.attributes.reputation`, `data.attributes.last_analysis_stats` (harmless/malicious/suspicious/undetected counts), `data.attributes.categories`
- **Additional endpoints:** `/api/v3/ip_addresses/{ip}`, `/api/v3/files/{hash}`, `/api/v3/search?query={query}`
- **Rate limit:** 4 req/min, 500/day (free key)
- **Failure mode:** 401 = bad key, 429 = rate limited, 404 = not found
- **Note:** API key in `.env` as `VIRUSTOTAL_API_KEY`. Google-owned. Returns DNS, WHOIS, reputation, detection stats, categories.

---

## Threat Intelligence Stream

### MITRE ATT&CK
- **Query:** `WebSearch site:attack.mitre.org "{target}"`
- **Alternative:** `WebFetch https://attack.mitre.org/groups/` (static page, lists all groups)
- **Extract:** Group profiles (aliases, target sectors, techniques used, associated malware, operations)
- **Note:** Static HTML, works reliably via WebFetch

### SOCRadar LABS
- **Query:** `WebSearch site:socradar.io "{target}" threat actor`
- **Extract:** Threat actor profiles, TTPs, recent campaigns, geographic targets

### Pulsedive
- **Query:** `WebSearch site:pulsedive.com "{query}"` (API returns 429 without key)
- **Note:** API endpoint requires API key for access. Use WebSearch fallback.
- **Extract from WebSearch:** Threat indicators, risk scores, related IoCs

---

## Username & People Search Stream

### NameCheckup
- **Query:** `WebFetch https://namecheckup.com/wp-json/namecheckup/v1/check/{username}`
- **Response:** JSON with platform availability
- **Note:** Free REST API. No auth required. Checks 40+ TLDs and 20+ social platforms.
- **Rate limit:** Unlimited (no documented limit)

### PeekYou
- **Query:** `WebSearch "{person}" site:peekyou.com`
- **Note:** Completely free. 10M+ monthly searches. US-focused.
- **Extract:** Social profiles, age, location, related people

### That's Them
- **Query:** `WebSearch "{person}" site:thatsthem.com`
- **Note:** 10 free lookups/day. 2.2B records. US-focused.
- **Extract:** Phone, email, address, age, associated people

### WebMii
- **Query:** `WebSearch "{person}" site:webmii.com`
- **Note:** Free. Aggregates public web information.
- **Extract:** Web visibility score, social profiles, mentions

---

## Social Media OSINT Stream

### SowSearch (Facebook)
- **Query:** `WebSearch "{person}" site:sowsearch.info OR site:facebook.com`
- **Note:** Open source Facebook search query builder. Moved from sowdust.github.io to sowsearch.info.
- **Extract:** Public profiles, posts, groups, check-ins

### Spoonbill.io
- **Query:** `WebSearch "{person}" site:spoonbill.io`
- **Note:** Tracks social profile changes (Twitter, GitHub, Product Hunt). Free.
- **Extract:** Profile bio changes, name changes, handle changes over time

### Tinfoleak
- **Query:** `WebSearch "{person}" site:tinfoleak.com`
- **Note:** Twitter OSINT dossier generator. May be degraded by Twitter/X API changes post-2023.
- **Extract:** Twitter geolocation, device info, hashtag analysis, activity patterns

---

## Data Breach Stream

### HIBP (API)
- (See Email Intelligence Stream above for full API documentation)
- **For domain searches:** Use `/breacheddomain/{domain}` endpoint
- **For stealer logs:** Use `/stealerlogsbyemail/{email}` (requires Pwned 5+ subscription)

### DeHashed (API)
- **Query:** `curl -s -X POST "https://api.dehashed.com/v2/search" -H "Dehashed-Api-Key: {DEHASHED_API_KEY}" -H "Content-Type: application/json" -d '{"query":"email:{target}","size":100,"page":1,"de_dupe":true}'`
- **Response:** JSON with `balance`, `total`, `entries[]`
- **Search fields:** `email:`, `username:`, `name:`, `domain:`, `ip_address:`, `phone:`
- **Note:** API key in `.env` as `DEHASHED_API_KEY`. Max 10000 results per page.

### LeakCheck
- **Query:** `WebSearch "{target}" site:leakcheck.io`
- **Note:** 7B+ records. API available with paid plans ($2.99/day+). Has Telegram bot.
- **Extract:** Breach sources, exposed data types

### SnusBase
- **Query:** `WebSearch "{target}" site:snusbase.com`
- **Note:** Paid membership required. API (2048 req/day) included with membership. Docs: docs.snusbase.com
- **Extract:** Email, name, username, IP, phone, hash matches

---

## Fact Check & Verification Stream

### Wayback Machine
- **Query:** `WebFetch https://archive.org/wayback/available?url={target_url}`
- **Response:** JSON
- **Key fields:** `archived_snapshots.closest.available` (boolean), `archived_snapshots.closest.url` (snapshot URL), `archived_snapshots.closest.timestamp`
- **Rate limit:** None for availability API
- **Failure mode:** `{"archived_snapshots": {}}` when no snapshot exists
- **Note:** Use to verify historical content, check deleted pages, validate claims about past statements

---

## Tools Requiring Manual Access

These tools cannot be automated via WebFetch and should be listed in the "Manual Investigation Recommended" section of every brief.

| Tool | Why Manual | What to Provide to User |
|------|-----------|------------------------|
| Maigret | CLI tool, requires local install | `maigret {username} --all-sites` |
| Holehe | CLI tool, requires local install | `holehe {email}` |
| PimEyes | Face upload required, blocks automation | URL: `https://pimeyes.com` |
| FaceCheck.ID | Face upload required | URL: `https://facecheck.id` |
| TinEye | Blocks automated requests (403) | URL: `https://tineye.com` with image URL |
| Liveuamap | JS-rendered, blocks WebFetch (403) | URL: `https://liveuamap.com` with region |
| Intelligence X | Requires auth + JS | URL: `https://intelx.io` (50 free lookups/day) |

---

## Rate Limits Summary

| Tool | Free Tier Limit | Reset |
|------|----------------|-------|
| OpenSanctions | Unlimited search | N/A |
| OCCRP Aleph | Unlimited (no auth) | N/A |
| Hunter.io | 25 searches/month, 50 leads | Monthly |
| Intelligence X | 50 lookups/day | Daily |
| SecurityTrails | 50 lookups/month | Monthly |
| HIBP | API key required ($3.50/month+) | Subscription |
| DeHashed | API key + credits required | Credit-based |
| Shodan | Limited without paid account | N/A |
| EmailRep | Rate-limited (unspecified) | Rolling |
| VoilaNorbert | 50 leads free | One-time |
| Email Checker | Unlimited (single validation) | N/A |
| LeakCheck | Limited free, paid $2.99/day+ | Daily |
| SnusBase | Paid only (2048 req/day with membership) | Daily |
| That's Them | 10 lookups/day | Daily |
| PeekYou | Unlimited | N/A |
| NameCheckup | Unlimited (free API) | N/A |
| DNSlytics | Freemium | N/A |
| VirusTotal | Free API (rate-limited) | Rolling |
| AI HIT | Free with registration | N/A |
| crt.sh | Unlimited | N/A |
| urlscan.io | Unlimited (free API) | N/A |
| Wayback Machine | Unlimited (availability API) | N/A |

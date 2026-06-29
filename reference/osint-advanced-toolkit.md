# OSINT-Advanced Tool Registry

Curated registry of specialized OSINT tools for deep investigations via `/osint-advanced`. Extracted from [awesome-osint](https://github.com/jivoi/awesome-osint) and filtered for 31C operational relevance.

Consumed by: `/osint-advanced` skill (explicit invocation only)
Upstream source: https://github.com/jivoi/awesome-osint
Last synced: 2026-03-21
Last validated: 2026-03-21
Last Updated: 2026-04-18

---

## How This File Is Used

The `/osint-advanced` skill reads this file during Phase 0 and selects tools from the relevant categories based on investigation mode. Tools are queried via WebFetch (for working APIs), Firecrawl (for JS-rendered pages), or WebSearch `site:` fallback (for blocked sites).

To check for upstream changes and validate tools: `python scripts/osint-advanced-sync.py report`

---

## 1. Sanctions & Compliance

### OpenSanctions
- URL: https://www.opensanctions.org
- Access: web (HTML search page works; API requires key)
- Endpoint: `https://www.opensanctions.org/search/?q={query}` (WebFetch WORKS -- returns HTML with entity results)
- Response: HTML with entity cards showing name, type, datasets, countries
- WebFetch Status: WORKING (web search page)
- Free Tier: yes (web search free; API requires key for commercial use)
- Last Tested: 2026-03-21
- Notes: 2.1M+ entities from 329 sources (OFAC, EU, UN, Interpol). Daily updates. API endpoint (`api.opensanctions.org`) returns 401 without key -- use the web search page instead.

### OCCRP Aleph
- URL: https://aleph.occrp.org
- Access: web (API endpoint returns 404 -- may have changed versions)
- Endpoint: N/A (use WebSearch: `site:aleph.occrp.org "{query}"`)
- Response: HTML (SPA -- WebFetch returns empty; use WebSearch fallback)
- WebFetch Status: BLOCKED (SPA + API 404)
- Free Tier: yes
- Last Tested: 2026-03-21
- Notes: Cross-border investigation platform. Corporate registries, court records, leaks from 100+ countries. API v2 endpoint returned 404 during validation -- use WebSearch fallback.

### ICIJ Offshore Leak Database
- URL: https://offshoreleaks.icij.org
- Access: web (SPA -- requires WebSearch fallback)
- Endpoint: N/A (use WebSearch: `site:offshoreleaks.icij.org "{query}"`)
- Response: HTML (JS-rendered)
- WebFetch Status: BLOCKED (SPA)
- Free Tier: yes
- Last Tested: 2026-03-21
- Notes: Panama Papers, Paradise Papers, Pandora Papers. Offshore entities, officers, intermediaries.

### OpenOwnership Register
- URL: https://register.openownership.org
- Access: web
- Endpoint: `https://register.openownership.org/search?q={query}`
- Response: HTML
- WebFetch Status: UNTESTED
- Free Tier: yes
- Last Tested: --
- Notes: Beneficial ownership transparency register. Cross-reference ultimate owners.

---

## 2. Corporate Registry

### OpenCorporates
- URL: https://opencorporates.com
- Access: web (CAPTCHA blocks WebFetch)
- Endpoint: N/A (use WebSearch: `site:opencorporates.com "{query}"`)
- Response: HTML
- WebFetch Status: BLOCKED (CAPTCHA)
- Free Tier: limited (web free, API paid)
- Last Tested: 2026-03-21
- Notes: World's largest open corporate database. 140+ jurisdictions.

### EDGAR (SEC)
- URL: https://www.sec.gov/cgi-bin/browse-edgar
- Access: web (EFTS API returns 403)
- Endpoint: N/A (use WebSearch: `site:sec.gov "{query}" 10-K OR 10-Q OR 8-K`)
- Response: HTML
- WebFetch Status: BLOCKED (403 on EFTS API)
- Free Tier: yes
- Last Tested: 2026-03-21
- Notes: US SEC filings. 10-K, 10-Q, 8-K, proxy statements. WebSearch fallback is reliable.

### CrunchBase
- URL: https://www.crunchbase.com
- Access: web
- Endpoint: N/A (use WebSearch: `site:crunchbase.com "{query}"`)
- Response: HTML
- WebFetch Status: UNTESTED (likely gated)
- Free Tier: limited
- Last Tested: --
- Notes: Startup/company database with funding rounds, acquisitions, leadership, investors.

### European Business Register
- URL: https://www.ebr.org
- Access: web
- Endpoint: N/A
- Response: HTML
- WebFetch Status: UNTESTED
- Free Tier: limited
- Last Tested: --
- Notes: Pan-European company registry search across EU member states.

### YouControl
- URL: https://youcontrol.com.ua/en/
- Access: web
- Endpoint: N/A
- Response: HTML
- WebFetch Status: UNTESTED
- Free Tier: limited
- Last Tested: --
- Notes: Ukrainian company intelligence. Relevant for CIS market investigations.

### AI HIT
- URL: https://www.aihitdata.com/
- Access: web
- Endpoint: N/A (use WebSearch: `site:aihitdata.com "{company}"`)
- Response: HTML
- WebFetch Status: WORKING
- Free Tier: free access with registration
- Last Tested: 2026-03-21
- Notes: 12.5M+ company profiles. Executive changes, business relationships.

---

## 3. People & Username Search

### Maigret
- URL: https://github.com/soxoj/maigret
- Access: cli
- Endpoint: N/A (local install: `pip install maigret`)
- Response: CLI output, HTML/PDF/JSON reports
- WebFetch Status: N/A (CLI tool)
- Free Tier: yes (open source)
- Last Tested: --
- Notes: 3000+ sites. Extracts personal data from found profiles. Preferred over Sherlock (400 sites). Run: `maigret {username} --all-sites`

### WhatsMyName
- URL: https://whatsmyname.app
- Access: web (SPA)
- Endpoint: N/A (use WebSearch: `site:whatsmyname.app "{username}"` or manual browser)
- Response: HTML (JS-rendered)
- WebFetch Status: BLOCKED (HTTP 464, WAF)
- Free Tier: yes
- Last Tested: 2026-03-21
- Notes: Multi-platform username checker with web UI. Good for quick checks without CLI.

### Epieos
- URL: https://epieos.com
- Access: web (SPA)
- Endpoint: N/A (use manually or WebSearch: `site:epieos.com "{email}"`)
- Response: HTML (JS-rendered)
- WebFetch Status: BLOCKED (SPA)
- Free Tier: limited
- Last Tested: --
- Notes: Email/phone to social account mapping. Finds linked accounts from email address.

### Social Analyzer
- URL: https://github.com/qeeqbox/social-analyzer
- Access: cli
- Endpoint: N/A (local install)
- Response: CLI output
- WebFetch Status: N/A (CLI tool)
- Free Tier: yes (open source)
- Last Tested: --
- Notes: Profile analysis across 1000+ platforms with automated reporting.

### That's Them
- URL: https://thatsthem.com/
- Access: web
- Endpoint: N/A (use WebSearch: `site:thatsthem.com "{target}"`)
- Response: HTML
- WebFetch Status: WORKING
- Free Tier: 10 lookups/day
- Last Tested: 2026-03-21
- Notes: 2.2B records. Name, phone, email, address lookup. US-focused.

### PeekYou
- URL: https://peekyou.com/
- Access: web
- Endpoint: N/A (use WebSearch: `site:peekyou.com "{target}"`)
- Response: HTML
- WebFetch Status: WORKING
- Free Tier: completely free
- Last Tested: 2026-03-21
- Notes: 10M+ monthly searches. Aggregates social, news, blogs. US-focused.

### NameCheckup
- URL: https://namecheckup.com/
- Access: web+api
- Endpoint: `https://namecheckup.com/wp-json/namecheckup/v1/check/{username}`
- Response: JSON
- WebFetch Status: WORKING
- Free Tier: completely free (no signup)
- Last Tested: 2026-03-21
- Notes: Checks 40+ TLDs and 20+ social platforms. REST API, no auth required.

---

## 4. Email Intelligence

### Hunter.io
- URL: https://hunter.io
- Access: api (v2)
- Endpoint: `GET https://api.hunter.io/v2/domain-search?domain={domain}&api_key={HUNTER_API_KEY}`
- Additional Endpoints:
  - `GET /v2/email-finder?domain={domain}&first_name={first}&last_name={last}&api_key={key}` -- find specific person's email
  - `GET /v2/email-verifier?email={email}&api_key={key}` -- verify an email address
  - `GET /v2/email-count?domain={domain}` -- count emails (no auth needed)
- Response: JSON (`data.emails[]` with value, type, confidence, sources)
- WebFetch Status: WORKING (API)
- Free Tier: 25 searches/month, 50 verifications/month
- Last Tested: 2026-03-22
- Notes: Email discovery by domain. 6M+ users. API key in `.env` as `HUNTER_API_KEY`. Returns email addresses, confidence scores, sources, and organization data.

### Have I Been Pwned
- URL: https://haveibeenpwned.com
- Access: api (v3)
- Endpoint: `GET https://haveibeenpwned.com/api/v3/breachedaccount/{account}?truncateResponse=false`
- Auth: Header `hibp-api-key: {HIBP_API_KEY}` + `user-agent: 31C-OSINT`
- Additional Endpoints:
  - `GET /api/v3/pasteaccount/{account}` -- pastes containing email
  - `GET /api/v3/breacheddomain/{domain}` -- breached addresses on a domain
  - `GET /api/v3/stealerlogsbyemail/{email}` -- stealer log exposure
  - `GET /api/v3/breach/{name}` -- single breach details (no auth needed)
  - `GET /api/v3/subscription/status` -- check subscription/credits
- Response: JSON (array of breach objects with Name, Domain, BreachDate, PwnCount, DataClasses, etc.)
- WebFetch Status: WORKING (API)
- Free Tier: none (subscription required, $3.50/month+)
- Last Tested: 2026-03-21
- Notes: 962+ breached sites, 17.5B+ compromised accounts. Industry standard. API key in `.env` as `HIBP_API_KEY`. Requires `user-agent` header or returns 403. Rate limited (429 with retry-after header). Stealer logs require Pwned 5+ subscription.

### EmailRep
- URL: https://emailrep.io
- Access: api (rate-limited without key)
- Endpoint: `https://emailrep.io/{email}` (returns 429 without API key header)
- Response: JSON -- reputation score, breach history, suspicious indicators
- WebFetch Status: BLOCKED (429 rate limit without API key)
- Free Tier: limited (requires API key header for reliable access)
- Last Tested: 2026-03-21
- Notes: Email reputation and risk scoring. Use WebSearch: `site:emailrep.io "{email}"` as fallback.

### Holehe
- URL: https://github.com/megadose/holehe
- Access: cli
- Endpoint: N/A (local install: `pip install holehe`)
- Response: CLI output
- WebFetch Status: N/A (CLI tool)
- Free Tier: yes (open source)
- Last Tested: --
- Notes: Check which services an email is registered on. Run: `holehe {email}`

### Snov.io
- URL: https://snov.io/email-finder
- Access: web+api
- Endpoint: N/A (use WebSearch or manual)
- Response: HTML
- WebFetch Status: UNTESTED
- Free Tier: limited
- Last Tested: --
- Notes: Email finder and verifier. Good for prospecting and CRM enrichment.

### VoilaNorbert
- URL: https://www.voilanorbert.com/
- Access: web+api
- Endpoint: API with Node.js, PHP, Python, Ruby SDKs
- Response: JSON
- WebFetch Status: WORKING (web)
- Free Tier: 50 leads free, 2500 emails/month free sequences
- Last Tested: 2026-03-21
- Notes: Email finding, verification, enrichment. 58K+ customers. 98% claimed accuracy.

### Email Checker
- URL: https://email-checker.net/validate
- Access: web+api
- Endpoint: API via RapidAPI
- Response: JSON
- WebFetch Status: WORKING
- Free Tier: completely free (single validation, no signup)
- Last Tested: 2026-03-21
- Notes: Free single email validation. Bulk checking premium. Also has email extraction tool.

---

## 5. Domain/IP/Infrastructure

### Shodan
- URL: https://www.shodan.io
- Access: web+api
- Endpoint: N/A free (use WebSearch: `site:shodan.io "{query}"`)
- Response: HTML
- WebFetch Status: UNTESTED (API requires key)
- Free Tier: limited (free account, paid API)
- Last Tested: --
- Notes: IoT and exposed device search. 3M+ users, 89% of Fortune 100.

### Censys
- URL: https://search.censys.io
- Access: web+api
- Endpoint: N/A free (use WebSearch: `site:search.censys.io "{query}"`)
- Response: HTML
- WebFetch Status: UNTESTED (API requires key)
- Free Tier: limited
- Last Tested: --
- Notes: Internet-wide scanning. Hosts, certificates, protocols.

### crt.sh
- URL: https://crt.sh
- Access: api
- Endpoint: `https://crt.sh/?q={domain}&output=json`
- Response: JSON array of certificate entries
- WebFetch Status: WORKING
- Free Tier: yes
- Last Tested: 2026-03-21
- Notes: Certificate Transparency log search. All SSL certificates for a domain.

### urlscan.io
- URL: https://urlscan.io
- Access: api
- Endpoint: `https://urlscan.io/api/v1/search/?q=domain:{domain}`
- Response: JSON with scan results, technologies, screenshots
- WebFetch Status: WORKING
- Free Tier: yes (open API)
- Last Tested: 2026-03-21
- Notes: Website scanner. Captures DOM, network requests, screenshots.

### DNSDumpster
- URL: https://dnsdumpster.com
- Access: web
- Endpoint: N/A (use WebSearch: `site:dnsdumpster.com "{domain}"`)
- Response: HTML (JS-rendered)
- WebFetch Status: BLOCKED (JS)
- Free Tier: yes
- Last Tested: --
- Notes: DNS reconnaissance. Subdomains, MX records, TXT records, hosting.

### BuiltWith
- URL: https://builtwith.com
- Access: web
- Endpoint: N/A (use WebSearch: `site:builtwith.com "{domain}"`)
- Response: HTML
- WebFetch Status: WORKING (web, JS-heavy)
- Free Tier: limited
- Last Tested: 2026-03-21
- Notes: Technology profiler. Identify tech stack, analytics, CDN, hosting.

### DNSlytics
- URL: https://dnslytics.com/
- Access: web+api
- Endpoint: `https://dnslytics.com/api` (paid tier)
- Response: JSON (API), HTML (web)
- WebFetch Status: WORKING
- Free Tier: freemium
- Last Tested: 2026-03-21
- Notes: 25M+ IPs, 330M+ domains. Reverse IP, DNS records, domain history. Premium for historical data and CIDR searches.

### SecurityTrails
- URL: https://securitytrails.com
- Access: web+api
- Endpoint: N/A free (API requires key)
- Response: HTML / JSON (API)
- WebFetch Status: UNTESTED
- Free Tier: limited (50/month)
- Last Tested: --
- Notes: DNS history, WHOIS history, subdomain enumeration.

### Intelligence X
- URL: https://intelx.io
- Access: web+api
- Endpoint: N/A via WebFetch (requires auth + JS)
- Response: HTML (JS-rendered, auth-gated)
- WebFetch Status: BLOCKED (auth+JS)
- Free Tier: limited (50 lookups/day)
- Last Tested: 2026-03-21
- Notes: Dark web, leaks, pastes, web archives. Use WebSearch fallback.

---

## 6. Threat Intelligence

### MITRE ATT&CK
- URL: https://attack.mitre.org
- Access: web
- Endpoint: `https://attack.mitre.org/groups/` (group listing)
- Response: HTML (static, works with WebFetch)
- WebFetch Status: WORKING
- Free Tier: yes
- Last Tested: --
- Notes: Industry-standard adversary tactics and techniques. Group profiles with TTPs.

### SOCRadar LABS
- URL: https://socradar.io/labs/threat-actor
- Access: web
- Endpoint: N/A (use WebSearch: `site:socradar.io "{query}" threat actor`)
- Response: HTML
- WebFetch Status: UNTESTED
- Free Tier: yes
- Last Tested: --
- Notes: Threat actor profiles with TTPs, targets, tools, IoCs.

### Malpedia
- URL: https://malpedia.caad.fkie.fraunhofer.de
- Access: web (limited, invite for full)
- Endpoint: N/A (use WebSearch: `site:malpedia.caad.fkie.fraunhofer.de "{query}"`)
- Response: HTML
- WebFetch Status: UNTESTED (invite-only for full access)
- Free Tier: limited (public actor list available)
- Last Tested: --
- Notes: Curated malware/threat actor database by Fraunhofer FKIE.

### Pulsedive
- URL: https://pulsedive.com
- Access: web+api (API rate-limited without key)
- Endpoint: N/A (API returns 429 without key; use WebSearch: `site:pulsedive.com "{query}"`)
- Response: HTML / JSON (API requires key)
- WebFetch Status: BLOCKED (429 rate limit)
- Free Tier: limited (requires API key)
- Last Tested: 2026-03-21
- Notes: Threat intelligence platform. IoC enrichment, risk scoring. Use WebSearch fallback.

### VirusTotal
- URL: https://www.virustotal.com
- Access: api (v3)
- Endpoint: `GET https://www.virustotal.com/api/v3/domains/{domain}`
- Auth: Header `x-apikey: {VIRUSTOTAL_API_KEY}`
- Additional Endpoints:
  - `GET /api/v3/ip_addresses/{ip}` -- IP address report
  - `GET /api/v3/urls/{url_id}` -- URL analysis (base64-encode URL without padding for id)
  - `GET /api/v3/files/{hash}` -- File hash analysis (MD5, SHA-1, SHA-256)
  - `GET /api/v3/search?query={query}` -- search across all types
- Response: JSON (`data.attributes` with DNS records, WHOIS, detection stats, categories)
- WebFetch Status: WORKING (API)
- Free Tier: 4 requests/min, 500 requests/day, 15.5K/month
- Last Tested: 2026-03-22
- Notes: Google-owned. API key in `.env` as `VIRUSTOTAL_API_KEY`. Returns DNS, WHOIS, reputation, community votes, detection results. Premium via Enterprise for higher limits.

### Abuse.ch
- URL: https://abuse.ch
- Access: web
- Endpoint: Various (MalwareBazaar, URLhaus, ThreatFox)
- Response: HTML / JSON
- WebFetch Status: UNTESTED
- Free Tier: yes
- Last Tested: --
- Notes: Community threat intelligence feeds -- malware samples, malicious URLs, IoCs.

---

## 7. Image & Face Search

### Yandex Images
- URL: https://yandex.com/images
- Access: web
- Endpoint: N/A (use WebSearch: `site:yandex.com/images "{query}"` or manual reverse search)
- Response: HTML
- WebFetch Status: UNTESTED
- Free Tier: yes
- Last Tested: --
- Notes: Best free reverse image search for face matching. Outperforms Google for faces, especially CIS region.

### PimEyes
- URL: https://pimeyes.com
- Access: web (manual only -- requires face upload)
- Endpoint: N/A
- Response: HTML
- WebFetch Status: N/A (requires manual face upload)
- Free Tier: limited (3 free searches, then $30+/month)
- Last Tested: --
- Notes: Face recognition across internet. Provide URL for manual use.

### FaceCheck.ID
- URL: https://facecheck.id
- Access: web (manual only -- requires face upload)
- Endpoint: N/A
- Response: HTML
- WebFetch Status: N/A (requires manual face upload)
- Free Tier: limited
- Last Tested: --
- Notes: Face recognition with mugshot and social media coverage.

### TinEye
- URL: https://tineye.com
- Access: web+api
- Endpoint: N/A (blocks automated requests; API is paid)
- Response: HTML
- WebFetch Status: BLOCKED (403)
- Free Tier: web free (limited daily), API paid ($200+/month)
- Last Tested: 2026-03-21
- Notes: Reverse image search. Use WebSearch: `site:tineye.com "{query}"` as fallback.

### FotoForensics
- URL: https://fotoforensics.com
- Access: web
- Endpoint: N/A (manual upload)
- Response: HTML
- WebFetch Status: UNTESTED
- Free Tier: yes
- Last Tested: --
- Notes: Image forensic analysis. ELA for manipulation detection.

### GeoSpy
- URL: https://geospy.web.app
- Access: web
- Endpoint: N/A (manual upload)
- Response: HTML
- WebFetch Status: UNTESTED
- Free Tier: yes
- Last Tested: --
- Notes: AI-powered image geolocation. Upload photo, get estimated location.

---

## 8. Geospatial & Conflict

### Liveuamap
- URL: https://liveuamap.com
- Access: web (blocks automated requests)
- Endpoint: N/A (use WebSearch: `site:liveuamap.com "{region}"`)
- Response: HTML (JS-rendered)
- WebFetch Status: BLOCKED (403)
- Free Tier: yes (with ads)
- Last Tested: 2026-03-21
- Notes: Real-time conflict and crisis mapping. Ukraine, Middle East, Africa.

### Sentinel Hub
- URL: https://www.sentinel-hub.com/explore/sentinelplayground
- Access: web+api
- Endpoint: N/A (manual or API with registration)
- Response: HTML / imagery
- WebFetch Status: UNTESTED
- Free Tier: limited (registration required)
- Last Tested: --
- Notes: ESA satellite imagery. Multi-spectral analysis, change detection.

### Zoom Earth
- URL: https://zoom.earth
- Access: web
- Endpoint: N/A
- Response: HTML (JS-rendered map)
- WebFetch Status: UNTESTED
- Free Tier: yes
- Last Tested: --
- Notes: Real-time satellite and weather imagery.

### SunCalc
- URL: https://www.suncalc.org
- Access: web
- Endpoint: N/A
- Response: HTML (JS-rendered)
- WebFetch Status: UNTESTED
- Free Tier: yes
- Last Tested: --
- Notes: Sun position calculator. Verify time/location claims from shadow analysis.

### USGS EarthExplorer
- URL: https://earthexplorer.usgs.gov
- Access: web
- Endpoint: N/A
- Response: HTML
- WebFetch Status: UNTESTED
- Free Tier: yes
- Last Tested: --
- Notes: Satellite imagery archive. Landsat, Sentinel, aerial photography.

---

## 9. Data Breach & Dark Web

### Have I Been Pwned
- (See Email Intelligence section -- cross-listed)

### Intelligence X
- (See Domain/IP/Infrastructure section -- cross-listed)

### DeHashed
- URL: https://dehashed.com
- Access: api (v2)
- Endpoint: `POST https://api.dehashed.com/v2/search`
- Auth: Header `Dehashed-Api-Key: {DEHASHED_API_KEY}` + `Content-Type: application/json`
- Request Body: `{"query": "email:{target}", "size": 100, "page": 1, "de_dupe": true, "wildcard": false, "regex": false}`
- Search Fields: email, username, ip_address, name, phone, domain, password, hashed_password, vin, address
- Response: JSON (`balance`, `total`, `entries[]`)
- WebFetch Status: WORKING (API, not web scrape)
- Free Tier: credit-based (paid subscription required)
- Last Tested: 2026-03-21
- Notes: Breach search. API key in `.env` as `DEHASHED_API_KEY`. Query syntax: `field:value` (e.g., `email:user@example.com`, `domain:example.com`, `name:John`). Returns breach records with email, username, password, hashed_password, ip_address, etc. Max 10000 per page.

### LeakCheck
- URL: https://leakcheck.io/
- Access: web+api
- Endpoint: API included with paid plans ($2.99/day+)
- Response: JSON (API), HTML (web)
- WebFetch Status: WORKING (web)
- Free Tier: limited free checks, paid from $2.99/day
- Last Tested: 2026-03-21
- Notes: 7B+ records. Email, username, keyword, password search. Has Telegram bot. Bulk checking up to 100K lines.

### SnusBase
- URL: https://snusbase.com/
- Access: web+api
- Endpoint: API free with paid membership (2048 req/day). Docs: docs.snusbase.com
- Response: JSON
- WebFetch Status: WORKING (web)
- Free Tier: none (paid members only)
- Last Tested: 2026-03-21
- Notes: Email, name, username, IP, phone, hash search. Since 2016.

---

## 10. Fact Checking & Verification

### Snopes
- URL: https://www.snopes.com
- Access: web
- Endpoint: N/A (use WebSearch: `site:snopes.com "{claim}"`)
- Response: HTML
- WebFetch Status: UNTESTED
- Free Tier: yes
- Last Tested: --
- Notes: Fact-checking and debunking. Urban legends, misinformation, political claims.

### Wayback Machine
- URL: https://web.archive.org
- Access: web+api
- Endpoint: `https://archive.org/wayback/available?url={url}`
- Response: JSON -- `archived_snapshots.closest.url`, `.timestamp`, `.available`
- WebFetch Status: WORKING
- Free Tier: yes
- Last Tested: 2026-03-21
- Notes: Internet Archive. Historical snapshots of any website. Essential for deleted content.

### Archive.today
- URL: https://archive.ph
- Access: web
- Endpoint: N/A (use WebSearch: `site:archive.ph "{url}"`)
- Response: HTML
- WebFetch Status: UNTESTED
- Free Tier: yes
- Last Tested: --
- Notes: Website snapshots. Create and search permanent page captures.

---

## Upstream Sync

To check for upstream changes and validate tool status:

```bash
python scripts/osint-advanced-sync.py check      # Diff upstream vs local
python scripts/osint-advanced-sync.py validate    # HTTP health-check all tools
python scripts/osint-advanced-sync.py report      # Full report (check + validate)
```

Updates to this file happen ONLY after validation confirms new tools are working AND the user approves the changes.

# OSINT-Advanced - Deep Stream Catalogue

Consumed by: `.claude/skills/osint-advanced/SKILL.md` Phase 1 stream execution.

Specialised database query templates for sanctions, corporate registry, digital footprint, breach DBs, threat actors, infrastructure scanners, and image/face search. Each stream lists the canonical query forms, parsing hints, API patterns, and fallback notes. Streams are selected per Phase 1's mode-to-stream matrix.

For per-tool query patterns, response parsing, confidence scoring, rate limits, and fallback chains, see `tool-integration-guide.md` in the same folder.

---

## Stream: Sanctions/Compliance (MANDATORY for company/person)

Screen target against international sanctions lists, PEP databases, and investigation platforms.

1. **OpenSanctions API** - WebFetch `https://api.opensanctions.org/match/default` (POST with JSON body: `{"queries": {"q1": {"schema": "Person", "properties": {"name": ["{target}"]}}}}`, header `Authorization: ApiKey ${OPENSANCTIONS_API_KEY}` from `.env`)
   - Parse JSON response: extract entity matches with name, schema, datasets, countries, score
   - Fallback if API fails: WebFetch `https://www.opensanctions.org/search/?q={url_encoded_target}`
2. **OCCRP Aleph** - WebSearch `"{target}" site:aleph.occrp.org`
   - Note: API v2 endpoint returns 404; web is SPA. Use WebSearch fallback.
3. **ICIJ Offshore Leaks** - WebSearch `"{target}" site:offshoreleaks.icij.org`
   - Extract: entity matches, officer connections, intermediaries
4. **General sanctions** - WebSearch `"{target}" sanctions OFAC SDN EU sanctions list PEP`

**Output section: "Sanctions & Compliance Status"**
- **CLEAR:** "No matches found across [N] databases. Screening date: [date]."
- **MATCH:** Full details with entity type, sanction program, listing date, confidence score.
- **PARTIAL MATCH:** Possible matches requiring human review. Flag for Misha's attention.

---

## Stream: Corporate Registry (company/market)

Verify corporate identity, jurisdiction, ownership, and filing history.

1. **OpenCorporates** - WebSearch `"{company}" site:opencorporates.com`
   - Extract: company number, jurisdiction, status, registered address, officers
2. **EDGAR (SEC)** - WebSearch `"{company}" site:sec.gov 10-K OR 10-Q OR 8-K`
   - Extract: recent filings, form types, filing dates
3. **CrunchBase** - WebSearch `"{company}" site:crunchbase.com`
   - Extract: funding rounds, total raised, investors, acquisitions
4. **AI HIT** - WebSearch `"{company}" site:aihitdata.com`
   - Extract: company profile, executive changes, business relationships

---

## Stream: Digital Footprint (person)

Map a person's digital presence across platforms.

1. **WebSearch** username enumeration: `"{person}" site:github.com OR site:twitter.com OR site:reddit.com OR site:medium.com`
2. **WebSearch** social presence: `"{person}" profile social media accounts`

---

## Stream: Username & People Search (person)

Enumerate usernames across platforms and search people aggregators.

1. **NameCheckup** - WebFetch `https://namecheckup.com/wp-json/namecheckup/v1/check/{username}` (REST API, free)
   - Parse JSON: platform availability across 40+ TLDs and 20+ social platforms
2. **PeekYou** - WebSearch `"{person}" site:peekyou.com`
   - Extract: social profiles, age, location, related people
3. **That's Them** - WebSearch `"{person}" site:thatsthem.com`
   - Extract: phone, email, address, age from 2.2B records
4. **WebMii** - WebSearch `"{person}" site:webmii.com`
   - Extract: web visibility score, social profiles, mentions
5. **General people search** - WebSearch `"{person}" "{location}" profile social accounts`
6. **CLI recommendation:** Note for user - "For comprehensive username enumeration, run locally: `maigret {username} --all-sites` (3000+ sites)"

---

## Stream: Social Media OSINT (person)

Platform-specific investigation across social media.

1. **SowSearch (Facebook)** - WebSearch `"{person}" site:sowsearch.info OR site:facebook.com`
   - Extract: public profiles, posts, groups, check-ins
2. **InstaDP (Instagram)** - WebSearch `"{person}" site:instagram.com`
   - Note for user: Anonymous Instagram viewing at `https://www.instadp.com/` and `https://imginn.com/`
3. **Spoonbill.io** - WebSearch `"{person}" site:spoonbill.io`
   - Extract: social profile change history (Twitter, GitHub, Product Hunt)
4. **Tinfoleak (Twitter)** - WebSearch `"{person}" site:tinfoleak.com`
   - Extract: Twitter geolocation, device info, hashtag analysis
   - Caveat: May be degraded by Twitter/X API changes post-2023
5. **General social** - WebSearch `"{person}" site:linkedin.com OR site:twitter.com OR site:github.com OR site:reddit.com`

---

## Stream: Email Intelligence (person)

Discover, verify, and assess risk of email addresses.

1. **EmailRep** - WebSearch `"{email}" site:emailrep.io` (API returns 429 without key)
   - Extract: reputation indicators, breach history mentions
2. **Hunter.io** - API call via Bash: `curl -s "https://api.hunter.io/v2/domain-search?domain={company_domain}&api_key=$(python3 -c \"import os,sys;sys.path.insert(0,'.');from scripts.utils.api import load_api_key;print(load_api_key('HUNTER_API_KEY'))\")"` - for specific person use `/v2/email-finder?domain={domain}&first_name={first}&last_name={last}&api_key={key}`
3. **VoilaNorbert** - WebSearch `"{person}" email site:voilanorbert.com`
4. **Email Checker** - WebSearch `"{email}" site:email-checker.net` (free validation, no signup)
5. **HIBP** - API call via Bash: `curl -s -H "hibp-api-key: $(python3 -c \"import os,sys;sys.path.insert(0,'.');from scripts.utils.api import load_api_key;print(load_api_key('HIBP_API_KEY'))\")" -H "user-agent: 31C-OSINT" "https://haveibeenpwned.com/api/v3/breachedaccount/{email}?truncateResponse=false"` - also try `/pasteaccount/{email}` for paste exposure
6. **CLI recommendation:** Note for user - "For email-to-social mapping, run locally: `holehe {email}`"

---

## Stream: Infrastructure Recon (company/technology)

Technical reconnaissance on domains, IPs, SSL certificates, tech stack.

1. **crt.sh** - WebFetch `https://crt.sh/?q={domain}&output=json`
   - Parse JSON array: extract subdomains, issuing CAs, validity dates
2. **urlscan.io** - WebFetch `https://urlscan.io/api/v1/search/?q=domain:{domain}`
   - Parse JSON: technologies, outbound connections, trackers
3. **Shodan** - WebSearch `"{domain}" site:shodan.io`
   - Extract: open ports, services, hosting provider
4. **BuiltWith** - WebSearch `"{domain}" site:builtwith.com`
   - Extract: technology stack, analytics, CDN
5. **DNSlytics** - WebSearch `"{domain}" site:dnslytics.com`
   - Extract: reverse IP, DNS records, domain history, CIDR blocks
6. **VirusTotal** - API call via Bash: `curl -s -H "x-apikey: $(python3 -c \"import os,sys;sys.path.insert(0,'.');from scripts.utils.api import load_api_key;print(load_api_key('VIRUSTOTAL_API_KEY'))\")" "https://www.virustotal.com/api/v3/domains/{domain}"` - also try `/api/v3/ip_addresses/{ip}` for IP analysis
   - Extract: DNS records, WHOIS, reputation, detection stats, categories
7. **DNS/WHOIS** - WebSearch `"{domain}" DNS records whois registration`

---

## Stream: Threat Intelligence (company/technology)

Cross-reference target against threat actor databases and cyber threat landscape.

1. **MITRE ATT&CK** - WebSearch `site:attack.mitre.org "{target}"`
   - Extract: group profiles, TTPs, associated malware, target sectors
2. **SOCRadar** - WebSearch `site:socradar.io "{target}" threat actor`
3. **Malpedia** - WebSearch `site:malpedia.caad.fkie.fraunhofer.de "{target}"`
4. **VirusTotal** - API call via Bash: `curl -s -H "x-apikey: $(python3 -c \"import os,sys;sys.path.insert(0,'.');from scripts.utils.api import load_api_key;print(load_api_key('VIRUSTOTAL_API_KEY'))\")" "https://www.virustotal.com/api/v3/search?query={target}"`
5. **General threat** - WebSearch `"{target}" APT threat actor vulnerability CVE`

---

## Stream: Image/Face Search (person)

Reverse image search and face recognition.

1. **Yandex Images** - WebSearch `"{person}" site:yandex.com/images` (best free face-matching)
2. **Google Images** - WebSearch `"{person}" photo portrait`
3. **Manual tools:** Note URLs for user to run manually in browser:
   - PimEyes: `https://pimeyes.com` (paid, face upload required)
   - FaceCheck.ID: `https://facecheck.id` (face upload required)
   - TinEye: `https://tineye.com` (reverse image search - blocks automation)

---

## Stream: Geospatial/Conflict (market)

Live conflict monitoring and geographic context.

1. **Liveuamap** - WebSearch `site:liveuamap.com "{region}"`
   - Extract: recent events, conflict incidents, security alerts
2. **General geospatial** - WebSearch `"{region}" conflict security situation military`
3. **Satellite imagery note:** Sentinel Hub (`sentinel-hub.com`) and USGS EarthExplorer (`earthexplorer.usgs.gov`) for manual satellite analysis

---

## Stream: Data Breach (company/person)

Check for breach exposure, compromised credentials, dark web mentions.

1. **HIBP** - API call via Bash: `curl -s -H "hibp-api-key: $(python3 -c \"import os,sys;sys.path.insert(0,'.');from scripts.utils.api import load_api_key;print(load_api_key('HIBP_API_KEY'))\")" -H "user-agent: 31C-OSINT" "https://haveibeenpwned.com/api/v3/breachedaccount/{target}?truncateResponse=false"` - for domains use `/breacheddomain/{domain}`, for stealer logs use `/stealerlogsbyemail/{email}`
2. **Intelligence X** - WebSearch `site:intelx.io "{target}"`
3. **DeHashed** - API call via Bash: `curl -s -X POST "https://api.dehashed.com/v2/search" -H "Dehashed-Api-Key: $(python3 -c \"import os,sys;sys.path.insert(0,'.');from scripts.utils.api import load_api_key;print(load_api_key('DEHASHED_API_KEY'))\")" -H "Content-Type: application/json" -d '{"query":"email:{target}","size":100,"page":1,"de_dupe":true}'` - also try `name:{target}`, `username:{target}`, `domain:{target}` as appropriate
4. **LeakCheck** - WebSearch `"{target}" site:leakcheck.io`
5. **SnusBase** - WebSearch `"{target}" site:snusbase.com`
6. **General breach** - WebSearch `"{target}" data breach leak exposed credentials`

---

## Stream: Fact Check (all modes)

Verify claims, check historical content, validate sources.

1. **Wayback Machine** - WebFetch `https://archive.org/wayback/available?url={target_url}`
   - Parse JSON: check `archived_snapshots.closest.available`, `.url`, `.timestamp`
2. **Snopes** - WebSearch `site:snopes.com "{claim}"`
3. **Archive.today** - WebSearch `site:archive.ph "{target}"`
4. **General verification** - WebSearch `"{target}" fact check verification debunked`

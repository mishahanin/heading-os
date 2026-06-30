<!-- version: 1.0.0 | last-updated: 2026-07-01 -->
# Integrations & credentials

How to connect the external services HEADING OS talks to: corporate email
(Exchange), your personal Telegram, Google contacts, and the OSINT and web-research
APIs. Each one lights up a set of skills and is independent. Set up only the ones you
use; the rest of the engine runs fine without them.

> AI models (Ollama embeddings, the Council voices) have their own guide:
> [MODELS-SETUP](MODELS-SETUP.html). This page covers everything else.

Every credential lives in the engine's gitignored `.env`; interactive sessions
(Telegram, Google OAuth) cache tokens under the gitignored `.sessions/`. Neither is
ever committed, and a push-time content scan blocks any credential that slips into a
tracked file.

---

## 1. What needs what

| Integration | Lights up | Credentials | Where to get them |
|---|---|---|---|
| Exchange email | `/email-intel`, `/email-draft`, `/email-respond`, `send-email.py`, sync-exchange daemon | `EXCHANGE_SERVER`, `EXCHANGE_EMAIL`, Exchange password | your mail administrator |
| Telegram (personal) | `/telegram`, `/viraid`, Sentinel monitor | `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_PHONE` | my.telegram.org |
| Google contacts | `/google-contacts` | OAuth `credentials.json` | Google Cloud console |
| Web research | `/osint`, `/deep-research-advance`, browsing | `TAVILY_API_KEY`, `BRAVE_API_KEY`, `FIRECRAWL_API_KEY`, `PERPLEXITY_API_KEY` | per-service signup |
| Breach / OSINT | `/osint`, `/osint-advanced` | `DEHASHED_API_KEY`, `HIBP_API_KEY`, `HUNTER_API_KEY`, `VIRUSTOTAL_API_KEY` | per-service signup |
| Image generation | `/flux-image`, `/design` | `REPLICATE_API_TOKEN` | replicate.com |

The fastest way to fill `.env` interactively is the [setup wizard](MAKE-IT-YOURS.html):
`/setup-wizard` walks you through these keys question by question. This page is the
reference for doing it by hand or understanding what each one does.

---

## 2. Exchange email (the comms backbone)

Email is the spine of the communications skills. HEADING OS connects to Exchange over
**EWS** using the `exchangelib` library, with an **explicit server** and autodiscover
turned off (it does not guess your endpoint). This targets an on-premises or hosted
Exchange mailbox with delegate access.

### 2.1 Credentials (in `.env`)

| Variable | Required | Meaning |
|---|---|---|
| `EXCHANGE_EMAIL` | yes | the mailbox address |
| `EXCHANGE_PASSWORD` | yes | the mailbox password (placeholder in `.env.example`) |
| `EXCHANGE_SERVER` | yes | the EWS host: bare hostname, no `https://`, no `/EWS/Exchange.asmx` |
| `EXCHANGE_USERNAME` | optional | only if the login differs from the email (some domains use `DOMAIN\user`) |
| `EXCHANGE_AUTH_TYPE` | optional | `NTLM` (default) or `basic`, per your server |
| `EXCHANGE_TIMEZONE` | optional | your mailbox timezone, for correct item times |

`EXCHANGE_SERVER` is the bare hostname the EWS URL is built from. If you reach webmail
at `https://mail.yourcompany.com/owa`, then `mail.yourcompany.com` is your server.
When the login name differs from the address, set `EXCHANGE_USERNAME`; otherwise the
email doubles as the username.

If you do not know the server or auth type, ask whoever runs your mail. This is not a
public-signup integration: the values come from your organization.

### 2.2 Sending mail

`scripts/send-email.py` is the **single entry point for all outbound email**. It
attaches the branded signature with inline images and is the only sanctioned way to
send. Never call the transport any other way.

```bash
# basic
uv run python scripts/send-email.py \
  --to "person@example.com" \
  --subject "Subject line" \
  --body "<p>HTML body</p>"

# cc / bcc, multiple recipients
uv run python scripts/send-email.py \
  --to "a@example.com" "b@example.com" --cc "c@example.com" --bcc "d@example.com" \
  --subject "Subject" --body "<p>Body</p>"

# threaded reply / reply-all / forward (match the original by subject or sender)
uv run python scripts/send-email.py --reply --match-subject "31C / Globex" --body "<p>...</p>"
```

A plain-text `--body` is auto-wrapped in HTML. Outbound is always human-gated: skills
draft and show you the message; sending is your deliberate action.

### 2.3 Verify

```bash
# round-trip a message to yourself
uv run python scripts/send-email.py --to "you@yourcompany.com" \
  --subject "HEADING OS test" --body "It works."
```

A clean send and a message in your inbox confirms the connection. The reading side
(`/email-intel`, sync-exchange) uses the same credentials, so a successful send means
the whole Exchange path is wired.

### 2.4 Troubleshooting

| Symptom | Cause & fix |
|---|---|
| Auth or 401 on connect | Wrong password, or wrong `EXCHANGE_AUTH_TYPE`. Try `basic` if `NTLM` fails, or the reverse. Confirm the password is current. |
| Cannot reach the server | `EXCHANGE_SERVER` wrong or unreachable. Use the bare hostname, no scheme or path. Check VPN if the server is internal. |
| Login name rejected | Set `EXCHANGE_USERNAME` explicitly (some servers want `DOMAIN\user`). |
| Wrong times on items | Set `EXCHANGE_TIMEZONE` to your mailbox timezone. |

---

## 3. Telegram (personal client)

The `/telegram` skill drives a real Telegram **user** account (yours), through
Telethon. It is a one-time interactive login that caches a session; after that the
skill reads and sends as you. (This is separate from the optional Fireside team
**bot**, which uses its own bot token and is covered in [Daemons](daemons.html).)

### 3.1 Get API credentials

1. Go to [my.telegram.org](https://my.telegram.org) and sign in with your phone.
2. Open **API development tools** and create an application.
3. Copy the **api_id** and **api_hash** into `.env`:

| Variable | Meaning |
|---|---|
| `TELEGRAM_API_ID` | the numeric api_id from my.telegram.org |
| `TELEGRAM_API_HASH` | the api_hash from my.telegram.org |
| `TELEGRAM_PHONE` | the account's number, with country code (e.g. `+1...`) |

### 3.2 Authenticate (one time)

```bash
# request the login code (Telegram sends it to your app)
uv run python .claude/skills/telegram/scripts/telegram_client.py setup

# enter the code you received
uv run python .claude/skills/telegram/scripts/telegram_client.py verify 12345
```

If the account has two-step verification, you are prompted for the password. The
session is stored at `.sessions/telegram/telegram.session` (gitignored). You log in
once per machine; the skill reuses the session after that.

### 3.3 Verify

```bash
uv run python .claude/skills/telegram/scripts/telegram_client.py chats --limit 5
```

A list of your recent chats means the client is authenticated. Inside a session,
`/telegram` and `/viraid` then work directly.

### 3.4 Troubleshooting

| Symptom | Cause & fix |
|---|---|
| `setup` says credentials missing | `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` / `TELEGRAM_PHONE` not set in `.env`. |
| Code never arrives | It is delivered inside Telegram (the app), not by SMS. Check your other Telegram sessions. |
| Session keeps re-prompting | The `.sessions/telegram/` file was deleted or is unwritable. Re-run `setup` then `verify`. |
| Datacenter-IP block on read | Some networks rate-limit. See the VPN preflight note in [Prerequisites](prerequisites.html). |

---

## 4. Google contacts

`/google-contacts` reads and writes your Google contacts through the People API. It is
a standalone connector, fully separate from the workspace CRM (`/crm`). It uses
standard Google OAuth with a desktop client.

> Calendar, Gmail, and Drive in a Claude Code session come through Claude's own
> managed connectors (configured in your Claude account), not through engine `.env`.
> This section covers only the scripted People-API integration the engine ships.

### 4.1 Set up OAuth

1. Go to the [Google Cloud console credentials page](https://console.cloud.google.com/apis/credentials).
2. Create an **OAuth 2.0 Client ID** of type **Desktop application**.
3. Enable the **People API** (APIs & Services, Library, search "People API").
4. Download the credentials JSON.
5. Place it at `.sessions/google/credentials.json`.

### 4.2 First run

```bash
uv run python scripts/google-contacts.py list --limit 5
```

The first run opens a browser for OAuth consent (it handles two-factor). After you
approve, the token is cached at `.sessions/google/token.json` and subsequent runs are
silent. Both files are gitignored.

---

## 5. OSINT & web-research keys (optional)

These power research and browsing. All are optional: the skills degrade to whatever
is available and skip sources whose key is absent. Most have a free tier.

| Key | Service | Used by | Get it at |
|---|---|---|---|
| `TAVILY_API_KEY` | Tavily search | `/osint` entity resolution | [tavily.com](https://tavily.com) (free: ~1,000/mo) |
| `BRAVE_API_KEY` | Brave Search API | `/osint` (Tavily fallback) | [brave.com/search/api](https://brave.com/search/api/) (free: ~2,000/mo) |
| `FIRECRAWL_API_KEY` | Firecrawl | web scraping | [firecrawl.dev](https://firecrawl.dev) |
| `PERPLEXITY_API_KEY` | Perplexity | `/deep-research-advance` | [perplexity.ai](https://www.perplexity.ai) (API settings) |
| `DEHASHED_API_KEY` (+ `DEHASHED_EMAIL`) | DeHashed | `/osint-advanced` breach intel | [dehashed.com](https://dehashed.com) |
| `HIBP_API_KEY` | Have I Been Pwned | `/osint-advanced` | [haveibeenpwned.com/API/Key](https://haveibeenpwned.com/API/Key) |
| `HUNTER_API_KEY` | Hunter.io | email discovery | [hunter.io](https://hunter.io) |
| `VIRUSTOTAL_API_KEY` | VirusTotal | `/osint-advanced` | [virustotal.com](https://www.virustotal.com) |
| `REPLICATE_API_TOKEN` | Replicate | `/flux-image`, `/design` | [replicate.com](https://replicate.com) |
| `CONTEXT7_API_KEY` | Context7 | `/context7` library docs | [context7.com](https://context7.com) |
| `GH_TOKEN` | GitHub | `push-all.py` backups | GitHub settings (repo scope) |

`GH_TOKEN` is the one non-optional key for the backup path; it is covered in
[DEPLOYMENT](DEPLOYMENT.html). The rest you add as you adopt the skills that use them.

---

## 6. Where credentials live

| Location | Holds | Committed? |
|---|---|---|
| `.env` (engine root) | every API key and Exchange / Telegram credential | no (gitignored) |
| `.sessions/telegram/` | Telegram login session | no (gitignored) |
| `.sessions/google/` | Google `credentials.json` + cached `token.json` | no (gitignored) |

Never paste a live credential into chat, a ticket, or a tracked file. To read a value
locally: `grep KEY .env`. If a secret is ever exposed, treat it as compromised:
rotate it first, then scrub. The push-time scan is the backstop, not the first line of
defense; you are.

---

## 7. Reference

| File | Role |
|---|---|
| `.env.example` | Template listing every credential the engine reads |
| `scripts/send-email.py` | The single outbound-email entry point (Exchange / EWS) |
| `scripts/inbox_pulse/exchange.py` | Exchange read-side connection helper |
| `.claude/skills/telegram/scripts/telegram_client.py` | Telegram user client (setup / verify / send / read) |
| `scripts/google-contacts.py` | Google People-API client (OAuth) |
| `.sessions/` | Cached interactive sessions (gitignored) |

---

*HEADING OS · Integrations & credentials · maintained by 31 Concept · see also
[MODELS-SETUP](MODELS-SETUP.html) for AI models and [MAKE-IT-YOURS](MAKE-IT-YOURS.html)
to personalize the workspace.*

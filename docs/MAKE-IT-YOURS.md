<!-- version: 1.1.0 | last-updated: 2026-07-01 -->
# Make it yours

The engine ships as one person's working system. It carries 31 Concept's house
language, an executive's voice, and a built-in identity. None of that is wired into
the code: it lives in editable rules, templates, and your own private data. This page
is how you turn a fresh clone into *your* workspace, speaking in *your* voice about
*your* work.

> Credentials for the services below are in their own guides:
> [INTEGRATIONS-SETUP](INTEGRATIONS-SETUP.html) (email, Telegram, Google, OSINT) and
> [MODELS-SETUP](MODELS-SETUP.html) (Ollama, Council models). For the full step-by-step
> on Telegram, your own capture and alert channels, Viraid, and the Sentinel monitor, see
> [TELEGRAM-AND-ALERTS](TELEGRAM-AND-ALERTS.html).

---

## 1. The idea

HEADING OS splits **shared logic** from **personal substance**, the same way it
splits the engine repo from your data repo:

- The **engine** is the machinery: skills, scripts, rules, hooks. It is the same for
  everyone and is meant to be shared.
- **You** supply the substance: who you are, what your company is, how you write,
  which services you connect. That lives in your `.env`, your data overlay, and a few
  editable rule and template files.

So personalizing is not forking the code. It is filling in the substance the engine
deliberately left as templates and defaults.

---

## 2. The fast path: `/setup-wizard`

The wizard is the intended way to personalize a clone. It asks about 22 questions for
a public HEADING OS clone, enriches your short answers into full voice, personal, and
business documents, and captures any API keys into `.env` as it goes. It refuses to
run on the original maintainer's master workspace, so it is safe on any clone.

```bash
# in a Claude Code session, from the engine
/setup-wizard
```

Per question you can type an answer, `skip` (come back later), `example` (see a sample
answer), or `help` (what the question is for). Nothing is saved until you confirm.

Check progress or resume any time:

```bash
uv run python scripts/apply-wizard-answers.py --status
```

That prints a completion percentage and which answers are still pending. Re-run
`/setup-wizard` whenever you want to edit an answer; it is idempotent.

What the wizard fills, drawn from `config/wizard-templates/`:

| Template | Becomes | Holds |
|---|---|---|
| `business-info.md.tmpl` | your business profile | company, product, market, positioning |
| `personal-info.md.tmpl` | who you are | role, background, the facts skills reference |
| `ceo-voice.md.tmpl` | your voice reference | how you write, what you never say |
| `calendar-policy.md.tmpl` | your scheduling rules | meeting policy, working hours |

These land in your data overlay (`context/`, `reference/`), never in the shared
engine, so your substance stays private and your clone stays clean.

---

## 3. The personalization surface (by hand)

If you would rather edit directly, or want to know what the wizard touches, this is
the full surface.

### 3.1 Identity

`.workspace-identity.json` at the engine root (gitignored) declares who the workspace
belongs to. For a **solo** clone you can leave it absent: the engine defaults to a
single-user master workspace. A **managed** (multi-person) deployment uses the
`exec-workspace` shape covered in [DEPLOYMENT](DEPLOYMENT.html).

### 3.2 Your facts and voice

The documents the wizard generates are plain markdown you can keep editing:

- **Business and personal profile** in `context/` (your company, your role, the facts
  skills cite when they draft and research).
- **Voice reference** in `reference/` (how you write). The communication skills read
  it before drafting, so this is what makes outbound prose sound like you rather than
  like a generic assistant.

Both live in your private data overlay. Fill them with real specifics; the more
concrete they are, the better every draft and brief gets.

### 3.3 Brand language (the terminology and voice rules)

A few always-on rules in `.claude/rules/` encode the maintainer's house language:

- `terminology.md` defines 31C-specific vocabulary (for example the "Tribe", ODUN.ONE,
  DPI+, the Navigation Principle, the Five Principles).
- `voice.md` and `humanization.md` are the prose rules: tone discipline and the
  write-like-a-human machinery.

On your own clone you own these files. If you run your own brand, adapt
`terminology.md` to your language and point the voice rules at your own voice doc. If
you are using the engine for personal operations rather than a branded company, you
can trim the 31C terminology. The `humanization.md` machinery is generic and worth
keeping as is: it makes prose read as written by a person, whoever that is.

### 3.4 Credentials

Your API keys and service logins go in `.env` and `.sessions/`. See
[INTEGRATIONS-SETUP](INTEGRATIONS-SETUP.html) and [MODELS-SETUP](MODELS-SETUP.html).
The wizard can capture the common ones for you.

---

## 4. Configuring behavior: hooks, permissions, skills

### 4.1 Two settings files

| File | Tracked? | Holds |
|---|---|---|
| `.claude/settings.json` | yes (shared) | enabled plugins, shared hook wiring |
| `.claude/settings.local.json` | no (machine-local) | your tool permissions and any local hooks |

The local file is gitignored and seeded per machine from the tracked per-OS templates
`.claude/settings.local.linux.json` / `.macos.json` / `.windows.json`. The
`.claude/settings.README.md` explains the split. Edit `settings.local.json` to change
what runs without a permission prompt, or to add or remove a hook on your machine.

### 4.2 Turning skills and rules on or off

- **Skills** are folders under `.claude/skills/`. To retire one, move it into
  `.claude/skills/archive/` (the engine convention); it stops being routable without
  being deleted.
- **Rules** are files under `.claude/rules/` and load automatically. To change a
  behavior (voice, classification, a guardrail), edit the rule; to stand one down,
  move it out of the directory on your clone.

Change rules deliberately: several encode security controls (the outbound-send gate,
the engine/data separation, the secret guards). Adapt the brand and voice rules
freely; leave the security ones in place.

### 4.3 Per-skill overrides that survive updates

A few skills read an optional personal override file at
`config/skill-custom/{skill}.user.toml`. These `*.user.toml` files are gitignored, so they
are the intended way to tune a skill's behavior without editing the tracked skill itself
and without a future update overwriting your change. When a skill supports one, its
SKILL.md says so. Prefer this over hand-editing a tracked skill whenever it is available.

### 4.4 Telegram, Viraid, and Sentinel

These three deserve their own walkthrough: creating your capture and alert channels,
pointing Viraid at your channel, and tuning what Sentinel watches and where it sends. See
[TELEGRAM-AND-ALERTS](TELEGRAM-AND-ALERTS.html).

---

## 5. What stays

Two things are the engine's provenance, not your personalization, and should not be
scrubbed:

- **Author attribution.** The engine is built and maintained by Misha Hanin /
  31 Concept. That authorship is part of the project's identity and stays in the
  code, the same way any open project keeps its authors. It does not leak into your
  outputs: those use *your* voice and *your* facts from your data overlay.
- **License and notices.** `LICENSE` and `NOTICE` (Apache-2.0) govern your use. Keep
  them.

Making a clone yours is about your substance, not erasing the engine's origin.

---

## 6. Verify

```bash
# personalization completion
uv run python scripts/apply-wizard-answers.py --status

# load the workspace and see it reflect your identity
claude        # then /prime
```

A `/prime` that greets you by your role, cites your business profile, and routes in
your voice means the workspace is yours.

---

## 7. What happens when you update the engine

The engine keeps improving, and you get new versions with a plain `git pull` (or the
`/sync` command, which wraps it). The natural worry is: *will an update wipe the changes
I made?* The answer depends on which kind of thing you changed. There are three kinds, and
they behave very differently.

### 7.1 Your private substance is never touched

Everything personal lives **outside** the engine repository, so an engine update cannot
reach it:

- Your whole data overlay (`.heading-os-data/`): CRM, knowledge, outputs, threads,
  context, plans, your Sentinel config. This is a separate private repository; the engine
  update has no idea it exists.
- Your credentials and machine-local files, which are gitignored inside the engine clone:
  `.env`, `.sessions/`, `.sentinel/`, `.workspace-identity.json`,
  `.claude/settings.local.json`, and the `config/skill-custom/*.user.toml` overrides from
  section 4.3.

Because git never tracks any of these, `git pull` leaves every one of them exactly as it
was. Your facts, voice, keys, logins, and per-skill overrides all carry across updates
untouched. This is the whole point of the engine/data split: your substance is not in the
thing that gets updated.

### 7.2 A brand-new file you create is left alone

If you create something genuinely new, for example your own skill at
`.claude/skills/my-skill/`, a plain `git pull` does **not** delete or overwrite it. An
update only changes files the maintainer also changed; a file that exists only on your
machine is invisible to it and stays put.

The one situation to know about: if the maintainer later ships a skill with the *same
folder name* you chose, the two collide. Avoid it by giving your own skills a distinctive
name (a personal prefix such as `acme-` works well). A brand-new script, rule, or reference
file behaves the same way: yours, untouched, unless a future update happens to add a file
at the identical path.

### 7.3 Editing a file the engine also ships is the case to handle with care

This is the one that can bite. If you **edit a tracked engine file** (a shipped rule like
`terminology.md`, or a shipped skill) and the maintainer later changes that same file, the
update cannot simply fast-forward. HEADING OS pulls with `--ff-only` on purpose, so instead
of silently merging or clobbering your edit, the pull **stops and tells you** the histories
diverged. Nothing is lost; you are just asked to decide.

Two clean ways to work so this never hurts:

1. **Prefer a non-tracked seam when one exists.** For behavior, use a
   `config/skill-custom/*.user.toml` override (section 4.3). For your facts and voice, edit
   the data-overlay documents (section 3.2), not engine rules. These live outside the
   tracked engine, so there is nothing to collide.
2. **When you must edit a tracked file, keep the edit as a small local commit** and expect
   to re-apply it occasionally. The three brand rules (`terminology.md`, `voice.md`,
   `humanization.md`) are meant to be personalized this way. If a `git pull` reports a
   conflict on one of them, it means the maintainer touched the same file; reconcile the
   two versions by hand, keeping your brand language and their improvement.

Rule of thumb: **substance in the overlay, behavior through overrides, and only reach into
tracked engine files when there is no seam, keeping those edits small and committed.** Do
that and updates stay boring, which is what you want.

### 7.4 What an update actually pulls

For completeness, `git pull` (and `/sync`) refresh the shared engine: skills, rules,
scripts, hooks, docs, config templates. On a managed multi-person deployment, `/sync` also
refreshes the separate corporate-content clone. Neither ever pulls into your private data
overlay; that repository you push and pull yourself.

---

## 8. Reference

| File / path | Role |
|---|---|
| `.claude/skills/setup-wizard/SKILL.md` | The guided personalization flow |
| `scripts/apply-wizard-answers.py` | Applies and reports wizard answers (`--status`) |
| `config/wizard-templates/` | Templates for business, personal, voice, calendar docs |
| `.workspace-identity.json` | Who the workspace belongs to (gitignored) |
| `.claude/rules/terminology.md` | House vocabulary (adapt to your brand) |
| `.claude/rules/voice.md`, `humanization.md` | Prose and voice rules |
| `.claude/settings.json` / `settings.local.json` | Plugins/hooks (shared) and permissions (local) |
| `.claude/settings.README.md` | How the two settings files relate |
| `config/skill-custom/*.user.toml` | Gitignored per-skill overrides (survive updates) |
| `.heading-os-data/` | Your private data overlay (never touched by an engine update) |
| [TELEGRAM-AND-ALERTS](TELEGRAM-AND-ALERTS.html) | Telegram, capture/alert channels, Viraid, Sentinel |

---

*HEADING OS · Make it yours · maintained by 31 Concept · see also
[INTEGRATIONS-SETUP](INTEGRATIONS-SETUP.html), [MODELS-SETUP](MODELS-SETUP.html), and
[TELEGRAM-AND-ALERTS](TELEGRAM-AND-ALERTS.html) for the services a personalized workspace
connects to.*

---
name: translate
description: Translate text between Russian and English (or other languages on request), preserving register, tone, and meaning rather than producing a literal word-for-word rendering. Use when the user wants a passage translated. Trigger when the user says "translate", "translate this to Russian/English", or pastes Russian text that needs an English rendering (or vice versa). Do NOT use for drafting new content (use the relevant content/comms skill).
argument-hint: "[language] [text]"
allowed-tools: "Read"
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.1"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - translate
x-31c-capability:
  what: >
    Translates text between English and Russian while preserving Misha's
    authentic voice and matching register (personal, business, formal, or tribe).
  how: >
    Type /translate [language] [text]. It reads reference/misha-voice.md first,
    auto-detects direction, returns the translation, and flags any phrases that
    needed cultural adaptation rather than word-for-word translation.
  when: >
    Use to render a message or document in the other language in Misha's voice.
    Leaves ODUN.ONE, 31C, DPI+, and proper nouns untranslated.
---
# Translate

Translate between English and Russian, preserving Misha's authentic voice.

## Variables

direction: en-to-ru | ru-to-en — default: auto-detect
register: personal | business | formal | tribe — default: auto-detect from content

[Paste the text to translate below]

---

[PASTE TEXT HERE]

---

## Instructions

Read `reference/misha-voice.md` before translating (especially the Russian Language Notes section).

Translate the provided text with these guidelines:

**English to Russian:**
- Use natural Russian — not stiff literal translation
- Match the register: personal messages get warmer Russian, business messages get direct professional Russian
- Preserve maritime metaphors where they work in Russian; adapt where they don't
- Do NOT translate ODUN.ONE, 31C, DPI+, or proper nouns
- Personal/emotional messages: allow the warmth to come through more fully than in English (Russian allows it)

**Russian to English:**
- Translate to Misha's English voice — direct, warm, authentic
- Do not over-formalize; Russian formality often over-translates into stiff English
- Flag any cultural nuances the translation should preserve or adapt

After translation:
- Provide the translation
- Note any phrases that required cultural adaptation (not word-for-word translation)
- If business context: suggest any additional 31C terminology to add or adjust

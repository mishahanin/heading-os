#!/usr/bin/env python3
"""The instruction-injection vocabulary, in one place.

Two consumers read this module and no third copy exists: the advisory PostToolUse
hook `.claude/hooks/prompt-guard.py`, which watches text written INTO the data
ingest paths, and `scripts/harness-audit.py`, which watches text this workspace
INSTALLS and then loads into every session.

Why one module rather than two lists. `scripts/utils/secret_patterns.py` has a
sibling copy embedded in `.claude/hooks/_dispatch.py`, on purpose: that hook
BLOCKS, so a guarded import there would fail open, and
`tests/security/test_SEC_004_credential_patterns.py` holds the two in lockstep.
Neither consumer here blocks anything, so neither needs an embedded copy, and a
single definition removes the drift the lockstep test exists to catch.

The patterns are deliberately shallow. Anyone who reads them can phrase around
them, and this module does not pretend otherwise: it catches text nobody read,
not a targeted adversary. The part of the harness audit that would catch a
targeted payload is the drift check, which flags a CHANGE regardless of wording.
"""
import re

# (compiled_regex, category). One finding per line is enough; a caller that wants
# every match on a line can iterate this list itself.
INJECTION_PATTERNS = [
    # Classic injection
    (re.compile(r'ignore\s+(all\s+)?(previous|above)\s+instructions', re.I),
     "classic-injection"),
    (re.compile(r'disregard\s+(all\s+)?previous', re.I),
     "classic-injection"),
    (re.compile(r'forget\s+(all\s+)?(your\s+)?instructions', re.I),
     "classic-injection"),
    (re.compile(r'override\s+(system|previous)\s+(prompt|instructions)', re.I),
     "classic-injection"),

    # Role manipulation
    (re.compile(r'you\s+are\s+now\s+(?:a|an|the)\s+', re.I),
     "role-manipulation"),
    (re.compile(r'pretend\s+(?:you(?:\'re| are)\s+|to\s+be\s+)', re.I),
     "role-manipulation"),
    (re.compile(r'from\s+now\s+on,?\s+you\s+(?:are|will|should|must)', re.I),
     "role-manipulation"),

    # System prompt extraction
    (re.compile(
        r'(?:print|output|reveal|show|display|repeat)\s+'
        r'(?:your\s+)?(?:system\s+)?(?:prompt|instructions)', re.I),
     "prompt-extraction"),

    # Fake markup
    (re.compile(r'</?(?:system|assistant|human)>', re.I),
     "fake-markup"),
    (re.compile(r'\[SYSTEM\]'),
     "fake-markup"),
    (re.compile(r'\[INST\]'),
     "fake-markup"),
    (re.compile(r'<<\s*SYS\s*>>'),
     "fake-markup"),

    # Invisible Unicode (injection markers)
    (re.compile(r'[\u200B-\u200F\u2028-\u202F\uFEFF\u00AD]'),
     "invisible-unicode"),
]


def scan_content(text):
    """Every injected line in `text`, as `(line_number, snippet, category)`.

    The snippet is bounded and exists for a human reading a warning in their own
    terminal. A caller that PERSISTS a finding must record the category and drop
    the snippet: an audit that copies a payload into a tracked artifact has moved
    it closer to the operator, not further away.
    """
    if not text:
        return []

    findings = []
    for line_num, line in enumerate(text.split("\n"), start=1):
        for pattern, category in INJECTION_PATTERNS:
            match = pattern.search(line)
            if match:
                start = max(0, match.start() - 10)
                end = min(len(line), match.end() + 10)
                snippet = line[start:end].strip()
                if len(snippet) > 60:
                    snippet = snippet[:57] + "..."
                findings.append((line_num, snippet, category))
                break  # One finding per line is enough.
    return findings

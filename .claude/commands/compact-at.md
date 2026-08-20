---
description: Set the hard threshold where this session compacts (soft reminder lands 5 below).
argument-hint: "N | status | off"
allowed-tools: "Bash(python scripts/checkpoint-paths.py:*), Bash(python3 scripts/checkpoint-paths.py:*)"
disable-model-invocation: true
---

Set the compaction threshold for THIS session. Do nothing else.

The argument is `$ARGUMENTS`. Read the first token only.

1. If the token is a whole number, `status`, or `off`, run that command:

```bash
python scripts/checkpoint-paths.py --compact-at <token>
```

2. If the argument is empty, run the status command:

```bash
python scripts/checkpoint-paths.py --compact-at status
```

3. If the token is anything else, print one line: `usage: /compact-at N|status|off`. Run no command.

Report the command output in one line. Then stop.

The command refuses a number outside 15-90, and refuses one at or below the
session's last rendered fill. It does NOT turn `auto` or `unattended` on; without
one of those the hook asks at the threshold and compacts nothing.

Do NOT write a checkpoint file. Do NOT run `/compact`. Do NOT continue any task.

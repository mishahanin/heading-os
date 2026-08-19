---
description: Turn unattended mode on or off for this session (checkpoint unattended switch).
argument-hint: "on | off | status"
allowed-tools: "Bash(python scripts/checkpoint-paths.py:*), Bash(python3 scripts/checkpoint-paths.py:*)"
disable-model-invocation: true
---

Set the unattended switch for THIS session. Do nothing else.

The argument is `$ARGUMENTS`. Read the first token only.

1. If the token is `on`, `off`, or `status`, run that command:

```bash
python scripts/checkpoint-paths.py --unattended <token>
```

2. If the argument is empty, run the status command:

```bash
python scripts/checkpoint-paths.py --unattended status
```

3. If the token is anything else, print one line: `usage: /unattended on|off|status`. Run no command.

Report the command output in one line. Then stop.

Do NOT write a checkpoint file. Do NOT run `/compact`. Do NOT continue any task.
`on` also sets auto mode. Only the operator lowers this switch.

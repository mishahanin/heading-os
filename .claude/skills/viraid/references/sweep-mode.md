# Viraid - Sweep Mode

Consumed by: `.claude/skills/viraid/SKILL.md` when `/viraid sweep` is invoked. Interactive triage of all active tasks. Run when Misha says `/viraid sweep` or "sweep viraid" or "triage viraid tasks".

### Sweep Step 1 -- Load Tasks

1. Read `outputs/operations/viraid/tasks.md`
2. Read `outputs/operations/viraid/state.json`
3. Parse all tasks from the `## Active` section
4. Compute age for each task from its creation date (`YYYY-MM-DD` in the task line)
5. Today's date minus creation date = age in days

### Sweep Step 2 -- Present Tasks by Priority

Group tasks and present in priority order. Flag aging tasks:

```
### Viraid Sweep -- [date]

**P1 -- Critical** ([N] tasks)
1. [ ] **2026-03-16** | `P1` | Start assembling protocol team... | Source: Viraid #9 | **STALE (6d)**
2. ...

**P2 -- Important** ([N] tasks)
3. [ ] **2026-03-21** | `P2` | Audit all Tribe 1:1s... | Source: Viraid #21
4. [ ] **2026-03-19** | `P2` | Discuss AI design with Val... | Source: Viraid #17 | **AGING (3d)**
5. ...

**P3 -- Background** ([N] tasks)
6. ...

---
**Stats:** [N] active | [N] aging (>3d) | [N] stale (>7d) | Completion rate: [N]%

For each task: **Complete / Keep / Delegate / Delete**
Or: "complete 1,3,5", "delete 2", "keep all P2", "delegate 4 to [person]"
```

**STOP HERE and wait for Misha's response before proceeding.**

### Sweep Step 3 -- Execute Decisions

| Decision | What happens |
|----------|-------------|
| **Complete** | Move task from Active to Completed section with completion date and original priority |
| **Keep** | Leave in Active, no changes |
| **Delegate** | Add "[Delegated to: Name]" suffix, keep in Active |
| **Delete** | Remove from Active entirely (not moved to Completed -- it was never done) |

**Completing a task** -- move from `## Active` to `## Completed`:
```markdown
- [x] **2026-03-16** | `P1` | Start assembling protocol team... | Source: Viraid #9 | Completed: 2026-03-22
```

### Sweep Step 4 -- Update State

After sweep execution:
1. Write updated `outputs/operations/viraid/tasks.md`
2. Update `state.json`:
   - `stats.completed_count` -> total items in Completed section
   - `stats.completion_rate` -> `completed_count / (completed_count + active_count)`
   - `last_run` -> current ISO timestamp

### Sweep Step 5 -- Report

```
### Sweep Complete -- [date]
- Completed: [N]
- Kept: [N]
- Delegated: [N]
- Deleted: [N]
- Remaining active: [N] (P1: [N], P2: [N], P3: [N])
- Aging: [N] | Stale: [N]
- Completion rate: [N]% (was [N]%)
```

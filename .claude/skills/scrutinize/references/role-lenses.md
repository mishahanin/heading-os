# Role Lenses - /scrutinize discipline checklists

**Consumed by:** `.claude/skills/scrutinize/SKILL.md` (Phase 1, Phase 2)
**Last Updated:** 2026-08-09
**Last Verified:** 2026-08-09

A lens is a checklist plus tool bindings. The job title on it is a label, not a
persona: a prompt that says "you are a Senior Infrastructure Engineer" is costume
and adds nothing a checklist does not already give. What makes a lens real is
that it ships three things a generic reviewer does not have - its own artifacts
to read, its own commands to run, and a closed list of defects it must check.

**The rejection rule.** A finding attributed to a lens that cites neither one of
the lens's commands nor one of its artifacts is not a lens finding. Strike the
attribution and let the finding stand or fall on its own evidence.

## Why these three, and not a longer list

Derived from what this codebase measurably is, not from a list of job titles.
The engine is 671 Python files against a deliberately thin third-party surface
(yaml, apscheduler, sqlite3, requests, fastapi, pydantic), so a generic
"senior Python developer" lens would re-check what ruff, bandit and
`artifact-evaluator.py` already check deterministically. The largest specialised
surface is operations: 29 systemd unit and timer templates, 17 installers, 5
long-running daemons, a watchdog and a fleet-health probe - and two production
incidents already enshrined as rules.

**Where the value actually sits.** One of those two incidents is guarded by a
test (`tests/test_scheduler_misfire_guard.py`), and one is guarded by nothing:
`grep -rl enable-linger tests/` returns nothing. A lens re-catching a guarded
defect adds little. A lens is the only thing that catches the unguarded one, and
it catches it best in a PLAN, before the installer is written. Read these lenses
as prospective, not as a second opinion on code the deterministic layer already
covers.

**Deliberately absent.** No business-strategy or content lens. That defect class
is real in this workspace - a proposal contradicting the DataStore, a round
number where `voss.md` forbids one - but this skill routes content quality to
`/evaluate` and `/validate`. The lens is missing to preserve a boundary, not
because the defects are imaginary. Do not add it back as an "oversight".

## Selection

Path decides scope; lens decides discipline. The two axes compose - they do not
replace each other, and the five path areas in `workspace-areas.md` are
unchanged.

A lens fires **iff** its trigger matches a path in the resolved scope. The
trigger table is code, in `scripts/scrutinize-dispatch.py` (`LENS_GLOBS` and the
scheduler content markers), because a trigger the model decides is not a trigger.
Run `python scripts/scrutinize-dispatch.py --role-scan --run-id <id> --target <t>
--paths <...>`; each firing lens writes one `role` row into the run record.

By default a lens is a checklist the main reviewer loads. It becomes a separate
agent only when the scope is large enough to need the parallelism, or when the
independence of handing it to the k3 side is worth the latency. The global cap of
5 parallel agents still applies.

---

## Lens: ops (operations and units)

**Triggers:** `*.service`, `*.timer`, `*install-*.sh`, `*/templates/systemd/*`

**Artifacts:** `scripts/templates/systemd/README.md`, the rendered unit under
`~/.config/systemd/user/`, the installer that writes it.

**Taxonomy** (from the reboot-survival rule in `.claude/rules/development-standards.md`):

1. **The triad, all three or none.** `Persistent=true` in the `.timer`,
   `systemctl --user enable` with `WantedBy=timers.target`, and
   `loginctl enable-linger "$USER"` in the installer. The third is the one
   forgotten, and without it a user timer is silent after an unattended reboot
   while the first two make it look installed.
2. **Timezone by token, never by literal.** Templates carry `{{TZ}}`, filled from
   `HEADING_OS_TZ`. A geographic literal in a template is a defect.
3. **A health gate polls, it does not probe once.** `systemctl start` returns on
   spawn, not on listen. A single immediate probe rolled back a healthy binary
   once already; the gate waits with a deadline.
4. **A new timer is a plan-level constraint, not an implementation detail.** If a
   plan adds one, reboot survival belongs in its Constraints and its Validation.

**Commands:**

```bash
systemctl --user is-enabled <name>.timer     # expect: enabled
loginctl show-user "$USER" | grep Linger     # expect: Linger=yes
grep -c 'Persistent=true' ~/.config/systemd/user/<name>.timer
```

---

## Lens: scheduler (in-process job lifecycle)

**Triggers:** any Python file importing `apscheduler` or calling `add_job`
(content-triggered - a daemon is a plain `.py` and only its imports say it
schedules anything).

**Artifacts:** `scripts/utils/scheduler_defaults.py`, the daemon's scheduler
construction site, `tests/test_scheduler_misfire_guard.py`.

**Taxonomy** (from the misfire rule in `.claude/rules/development-standards.md`):

1. **`job_defaults` on the scheduler, never per job.** A scheduler-level default
   is inherited by jobs registered later, by authors who never read the rule; a
   per-`add_job` argument is not. This exact distinction cost 1059 of 1440
   heartbeat runs while systemd reported the daemon healthy.
2. **`misfire_grace_time` present and deliberate.** APScheduler defaults it to 1
   second, so a job whose due moment slips past that is DISCARDED with a journal
   warning and nothing else.
3. **Behaviour when a tick is late is stated.** Coalescing, catch-up, or drop -
   pick one and say which.
4. **The failure is silent by construction.** A scheduler defect does not raise;
   it under-runs. Treat "the daemon is healthy" as evidence of nothing.

**Commands:**

```bash
.venv/bin/python -m pytest tests/test_scheduler_misfire_guard.py -q
grep -rn "AsyncIOScheduler(\|BackgroundScheduler(" scripts/ | grep -v job_defaults
```

---

## Lens: boundary (repository and egress)

**Triggers:** `*routing-map.yaml`, `.claude/hooks/*`, `*leak-guard.py`,
`*engine_guard.py`, `*tool-risk.json`

**Artifacts:** `config/routing-map.yaml`, `.claude/rules/classification.md`,
`.claude/rules/tiered-risk.md`, `.claude/rules/lethal-trifecta.md`,
`config/tool-risk.json`.

**Taxonomy:**

1. **A new write path fails closed.** The routing map's default is `engine`,
   which is the SHAREABLE side. Every data directory carries an explicit
   `private` rule for that reason; a new one that does not is a leak waiting for
   its first write.
2. **A new `action_type` that can send anything outbound is in `send_capable`.**
   Forgetting also fails safe (unknown resolves `gated`), but the omission is
   still a defect: the ledger is data, the send gate is code, and the invariant
   is that data can raise friction and never lower it.
3. **A path's routing destination does not contradict the map.** Longest matching
   rule key wins; a new file placed on the wrong side of the engine/data seam is
   invisible until it is public.
4. **A guard that can be bypassed is named as bypassable.** The commit hook is
   skippable by `--no-verify`; the push-time scan is not. Do not describe the
   first as the wall.

**Commands:**

```bash
.venv/bin/python scripts/leak-guard.py
.venv/bin/python scripts/classification-health.py
.venv/bin/python scripts/secret-scanner.py <changed-files>
```

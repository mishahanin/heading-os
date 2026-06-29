<!-- version: 1.2.0 | last-updated: 2026-06-26 -->

# Emergency Procedures

> What to do when the normal sync/push/update chain is broken. For routine operations, see `GETTING-STARTED.md` (execs) or `CEO-ADMIN-GUIDE.md` (CEO).

---

## Scenario 1: Corporate repo inaccessible (GitHub outage, credentials revoked, repo deleted)

**Exec-side symptoms:** `/sync` (or a manual `git pull` on your clones) starts failing with `git pull` / auth errors. Your engine clone and `corporate/` content stop receiving updates. (There is no scheduled sync job anymore -- sync is the manual `git pull` wrapped by `/sync`.)

**What you keep:**
- Everything in `personal/` (your CRM, knowledge notes, local plans, outputs)
- The last-pulled copy of `corporate/` content from before the outage (your local clone)
- All `.claude/skills/` and `.claude/rules/` (these live in your engine clone, so you hold the last good version)

**What to do:**

1. Don't panic. Your workspace remains fully operational on the last pulled state. Only new CEO-pushed updates will be missing.
2. Stop retrying `/sync` -- it will keep erroring until access is restored. There is no scheduled job to disable; nothing is alarming in the background.
3. Contact CEO via the non-workspace channel (Telegram, personal phone). Do not assume Slack/email works if CEO infra is implicated.
4. Wait for CEO confirmation of restoration, then run `/sync` once to catch up.

**Do NOT:**
- Try to push your own changes "to help." Only the CEO publishes to the corporate repo.
- Clone a replacement copy from a third party. The canonical repo URL is set by the CEO; a wrong URL silently corrupts the trust chain.
- Modify files inside `corporate/`. They will be overwritten the moment sync resumes.

---

## Scenario 2: CEO workspace unavailable (CEO machine compromised, corrupted, offline for extended period)

**Fleet-side symptoms:** BUILD.json stops advancing. No new pushes from CEO for >1 week despite normal CEO activity.

**Exec autonomy during outage:**
- Continue operating from local `corporate/` snapshot. All skills work.
- Personal CRM, knowledge notes, local outputs all function normally.
- `/backup` still works (`push-all.py` pushes your data repo to your own GitHub).
- `/sync` still works for the data-up backup (your CRM rides your private `heading-os-data-{slug}` repo).

**Bridge mode:** If the CEO designates a Deputy Admin in advance (via `config/admin.json` role field), the deputy can:
- Run `/publish-corporate` from their own workspace (requires they hold GitHub push rights)
- Issue temporary policy updates via an emergency branch, clearly marked `emergency-{date}`
- Never override classification boundaries; all deputy pushes must still pass `sanitize-check.py`

**Do NOT:**
- Elect an unofficial deputy. Admin authority flows from `config/admin.json`, not from group consensus.
- Push directly to the corporate repo without explicit admin role.
- Attempt credential recovery on the CEO's behalf.

---

## Scenario 3: Credentials leaked (API key, session token, or password exposed in a commit)

**Who this applies to:** CEO and every exec equally.

**Immediate action (within 30 minutes of discovery):**

1. **Rotate the credential first, scrub history second.** In that order.
   - API key: regenerate in the provider's console, update `.env` locally, confirm scripts work
   - Password: change in the source-of-truth system (password manager + provider), confirm access
   - OAuth/session token: invalidate via provider admin panel, re-auth
2. Notify CEO via Telegram (non-compromised channel). Include: which credential, where it leaked, rotation status.
3. Only after rotation: remove from git history. For a single commit:
   ```
   git reset --hard HEAD~1  # if local only and no one has pulled
   ```
   For already-pushed commits, use `git filter-repo` with the expression list per `.claude/rules/security.md`.
4. Force-push: `git push --force origin main` (CEO approves this explicitly for the affected repo only).
5. Document the incident in `outputs/operations/security/YYYY-MM-DD-{slug}.md`.

**Post-incident:**
- Add the leaked term to `.secrets.baseline` known patterns to prevent recurrence.
- Review commit history for adjacent exposures (often one leak means others).

---

## Scenario 4: Sentinel schedule not firing

> The hourly **sync** schedule was retired -- code/content sync is now a manual `git pull` (wrapped by `/sync`), so there is no sync task to fail. The only scheduled task is the 15-min Sentinel comms monitor. This scenario covers Sentinel.

**Windows symptoms:** Task Scheduler shows `31C-Sentinel-{slug}` with `Last Run Result: 0x1` or `0x8007010B`. Logs at `.sync/logs/sentinel-check-task.log` empty or stale.

**macOS symptoms:** `launchctl list | grep 31c` shows the Sentinel agent, but `.sync/logs/` has no recent entries.

**Recovery:**

1. Manually run `/sentinel` (or `python scripts/sentinel.py --check`) once and confirm it completes end-to-end.
2. Check the scheduled task command matches the current Python interpreter path:
   - Windows: `schtasks /Query /TN "31C-Sentinel-{slug}" /V /FO LIST` and look at `Task To Run`
   - macOS: `cat ~/Library/LaunchAgents/io.31c.sentinel.{slug}.plist`
3. If the path is stale (e.g., after Python upgrade), reinstall via `scripts/setup.py --reinstall-schedule`.
4. If still failing, run `python scripts/utils/schedule.py` directly to see the diagnostic output.

> Reminder: to refresh corporate content or engine code, just run `/sync` (a plain `git pull`) -- there is no sync schedule to repair.

---

## Scenario 5: Secret leaked and committed (already on GitHub)

Do both:

1. Execute Scenario 3 above (rotate + scrub + force-push).
2. **Assume the secret is compromised.** GitHub caches, forks, and archive services (archive.org) may have the exposed version indefinitely. Scrubbing history from the repo does not remove it from those surfaces.
3. Monitor the credential's provider for anomalous usage for 30 days.
4. If the credential grants write access to customer data, treat as a security incident per `docs/security/SECURITY-CONSTITUTION.md` and notify affected parties.

---

## Scenario 6: Bridge daemon dead (dashboard stale, sync-pill red)

Symptoms: browser dashboard at `http://127.0.0.1:<port>/` is unreachable, sync-pill is red, or `/bridge-health` reports `stale`/`missing` for your workspace.

1. Probe the local daemon:

   ```bash
   python scripts/bridge-daemon.py --health
   ```

   - Exit 0: daemon reachable, the dashboard pinned to a stale tab is the only issue - reload the browser.
   - Exit 1: daemon dead but `.daemon-state/heartbeat.json` survived; the script prints the last-known state from disk.
   - Exit 2: daemon never started, no on-disk state either.

2. Restart the daemon:
   - **Windows:** `& scripts\launch-bridge-daemon.bat`
   - **macOS:** `launchctl kickstart -k gui/$UID/com.31c.bridge-daemon`

3. If restart fails repeatedly, tail the log to find the crash cause:

   ```bash
   tail -100 .daemon-state/bridge.log
   ```

4. If the log shows `config snapshot failed` or `config reconcile failed` warnings, the `corporate/daemon/config.yaml` may be malformed. Roll back:

   ```bash
   python scripts/bridge-daemon.py --revert-config
   # then restart
   ```

5. If none of the above clears it, escalate to the CEO via the contact hierarchy below.

---

## Scenario 7: Auth token lost or compromised on bridge daemon

Symptoms: dashboard returns 401 on every page, or you suspect another local process read `.daemon-state/token`.

1. Rotate the token immediately:

   ```bash
   python scripts/bridge-daemon.py --rotate-token
   ```

   This rewrites `.daemon-state/token` with a fresh random nonce. **The running daemon still holds the OLD token in memory** - you must restart for the new token to take effect.

2. Restart the daemon per Scenario 6 step 2.

3. Reload the dashboard. The browser's `/_bootstrap` call refreshes its in-memory bearer token from the new file.

4. If the leak was via a committed `.env` or git history, treat as Scenario 3 (general credential leak) - rotate every adjacent credential on the same machine.

---

## Scenario 8: Bad `corporate/daemon/config.yaml` push breaks the whole fleet

Symptoms: after a CEO `/push-updates`, multiple execs report `error` or daemons in crash-loop. `daemon-fleet-health.py` shows red rows across the fleet.

CEO-side recovery:

1. Revert the corporate repo:

   ```bash
   cd ../31c-corporate
   git log -- daemon/config.yaml  # find the last-good commit
   git revert <bad-commit-hash>
   git push origin main
   ```

2. Each exec's daemon will pick up the revert on its next 60-second reconciliation tick (Phase B reconciliation tick from spec 3.6). Worst case: until the exec's next manual `git pull` (`/sync`) brings the reverted config down, plus 60s for the daemon tick.

3. For execs that need immediate recovery (before next sync), they can run:

   ```bash
   python scripts/bridge-daemon.py --revert-config
   ```

   This restores the most-recent prior snapshot from `.daemon-state/config-history/` to `.daemon-state/config.yaml` (per-user override). Daemon must be restarted to apply.

4. Verify recovery via `python scripts/daemon-fleet-health.py` - fleet should return to all-green within 5 minutes of step 1.

5. Do NOT push another corporate config change until the root cause is identified. Re-pushing the bad config (even by accident) restarts the crisis.

---

## Contact Hierarchy for Emergencies

| Channel | When to use |
|---------|-------------|
| Telegram direct to CEO | Primary escalation path for all above scenarios |
| Personal phone (SMS/call) | If Telegram is part of the compromise |
| Exec-to-exec via Telegram group | For coordinating exec-side response during extended CEO outage |
| Email to misha.hanin@31c.io | Last resort; assume monitored and non-urgent |

---

## Do-Not List (applies to all scenarios)

- Never post credentials, even partially, in any chat channel or commit.
- Never share screenshots of `.env` or `.sessions/` contents.
- Never attempt to "just quickly" bypass a safety gate (pre-commit hook, classification check, sanitize-check, VPN pre-flight) without CEO approval.
- Never modify `corporate/` on an exec workspace.
- Never push to the corporate repo from an exec workspace.
- Never disable the sync schedule permanently - only as a temporary response to a known outage.

For routine workflow: `GETTING-STARTED.md`. For admin procedures: `CEO-ADMIN-GUIDE.md`. For deeper security policy: `docs/security/SECURITY-CONSTITUTION.md`.

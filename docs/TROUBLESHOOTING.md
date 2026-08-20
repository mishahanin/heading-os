# Troubleshooting

Common problems, grouped by where they bite: install, data wiring, memory and recall, browsing, sending, and pushing. For a full outage playbook (a lost credential, a broken sync chain) see [emergency procedures](EMERGENCY-PROCEDURES.html).

## First move: run the health check

Most "something is off" reports resolve to one failing check. Run it first:

```bash
uv run python scripts/workspace-health.py
```

It reports the state of the data seam, the credential files, the commit gate, and the doc versions. Fix what it flags before chasing anything else.

## Install and toolchain

**`uv sync` fails or the wrong Python is used.** The engine targets Python 3.11+, managed by `uv`. Confirm `uv --version` and let `uv sync` create the environment; do not mix a system `pip` install into it. See [prerequisites](prerequisites.html).

**On Windows.** The engine runs under WSL2, not native Windows. Do the install and all work inside the WSL distribution. See the [full deployment guide](DEPLOYMENT.html).

**The commit hook does not fire on a fresh clone.** Git hooks are machine-local. Git does not share them, so a fresh clone has none until you arm them:

```bash
pre-commit install
```

Run it once per clone or relocation. Verify with `python scripts/install-hooks.py --check`. Never commit with `--no-verify`: the authoritative gate is the push-time scan, but the commit hook is the fast local warning.

## Data wiring

**The engine cannot find your data.** The engine resolves your private data repository through one seam. It looks for a sibling directory, or the `HEADING_OS_DATA` environment variable. If a skill reports a missing data root:

- Confirm your data repository exists (create it with `uv run python scripts/create-data-repo.py`).
- Set `HEADING_OS_DATA` to its path if it is not a sibling of the engine clone.

**A file landed in the engine that should be private.** The classifier fails closed, but if you wrote through an unusual path, check `config/routing-map.yaml` and run `scripts/classification-health.py`. Real data belongs in the data overlay; see [data overlay structure](data-structure.html) and [configuration](CONFIGURATION.html).

## Managed workspace identity

These bite only on a managed (administrator-provisioned) workspace. All three share one root cause: the engine does not know who it is running as.

**`/prime` says "No workspace-identity.json found — treating as CEO workspace".** The engine reads exactly one path, `<engine-root>/.workspace-identity.json`, and the name starts with a dot. A copy that drops the dot, or appends `.txt`, or lands in the data repository instead of the engine, leaves the engine with nothing to read. It does not stop: it falls back to a single-user workspace and keeps running, which is why this failure is quiet. Confirm the file with `ls -la .workspace-identity.json` in the engine root, replace it, then restart `claude` — the identity is cached for the life of the process. The required shape is in [deployment](DEPLOYMENT.html) section 6.

**`sync-corporate.py` prints "CEO workspace — nothing to consume".** Same cause. The script is a deliberate no-op for the CEO, who publishes corporate content rather than consuming it. Seeing it on an executive workspace means the identity file is missing or its `type` is not `exec-workspace`.

**`sync-corporate.py` fails on `gh repo clone /heading-os-corporate`.** The organisation name resolved to an empty string, so the repository argument has nothing before the slash. Set `github_org` in `<data-root>/config/operator.yaml`. A freshly scaffolded data repository ships no `config/` directory, so a new managed workspace has no such file until someone adds it. Setting `HEADING_OS_OPERATOR_GITHUB_ORG` in `.env` does not substitute: the script reads the organisation before it loads `.env`.

**A backup pushed into the engine and got a 403.** The engine is read-only on a managed workspace, so the 403 is the safety net working. Check that `push-all.py --dry-run` prints "Exec workspace — pushing the data overlay only". Any other first line points back at the identity file.

## Plugins

**An install reports `Plugin "" not found in marketplace`.** The plugin name arrived empty. A pasted multi-line block broke just before it. The terminal inserted a blank line inside a `\` continuation, so bash read two broken commands, and one of them ran with no argument. The paste usually also prints `bash: syntax error near unexpected token`. Install plugins one physical line at a time, never as a `for` loop pasted from a document.

**An install reports "not found in marketplace latest".** The form `plugin@latest` makes Claude search for a marketplace named "latest". Use `<plugin>@<marketplace>`, and add the marketplace first with `claude plugin marketplace add anthropics/claude-plugins-official`.

## Memory and recall

**`/recall` returns nothing or errors on the embedder.** Recall uses a local `bge-m3` embedder served by Ollama, on-machine at zero API cost. Confirm Ollama is installed and running and the model is pulled. See [AI models](MODELS-SETUP.html).

**The first index build takes a long time.** A full memory-index build is CPU-heavy. On a machine without a GPU it can run for an extended period. It is resumable and commits per file, so do not kill it with a short timeout; let it finish once, then incremental refreshes are fast.

## Browsing operations

**YouTube, Google, or LinkedIn return a blocked or empty result.** Public services block datacenter and many VPN exit IPs. The [VPN pre-flight rule](RULES-REFERENCE.html) gates these operations for exactly this reason. Switch to a residential-friendly exit before running a browsing skill, and confirm the pre-flight prompt.

**A browser automation skill refuses to launch.** The CDP-attach helper refuses to start when the automation browser is already running. Close the existing automation-profile browser and retry.

## Sending

**An outbound message will not send on its own.** That is the design, not a bug. Every outbound send is human-gated by the [lethal-trifecta control](SECURITY-MODEL.html): an agent drafts and queues, a human approves. Approve from the terminal with the Action Queue (`scripts/action-queue.py approve <id>`), which sends synchronously in that command.

## Pushing and backup

**A push is blocked with a secret-scan hit.** The push-time content scan is the unbypassable wall, with no skip flag. Remove the secret from the file, move it to `.env` or a password manager, then push again. Never reach for `--no-verify`; it does not bypass the push scan and it is forbidden.

**A push seems to hang.** The engine's pre-push gate runs the regression suite before it lets a push through, which takes a few minutes. Give it time (or run the push in the background) rather than killing it. The data repository has no such gate.

## Related

- [Emergency procedures](EMERGENCY-PROCEDURES.html): the full outage and incident playbook.
- [Prerequisites and install](prerequisites.html): the toolchain baseline.
- [Security model](SECURITY-MODEL.html): why sends are gated and pushes are scanned.

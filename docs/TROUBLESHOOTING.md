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

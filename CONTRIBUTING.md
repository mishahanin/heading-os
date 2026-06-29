# Contributing to HEADING OS

Thanks for your interest. HEADING OS began as the system one chief executive runs their company from, opened so others can study, run, and adapt it. Here is how to engage with it.

## Issues are welcome

Open an issue for any of these:

- **Bug reports** — something behaves differently than the docs say. Include your OS, Python version, the command you ran, and the output (with secrets removed).
- **Questions** — about the architecture, the setup, or how a piece works. Use the discussion-style issue template.
- **Ideas and feature requests** — what you'd want the engine to do, and why.

Before opening one, a quick search of existing issues saves everyone time.

## Pull requests are by invitation

This is a single-maintainer project with a deliberate direction, so code contributions work a little differently than a typical open-source project:

1. **Open an issue first** describing the change you have in mind.
2. If it fits the direction, the maintainer will say so and invite a pull request.
3. Then send the PR, referencing the issue.

This keeps the system coherent and avoids anyone investing real effort in a change that does not fit. Unsolicited PRs may be closed with a pointer to this process — it is not a judgement of the work, just how the project is run.

## If you are invited to send a PR

Keep the bar the engine holds itself to:

- **Tests.** Every behaviour you change needs a test. The suite lives in `tests/` (security tests in `tests/security/`). Run it with `python scripts/run-tests.py`.
- **Lint and format.** `ruff` is the linter; `pre-commit` runs it (and the secret scan) on commit. Run `pre-commit install` once, and never commit with `--no-verify`.
- **Security first.** No secrets in tracked files. No forbidden patterns (`eval`/`exec` on input, `pickle` on untrusted data, `shell=True`, unsafe YAML, disabled TLS). Anything touching auth, send, or the data seam gets extra scrutiny.
- **Scope discipline.** Change what the issue asks for. Don't refactor adjacent code in the same PR.
- **No new dependencies** without raising it in the issue first; pin exact versions.
- **Match the surrounding style.** Read the file before editing it.

## Development setup

The full environment setup is in [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md). The short version:

```bash
uv sync --group dev      # runtime + dev tools (pytest, ruff, pre-commit)
pre-commit install       # arm the commit-time gates
python scripts/run-tests.py
```

## Code of Conduct

Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md). Be decent.

## License of contributions

By submitting a contribution, you agree it is licensed under the project's [Apache License 2.0](LICENSE), consistent with Section 5 of that license.

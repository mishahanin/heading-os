---
paths:
  - "scripts/**"
---

# X31C Trace ID Convention (R12)

Last Updated: 2026-06-03
Last Verified: 2026-06-03

One correlation ID per process tree, so "what happened in this run?" is a single grep across the daemon log surfaces instead of a cross-file timestamp hunt. Implemented by `scripts/utils/trace.py` (mint/get/set) and `scripts/utils/trace_filter.py` (the log-record factory + filter).

## Scope (honest)

The ID correlates **one process tree** - a daemon boot plus the subprocesses that boot spawns - via an environment variable that children inherit. It is **not** a per-business-flow ID that follows an email or a deal across multiple daemons; an item touched by sync-exchange, then bridge, then sentinel gets three different IDs. True cross-daemon flow-threading (passing one ID through a shared queue/file) is deferred to a later phase.

## Mint points

Call `trace.mint()` once at process entry, before any logging is configured:

- Daemons: inside each `_setup_logging()` (fireside, sync-exchange, eval-drift, sentinel) or at the top of `start_daemon()` (bridge). All five do this today.
- New CLI scripts that log: mint at the top of `main()` (or call `trace.ensure()` to adopt an inherited ID if present, else mint).

A fresh UUID4 per boot means no cross-restart contamination.

## Environment propagation

The ID lives in `os.environ["X31C_TRACE_ID"]`. Every `subprocess.run([...])` a daemon issues inherits it automatically because none of the daemons pass an `env=` override. **If you add a `subprocess` call with an explicit `env=` dict, build it from `os.environ` (`dict(os.environ, ...)`) or add `"X31C_TRACE_ID": os.environ.get("X31C_TRACE_ID", "")`** so inheritance is preserved. (Audit: `grep -rn "subprocess\.\(run\|Popen\)" scripts/ | grep env=` - the only hit, `modem-tune.py`, already copies `os.environ`.)

## Log format

Install the record factory once per process (`install_log_factory()` from `trace_filter`), which gives **every** `LogRecord` a `trace_id` attribute (defaulting to `"-"`), then include `[%(trace_id)s]` in the formatter. The factory means a formatter referencing `%(trace_id)s` never raises `KeyError`, even on records emitted by third-party loggers. New single-logger CLI scripts can use `trace_filter.attach(logger)` to do all of this in one call.

Direct file appends that bypass the logging module (e.g. eval-drift's `errors.log`) should prepend `trace.get()` manually so they correlate with the formatted lines.

## Langfuse bridge

When observability is active, `scripts/utils/observability.py`'s `@observe` wrapper stamps `metadata={"x31c_trace_id": ...}` onto the current Langfuse trace at call time (best-effort; never breaks the traced call). This links Langfuse traces to the `[trace_id]` log lines for the same run.

## Skills convention (not enforced this phase)

Interactive skills run inside the Claude session, where an env-var trace ID is fuzzy. The convention - export the ID before Bashing a script so the script's logs join the session's flow - is documented but not enforced this phase. The mechanical, high-value surface is the daemon/script/log path above.

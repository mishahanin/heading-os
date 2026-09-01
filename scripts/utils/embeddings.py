"""Local embedding wrapper over a running ollama instance.

Thin, reusable client for the workspace associative-memory index. No store
logic, no path knowledge -- just text in, vectors out, against a local ollama
embed endpoint (default bge-m3, 1024-dim, multilingual RU/EN).

Sovereignty: all computation is local. The host is passed in by the caller
(read from config/memory-index.yaml) so a future VM offload is a one-line
config change, never a code change.

Usage:
    from scripts.utils.embeddings import embed
    vecs = embed(["sovereignty", "пилот"], model="bge-m3",
                 host="http://localhost:11434")
    # vecs -> list[list[float]], one 1024-dim vector per input text
"""

import json
import time
import urllib.error
import urllib.request

from scripts.utils.ollama_host import is_http_url


# The model this workspace embeds with when `config/memory-index.yaml` names
# none. Kept identical to `scripts/memory-index.py`'s own
# `cfg.setdefault("model", ...)`, and pinned equal to it by
# tests/test_memory_health_redundancy.py: two fallbacks that disagree would split
# the corpus on exactly the machine whose config went missing.
INDEX_EMBED_MODEL_DEFAULT = "bge-m3"

# How long ollama holds the embed model resident after a request, when
# `config/memory-index.yaml` names no `keep_alive`.
#
# Ollama's own default is five minutes, and that default is what made the first
# query after any pause cost 7.00 s against 0.87 s warm, measured 2026-08-22 on
# the Windows-side instance this workspace embeds through. The 6.1 s is the model
# being read back into video memory; nothing about the index or the query changed.
# A working session pauses for more than five minutes constantly, so the slow
# path was the common one -- including for the `recall-inject` hook, which fires
# on 80% of prompts.
#
# `keep_alive` is decided per request, by the most recent one, so every request
# carries it. bge-m3 holds 664 MB while resident.
INDEX_EMBED_KEEP_ALIVE_DEFAULT = "30m"


class EmbeddingError(RuntimeError):
    """Raised when the local embedder is unreachable or returns no vectors."""


def _index_config(root=None) -> dict:
    """`config/memory-index.yaml`, or {} when it cannot be read.

    Degrades rather than raising: every caller below is advisory, interactive, or
    a health probe, and none of them should die over a config file.

    `root` exists for callers that already know which workspace they mean -
    `scripts/memory-index.py` is handed a root and must not silently read a
    different clone's config through `get_workspace_root()`.

    A config it cannot read is NAMED on stderr before the `{}` goes back. The
    fallbacks this degrades onto are real choices - an embedding host, a chunk
    size - and a caller that silently ran on defaults could not tell that from
    a config file that genuinely said nothing.
    """
    import sys

    import yaml

    from scripts.utils import yamlio
    from scripts.utils.workspace import get_workspace_root

    # `path` is resolved INSIDE the try, exactly as it was before this fix, so
    # an `OSError` out of `get_workspace_root()` still degrades rather than
    # escaping. It is pre-bound only so the handler can name the file.
    path = "config/memory-index.yaml (workspace root unresolved)"
    try:
        path = (root or get_workspace_root()) / "config" / "memory-index.yaml"
        with open(path, encoding="utf-8") as fh:
            return yamlio.safe_load(fh) or {}
    # An ABSENT config is silent, exactly as it was before this widening: a
    # clone with no `config/memory-index.yaml` is running on defaults on
    # purpose. Only a config that EXISTS and cannot be read is worth a line.
    except FileNotFoundError:
        return {}
    # `UnicodeDecodeError` is a `ValueError`; it is neither an `OSError` nor a
    # `yaml.YAMLError`, so both names here were blind to it. `yaml.safe_load`
    # over an open TEXT handle decodes lazily WHILE it parses, so the error
    # surfaces from inside the yaml call and still is not a `YAMLError`.
    # MEASURED 2026-09-01 with one 0xe9 byte in `config/memory-index.yaml`:
    # `UnicodeDecodeError: invalid continuation byte` raised out of every
    # caller of this function - the recall hook, the index builder and the ops
    # radar - none of which, per the sentence above, should die over a config
    # file.
    except (OSError, yaml.YAMLError, UnicodeDecodeError) as exc:
        print(f"memory-index config at {path} is unreadable ({exc}); "
              f"falling back to built-in defaults for every setting in it",
              file=sys.stderr)
        return {}


def index_embed_preference(*, root=None):
    """The raw, UNRESOLVED embedding host preference. "" when nothing is set.

    One place answers "where should embedding go", so the index builder, the
    ops radar and every embed caller cannot drift apart on the question. It
    returns the preference rather than an address because the resolvers differ:
    embedding refuses when its pin is down, generation degrades.

    Three sources, most explicit first:

    1. `host` in `config/memory-index.yaml` - tracked, so an operator who writes
       one there means it for every clone of this repo.
    2. `HEADING_OS_OLLAMA_EMBED_HOST` - a one-off, for a single run.
    3. `embed:` in `config/ollama-hosts.yaml` - gitignored, this machine's
       standing default. Last because it is a default, not an override.

    Source 3 exists because sources 1 and 2 could not hold the fact. The pin sat
    in the tracked config for one day (2026-08-23) and broke every clone that
    was not this WSL2 laptop: `auto:11434` names whatever answers at the local
    default gateway, and since a pin refuses rather than degrades, a fresh clone
    with a working ollama could not build its index at all.
    """
    import os

    from scripts.utils.ollama_host import machine_hosts

    return (
        _index_config(root).get("host")
        or os.environ.get("HEADING_OS_OLLAMA_EMBED_HOST", "")
        or machine_hosts("embed", root=root)
    )


def index_embed_model(*, root=None) -> str:
    """The model tag this workspace embeds with. Reads a file, probes nothing.

    Split from `index_embed_target` for the one caller that wants the name and
    must NOT touch the network to get it: `ops_signals.ollama_state` asks the
    LOCAL daemon whether the embed model is present, and a health probe that
    resolves a remote host to learn a string would be measuring the wrong machine.

    `root` carries the same meaning it has in `index_embed_preference`, and was
    missing here until 2026-08-30. `ops_signals.ollama_accel_state` is handed an
    engine root, reads the pinned HOST from that root's config, and then asked
    this function for the MODEL - which fell to `get_workspace_root()` and
    answered out of a different clone. Measured that day: an engine root whose
    `config/memory-index.yaml` names `nomic-embed-text`, against a host holding
    only `bge-m3`, was reported as having the embed model pulled. Same defect
    class as the hardcoded `EMBED_MODEL_PREFIX` removed on 2026-08-22, one clone
    deeper: a monitor answering about a model the workspace it names does not use.
    """
    return _index_config(root).get("model") or INDEX_EMBED_MODEL_DEFAULT


def index_embed_keep_alive() -> str:
    """How long the embed model stays resident, read where the host is read.

    In config rather than in a literal for the same reason `host` is: the file is
    where this workspace states how it embeds, so trading residency for video
    memory is a one-line config edit and not a code change.
    """
    return _index_config().get("keep_alive") or INDEX_EMBED_KEEP_ALIVE_DEFAULT


def index_embed_target(*, allow_fallback: bool = False) -> tuple[str, str]:
    """The (host, model) this workspace embeds with, read where the index reads them.

    The host comes from `index_embed_preference()`, which `scripts/memory-index.py`
    also calls, so the builder and every other embed caller cannot disagree about
    where vectors are computed. `model` comes from `config/memory-index.yaml`,
    else `INDEX_EMBED_MODEL_DEFAULT`.

    **A configured host is a PIN, not a preference** (operator directive,
    2026-08-23). When one is set and nothing it names answers, this raises
    `EmbeddingError` instead of quietly embedding somewhere else. The previous
    arrangement degraded to the local daemon on the argument that both hosts ran
    the same `bge-m3` digest, so the vectors matched to cosine 0.99997 — true,
    and it still cost a morning: the Windows daemon came back on port 11434
    instead of the pinned 11436, every recall answered from the WSL CPU, and the
    only signal was a stderr banner the recall hook throws away. The fallback did
    not preserve a capability, it hid an outage.

    `allow_fallback=True` is the named way out, used by
    `memory-index build --allow-host-fallback`. A workspace with NO host
    configured is not pinned to anything and still uses the local daemon: a
    public clone must not need this laptop's Windows side to embed at all.

    Why this exists rather than four literals. Until 2026-08-22 the index, the
    memory-hygiene redundancy scan, `chronicle personal-recall` and the ops radar
    each named their own embedder. None of the first three was WRONG - each
    compares only its own vectors - but `memory_health` spelled out
    `http://localhost:11434` and `chronicle` read only an environment variable
    nobody sets, so both ran on the WSL CPU while the index ran on the Windows
    iGPU. Measured that day on the real auto-memory corpus: 267s against 87s. The
    model copies were the same defect one step quieter: a model changed in config
    would leave a second one resident in ollama beside the first, and nothing
    would say so. The radar's copy was the worst of the four, because a monitor
    that hardcodes what it monitors reports on a model the workspace may have
    stopped using.

    CALL THIS LAZILY, never at module scope. For an `auto:` preference it probes
    a host - work that a command which never embeds (`chronicle stats`,
    `chronicle build`) should not pay for on import.
    """
    import os

    from scripts.utils.ollama_host import (
        LOCAL_HOST,
        OllamaHostUnavailable,
        resolve_ollama_host,
        resolve_pinned_host,
    )

    config = _index_config()
    model = config.get("model") or INDEX_EMBED_MODEL_DEFAULT
    pin = index_embed_preference()

    if not pin:
        return LOCAL_HOST, model
    if allow_fallback:
        return resolve_ollama_host(pin, env_var="HEADING_OS_OLLAMA_EMBED_HOST"), model
    try:
        return resolve_pinned_host(pin), model
    except OllamaHostUnavailable as exc:
        # Re-raised as the error every caller of this module already handles, so
        # a down embedder reads as "cannot embed" and not as an unrelated crash.
        raise EmbeddingError(
            f"{exc}. Embedding is pinned to that host; start it, or pass "
            f"--allow-host-fallback to accept a mixed-provenance store."
        ) from exc


def embed(
    texts,
    *,
    model: str,
    host: str,
    batch: int = 32,
    timeout: int = 120,
    keep_alive: str | None = None,
):
    """Embed a list of texts via a local ollama /api/embed endpoint.

    Args:
        texts: list of strings to embed.
        model: ollama model tag, e.g. "bge-m3".
        host: base URL of the ollama server, e.g. "http://localhost:11434".
        batch: number of texts per request (ollama accepts a list `input`).
        timeout: per-request socket timeout in seconds.
        keep_alive: how long ollama holds the model resident after the request.
            None reads `config/memory-index.yaml`. EVERY batch carries it,
            because ollama takes the value from the most recent request — send it
            once and the last batch of a build would hand the model back the
            five-minute default this exists to replace.

    Returns:
        list[list[float]] -- one embedding vector per input text, in order.

    Raises:
        EmbeddingError: ollama unreachable after retries, or empty response.
    """
    if not texts:
        return []

    if keep_alive is None:
        # Resolved once, outside the batch loop: a build makes thousands of
        # batches and none of them needs the config file re-read.
        keep_alive = index_embed_keep_alive()

    url = f"{host.rstrip('/')}/api/embed"
    out: list[list[float]] = []

    for start in range(0, len(texts), batch):
        chunk = texts[start : start + batch]
        payload = json.dumps(
            {"model": model, "input": chunk, "keep_alive": keep_alive}
        ).encode("utf-8")
        vectors = _post_with_retry(url, payload, timeout)
        if len(vectors) != len(chunk):
            raise EmbeddingError(
                f"embedder returned {len(vectors)} vectors for {len(chunk)} "
                f"inputs (model={model}, host={host})"
            )
        out.extend(vectors)

    return out


def model_digest(*, model: str, host: str, timeout: int = 10) -> str | None:
    """The sha256 of the model weights this host would use. None if unknown.

    A tag is not an identity. `bge-m3` on two hosts is the same NAME and can be
    different WEIGHTS the moment one of them pulls an update -- and vectors from
    two builds of a model are not comparable, while cosine reports a plausible
    number either way. The digest is the only field that changes when the weights
    do, so it is what a provenance check has to compare.

    Returns None rather than raising: the digest is a diagnostic, and failing a
    build because the tags endpoint hiccuped would cost more than the drift it
    detects. A None is recorded as "unknown", never as "same".

    `ValueError` is in the caught set for the same reason, and it is not
    theoretical: a `host` with no scheme (`stub`, or a config typo like
    `172.30.48.1:11436`) makes `urlopen` raise `unknown url type` before any
    socket opens. Letting that through would abort a build with an opaque stack
    trace instead of the clear "cannot reach embedder" the embed call itself
    gives a moment later.
    """
    # `host` arrives from config or an env var, and `urlopen` honours whatever
    # scheme it is handed -- `file:///etc/passwd` would be opened and read. Only
    # http(s) can ever be an ollama endpoint, so the rest is refused before the
    # opener sees it. Same guard, same reason, as `ollama_host.probe`.
    if not is_http_url(host):
        return None
    url = f"{host.rstrip('/')}/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError,
            OSError, ValueError):
        return None
    # Inside the None-not-raise contract, because valid JSON is not a valid
    # reply: a proxy that answers `/api/tags` with a list or a string decodes
    # fine and then raises AttributeError on `body.get` below, aborting the
    # build this docstring exists to keep running.
    if not isinstance(body, dict):
        return None
    # Match the TAG, not the family. `model.split(":")[0]` compared bare names, so
    # asking for `bge-m3:567m` on a host that also holds `bge-m3:latest` returned
    # whichever entry the server listed first - `:latest`'s digest under the
    # `:567m` name. `scripts/memory-index.py` stamps this into `meta.model_digest`
    # and prints "WEIGHTS CHANGED" when it moves, so the one thing the digest
    # exists to detect - a re-pulled tag with different weights - was being read
    # off a different model entirely.
    #
    # Ollama resolves a bare name to `:latest`, so that is the normalisation. The
    # unique-prefix fallback keeps a host that pulled only one specific tag
    # working; two or more candidates return None, because an unproven digest is
    # better than a confidently wrong one.
    want = model if ":" in model else f"{model}:latest"
    family = want.split(":")[0]
    prefix_hits = []
    # The shape guard above asked only whether the ENVELOPE was an object, and
    # the reply's shape does not stop there. MEASURED 2026-09-01 against this
    # function, four replies that reach the loop and leave through an exception
    # rather than through the None this docstring promises:
    #   {"models": ["bge-m3"]}  -> AttributeError: 'str' has no attribute 'get'
    #   {"models": "bge-m3"}    -> AttributeError, iterating the string's chars
    #   {"models": [null]}      -> AttributeError on None
    #   {"models": 5}           -> TypeError: 'int' object is not iterable
    # A proxy or a future ollama that answers /api/tags in any of those shapes
    # aborted `scripts/memory-index.py` with a stack trace, which is exactly the
    # cost this function's "diagnostic, never fatal" contract exists to avoid.
    # Both levels are checked, because one level of shape checking is what the
    # first fix established and it was not enough.
    listed = body.get("models")
    if not isinstance(listed, (list, tuple)):
        return None
    for entry in listed:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", ""))
        full = name if ":" in name else f"{name}:latest"
        if full == want:
            return entry.get("digest") or None
        if full.split(":")[0] == family:
            prefix_hits.append(entry.get("digest") or None)
    if len(prefix_hits) == 1:
        return prefix_hits[0]
    return None


def _post_with_retry(url: str, payload: bytes, timeout: int, attempts: int = 3):
    """POST to the embed endpoint with linear backoff; return embeddings list.

    Catches HTTPError before URLError (HTTPError is a subclass). On the final
    failed attempt, raises EmbeddingError with a clear, actionable message
    rather than swallowing the error.
    """
    last_err = None
    for attempt in range(attempts):
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            # Valid JSON is not a valid reply. A proxy, a misconfigured gateway
            # or an ollama version mismatch can answer 200 with `[]`, `null` or
            # a bare string; each parses fine and then raised AttributeError on
            # `.get` below. AttributeError is in none of the except clauses, so
            # it escaped with NO retry and, worse, as something other than
            # EmbeddingError -- breaking the one contract every caller of this
            # module handles. Measured 2026-08-30 for `[]`, `null`, `"oops"`
            # and `3`. `model_digest` has carried the same isinstance guard, for
            # the same reason, since it was written; this half never got it.
            if not isinstance(body, dict):
                raise EmbeddingError(
                    f"embed endpoint {url} returned a non-object body "
                    f"({type(body).__name__}); an ollama /api/embed reply is a "
                    f"JSON object"
                )
            vectors = body.get("embeddings")
            if not vectors:
                raise EmbeddingError(
                    f"embed endpoint {url} returned no 'embeddings' "
                    f"(body keys: {sorted(body)})"
                )
            return vectors
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code} from {url}: {e.reason}"
        except urllib.error.URLError as e:
            last_err = (
                f"cannot reach embedder at {url}: {e.reason}. "
                f"Is ollama running? (`ollama serve` / check the host in "
                f"config/memory-index.yaml)"
            )
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError) as e:
            # `UnicodeDecodeError` is a `ValueError` and a SIBLING of
            # `json.JSONDecodeError`, not a subclass of it, so the decode one
            # line above this try's `json.loads` was never covered by the clause
            # that covers the parse. MEASURED 2026-09-01 by answering 200 with
            # `b'{"embeddings": [[0.1]]}\xff\xfe'`: it left this module as
            # `UnicodeDecodeError`, with no retry and no backoff, and as
            # something other than `EmbeddingError` - which is verbatim the
            # contract breach the non-object guard twelve lines up was added to
            # end, on the very next expression.
            #
            # `model_digest` in this same file catches bare `ValueError` and so
            # has always been covered; this is the second half of the module
            # again, exactly as the isinstance guard was.
            #
            # Grouped with the parse rather than decoded with `errors="replace"`
            # because they are the same answer: the endpoint returned bytes that
            # are not a JSON object, and the retry is worth taking in case the
            # next attempt reaches a healthy backend.
            last_err = f"malformed response from {url}: {e}"
        except TimeoutError as e:
            # A read-phase timeout (after connection) raises bare TimeoutError,
            # not wrapped in URLError -- without this branch it propagated
            # uncaught and skipped the retry/backoff below entirely.
            last_err = f"timed out waiting for {url} (timeout={timeout}s): {e}"

        if attempt < attempts - 1:
            time.sleep(1.5 * (attempt + 1))

    raise EmbeddingError(f"embedding failed after {attempts} attempts -- {last_err}")

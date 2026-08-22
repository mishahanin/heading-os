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


def _index_config() -> dict:
    """`config/memory-index.yaml`, or {} when it cannot be read.

    Degrades rather than raising: every caller below is advisory, interactive, or
    a health probe, and none of them should die over a config file.
    """
    import yaml

    from scripts.utils import yamlio
    from scripts.utils.workspace import get_workspace_root

    try:
        path = get_workspace_root() / "config" / "memory-index.yaml"
        with open(path, encoding="utf-8") as fh:
            return yamlio.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return {}


def index_embed_model() -> str:
    """The model tag this workspace embeds with. Reads a file, probes nothing.

    Split from `index_embed_target` for the one caller that wants the name and
    must NOT touch the network to get it: `ops_signals.ollama_state` asks the
    LOCAL daemon whether the embed model is present, and a health probe that
    resolves a remote host to learn a string would be measuring the wrong machine.
    """
    return _index_config().get("model") or INDEX_EMBED_MODEL_DEFAULT


def index_embed_keep_alive() -> str:
    """How long the embed model stays resident, read where the host is read.

    In config rather than in a literal for the same reason `host` is: the file is
    where this workspace states how it embeds, so trading residency for video
    memory is a one-line config edit and not a code change.
    """
    return _index_config().get("keep_alive") or INDEX_EMBED_KEEP_ALIVE_DEFAULT


def index_embed_target() -> tuple[str, str]:
    """The (host, model) this workspace embeds with, read where the index reads them.

    Resolution order is the SAME as `scripts/memory-index.py`: `host` from
    `config/memory-index.yaml`, else `HEADING_OS_OLLAMA_EMBED_HOST`, else the
    local daemon; `model` from the same file, else `INDEX_EMBED_MODEL_DEFAULT`.
    An unreachable host degrades to local, because a slower answer beats no
    answer.

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
    from scripts.utils.ollama_host import resolve_ollama_host

    config = _index_config()
    host = resolve_ollama_host(
        config.get("host"), env_var="HEADING_OS_OLLAMA_EMBED_HOST"
    )
    return host, config.get("model") or INDEX_EMBED_MODEL_DEFAULT


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
    want = model.split(":")[0]
    for entry in body.get("models") or []:
        if str(entry.get("name", "")).split(":")[0] == want:
            return entry.get("digest") or None
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
        except (json.JSONDecodeError, KeyError) as e:
            last_err = f"malformed response from {url}: {e}"
        except TimeoutError as e:
            # A read-phase timeout (after connection) raises bare TimeoutError,
            # not wrapped in URLError -- without this branch it propagated
            # uncaught and skipped the retry/backoff below entirely.
            last_err = f"timed out waiting for {url} (timeout={timeout}s): {e}"

        if attempt < attempts - 1:
            time.sleep(1.5 * (attempt + 1))

    raise EmbeddingError(f"embedding failed after {attempts} attempts -- {last_err}")

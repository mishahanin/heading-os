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


class EmbeddingError(RuntimeError):
    """Raised when the local embedder is unreachable or returns no vectors."""


def embed(
    texts,
    *,
    model: str,
    host: str,
    batch: int = 32,
    timeout: int = 120,
):
    """Embed a list of texts via a local ollama /api/embed endpoint.

    Args:
        texts: list of strings to embed.
        model: ollama model tag, e.g. "bge-m3".
        host: base URL of the ollama server, e.g. "http://localhost:11434".
        batch: number of texts per request (ollama accepts a list `input`).
        timeout: per-request socket timeout in seconds.

    Returns:
        list[list[float]] -- one embedding vector per input text, in order.

    Raises:
        EmbeddingError: ollama unreachable after retries, or empty response.
    """
    if not texts:
        return []

    url = f"{host.rstrip('/')}/api/embed"
    out: list[list[float]] = []

    for start in range(0, len(texts), batch):
        chunk = texts[start : start + batch]
        payload = json.dumps({"model": model, "input": chunk}).encode("utf-8")
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

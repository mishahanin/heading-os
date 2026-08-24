"""FastAPI app builder. All authed endpoints require Authorization: Bearer <token>.
/_bootstrap and /health are unauthenticated for browser bootstrap and ops scripts."""
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from .auth import validate
from .auth import _image_nonces as _nonces
from .auth import mint_image_nonce as _mint_image_nonce
from .auth import consume_image_nonce as _consume_image_nonce
from .config import ConfigState
from .version import __version__
from .telemetry import Telemetry
from .watcher import WATCHED_COMPONENTS
from pydantic import (
    BaseModel as _PydanticBaseModel,
    ConfigDict as _PydanticConfigDict,
    Field as _PydanticField,
    model_validator as _pydantic_model_validator,
)
from scripts.utils.paths import get_data_root
from scripts.utils.workspace import get_default_tz_name

_log = logging.getLogger(__name__)


class ActionCardModel(_PydanticBaseModel):
    """Validated shape for a single Action Queue card submitted via POST /aq/deposit.

    Rejects malformed cards with 422 before they enter the queue.
    Extra fields pass through unchanged (extra="allow") so callers can
    embed action-specific payload alongside the required fields.

    ``kind`` defaults to ``action_type`` when omitted, preserving backward
    compatibility with existing callers that pre-date this model.
    """
    model_config = _PydanticConfigDict(extra="allow")

    kind: str | None = _PydanticField(default=None, max_length=64)
    title: str = _PydanticField(..., min_length=1, max_length=256)
    body: str = _PydanticField(default="", max_length=4096)
    action_type: str = _PydanticField(default="note", min_length=1, max_length=64)

    @_pydantic_model_validator(mode="after")
    def _default_kind_from_action_type(self) -> "ActionCardModel":
        if self.kind is None:
            # Omitted by caller — derive from action_type for backward compat.
            self.kind = self.action_type
        elif self.kind == "":
            raise ValueError("kind must not be empty when provided")
        return self


def _attach_freshness(payload: dict, component: str, computed_at: str | None = None) -> None:
    """Attach the data_time / server_now / watching envelope to a page payload.

    Three fields the UI's sync indicator depends on:
      - data_time: ISO-8601 of when THIS response was generated. For
        snapshot-backed endpoints pass computed_at from the snapshot
        (data was generated when the refresher wrote it). For per-request
        endpoints pass None -> stamped as 'now' (we just read the data).

        Why we OVERRIDE any pre-existing payload["data_time"] from the
        source: source-side semantics are inconsistent across the
        codebase. Some sources (calendar, pipeline, library, ...) set
        data_time = source file mtime ("when last edited"). Others
        (critical, studio) set data_time = newest entry's timestamp
        ("when last event happened"). Both are interesting facts but
        neither answers "how old is this response", which is what the
        sync-pill label "Computed X ago" promises. A user on /critical
        seeing "Computed 117h ago" for a freshly-served list correctly
        infers something is broken about the indicator. Stamping the
        response generation time keeps the indicator's contract honest.
        Per-page "newest entry" info belongs in the page body, not the
        topbar.
      - server_now: ISO-8601 of the server's clock at response time. The
        browser uses (server_now - data_time) to render "Computed Xs ago"
        without being misled by client-side clock skew.
      - watching: True when the daemon actively keeps this component fresh
        (Watchdog mapping or refresher), False for read-on-demand sources.

    Intentionally NOT used: state.data_time(component). That counter is bumped
    by Watchdog events and bare POST /refresh calls which do not actually
    recompute data - the source of "indicator says 0s ago, data is 6h old".
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    payload["data_time"] = computed_at if computed_at is not None else now_iso
    payload["server_now"] = now_iso
    payload["watching"] = component in WATCHED_COMPONENTS

ALLOWED_RETURN_PAGES = frozenset({
    "pulse", "inbox", "conversations", "capabilities", "library",
    "studio", "spaces", "tribe", "day", "tasks", "pipeline", "investors",
    "approvals", "threads", "settings", "search",
    # Phase 1.101: dedicated /signals overflow page.
    "signals",
    # Phase 1.35: full CRM contacts page.
    "contacts",
    # R1 (2026-06-03): Action Queue page.
    "action-queue",
})

def build_app(workspace_root: Path, state, token: str, user_slug: str,
              cfg_state: ConfigState | None = None,
              data_root: Path | None = None) -> FastAPI:
    app = FastAPI(title="Bridge daemon", version=__version__)
    started_at = time.time()  # per-app instance; each build_app() gets fresh clock

    # F-9.2 - DNS-rebinding / localhost-CSRF guard. The daemon binds 127.0.0.1
    # and every authed route already requires a CSPRNG bearer token, but the
    # unauthenticated surface (/_bootstrap, /health) deserves belt-and-suspenders:
    # a rebound external hostname that resolves to 127.0.0.1 must not reach the
    # API, and a hostile cross-origin page must not drive it. We validate by
    # hostname only (the port is irrelevant on a loopback-only bind), so no
    # runtime port needs to be threaded in here.
    _LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

    def _bare_host(raw: str) -> str:
        """Normalize a Host header value to a bare hostname.

        Handles IPv6 bracket form ([::1]:8765 -> ::1) and the common
        host:port form (127.0.0.1:8765 -> 127.0.0.1). A bare hostname with
        no port is returned unchanged.

        An UNBRACKETED IPv6 literal is returned whole. `rsplit(":", 1)` treated
        `::1` as host:port and handed back `::`, which is not in the loopback
        set, so a legitimate loopback client sending that Host got a 421. More
        than one colon means it cannot be host:port -- a port has exactly one.

        Lowercased, because DNS hostnames are case-insensitive (RFC 4343) and
        `_LOOPBACK_HOSTS` holds them lowercase. `curl http://LOCALHOST:31415/`
        sends the name as typed, so a bona fide loopback request was answered
        421 by a loopback-bound server. The Origin check on the same middleware
        never had this bug -- `urlsplit(...).hostname` lowercases for you --
        which is why only one half of one guard was wrong. Hex in an IPv6
        literal is case-insensitive too, so lowering is safe for every form
        this returns.
        """
        raw = raw.strip().lower()
        if raw.startswith("["):
            # IPv6 bracket form: take what is between the brackets.
            end = raw.find("]")
            return raw[1:end] if end != -1 else raw[1:]
        if raw.count(":") > 1:
            return raw
        return raw.rsplit(":", 1)[0] if ":" in raw else raw

    @app.middleware("http")
    async def _host_origin_guard(request: Request, call_next):
        host = _bare_host(request.headers.get("host", ""))
        if host and host not in _LOOPBACK_HOSTS:
            return JSONResponse(status_code=421, content={"detail": "host not allowed"})
        origin = request.headers.get("origin")
        if origin:
            origin_host = urlsplit(origin).hostname or ""
            if origin_host not in _LOOPBACK_HOSTS:
                return JSONResponse(status_code=403, content={"detail": "cross-origin blocked"})
        return await call_next(request)

    # ConfigState owns the in-memory merged config. The 60-second
    # reconciliation tick (Phase B / spec 3.6) calls cfg_state.reconcile()
    # which replaces cfg_state.config atomically on mtime change. Every
    # endpoint reads cfg_state.config at call time so they pick up the
    # latest values without a daemon restart. If the caller passed an
    # external cfg_state (production path: bridge-daemon.py shares it with
    # the scheduler), use that; otherwise construct one (test path).
    if cfg_state is None:
        cfg_state = ConfigState(workspace_root)

    # Adoption telemetry: usage events fed to the Phase 1 adoption gate.
    # See scripts/bridge_daemon/telemetry.py for the per-event schema.
    tel = Telemetry(workspace_root)

    # Engine/data separation (HEADING OS): DATA-reading sources (pulse, inbox,
    # studio, tribe, contacts, library, threads, tasks, pipeline, investors,
    # approvals, critical, conversations, calendar, search, action-queue, and the
    # pulse/finalize refreshers) read content from the data overlay, so they
    # receive `data_root`. ENGINE sources keep `workspace_root`: capabilities
    # (.claude/skills), ops/telemetry + config snapshots (.daemon-state), the
    # workspace display fields, and the terminal launch cwd. In an in-tree
    # layout data_root == workspace_root, so this is a no-op; after the split it
    # resolves to ../.heading-os-data. Injectable for tests (which pass a tmp
    # root holding the fixture data); production leaves it None -> get_data_root().
    if data_root is None:
        data_root = get_data_root()

    def _require_token(authorization: str | None) -> None:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        if not validate(authorization.split(" ", 1)[1], token):
            raise HTTPException(status_code=401, detail="invalid token")

    @app.get("/_bootstrap")
    def bootstrap(response: Response):
        # Include refresh cadences so the browser can drive polling
        # intervals from config, not hard-coded constants.
        # Cache-Control: no-store because the response carries the bearer token —
        # browsers/proxies must not cache it even though we serve from same-origin.
        response.headers["Cache-Control"] = "no-store"
        tz_name = get_default_tz_name()
        return {
            "token": token,
            "user": user_slug,
            "workspace": str(workspace_root),
            "refresh": cfg_state.config.get("refresh", {}),
            # Per-instance local timezone for the browser clock/labels. The
            # engine ships no hardcoded location; the daemon injects it.
            "tz": tz_name,
            "tz_label": tz_name.split("/")[-1].replace("_", " "),
        }

    @app.get("/health")
    def health():
        # Minimal, but do NOT read that as "local processes are untrusted".
        # They are trusted: `/_bootstrap` above is unauthenticated and hands
        # the full bearer token to any loopback caller, so a hostile local
        # process reads /version, /pulse and every mutating endpoint whatever
        # this one omits. The earlier comment here claimed the omission denied
        # information to "any local process", which was not true of the server
        # it described.
        #
        # The real boundary is the loopback bind plus the Host check above: a
        # remote or cross-origin caller cannot reach either endpoint. Within
        # the machine, the operator's own uid is the trust boundary.
        #
        # Keeping this response small is still worth the zero it costs; it just
        # is not a control. Gating /_bootstrap behind the 0600 token file on
        # disk would make it one, and that is an open design question, not
        # something this comment should imply is already done.
        return {
            "pid": os.getpid(),
            "version": __version__,
            "uptime_s": int(time.time() - started_at),
            "ok": True,
        }

    @app.get("/version")
    def version_endpoint(response: Response, authorization: str | None = Header(None),
                         if_none_match: str | None = Header(None)):
        _require_token(authorization)
        snap = state.snapshot()
        etag = f'"g{snap["global"]}"'
        if if_none_match == etag:
            # The validator goes on the 304 too (RFC 9110 §15.4.5). Without it
            # a client that refreshes its cached validator set from response
            # headers loses the ETag and degrades to unconditional fetches --
            # half-implemented conditional requests cost more than none.
            return Response(status_code=304, headers={"ETag": etag})
        response.headers["ETag"] = etag
        return snap

    @app.get("/settings")
    def settings_endpoint(authorization: str | None = Header(None)):
        _require_token(authorization)
        snap = state.snapshot()
        # Surface per-component data_time + refresh interval from config.
        refresh_cfg = (cfg_state.config.get("refresh") or {})
        components = []
        for comp, data_time in snap["data_times"].items():
            components.append({
                "name": comp,
                "data_time": data_time,
                "interval_s": refresh_cfg.get(comp, refresh_cfg.get("default")),
                "version": snap["components"].get(comp, 0),
            })
        # Sort: components with data_time first (oldest first so they
        # show up at the top with the stale warning).
        components.sort(key=lambda c: (c["data_time"] is None, c["data_time"] or ""))
        # Phase 1.157: surface the config-history snapshots so the CEO
        # can see what --revert-config would roll back to.
        from .config import list_snapshots as _list_snaps
        # One unreadable file skips ITSELF, not the list. A blanket
        # `except Exception: snapshots = []` used to discard every entry with no
        # log line, so the CEO's view of what `--revert-config` would roll back
        # to silently became "nothing to roll back to". The second `p.stat()`
        # for the size also sat outside the inner try, which is how a single
        # bad file reached that outer handler in the first place.
        snapshots = []
        try:
            candidates = list(_list_snaps(workspace_root))
        except OSError:
            _log.warning("could not list config snapshots", exc_info=True)
            candidates = []
        for snap_path in candidates:
            try:
                st = snap_path.stat()
            except OSError:
                _log.warning("skipping unreadable config snapshot %s", snap_path,
                             exc_info=True)
                continue
            snapshots.append({
                "name": snap_path.name,
                "size_bytes": st.st_size,
                "mtime_iso": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
            })
        return {
            "pid": os.getpid(),
            "version": __version__,
            "uptime_s": int(time.time() - started_at),
            "user": user_slug,
            "workspace": str(workspace_root),
            "components": components,
            "refresh_config": refresh_cfg,
            "config_snapshots": snapshots,
        }

    from .sources.ops import read_telemetry_summary as _ops_telemetry
    from .sources.ops import read_log_tail as _ops_log_tail

    @app.get("/settings/ops")
    def settings_ops(authorization: str | None = Header(None)):
        _require_token(authorization)
        return {
            "telemetry": _ops_telemetry(workspace_root),
            "log_tail": _ops_log_tail(workspace_root),
        }

    from .refreshers.pulse import read_snapshot as _pulse_snapshot
    from .sources.pulse import pulse_data as _pulse_source

    @app.get("/pulse")
    def pulse(authorization: str | None = Header(None)):
        _require_token(authorization)
        # Phase 2 cache (2026-05-24): serve the snapshot the refresher
        # writes every 60s. The ~7s cold-compute figure this was built
        # against was WSL /mnt/c; on today's ext4 the walk is 68 ms
        # (2026-08-20, see refreshers/pulse.py). Fall back to inline
        # compute only when
        # the snapshot is absent or corrupt (boot edge case or disk
        # error) so the dashboard always renders.
        snap = _pulse_snapshot(workspace_root)  # snapshot is machine-local (.daemon-state), engine root
        if snap is not None and isinstance(snap.get("data"), dict):
            payload = dict(snap["data"])
            _attach_freshness(payload, "pulse", computed_at=snap.get("computed_at"))
            return payload
        _log.warning("bridge.app: pulse snapshot missing/corrupt, falling back to inline compute")
        odin_5 = (cfg_state.config.get("kpi", {}) or {}).get("odin_5_target_date")
        payload = _pulse_source(data_root, odin_5_target=odin_5)
        _attach_freshness(payload, "pulse")
        return payload

    from .sources.pulse import sea_state as _sea_state

    @app.get("/sea-state")
    def sea_state_endpoint(authorization: str | None = Header(None)):
        _require_token(authorization)
        return _sea_state(data_root)

    # Phase 1.101: dedicated /signals page surfaces the full signal list
    # (Pulse hosts the top 3). Returns the same per-item shape as the
    # Pulse-embedded view + a data_time stamp.
    from .sources.pulse import signals as _signals_source
    from .sources.pulse import SIGNALS_CAP_FULL as _SIGNALS_CAP_FULL

    @app.get("/signals")
    def signals_endpoint(authorization: str | None = Header(None)):
        _require_token(authorization)
        items = _signals_source(data_root, cap=_SIGNALS_CAP_FULL)
        payload = {
            "signals": items,
            "total": len(items),
        }
        # /signals derives from pipeline.md per-request; data_time = now.
        _attach_freshness(payload, "pipeline")
        return payload

    from .sources.inbox import read_inbox as _inbox_source

    @app.get("/inbox")
    def inbox(authorization: str | None = Header(None)):
        _require_token(authorization)
        payload = _inbox_source(data_root)
        # No preference chain here, whatever this comment used to say: passing
        # no `computed_at` stamps data_time as NOW, and `_attach_freshness`
        # documents that it OVERRIDES any data_time the source already set. The
        # comment described a source-side preference the override had silently
        # defeated, on the one field the rest of this file treats as
        # load-bearing.
        _attach_freshness(payload, "inbox")
        return payload

    from .sources.inbox import read_conversation as _inbox_read_conv

    @app.get("/inbox/conversation")
    def inbox_conversation(id: str = "", authorization: str | None = Header(None)):
        _require_token(authorization)
        result = _inbox_read_conv(data_root, id)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error", "not found"))
        return result

    from .sources.inbox import mark_dismissed as _inbox_mark_dismissed
    from .sources.inbox import undo_dismissed as _inbox_undo_dismissed
    from .sources.inbox import dismiss_log_recent as _inbox_dismiss_log_recent
    from pydantic import BaseModel as _InboxDismissBaseModel

    class InboxDismissBody(_InboxDismissBaseModel):
        conv_id: str
        note: str = ""

    from .finalizers.mark_read import mark_conversation_read as _inbox_mark_conv_read

    @app.post("/inbox/dismiss")
    def inbox_dismiss(body: InboxDismissBody, authorization: str | None = Header(None)):
        _require_token(authorization)
        # Phase 1.34: 'Done' marks the conversation read in Exchange first,
        # so the next unread refresh will not re-surface it. If Exchange
        # cannot be updated, fail loudly - a dismiss that did not sync
        # would leave the email unread in Outlook and reappear later.
        # workspace_root, NOT data_root: `mark_conversation_read` locates
        # `scripts/email-intelligence.py` and cwd's there, and that script is
        # ENGINE. Post-cutover the data overlay has no `scripts/` tree, so this
        # returned 502 "email-intelligence.py not found" on every dismiss. It
        # only worked because the two roots still coincide today.
        mr = _inbox_mark_conv_read(workspace_root, body.conv_id, mark_read=True)
        if not mr.get("ok"):
            raise HTTPException(status_code=502, detail=f"Exchange not updated: {mr.get('error')}")
        result = _inbox_mark_dismissed(data_root, body.conv_id, body.note)
        if not result.get("ok"):
            # Exchange is ALREADY mutated at this point: the conversation is
            # marked read, so the next unread fetch will not re-surface it, and
            # the dismiss log has no record of why. It vanishes with no audit
            # trail and no undo path. The 400 alone said none of that, so the
            # half-applied state went into a log nobody reads.
            _log.critical(
                "HALF-APPLIED DISMISS: conv_id=%s was marked READ in Exchange but "
                "the local dismiss log write failed (%s). The conversation will "
                "not re-surface and has no undo entry. Restore it with "
                "POST /inbox/undo-dismiss, or mark it unread in Outlook.",
                body.conv_id, result.get("error"),
            )
            raise HTTPException(
                status_code=500,
                detail=(f"Exchange was updated but the local dismiss log was not: "
                        f"{result.get('error', 'write failed')}. "
                        f"conv_id={body.conv_id} is now READ with no dismiss record."),
            )
        state.bump("inbox")
        return {**result, "messages_changed": mr.get("messages_changed", 0)}

    @app.post("/inbox/undo-dismiss")
    def inbox_undo_dismiss(body: InboxDismissBody, authorization: str | None = Header(None)):
        _require_token(authorization)
        # Undo marks the conversation unread again in Exchange so it
        # genuinely returns to the inbox, then tombstones the dismiss log.
        mr = _inbox_mark_conv_read(workspace_root, body.conv_id, mark_read=False)  # engine root, see /inbox/dismiss
        if not mr.get("ok"):
            raise HTTPException(status_code=502, detail=f"Exchange not updated: {mr.get('error')}")
        result = _inbox_undo_dismissed(data_root, body.conv_id)
        if not result.get("ok"):
            # Mirror of /inbox/dismiss: the conversation is already UNREAD in
            # Exchange while the dismiss log still holds the original dismiss,
            # so it reappears in the inbox and still reads as dismissed here.
            _log.critical(
                "HALF-APPLIED UNDO: conv_id=%s was marked UNREAD in Exchange but "
                "the tombstone write failed (%s). It is back in the inbox while "
                "the dismiss log still says it was dismissed.",
                body.conv_id, result.get("error"),
            )
            raise HTTPException(
                status_code=500,
                detail=(f"Exchange was updated but the tombstone was not: "
                        f"{result.get('error', 'write failed')}. "
                        f"conv_id={body.conv_id} is now UNREAD but still logged as dismissed."),
            )
        state.bump("inbox")
        return result

    @app.get("/inbox/dismiss-log")
    def inbox_dismiss_log(limit: int = 20, authorization: str | None = Header(None)):
        _require_token(authorization)
        bounded = max(1, min(100, int(limit)))
        return {"items": _inbox_dismiss_log_recent(data_root, limit=bounded)}

    # Phase 1.33: defer + crm-log card actions.
    from .sources.inbox import mark_deferred as _inbox_mark_deferred
    from .sources.inbox import undo_deferred as _inbox_undo_deferred
    from .sources.inbox import defer_log_recent as _inbox_defer_log_recent
    from .finalizers.crm_log import log_to_crm as _inbox_log_to_crm

    class InboxDeferBody(_InboxDismissBaseModel):
        conv_id: str
        defer_until: str
        note: str = ""

    @app.post("/inbox/defer")
    def inbox_defer(body: InboxDeferBody, authorization: str | None = Header(None)):
        _require_token(authorization)
        result = _inbox_mark_deferred(data_root, body.conv_id, body.defer_until, body.note)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "bad request"))
        state.bump("inbox")
        return result

    @app.post("/inbox/undo-defer")
    def inbox_undo_defer(body: InboxDismissBody, authorization: str | None = Header(None)):
        _require_token(authorization)
        result = _inbox_undo_deferred(data_root, body.conv_id)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "bad request"))
        state.bump("inbox")
        return result

    @app.get("/inbox/defer-log")
    def inbox_defer_log(limit: int = 20, authorization: str | None = Header(None)):
        _require_token(authorization)
        bounded = max(1, min(100, int(limit)))
        return {"items": _inbox_defer_log_recent(data_root, limit=bounded)}

    @app.post("/inbox/crm-log")
    def inbox_crm_log(body: InboxDismissBody, authorization: str | None = Header(None)):
        _require_token(authorization)
        result = _inbox_log_to_crm(body.conv_id, data_root=data_root)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "bad request"))
        state.bump("inbox")
        return result

    from .sources.agenda import today_agenda as _day_source

    @app.get("/day")
    def day(authorization: str | None = Header(None)):
        _require_token(authorization)
        payload = _day_source(data_root)
        _attach_freshness(payload, "day")
        return payload

    # Phase 1.38: the Studio page is the LinkedIn artifacts reference.
    from .sources.studio import list_artifacts as _studio_source

    @app.get("/studio")
    def studio(authorization: str | None = Header(None)):
        _require_token(authorization)
        payload = _studio_source(data_root)
        _attach_freshness(payload, "studio")
        return payload

    from .sources.studio import read_inflight as _studio_read_inflight

    @app.get("/studio/file")
    def studio_file(path: str = "", authorization: str | None = Header(None)):
        _require_token(authorization)
        result = _studio_read_inflight(data_root, path)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error", "not found"))
        return result

    # Phase 1.38: artifact drill-down + image serving for the Studio page.
    from fastapi.responses import FileResponse

    from .sources.studio import read_artifact as _studio_read_artifact
    from .sources.studio import resolve_artifact_image as _studio_resolve_image

    @app.get("/studio/artifact")
    def studio_artifact(kind: str = "", slug: str = "",
                        authorization: str | None = Header(None)):
        _require_token(authorization)
        result = _studio_read_artifact(data_root, kind, slug)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error", "not found"))
        return result

    @app.post("/studio/image-nonce")
    def studio_image_nonce_mint(authorization: str | None = Header(None)):
        """Mint a short-lived one-use nonce for /studio/image (F-M1/F-L5).

        The browser requests a nonce immediately before rendering an <img> tag,
        substituting ?n=<nonce> for ?t=<bearer-token>. Nonces are 30s TTL and
        single-use, so the long-lived bearer never appears in an image URL
        (which would otherwise leak it into HTTP logs / Referer / history).
        """
        _require_token(authorization)
        return {"nonce": _mint_image_nonce()}

    @app.get("/studio/image")
    def studio_image(path: str = "", n: str = ""):
        # Auth: short-lived one-use nonce via ?n=<nonce> (F-M1/F-L5). The browser
        # calls POST /studio/image-nonce (bearer-authed) to mint a nonce immediately
        # before each <img> render, so the long-lived bearer NEVER appears in an
        # image URL (which would otherwise leak it into HTTP logs / Referer /
        # history). There is deliberately NO ?t=<bearer> fallback — the insecure
        # query-param path is removed outright; an old cached URL simply re-mints a
        # nonce on reload (the daemon restart that ships this code refreshes tabs).
        if not n:
            raise HTTPException(status_code=401, detail="nonce required")
        if not _consume_image_nonce(n):
            raise HTTPException(status_code=401, detail="invalid or expired nonce")
        img = _studio_resolve_image(data_root, path)
        if img is None:
            raise HTTPException(status_code=404, detail="image not found")
        return FileResponse(img)

    from .sources.tribe import list_tribe as _tribe_source

    @app.get("/tribe")
    def tribe(authorization: str | None = Header(None)):
        _require_token(authorization)
        payload = _tribe_source(data_root)
        _attach_freshness(payload, "tribe")
        return payload

    from .sources.tribe import read_contact as _tribe_read_contact

    @app.get("/tribe/contact")
    def tribe_contact(slug: str = "", authorization: str | None = Header(None)):
        _require_token(authorization)
        result = _tribe_read_contact(data_root, slug)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error", "not found"))
        return result

    # Phase 1.35: full CRM contacts (CEO's + every exec's, combined).
    from .sources.contacts import list_contacts as _contacts_source
    from .sources.contacts import read_one_contact as _contacts_read_one

    @app.get("/contacts")
    def contacts(authorization: str | None = Header(None)):
        _require_token(authorization)
        payload = _contacts_source(data_root)
        _attach_freshness(payload, "contacts")
        return payload

    @app.get("/contacts/contact")
    def contacts_contact(owner: str = "", slug: str = "",
                         authorization: str | None = Header(None)):
        # Drill-down keys on (owner, slug): the same slug can exist under
        # more than one owner (a contact tracked by several people).
        _require_token(authorization)
        result = _contacts_read_one(data_root, owner, slug)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error", "not found"))
        return result

    from .sources.capabilities import list_capabilities as _capabilities_source

    @app.get("/capabilities")
    def capabilities(authorization: str | None = Header(None)):
        _require_token(authorization)
        payload = _capabilities_source(workspace_root)
        _attach_freshness(payload, "capabilities")
        return payload

    from .sources.capabilities import read_skill as _capabilities_read_skill

    @app.get("/capabilities/skill")
    def capabilities_skill(slug: str = "", authorization: str | None = Header(None)):
        _require_token(authorization)
        result = _capabilities_read_skill(workspace_root, slug)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error", "not found"))
        return result

    from .sources.library import list_library as _library_source

    @app.get("/library")
    def library(authorization: str | None = Header(None)):
        _require_token(authorization)
        payload = _library_source(data_root)
        _attach_freshness(payload, "library")
        return payload

    from .sources.library import read_note as _library_read_note

    @app.get("/library/note")
    def library_note(path: str = "", authorization: str | None = Header(None)):
        _require_token(authorization)
        result = _library_read_note(data_root, path)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error", "not found"))
        return result

    from .sources.tasks import list_active_tasks as _tasks_source
    from .sources.tasks import mark_done as _tasks_mark_done
    from .sources.tasks import undo_done as _tasks_undo_done
    from .sources.tasks import done_log_recent as _tasks_done_log_recent

    @app.get("/tasks")
    def tasks(authorization: str | None = Header(None)):
        _require_token(authorization)
        payload = _tasks_source(data_root)
        _attach_freshness(payload, "tasks")
        return payload

    from pydantic import BaseModel as _TaskDoneBaseModel

    class TaskDoneBody(_TaskDoneBaseModel):
        task_key: str
        note: str = ""

    @app.post("/tasks/mark-done")
    def tasks_mark_done(body: TaskDoneBody, authorization: str | None = Header(None)):
        _require_token(authorization)
        result = _tasks_mark_done(data_root, body.task_key, body.note)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "bad request"))
        state.bump("tasks")
        return result

    @app.post("/tasks/undo-done")
    def tasks_undo_done(body: TaskDoneBody, authorization: str | None = Header(None)):
        _require_token(authorization)
        result = _tasks_undo_done(data_root, body.task_key)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "bad request"))
        state.bump("tasks")
        return result

    @app.get("/tasks/done-log")
    def tasks_done_log(limit: int = 20, authorization: str | None = Header(None)):
        _require_token(authorization)
        bounded = max(1, min(100, int(limit)))
        return {"items": _tasks_done_log_recent(data_root, limit=bounded)}

    from .sources.pipeline import list_pipeline as _pipeline_source

    @app.get("/pipeline")
    def pipeline(authorization: str | None = Header(None)):
        _require_token(authorization)
        payload = _pipeline_source(data_root)
        _attach_freshness(payload, "pipeline")
        return payload

    from .sources.pipeline import mark_touched as _pipeline_mark_touched
    from pydantic import BaseModel as _PipelineTouchBaseModel

    class MarkTouchedBody(_PipelineTouchBaseModel):
        company: str
        note: str = ""

    @app.post("/pipeline/mark-touched")
    def pipeline_mark_touched(body: MarkTouchedBody, authorization: str | None = Header(None)):
        _require_token(authorization)
        result = _pipeline_mark_touched(data_root, body.company, body.note)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "bad request"))
        # Bump the pipeline component so the browser re-fetches without
        # waiting for the filesystem watcher debounce.
        state.bump("pipeline")
        return result

    from .sources.investors import list_investors as _investors_source

    @app.get("/investors")
    def investors(authorization: str | None = Header(None)):
        _require_token(authorization)
        payload = _investors_source(data_root)
        _attach_freshness(payload, "investors")
        return payload

    from .sources.investors import read_dossier as _investors_read_dossier

    @app.get("/investors/dossier")
    def investors_dossier(path: str = "", authorization: str | None = Header(None)):
        _require_token(authorization)
        result = _investors_read_dossier(data_root, path)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error", "not found"))
        return result

    from .sources.investors import mark_sent as _investors_mark_sent
    from .sources.investors import undo_sent as _investors_undo_sent
    from pydantic import BaseModel as _MarkSentBaseModel

    class MarkSentBody(_MarkSentBaseModel):
        firm_num: int
        note: str = ""

    class UndoSentBody(_MarkSentBaseModel):
        firm_num: int

    @app.post("/investors/mark-sent")
    def investors_mark_sent(body: MarkSentBody, authorization: str | None = Header(None)):
        _require_token(authorization)
        result = _investors_mark_sent(data_root, body.firm_num, body.note)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "bad request"))
        # Bump the investors component version so the browser re-fetches
        # without waiting for the filesystem watcher debounce.
        state.bump("investors")
        return result

    @app.post("/investors/undo-sent")
    def investors_undo_sent(body: UndoSentBody, authorization: str | None = Header(None)):
        _require_token(authorization)
        result = _investors_undo_sent(data_root, body.firm_num)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "bad request"))
        state.bump("investors")
        return result

    from .sources.approvals import list_approvals as _approvals_source
    from .sources.approvals import read_draft as _approvals_read_draft
    from .sources.approvals import mark_sent as _approvals_mark_sent
    from .sources.approvals import undo_sent as _approvals_undo_sent
    from .sources.approvals import sent_log_recent as _approvals_sent_log_recent

    @app.get("/approvals")
    def approvals(authorization: str | None = Header(None)):
        _require_token(authorization)
        payload = _approvals_source(data_root)
        _attach_freshness(payload, "approvals")
        return payload

    @app.get("/approvals/draft")
    def approvals_draft(path: str = "", authorization: str | None = Header(None)):
        _require_token(authorization)
        result = _approvals_read_draft(data_root, path)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error", "not found"))
        return result

    from pydantic import BaseModel as _ApprovalSentBaseModel

    class ApprovalSentBody(_ApprovalSentBaseModel):
        path: str
        note: str = ""

    @app.post("/approvals/mark-sent")
    def approvals_mark_sent(body: ApprovalSentBody, authorization: str | None = Header(None)):
        _require_token(authorization)
        result = _approvals_mark_sent(data_root, body.path, body.note)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "bad request"))
        state.bump("approvals")
        return result

    @app.post("/approvals/undo-sent")
    def approvals_undo_sent(body: ApprovalSentBody, authorization: str | None = Header(None)):
        _require_token(authorization)
        result = _approvals_undo_sent(data_root, body.path)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "bad request"))
        state.bump("approvals")
        return result

    @app.get("/approvals/sent-log")
    def approvals_sent_log(limit: int = 20, authorization: str | None = Header(None)):
        _require_token(authorization)
        bounded = max(1, min(100, int(limit)))
        return {"items": _approvals_sent_log_recent(data_root, limit=bounded)}

    # Phase 1.127a: critical-items endpoints.
    from .sources.critical import list_critical as _critical_list
    from .sources.critical import mark_critical as _critical_mark
    from .sources.critical import unmark_critical as _critical_unmark
    from .sources.critical import recent_unmarked as _critical_recent_unmarked

    @app.get("/critical")
    def critical_endpoint(authorization: str | None = Header(None)):
        _require_token(authorization)
        payload = _critical_list(data_root)
        _attach_freshness(payload, "critical")
        return payload

    from pydantic import BaseModel as _CriticalBaseModel

    class CriticalMarkBody(_CriticalBaseModel):
        kind: str
        ref: str
        label: str
        source_page: str = ""
        note: str = ""

    class CriticalUnmarkBody(_CriticalBaseModel):
        id: str

    @app.post("/critical/mark")
    def critical_mark(body: CriticalMarkBody, authorization: str | None = Header(None)):
        _require_token(authorization)
        result = _critical_mark(
            data_root,
            body.kind, body.ref, body.label,
            body.source_page, body.note,
        )
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "bad request"))
        state.bump("critical")
        return result

    @app.post("/critical/unmark")
    def critical_unmark(body: CriticalUnmarkBody, authorization: str | None = Header(None)):
        _require_token(authorization)
        result = _critical_unmark(data_root, body.id)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "bad request"))
        state.bump("critical")
        return result

    @app.get("/critical/recent-unmarked")
    def critical_recent_unmarked(limit: int = 10, authorization: str | None = Header(None)):
        _require_token(authorization)
        bounded = max(1, min(100, int(limit)))
        return {"items": _critical_recent_unmarked(data_root, limit=bounded)}

    from .sources.threads import list_active_threads as _threads_source
    from .sources.threads import read_thread as _threads_read
    from .sources.conversations import list_conversations as _conversations_source

    @app.get("/conversations")
    def conversations_endpoint(authorization: str | None = Header(None)):
        _require_token(authorization)
        payload = _conversations_source(data_root)
        _attach_freshness(payload, "conversations")
        return payload

    @app.get("/threads")
    def threads_endpoint(authorization: str | None = Header(None)):
        _require_token(authorization)
        payload = _threads_source(data_root)
        _attach_freshness(payload, "threads")
        return payload

    @app.get("/threads/thread")
    def threads_thread(path: str = "", authorization: str | None = Header(None)):
        _require_token(authorization)
        result = _threads_read(data_root, path)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error", "not found"))
        return result

    from .sources.search import search as _search_source

    @app.get("/search")
    def search_endpoint(q: str = "", limit: int = 10, authorization: str | None = Header(None)):
        _require_token(authorization)
        # FastAPI auto-parses ?q=... and ?limit=... from the URL.
        # Bound the limit to avoid pathological queries.
        bounded_limit = max(1, min(50, int(limit)))
        # Both roots: search spans DATA sources and the ENGINE skill catalog.
        # Passing data_root alone made the capability results come from
        # `<data-root>/.claude/skills` — 1 skill instead of 96 (fixed 2026-08-23).
        return _search_source(data_root, q, limit=bounded_limit,
                              workspace_root=workspace_root)

    from pydantic import BaseModel
    from . import terminal as terminal_mod
    from .sessions import session_for_cwd
    from scripts.utils.operator_identity import operator_org

    _DEFAULT_TERMINAL_TITLE = operator_org() or "HEADING OS"

    class LaunchBody(BaseModel):
        action: str
        session_id: str | None = None
        cwd: str | None = None
        # Default from the operator seam, not a literal. The org name is the
        # operator's own public identity and is NOT being scrubbed -- it is
        # simply not the engine's to hardcode for a second instance, whose
        # terminal tab would otherwise read someone else's company.
        title: str = _DEFAULT_TERMINAL_TITLE
        # Spec section 3.3: 'context' carries any extra payload the
        # skill needs (e.g. conv_id for email-respond, prospect_id for
        # deal-strategy). Serialized to BRIDGE_CONTEXT env var (JSON
        # string) so the launched skill can pre-populate state. None or
        # empty dict means no context (legacy callers don't need it).
        context: dict | None = None

    @app.post("/launch")
    def launch(body: LaunchBody, authorization: str | None = Header(None)):
        _require_token(authorization)
        # Treat whitespace-only cwd as missing (defensive against " " edge case).
        body_cwd = body.cwd.strip() if body.cwd else None
        cwd = Path(body_cwd) if body_cwd else workspace_root
        if not cwd.is_dir():
            raise HTTPException(status_code=400, detail="cwd is not an existing directory")
        # If body did not supply session_id but did supply cwd, consult
        # the hook-maintained registry as a fallback. This is what
        # justifies the registry existing in Phase 1.
        resolved_sid = body.session_id
        if not resolved_sid and body_cwd:
            registry_path = Path.home() / ".claude" / "state" / "active-sessions.json"
            resolved_sid = session_for_cwd(registry_path, body_cwd)
        try:
            result = terminal_mod.spawn_or_focus(
                user_slug=user_slug, title=body.title, cwd=cwd,
                action=body.action, session_id=resolved_sid,
                context=body.context,
            )
        except terminal_mod.TerminalUnavailable as e:
            raise HTTPException(status_code=503, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        tel.event("launch", action=body.action)
        return result

    import webbrowser

    class ReturnBody(BaseModel):
        session_id: str
        target_page: str = "pulse"

    @app.post("/return")
    def return_to_browser(body: ReturnBody, authorization: str | None = Header(None)):
        # session_id is required in the body so Task 17 telemetry can correlate
        # return events to specific Claude sessions. Not used directly by /return.
        _require_token(authorization)
        if body.target_page not in ALLOWED_RETURN_PAGES:
            raise HTTPException(status_code=422, detail=f"unknown target_page: {body.target_page}")
        # Daemon's own URL - relies on BRIDGE_PORT being set by the CLI
        # launcher (Task 18). Fallback to the default starting port if not
        # set so this endpoint is functional in dev/test environments too.
        # Guarded: a stray `BRIDGE_PORT=auto` in the environment turned an
        # authenticated endpoint into an unhandled ValueError -> 500.
        raw_port = os.environ.get("BRIDGE_PORT", "31415")
        try:
            port = int(raw_port)
        except ValueError:
            _log.warning("BRIDGE_PORT=%r is not a number; using 31415", raw_port)
            port = 31415
        url = f"http://127.0.0.1:{port}/#/{body.target_page}"
        webbrowser.open(url, new=0)
        tel.event("return_to_browser", session_id=body.session_id, target=body.target_page)
        return {"opened": url}

    from .state import COMPONENTS
    from .finalizers.send_email import send_drafted

    # Phase 1: single send-email action. Phase 2 adds archive, dismiss.
    # Handler signature: (data_root: Path, artifact_id: str) -> dict. This line
    # said `workspace_root` until 2026-08-24 while the dispatch below passed
    # data_root, which is the same two-roots-one-name confusion the /inbox
    # dismiss comment records as a prior production break.
    _FINALIZE_ACTIONS: dict = {"send-email": send_drafted}

    class RefreshBody(BaseModel):
        component: str  # required - browser must name the component to refresh

    @app.post("/refresh")
    def refresh(body: RefreshBody, authorization: str | None = Header(None)):
        _require_token(authorization)
        if body.component not in COMPONENTS:
            raise HTTPException(status_code=422, detail=f"unknown component: {body.component}")
        # For refresher-backed components (currently only pulse writes a
        # snapshot the UI reads), actually trigger the refresher inline so
        # the user-initiated "refresh" produces fresh data, not just a
        # version bump. Per-request endpoints don't need this - they
        # recompute on every GET anyway.
        #
        # WHERE the bump comes from, per branch, because the previous wording
        # ("state.bump still fires") read as a promise this function makes and
        # is not: the pulse success path below never calls bump. It does not
        # need to. `refreshers.pulse.refresh` bumps "pulse" itself on every one
        # of its own paths, including its internal compute failure, and its
        # docstring says so. The 2026-08-25 k3 audit flagged this as a missing
        # bump on exactly that reading, so the sentence is now specific rather
        # than reassuring.
        #
        #   pulse, refresher returned  -> the refresher bumped
        #   pulse, refresher RAISED    -> this function bumps (nothing else did)
        #   any other component        -> this function bumps
        recomputed = False
        if body.component == "pulse":
            from .refreshers import pulse as _r_pulse
            try:
                # snapshot is machine-local (workspace_root/.daemon-state); the
                # payload is computed from data_root.
                _r_pulse.refresh(workspace_root, state, cfg_state, data_root=data_root)
                recomputed = True
            except Exception as e:
                _log.warning("bridge.app: POST /refresh pulse recompute failed: %s", e)
                state.bump(body.component)
        else:
            state.bump(body.component)
        return {
            "bumped": body.component,
            "version": state.version(body.component),
            "recomputed": recomputed,
        }

    class FinaliREDACTEDdy(BaseModel):
        action: str
        artifact_id: str

    @app.post("/finalize")
    def finalize(body: FinaliREDACTEDdy, authorization: str | None = Header(None)):
        _require_token(authorization)
        handler = _FINALIZE_ACTIONS.get(body.action)
        if handler is None:
            raise HTTPException(status_code=400, detail=f"unknown action: {body.action}")
        try:
            result = handler(data_root, body.artifact_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        tel.event("finalize", action=body.action, artifact_id=body.artifact_id)
        return result

    class PageViewBody(BaseModel):
        page: str
        duration_s: int | None = None

    @app.post("/telemetry/page-view")
    def page_view(body: PageViewBody, authorization: str | None = Header(None)):
        _require_token(authorization)
        if body.page not in ALLOWED_RETURN_PAGES:
            raise HTTPException(status_code=422, detail=f"unknown page: {body.page}")
        if body.duration_s is not None and not (0 <= body.duration_s <= 86_400):
            raise HTTPException(status_code=422, detail="duration_s must be in [0, 86400]")
        tel.event("page_view", page=body.page, duration_s=body.duration_s)
        return {"recorded": True}

    # Phase 1.150: adoption-gate summary endpoint. Reads usage.jsonl
    # (written by Telemetry.event) and emits the four Phase 1 -> Phase 2
    # gate metrics defined in the bridge spec section 4.
    from .adoption import summarize as _adoption_summarize

    @app.get("/telemetry/summary")
    def telemetry_summary(days: int = 14, authorization: str | None = Header(None)):
        _require_token(authorization)
        bounded = max(1, min(90, int(days)))
        return _adoption_summarize(workspace_root, days=bounded)

    # R1 (2026-06-03); REDESIGNED 2026-06-27: Action Queue is now TERMINAL-NATIVE.
    # Proactive agents (Cold-Sweep, email-intel, viraid) deposit drafted actions;
    # the CEO lists/approves/sends from the terminal via scripts/action-queue.py
    # (in-process, daemon-free). Approve is a SYNCHRONOUS send there - the daemon
    # NO LONGER approves, edits, dismisses, or sends. This web surface is
    # READ-ONLY: GET /action-queue lists for FYI; the mutation endpoints were
    # removed (a 200 no-op would be a silent-failure trap forbidden by
    # console-first). The deposit endpoint is retained as a programmatic depositor
    # path (not a CEO web action); cold-sweep/dead-letter now deposit in-process.
    from .sources.action_queue import list_action_queue as _aq_list
    from .sources.action_queue import append_cards as _aq_append

    @app.get("/action-queue")
    def action_queue(authorization: str | None = Header(None)):
        _require_token(authorization)
        payload = _aq_list(data_root)
        _attach_freshness(payload, "action_queue")
        return payload

    class AQDepositBody(BaseModel):
        cards: list[ActionCardModel]

    @app.post("/action-queue/deposit")
    def action_queue_deposit(body: AQDepositBody, authorization: str | None = Header(None)):
        # Programmatic depositor path. Dedup lives in append_cards (sole
        # authority). NOT a CEO mutation action - approve/edit/dismiss moved to
        # the terminal (scripts/action-queue.py).
        _require_token(authorization)
        result = _aq_append(data_root, [c.model_dump() for c in body.cards])
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "bad request"))
        state.bump("action_queue")
        return result

    # Static web served at /. The index route cache-busts app.css/app.js so
    # a daemon restart (done on every web/ change) serves fresh assets with
    # no manual browser hard-refresh. Token = newest asset mtime, read once.
    web_dir = Path(__file__).parent / "web"
    if web_dir.exists():
        index_path = web_dir / "index.html"
        if index_path.exists():
            asset_v = "0"
            try:
                mtimes = [(web_dir / f).stat().st_mtime
                          for f in ("app.css", "app.js") if (web_dir / f).exists()]
                if mtimes:
                    asset_v = str(int(max(mtimes)))
            except OSError:
                pass
            index_html = (index_path.read_text(encoding="utf-8")
                          .replace('href="app.css"', f'href="app.css?v={asset_v}"')
                          .replace('src="app.js"', f'src="app.js?v={asset_v}"'))

            @app.get("/", response_class=HTMLResponse)
            def _index() -> str:
                return index_html

        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")

    return app

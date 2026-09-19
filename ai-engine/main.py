"""
AI D&D Gamemaster Engine — Main Application

FastAPI server that:
1. Connects to FoundryVTT relay via WebSocket
2. Listens for player chat messages
3. Processes them through an LLM
4. Executes GM actions in Foundry
5. Serves the admin web panel
"""

import asyncio
import json
import logging
import secrets
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from typing import Dict

# Add the ai-engine directory to the path
sys.path.insert(0, str(Path(__file__).parent))

from config import settings
from campaign.vault import CampaignNotFound

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("ai-gm.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("ai-gm")


# GMSettings, SessionInfo, EventEntry, StateUpdate moved to api/routes/session.py
# (the only module that uses them).


# AppState, get_app_state, ErrorResponse, ApiError, require_foundry now live in
# api/deps.py so routers can import them without a circular import on main.
from api import startup  # noqa: E402
from api.deps import (  # noqa: E402
    ApiError,
    AppState,
    ErrorResponse,
    broadcast_state_update,
    websocket_clients,
)


# --- Context Manager ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build every component onto app.state, then tear them down again.

    The fifteen numbered construction steps live in api/startup.py, one
    function per seam, in this order. Keeping the order here rather than
    inside the builders makes the dependency chain readable in one screen:
    persistence, then context, then the LLM stack, then Foundry, then
    gameplay, then the chat listener that drives all of it.
    """
    app.state = AppState()
    logger.info("Initializing AI Gamemaster Engine...")

    startup.check_admin_exposure()

    async def on_state_update(data):
        await broadcast_state_update(data)

    async def notify_admin(results):
        await broadcast_state_update({"type": "actions_executed", "actions": results})

    await startup.build_persistence(app.state)
    await startup.build_context(app.state)
    startup.build_tts(app.state)
    startup.build_llm(app.state)
    await startup.build_foundry(app.state)
    startup.build_gameplay(app.state, on_state_update)
    await startup.build_chat(app.state, notify_admin)

    # Registered last: it closes over app.state, which is only complete now.
    from api.routes import session_control as session_control_routes
    app.include_router(session_control_routes.create_session_control_router(app.state))
    logger.info("Session control router registered")

    logger.info("AI Gamemaster Engine is RUNNING — ready for campaign selection")

    yield

    await startup.shutdown(app.state)



# --- WebSocket broadcast for admin panel ---

_admin_ws_rate: Dict[WebSocket, float] = {}
_api_rate: Dict[str, list[float]] = {}
_api_rate_lock = asyncio.Lock()
_API_RATE_MAX_CLIENTS = 10_000




# --- FastAPI App ---

app = FastAPI(
    title="Sage - AI D&D Gamemaster",
    description="AI D&D 5e Gamemaster integrated with FoundryVTT",
    version="1.0.0",
    lifespan=lifespan
)


@app.middleware("http")
async def protect_api_resources(request: Request, call_next):
    """Apply size/rate limits and, when ADMIN_TOKEN is set, require it on /api/*."""
    if request.url.path.startswith("/api/"):
        if settings.admin_token:
            supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            if not secrets.compare_digest(supplied, settings.admin_token):
                return JSONResponse(status_code=401, content={"error": "Authentication required"})
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                too_large = int(content_length) > settings.max_request_body_bytes
            except ValueError:
                too_large = True
            if too_large:
                return JSONResponse(status_code=413, content={"error": "Request body too large"})
        else:
            # No Content-Length means a chunked/streamed body, which the header
            # check above cannot see — that was a straight bypass of the size
            # limit. Buffer it ourselves, rejecting as soon as the running total
            # exceeds the cap (so an unbounded stream is never fully read), then
            # hand the bytes downstream through a replacement receive channel:
            # the route still needs to read the body we just consumed.
            chunks: list[bytes] = []
            total = 0
            async for chunk in request.stream():
                total += len(chunk)
                if total > settings.max_request_body_bytes:
                    return JSONResponse(status_code=413, content={"error": "Request body too large"})
                chunks.append(chunk)
            buffered = b"".join(chunks)

            async def _replay_body() -> dict:
                return {"type": "http.request", "body": buffered, "more_body": False}

            request._body = buffered
            request._receive = _replay_body
        now = time.time()
        client = request.client.host if request.client else "unknown"
        async with _api_rate_lock:
            bucket = [t for t in _api_rate.get(client, []) if now - t < 60]
            if len(_api_rate) > _API_RATE_MAX_CLIENTS:
                # Remove inactive buckets before admitting another client. This
                # keeps the LAN limiter bounded when client IPs rotate frequently.
                cutoff = now - 60
                # dict.update with a filtered subset of the dict's own items
                # merges, so this removed nothing and the expensive sort below
                # ran on every request past the cap instead of rarely.
                for ip in [ip for ip, times in _api_rate.items()
                           if not times or times[-1] < cutoff]:
                    del _api_rate[ip]
                # Pruning by recency may still leave the map over the cap (every
                # retained bucket was active within the window). Evict the
                # least-recently-active buckets until we're back under the limit,
                # so the map can't grow unbounded within a single 60s window.
                if len(_api_rate) > _API_RATE_MAX_CLIENTS:
                    by_recency = sorted(
                        _api_rate.items(), key=lambda kv: kv[1][-1] if kv[1] else 0.0
                    )
                    for ip, _ in by_recency[: len(_api_rate) - _API_RATE_MAX_CLIENTS]:
                        _api_rate.pop(ip, None)
            if len(bucket) >= settings.api_requests_per_minute:
                return JSONResponse(status_code=429, content={"error": "Rate limit exceeded"})
            bucket.append(now)
            _api_rate[client] = bucket
    return await call_next(request)

# CORS — Foundry runs on a different origin (e.g. localhost:30000) than this
# engine (localhost:18080). Foundry's AudioHelper decodes TTS audio via the Web
# Audio API, which silently fails on cross-origin responses without these
# headers. Origins come from CORS_ORIGINS (default: this engine on localhost
# and 127.0.0.1) — add your Foundry origin there rather than widening this.
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers extracted from main.py (Phase 1 of the modular architecture split,
# docs/archived/ARCHITECTURE_REFACTOR.md). More domains move here incrementally.
from api.routes import campaign as campaign_routes  # noqa: E402
from api.routes import canon as canon_routes  # noqa: E402
from api.routes import control as control_routes  # noqa: E402
from api.routes import downtime as downtime_routes  # noqa: E402
from api.routes import combat as combat_routes  # noqa: E402
from api.routes import immersion as immersion_routes  # noqa: E402
from api.routes import npc as npc_routes  # noqa: E402
from api.routes import procedural as procedural_routes  # noqa: E402
from api.routes import rules as rules_routes  # noqa: E402
from api.routes import scene as scene_routes  # noqa: E402
from api.routes import session as session_routes  # noqa: E402
from api.routes import setup as setup_routes  # noqa: E402
from api.routes import system as system_routes  # noqa: E402
from api.routes import camera as camera_routes  # noqa: E402

app.include_router(campaign_routes.router)
app.include_router(canon_routes.router)
app.include_router(control_routes.router)
app.include_router(downtime_routes.router)
app.include_router(combat_routes.router)
app.include_router(immersion_routes.router)
app.include_router(npc_routes.router)
app.include_router(procedural_routes.router)
app.include_router(rules_routes.router)
app.include_router(scene_routes.router)
app.include_router(session_routes.router)
app.include_router(setup_routes.router)
app.include_router(system_routes.router)
app.include_router(camera_routes.router)


@app.exception_handler(ApiError)
async def api_error_handler(request, exc: ApiError):
    return JSONResponse(
        status_code=exc.status,
        content=ErrorResponse(status="error", error=exc.error, code=exc.code).model_dump(),
    )


@app.exception_handler(CampaignNotFound)
async def campaign_not_found_handler(request, exc: CampaignNotFound):
    logger.info("Campaign lookup failed: %s", exc)
    return JSONResponse(
        status_code=404,
        content=ErrorResponse(
            status="error",
            error="Campaign not found",
            code="CAMPAIGN_NOT_FOUND",
        ).model_dump(),
    )

# Mount admin panel — prefer the Vite build output (dist/) when available,
# otherwise fall back to the standalone index.html at the panel root.
_panel_root = Path(__file__).parent / "admin-panel"
_panel_dist = _panel_root / "dist"
_admin_serve = _panel_dist if _panel_dist.exists() else _panel_root
if _admin_serve.exists():
    app.mount("/admin", StaticFiles(directory=str(_admin_serve), html=True), name="admin")

# Serve generated TTS audio.
_tts_audio_dir = Path(__file__).parent / settings.tts_audio_dir
_tts_audio_dir.mkdir(parents=True, exist_ok=True)


@app.get("/audio/{filename}")
async def serve_audio(filename: str):
    audio_root = _tts_audio_dir.resolve()
    audio_path = (audio_root / filename).resolve()
    if Path(filename).name != filename or audio_path.parent != audio_root or not audio_path.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found")
    return FileResponse(audio_path)


@app.websocket("/api/ws")
async def admin_websocket(websocket: WebSocket):

    """WebSocket endpoint for admin panel real-time updates."""
    if len(websocket_clients) >= settings.ws_max_connections:
        await websocket.close(code=1013, reason="Too many connections")
        return
    await websocket.accept()
    if settings.admin_token:
        # Authenticate in-band so the token never appears in a URL.
        try:
            first = json.loads(await asyncio.wait_for(websocket.receive_text(), timeout=5))
            ok = first.get("type") == "auth" and secrets.compare_digest(
                str(first.get("token") or ""), settings.admin_token
            )
        except (asyncio.TimeoutError, json.JSONDecodeError, TypeError, AttributeError):
            ok = False
        if not ok:
            await websocket.close(code=1008, reason="Authentication required")
            return
    websocket_clients.append(websocket)
    state = websocket.app.state
    logger.info(f"Admin panel connected (total: {len(websocket_clients)})")

    try:
        while True:
            # Read messages from admin panel (for commands)
            data = await websocket.receive_text()
            if len(data.encode("utf-8")) > settings.ws_max_message_bytes:
                await websocket.close(code=1009, reason="Message too large")
                return
            # The auth handshake above guards exactly these on the first
            # frame; the loop guarded none of them, so one bad frame fell to
            # the outer `except Exception` and closed the socket. The panel
            # reconnects with backoff, so each one blinded it for longer.
            try:
                msg = json.loads(data)
                if not isinstance(msg, dict):
                    raise TypeError("expected a JSON object")
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                await websocket.send_text(json.dumps({
                    "type": "error", "error": f"Malformed message ({type(e).__name__})",
                }))
                continue

            # Rate limit: max 5 messages per second per connection
            # Check rate limiting AFTER receiving (not before busy-spinning)
            now = time.time()
            if websocket in _admin_ws_rate and now - _admin_ws_rate[websocket] < 0.2:
                await websocket.send_text(json.dumps({"type": "rate_limited"}))
                continue
            _admin_ws_rate[websocket] = now

            # A command that arrives before the campaign has started finds
            # its collaborator missing. That used to raise AttributeError
            # into the same connection-closing handler.
            kind = msg.get("type")
            needs = {
                "pause": "chat_listener", "resume": "chat_listener",
                "roll_command": "foundry_client",
            }.get(kind)
            if needs and not getattr(state, needs, None):
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "error": f"Cannot {kind}: {needs.replace('_', ' ')} is not running yet",
                }))
                continue

            if kind == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
            elif kind == "pause":
                await state.chat_listener.pause()
                if state.foundry_client:
                    try:
                        await state.foundry_client.execute_js(
                            "if(!game.paused){game.togglePause(true,true);}"
                        )
                    except Exception as _e:
                        logger.warning(f"Admin pause: Foundry togglePause failed: {_e}")
                await broadcast_state_update({"type": "ai_paused"})
            elif kind == "resume":
                await state.chat_listener.resume()
                if state.foundry_client:
                    try:
                        await state.foundry_client.execute_js(
                            "if(game.paused){game.togglePause(false,true);}"
                        )
                    except Exception as _e:
                        logger.warning(f"Admin resume: Foundry togglePause failed: {_e}")
                if state.chat_listener:
                    state.chat_listener._reset_idle_timer()
                await broadcast_state_update({"type": "ai_resumed"})
            elif kind == "roll_command":
                formula = msg.get("formula", "1d20")
                speaker = msg.get("speaker", "GM")
                flavor = msg.get("flavor", "")
                await state.foundry_client.roll(formula, speaker=speaker, flavor=flavor)
    except WebSocketDisconnect:
        logger.info("Admin panel disconnected")
    except Exception as e:
        logger.error(f"Admin WebSocket error: {e}", exc_info=True)
    finally:
        if websocket in websocket_clients:
            websocket_clients.remove(websocket)
        _admin_ws_rate.pop(websocket, None)


# --- Entry Point ---
if __name__ == "__main__":
    import socket
    import uvicorn

    # Fail loudly before lifespan startup: uvicorn binds the socket *after*
    # running lifespan, and its bind error goes to the uvicorn.error logger
    # (propagate=False), so it never reaches ai-gm.log — a duplicate engine
    # looks like an unexplained instant self-shutdown.
    try:
        with socket.socket() as probe:
            probe.bind((settings.admin_host, settings.admin_port))
    except OSError:
        logger.error(
            f"Port {settings.admin_port} is already in use — another engine is "
            f"running. Stop it first: lsof -ti:{settings.admin_port} | xargs kill"
        )
        sys.exit(1)

    # Loopback-only by default; set ADMIN_HOST=0.0.0.0 (and ADMIN_TOKEN) in .env
    # to expose the admin API on the LAN.
    uvicorn.run(
        "main:app",
        host=settings.admin_host,
        port=settings.admin_port,
        log_level="info",
        reload=False,
        lifespan="on",
    )

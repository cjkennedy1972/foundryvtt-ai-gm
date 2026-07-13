"""Manages the embedded FoundryVTT REST API relay as a subprocess.

The relay (Go binary, source in the relay/ git submodule) is spawned with an
explicit environment, monitored by a watchdog, and terminated on shutdown.
If a relay is already answering on the configured port it is adopted instead
of spawned, so an externally managed relay keeps working unchanged.
"""

import asyncio
import json
import logging
import os
import secrets
import shutil
import signal
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from config import settings

logger = logging.getLogger("relay")

# macOS install path first; the relay's own auto-detect prefers Chromium,
# which is deprecated, so we always resolve and pass Chrome explicitly.
_CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome",
    "google-chrome-stable",
]

_RESTART_WINDOW_SECONDS = 300
_MAX_RESTARTS_PER_WINDOW = 3


def _is_permanent_headless_error(message: str) -> bool:
    """Return true for configuration errors that retries cannot repair."""
    lowered = message.lower()
    return (
        "configured foundry user not found" in lowered
        or "configured foundry user is already logged in" in lowered
    )


def _resolve_chrome_path() -> str:
    if settings.relay_chrome_path:
        return settings.relay_chrome_path
    for candidate in _CHROME_CANDIDATES:
        if "/" in candidate:
            if Path(candidate).exists():
                return candidate
        elif shutil.which(candidate):
            return shutil.which(candidate)
    return ""


class RelayManager:
    def __init__(self):
        self.repo_root = Path(__file__).resolve().parents[2]
        self.binary = (
            Path(settings.relay_binary_path).expanduser()
            if settings.relay_binary_path
            else self.repo_root / "bin" / "relay"
        )
        self.data_dir = (
            Path(settings.relay_data_dir).expanduser()
            if settings.relay_data_dir
            else self.repo_root / "data" / "relay"
        )
        self.static_dir = self.repo_root / "relay"
        self.port = urlparse(settings.relay_url).port or 13010
        self.dashboard_url = f"http://localhost:{self.port}"
        self.proc: subprocess.Popen | None = None
        self.adopted = False
        self.crashed = False
        self.restarts = 0
        self._restart_times: list[float] = []
        self._watchdog: asyncio.Task | None = None
        self._log_file = None
        self._credentials_path = self.data_dir / "aigm-credentials.json"
        self._headless_blocked = False

    # --- lifecycle ---

    async def start(self):
        if await self._is_healthy(timeout=1.0):
            logger.info(
                f"External relay detected on port {self.port} — adopting it "
                "(not spawning a managed instance)"
            )
            self.adopted = True
            # Relay process is running but Chrome session lock files from a
            # previously-killed instance may still be present — clean them now
            # so the relay can launch a headless session.
            self._clear_chrome_locks()
            return

        self._ensure_binary()
        if not (self.static_dir / "public-dist" / "index.html").exists():
            logger.warning(
                "relay/public-dist/index.html missing — the relay API will work "
                "but the pairing/dashboard UI won't. Run ./run.sh to build it."
            )

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._hint_migration()
        self._spawn()
        await self._wait_ready()
        self._watchdog = asyncio.create_task(self._watch())
        logger.info(
            f"Relay running (pid {self.proc.pid}) — dashboard: {self.dashboard_url}"
        )

    async def stop(self):
        if self._watchdog:
            self._watchdog.cancel()
            self._watchdog = None
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()  # SIGTERM; relay drains up to 15s
            try:
                await asyncio.to_thread(self.proc.wait, 20)
            except subprocess.TimeoutExpired:
                logger.warning("Relay did not exit after SIGTERM — killing")
                self.proc.kill()
                await asyncio.to_thread(self.proc.wait)
        if self._log_file:
            self._log_file.close()
            self._log_file = None
        logger.info("Relay stopped")

    async def restart(self):
        """Stop and restart the relay subprocess."""
        if self.adopted:
            raise RuntimeError("Cannot restart an externally-managed relay")
        await self.stop()
        self.crashed = False
        await self.start()
        logger.info("Relay restarted")

    def status(self) -> dict:
        running = self.adopted or (self.proc is not None and self.proc.poll() is None)
        return {
            "managed": not self.adopted,
            "running": running,
            "crashed": self.crashed,
            "pid": self.proc.pid if self.proc and self.proc.poll() is None else None,
            "port": self.port,
            "restarts": self.restarts,
            "dashboard_url": self.dashboard_url,
        }

    # --- API key provisioning ---

    async def ensure_api_key(self):
        creds = self._load_credentials()

        # The credentials file is the source of truth once it holds a key:
        # that key is the one the Foundry module was paired under.
        if creds.get("api_key"):
            if await self._key_is_valid(creds["api_key"]):
                settings.relay_api_key = creds["api_key"]
                logger.info("Relay API key loaded from stored credentials")
                return
            await self.stop()  # don't leave the subprocess orphaned
            raise RuntimeError(
                f"The relay API key stored in {self._credentials_path} was "
                "rejected by the relay. Delete the 'api_key' entry from that "
                "file to re-provision (note: re-provisioning rotates the key "
                "and requires re-pairing the Foundry module)."
            )

        if settings.relay_api_key:
            if await self._key_is_valid(settings.relay_api_key):
                creds["api_key"] = settings.relay_api_key
                self._save_credentials(creds)
                return
            logger.warning(
                "RELAY_API_KEY from .env was rejected by the managed relay "
                "(likely a leftover from a standalone relay with a different "
                "database) — provisioning a fresh key."
            )

        # regenerate-key is the only endpoint that returns the plaintext
        # master key. It also wipes connection tokens (Foundry pairing), so it
        # only ever runs here, before any key has been established.
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                resp = await client.post(
                    f"{settings.relay_url}/auth/regenerate-key",
                    json={"email": creds["email"], "password": creds["password"]},
                )
                resp.raise_for_status()
                api_key = resp.json()["apiKey"]
            except Exception as e:
                # Bootstrap fallback: if regenerate-key fails (e.g., credentials don't exist
                # in a fresh relay database), generate a temporary key and warn the user
                logger.warning(
                    f"Could not get API key from relay: {e}. Using bootstrap key. "
                    "You must pair the Foundry module to establish proper credentials."
                )
                api_key = secrets.token_hex(32)

        creds["api_key"] = api_key
        self._save_credentials(creds)
        settings.relay_api_key = api_key
        logger.info(
            f"Relay API key provisioned. Pair the Foundry module at "
            f"{self.dashboard_url} (log in as {creds['email']}; password is in "
            f"{self._credentials_path})"
        )

    async def ensure_rest_scoped_key(self, client_id: str | None = None):
        """Provision a scoped API key for REST calls (uploads, scene writes, etc.).

        The master key only works over WebSocket. REST endpoints require a
        scoped key sent as x-api-key. We create one with all scopes the engine
        uses and cache it in settings.relay_scoped_key.

        client_id, when known, binds the key's scopedClientId so the relay's
        /upload (and other REST) handlers resolve the target Foundry client
        directly from the key instead of falling back to "exactly one
        WebSocket client connected under this same master key" — which fails
        with 'clientId is required' because the headless browser's Foundry
        module and this scoped key authenticate as different principals and
        land in different connected-client groups. Pass the headless/paired
        session's clientId whenever it's known (see ensure_headless_session).
        """
        REST_SCOPES = [
            "file:read", "file:write",
            "entity:read", "entity:write",
            "scene:read", "scene:write",
            "canvas:read", "canvas:write",
            "chat:read", "chat:write",
            "world:info", "search",
            "macro:list", "macro:execute",
        ]
        KEY_NAME = "aigm-engine"

        creds = self._load_credentials()
        session_token = await self._get_session_token(creds)
        if not session_token:
            logger.error("Failed to get session token for REST scoped key provisioning")
            return

        headers = {"Authorization": f"Bearer {session_token}"}
        async with httpx.AsyncClient(timeout=10) as client:
            # Delete any existing key with this name to get a fresh plaintext copy
            resp = await client.get(f"{settings.relay_url}/auth/api-keys", headers=headers)
            if resp.status_code == 200:
                payload = resp.json()
                key_list = payload.get("keys", payload) if isinstance(payload, dict) else payload
                for entry in key_list:
                    if entry.get("name") == KEY_NAME:
                        await client.delete(
                            f"{settings.relay_url}/auth/api-keys/{entry['id']}",
                            headers=headers,
                        )
                        break

            body = {"name": KEY_NAME, "scopes": REST_SCOPES}
            if client_id:
                body["scopedClientId"] = client_id
            resp = await client.post(
                f"{settings.relay_url}/auth/api-keys",
                headers=headers,
                json=body,
            )
            if resp.status_code == 201:
                settings.relay_scoped_key = resp.json().get("key", "")
                logger.info(
                    "Relay REST scoped key provisioned"
                    + (f" (bound to client {client_id})" if client_id else "")
                )
            else:
                logger.error(f"REST scoped key creation failed: {resp.status_code} {resp.text[:200]}")

    async def _key_is_valid(self, api_key: str) -> bool:
        # Master keys are only accepted on the WebSocket auth path (REST
        # x-api-key takes scoped keys), so validate with the same handshake
        # FoundryClient performs. The relay checks the key before looking up
        # a Foundry client, so a 4002 close with "No connected Foundry client
        # found" still proves the key is valid (Foundry just isn't paired or
        # online yet); only an explicit "Invalid API key" close means invalid.
        import websockets

        try:
            ws = await asyncio.wait_for(
                websockets.connect(settings.relay_ws_url), timeout=5
            )
        except Exception:
            logger.warning("Could not reach relay WS endpoint to validate key")
            return True  # ambiguous — don't block startup
        try:
            await ws.send(json.dumps({"type": "auth", "token": api_key}))
            ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            return ack.get("type") == "connected"
        except websockets.exceptions.ConnectionClosed as e:
            # websockets exceptions expose 'code' and 'reason' on ConnectionClosed.
            # Fall back to str(e) if reason isn't available across versions.
            reason = ""
            try:
                reason = getattr(e, 'reason', '') or str(e)
            except Exception:
                reason = str(e)
            if isinstance(reason, (bytes, bytearray)):
                try:
                    reason = reason.decode(errors='ignore')
                except Exception:
                    reason = str(reason)
            if "Invalid API key" in reason:
                return False
            if "No connected Foundry client" in reason:
                return True
            logger.warning(f"Ambiguous relay WS close during key check: {reason!r}")
            return True
        except Exception as e:
            logger.warning(f"Relay key validation inconclusive: {e}")
            return True
        finally:
            await ws.close()

    def admin_credentials(self) -> dict:
        """Email/password for the relay admin user (created via ADMIN_EMAIL env)."""
        return self._load_credentials()

    # --- headless Chrome session ---

    async def ensure_headless_session(self) -> str | None:
        """Launch a headless Chrome session connecting to FoundryVTT.

        Returns the clientId the relay assigned to the session, or None if
        FOUNDRY_URL / FOUNDRY_USERNAME / FOUNDRY_PASSWORD are not configured.

        The session replaces the manual module-pairing workflow: Chrome logs
        into Foundry as a GM, the relay's Foundry module (injected into the
        browser) connects back to the relay, and the AI-GM's FoundryClient
        then talks to that live session.

        This is idempotent: if a session is already active for this user we
        return its clientId without starting a new browser.
        """
        if not (settings.foundry_url and settings.foundry_username and settings.foundry_password):
            logger.info(
                "FOUNDRY_URL / FOUNDRY_USERNAME / FOUNDRY_PASSWORD not set — "
                "skipping headless session. Connect the Foundry module manually "
                "at the relay dashboard or configure these env vars."
            )
            return None
        if self._headless_blocked:
            logger.warning(
                "Headless Foundry session disabled for this run after a permanent "
                "credential error; connect the Foundry module manually or correct "
                "FOUNDRY_USERNAME/FOUNDRY_PASSWORD and restart the engine."
            )
            return None

        creds = self._load_credentials()
        session_token = await self._get_session_token(creds)
        if not session_token:
            logger.error("Failed to obtain relay session token for headless setup")
            return None

        scoped_key = await self._get_or_create_scoped_key(session_token)
        if not scoped_key:
            logger.error("Failed to obtain session:manage scoped key")
            return None

        # Check for an already-running session (avoids redundant Chrome launch)
        existing = await self._find_active_session(scoped_key)
        if existing:
            logger.info(f"Reusing existing headless session (clientId={existing})")
            await self.ensure_rest_scoped_key(existing)
            return existing

        client_id = await self._launch_headless_session(scoped_key)
        if client_id:
            logger.info(
                f"Headless Chrome session active — Foundry connected "
                f"(clientId={client_id})"
            )
            await self.ensure_rest_scoped_key(client_id)
        return client_id

    async def restart_headless_session(self) -> str | None:
        """Force a fresh headless session, killing any dead-but-alive Chrome.

        Used for self-healing: if the headless browser's module silently drops
        its relay connection, the Chrome process lingers (so a plain
        ensure_headless_session would *reuse* the dead session). Killing the
        profile's Chrome first guarantees a clean relaunch.

        If the Chrome kill alone isn't enough (relay returns 408 "context
        canceled"), the relay process itself is restarted to flush stale CDP
        state before retrying.
        """
        if self._headless_blocked:
            logger.warning(
                "Skipping headless self-heal because the configured Foundry "
                "credentials were previously rejected; restart the engine after "
                "correcting FOUNDRY_USERNAME/FOUNDRY_PASSWORD."
            )
            return None
        logger.info("Restarting headless session (self-heal)…")
        self._kill_profile_chrome()
        self._clear_chrome_locks()
        await asyncio.sleep(3.0)  # give relay time to detect Chrome died

        client_id = await self.ensure_headless_session()
        if client_id:
            return client_id

        # Chrome kill alone didn't recover — restart the relay process to
        # flush any stale Chrome DevTools Protocol state.
        if not self.adopted:
            logger.warning(
                "Headless session still failing after Chrome kill — restarting relay process…"
            )
            try:
                await self.restart()
                await asyncio.sleep(2.0)
                client_id = await self.ensure_headless_session()
            except Exception as e:
                logger.error(f"Relay restart during self-heal failed: {e}", exc_info=True)

        return client_id

    async def _get_session_token(self, creds: dict) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{settings.relay_url}/auth/login",
                    json={"email": creds["email"], "password": creds["password"]},
                )
                if resp.status_code == 200:
                    return resp.json().get("sessionToken")
                logger.error(f"Relay login failed: {resp.status_code} {resp.text[:200]}")
        except httpx.HTTPError as e:
            logger.error(f"Relay login request failed: {e}", exc_info=True)
        return None

    async def _get_or_create_scoped_key(self, session_token: str) -> str | None:
        headers = {"Authorization": f"Bearer {session_token}"}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                # Check existing keys first to avoid accumulating them
                resp = await client.get(
                    f"{settings.relay_url}/auth/api-keys", headers=headers
                )
                if resp.status_code == 200:
                    payload = resp.json()
                    key_list = payload.get("keys", payload) if isinstance(payload, dict) else payload
                    for key_entry in key_list:
                        scopes = key_entry.get("scopes", [])
                        name = key_entry.get("name", "")
                        if "session:manage" in scopes and name == "aigm-headless":
                            # Can't retrieve the plaintext key after creation;
                            # delete and recreate to get a fresh one.
                            await client.delete(
                                f"{settings.relay_url}/auth/api-keys/{key_entry['id']}",
                                headers=headers,
                            )
                            break

                resp = await client.post(
                    f"{settings.relay_url}/auth/api-keys",
                    headers=headers,
                    json={"name": "aigm-headless", "scopes": ["session:manage"]},
                )
                if resp.status_code == 201:
                    return resp.json().get("key")
                logger.error(f"Scoped key creation failed: {resp.status_code} {resp.text[:200]}")
        except httpx.HTTPError as e:
            logger.error(f"Scoped key request failed: {e}", exc_info=True)
        return None

    async def _find_active_session(self, scoped_key: str) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{settings.relay_url}/session",
                    headers={"x-api-key": scoped_key},
                )
                if resp.status_code == 200:
                    payload = resp.json()
                    if isinstance(payload, dict):
                        sessions = payload.get("activeSessions", [])
                    else:
                        sessions = payload
                    if sessions:
                        return sessions[0].get("clientId")
        except httpx.HTTPError:
            pass
        return None

    async def _launch_headless_session(self, scoped_key: str) -> str | None:
        import base64
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.backends import default_backend

        headers = {"x-api-key": scoped_key}
        async with httpx.AsyncClient(timeout=240) as client:
            # Step 1: handshake — relay generates RSA key pair and nonce
            resp = await client.post(
                f"{settings.relay_url}/session-handshake",
                headers={
                    **headers,
                    "x-foundry-url": settings.foundry_url,
                    "x-username": settings.foundry_username,
                    **({"x-world-name": settings.foundry_world} if settings.foundry_world else {}),
                },
            )
            if resp.status_code != 200:
                logger.error(
                    f"Session handshake failed: {resp.status_code} {resp.text[:300]}"
                )
                return None
            hs = resp.json()
            handshake_token = hs["token"]
            nonce = hs["nonce"]
            public_key_pem = hs["publicKey"].encode()

            # Step 2: encrypt {"password": "...", "nonce": "..."} with the
            # relay's RSA-2048 public key using OAEP + SHA-256.
            pub_key = serialization.load_pem_public_key(public_key_pem, backend=default_backend())
            plaintext = json.dumps(
                {"password": settings.foundry_password, "nonce": nonce}
            ).encode()
            encrypted = pub_key.encrypt(plaintext, padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ))
            encrypted_b64 = base64.b64encode(encrypted).decode()

            # Step 3: start the session (Chrome launches here — up to 90s).
            # Clear any orphaned Chrome + stale profile lock immediately before
            # launch — a prior failed attempt can leave a SingletonLock that
            # makes this launch fail instantly with "File exists".
            self._clear_chrome_locks()
            logger.info(
                f"Launching headless Chrome session for {settings.foundry_url} "
                f"(this may take up to 90s)…"
            )
            try:
                resp = await client.post(
                    f"{settings.relay_url}/start-session",
                    headers=headers,
                    json={"handshakeToken": handshake_token, "encryptedPassword": encrypted_b64},
                )
            except httpx.HTTPError as e:
                # The Chrome launch can exceed the HTTP timeout; a ReadTimeout
                # here must degrade to "no headless session" (the caller retries
                # in the background) rather than propagate and crash engine
                # startup — which is exactly what happened in the field.
                logger.error(
                    f"Headless session start errored ({type(e).__name__}: {e}) — "
                    "continuing without it; will retry in background"
                )
                return None
            if resp.status_code == 200:
                return resp.json().get("clientId")
            logger.error(
                f"Headless session start failed: {resp.status_code} {resp.text[:300]}"
            )
            if _is_permanent_headless_error(resp.text):
                self._headless_blocked = True
                logger.error(
                    "Headless session will not be retried this run: the configured "
                    f"Foundry account ({settings.foundry_username!r}) is unavailable "
                    "or already logged in. Correct the Foundry credentials or "
                    "leave them unset to use manual module pairing."
                )
        return None

    # --- internals ---

    def _ensure_binary(self):
        if self.binary.exists():
            return
        if shutil.which("go"):
            logger.info("Relay binary missing — building with go build...")
            subprocess.run(
                ["go", "build", "-o", str(self.binary), "./cmd/server"],
                cwd=self.repo_root / "relay" / "go-relay",
                check=True,
            )
            return
        raise RuntimeError(
            f"Relay binary not found at {self.binary} and Go is not installed. "
            "Run ./run.sh (with Go installed), or set RELAY_MANAGED=false and "
            "run an external relay."
        )

    def _kill_profile_chrome(self):
        """Kill any orphaned Chrome still using our headless profile.

        On an engine restart the previous run's headless Chrome is not a child
        of the new process, so it survives and keeps holding the profile's
        SingletonLock. A new headless session then fails with
        "SingletonLock: File exists". Removing the lock files alone doesn't
        help while that Chrome is alive — it must be killed first.
        """
        profile = str(self.data_dir / "chrome-profile")
        try:
            out = subprocess.run(
                ["pgrep", "-f", profile], capture_output=True, text=True
            )
        except FileNotFoundError:
            return  # pgrep unavailable (non-POSIX); skip
        me = os.getpid()
        killed = 0
        for line in out.stdout.split():
            if not line.isdigit():
                continue
            pid = int(line)
            if pid == me:
                continue
            try:
                os.kill(pid, signal.SIGKILL)
                killed += 1
            except (ProcessLookupError, PermissionError):
                pass
        if killed:
            logger.info(
                f"Killed {killed} orphaned Chrome process(es) holding the relay profile"
            )
            time.sleep(0.5)  # let the OS release the profile before relaunch

    def _clear_chrome_locks(self):
        """Remove stale Chrome SingletonLock/Socket/Cookie files.

        Safe to call whenever the relay is not running (or not yet started).
        Chrome leaves these behind if the process is killed hard.
        """
        # Kill any orphaned Chrome holding the profile first — otherwise it
        # recreates the lock the moment we delete it.
        self._kill_profile_chrome()
        for lock in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            lock_path = self.data_dir / "chrome-profile" / lock
            if lock_path.is_symlink() or lock_path.exists():
                lock_path.unlink(missing_ok=True)
                logger.debug(f"Removed stale Chrome lock: {lock_path.name}")

    def _spawn(self):
        creds = self._load_credentials()
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", ""),
            "PORT": str(self.port),
            "DB_TYPE": "sqlite",
            "DATA_DIR": str(self.data_dir),
            "STATIC_DIR": str(self.static_dir),
            "ADMIN_EMAIL": creds["email"],
            "ADMIN_PASSWORD": creds["password"],
            "LOG_LEVEL": settings.relay_log_level,
            "MONTHLY_REQUEST_LIMIT": "0",
            "FRONTEND_URL": self.dashboard_url,
            "ALLOW_HEADLESS": "true" if settings.relay_allow_headless else "false",
        }
        chrome = _resolve_chrome_path()
        if chrome:
            env["PUPPETEER_EXECUTABLE_PATH"] = chrome
        elif settings.relay_allow_headless:
            logger.warning(
                "Google Chrome not found — headless Foundry sessions will not "
                "work. Install Chrome or set RELAY_CHROME_PATH."
            )
        # RELAY_ENV_* passthrough: full relay feature set (Stripe, SMTP, Redis,
        # headless tuning) stays configurable without code changes here.
        for key, value in os.environ.items():
            if key.startswith("RELAY_ENV_"):
                env[key[len("RELAY_ENV_"):]] = value

        self._clear_chrome_locks()

        if self._log_file:
            self._log_file.close()
        self._log_file = open(self.data_dir / "relay.log", "a")
        # cwd is the data dir so the relay's godotenv lookup (./.env, ../.env)
        # can never pick up the AI-GM's .env or any other stray config.
        self.proc = subprocess.Popen(
            [str(self.binary)],
            cwd=self.data_dir,
            env=env,
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
        )

    async def _wait_ready(self, timeout: float = 30.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"Relay exited during startup (code {self.proc.returncode}).\n"
                    f"Last log lines:\n{self._tail_log()}"
                )
            if await self._is_healthy(timeout=1.0):
                return
            await asyncio.sleep(0.5)
        raise RuntimeError(
            f"Relay did not become healthy within {timeout}s.\n"
            f"Last log lines:\n{self._tail_log()}"
        )

    async def _is_healthy(self, timeout: float = 2.0) -> bool:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(f"{settings.relay_url}/api/health")
                return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def _watch(self):
        while True:
            await asyncio.sleep(2)
            if self.proc.poll() is None:
                continue
            now = time.monotonic()
            self._restart_times = [
                t for t in self._restart_times if now - t < _RESTART_WINDOW_SECONDS
            ]
            if len(self._restart_times) >= _MAX_RESTARTS_PER_WINDOW:
                self.crashed = True
                logger.error(
                    f"Relay crashed {_MAX_RESTARTS_PER_WINDOW} times in "
                    f"{_RESTART_WINDOW_SECONDS}s — giving up. Last log lines:\n"
                    f"{self._tail_log()}"
                )
                return
            backoff = 2 ** len(self._restart_times)
            logger.warning(
                f"Relay exited unexpectedly (code {self.proc.returncode}) — "
                f"restarting in {backoff}s"
            )
            await asyncio.sleep(backoff)
            self._restart_times.append(now)
            self.restarts += 1
            self._spawn()
            try:
                await self._wait_ready()
                logger.info(f"Relay restarted (pid {self.proc.pid})")
            except RuntimeError as e:
                logger.error(f"Relay restart failed: {e}", exc_info=True)

    def _load_credentials(self) -> dict:
        if self._credentials_path.exists():
            return json.loads(self._credentials_path.read_text())
        email = settings.relay_admin_email
        password = settings.relay_admin_password
        if not password:
            # Relay password rules: >=8 chars with upper, lower, and digit.
            password = secrets.token_urlsafe(18) + "Aa1"
        creds = {"email": email, "password": password}
        self._save_credentials(creds)
        return creds

    def _save_credentials(self, creds: dict):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._credentials_path.write_text(json.dumps(creds, indent=2) + "\n")
        self._credentials_path.chmod(0o600)

    def _tail_log(self, lines: int = 20) -> str:
        log_path = self.data_dir / "relay.log"
        if not log_path.exists():
            return "(no relay.log)"
        return "\n".join(log_path.read_text().splitlines()[-lines:])

    def _hint_migration(self):
        old_db = self.repo_root.parent / "foundryvtt-rest-api-relay" / "data" / "relay.db"
        new_db = self.data_dir / "relay.db"
        if old_db.exists() and not new_db.exists():
            logger.info(
                f"Found an existing relay database at {old_db}. To keep your "
                f"paired Foundry worlds, stop the app and copy that data/ "
                f"directory's contents into {self.data_dir}."
            )

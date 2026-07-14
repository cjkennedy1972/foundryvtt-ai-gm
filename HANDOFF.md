# AI-GM / Relay Handoff

## Current state

The application repository is on branch `agent/remaining-hardening` and the relay is a submodule tracking the fork branch `fix/ws-concurrent-write-panic`.

Latest commits:

- Main repository: `683070c`
- Relay fork: `f11f582`

The working tree has one pre-existing untracked file, `test.txt`. It has not been modified or staged.

## Completed work

- Python upgraded and dependency compatibility work completed.
- Node versioning migrated to Node 24.
- Pytest installed in `.venv-test` and full suite passes: `366 passed, 1 skipped`.
- Relay Go test suite passes; `go build`/`go vet` clean.
- Foundry administrator password is stored and read from the encrypted relay credential database.
- Foundry account/password values are no longer expected from the AI-GM `.env`.
- Relay supports administrator authentication before world selection.
- Campaign/world association and new-world provisioning are implemented.
- Foundry Virtual Tabletop auto-start and ownership-aware shutdown are implemented.
- Relay fork changes have been pushed to the user fork.

### Resolved this session (was the previous "blocking issue")

The previous handoff's blocker — "Chrome DevTools session terminates before
browser navigation" (`could not dial ws://…: EOF`, `context canceled`) — was
**not** a Chrome/CDP incompatibility. Root cause was **orphaned Chrome
processes accumulating across relay restarts**, which starved every new launch.
Three fixes landed:

1. **Orphaned Chrome leak** (`relay` commit `3ecc52e`,
   `go-relay/cmd/server/main.go`): `HeadlessManager.Shutdown()` was defined but
   never wired into graceful shutdown, so the shared Chrome subprocess survived
   every restart. Also bind the HTTP port *before* the Chrome warm-up goroutine,
   so a port-bind `log.Fatal` can't orphan a just-spawned browser.
2. **Startup hardening** (main repo commit `2061cff`,
   `ai-engine/relay_proc/manager.py`): clear stale Chrome before every relay
   spawn (survives hard crashes the relay's own Shutdown can't), and wait for
   Foundry's HTTP port to respond after auto-start before launching a session.
3. **Foundry v14 administrator-gate auth** (`relay` commit `f11f582`,
   `go-relay/internal/worker/headless.go`): three defects that all surfaced as a
   misleading `login form not found` GM-login timeout —
   - `detectPage()` never recognized the v14 admin gate (its `form[action="/auth"]`
     / `form#setup-authentication` selectors don't match v14 markup). Now matches
     the stable `input[name="adminPassword"]` field.
   - `loginToFoundryAdmin()` POSTed `{adminPassword}` **without** the required
     `action:"adminPassword"` field, so the server ignored it (returning 200) and
     the relay reported a false success.
   - success was read from HTTP status, but a rejected password also returns 200;
     now detected via the response redirecting away from `/auth`.

   **Verified end to end via a debug screenshot: the headless browser now passes
   the admin gate and reaches the Foundry world-selection page (worlds Valdris,
   Valenthal, Eldoria visible).**

## Remaining issue (next investigation)

The headless session now reaches the **world-selection page** but stops there,
then fails the subsequent GM login with `login form not found` — because no
world is ever launched, so the `/join` GM-login form (`input[name="password"]`)
never renders.

Root cause is a wiring gap, not a bug in the now-fixed auth path:

- `ai-engine/relay_proc/manager.py::_launch_headless_session` POSTs to
  `/start-session` with only `handshakeToken` (plus optional
  `createWorldName`/`createWorldSystem`). It never sends a `worldName`.
- The relay's `sessionStartHandler` sets `worldName = hs.WorldName` (empty), and
  `HeadlessManager.LaunchSession` **skips `selectWorld()` when `worldName == ""`**.
- So Chrome sits on the world list and `loginToFoundry` waits for a GM-login
  password field that only exists after a world is launched.

Decision needed before coding this (product/config, not mechanical):

1. Which existing world should the AI-GM launch? There are 3 on this server.
   The relay can already select a world by name (`selectWorld`) — the AI-GM side
   just needs to pass the correct `worldName` to `/start-session` (thread it from
   the active campaign's world, e.g. `KnownClient.WorldID`/world title).
2. Or is the intended flow always "create a fresh world" via the campaign
   builder (`createWorldName`)? In that case the plain auto-connect path still
   needs a target world for an already-provisioned campaign.

Relevant fields already exist: `doAutoStartForKnownClient` carries
`known.WorldID` as `ExpectedWorldID`, but `LaunchSession`/`/start-session` are
invoked with an empty `worldName`.

## Environment

- macOS
- Foundry app: `/Applications/Foundry Virtual Tabletop.app`
- Foundry v14 Build 364 (admin gate + world list confirmed via headless screenshot)
- Foundry port: `30000`
- Relay port: `13010`
- AI-GM admin/API port: `18080`
- Chrome executable: `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` (v150)
- Relay data: `data/relay/relay.db`
- Relay log: `data/relay/relay.log`
- AI-GM log: `ai-engine/ai-gm.log`
- Headless debug screenshots (written on failure): `data/relay/headless-login-debug.png`, `data/relay/headless-debug.png`

Do not add credentials or passwords to this file, source control, or diagnostic output.

## Reproduction

From the repository root:

```bash
./start.sh
```

Inspect the result:

```bash
tail -80 ai-engine/ai-gm.log
tail -100 data/relay/relay.log
lsof -nP -iTCP:13010 -sTCP:LISTEN
```

Watch the headless flow specifically — the relay log now reports the detected
page type before login:

```text
Detected page before login   pageType=admin      # admin gate reached
# (no "administrator authentication rejected")   # admin auth succeeded
# stops on the world list; GM login then times out with "login form not found"
```

Run tests:

```bash
./.venv-test/bin/python -m pytest -q
cd relay/go-relay
GOCACHE=/private/tmp/go-build-cache go test ./...
```

## Cleanup note

When killing test instances, match the AI-GM process by its command line
`Python main.py` (optionally filtered by cwd under this repo) — **not** by a
`foundryvtt-ai-gm.*main.py` pattern, which never matches because the process's
argv is just `python main.py`. A missed kill leaves the engine self-healing and
relaunching relays/Chrome in the background.

## Important implementation locations

- Relay browser lifecycle / admin auth: `relay/go-relay/internal/worker/headless.go`
  (`detectPage`, `loginToFoundryAdmin`, `loginToFoundry`, `selectWorld`, `LaunchSession`)
- Relay session start handler: `relay/go-relay/internal/handler/session.go` (`sessionStartHandler`)
- Relay server shutdown / port bind: `relay/go-relay/cmd/server/main.go`
- AI-GM relay process manager: `ai-engine/relay_proc/manager.py`
  (`_launch_headless_session`, `_ensure_foundry_started`, `_wait_for_foundry_http`)
- AI-GM Foundry client: `ai-engine/foundry/client.py`
- Campaign API: `ai-engine/api/routes/campaign.py`
- Campaign builder UI: `ai-engine/admin-panel/src/pages/CampaignBuilder.jsx`
- Launcher: `start.sh`, `run.sh`

## Current conclusion

The credential and Foundry-authentication workflow are now operational on macOS:
the relay auto-starts Foundry, launches headless Chrome without orphan/CDP
failures, and authenticates through Foundry v14's administrator gate to the
world-selection page. The one remaining gap to an end-to-end connected session
is passing the target world name from the AI-GM into `/start-session` so the
relay launches a world and the GM login can complete — a small, well-scoped
change pending the "which world" decision above.

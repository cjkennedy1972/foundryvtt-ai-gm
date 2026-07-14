# AI-GM / Relay Handoff

## Current state

The application repository is on branch `agent/remaining-hardening` and the relay is a submodule tracking the fork branch `fix/ws-concurrent-write-panic`.

Latest commits:

- Main repository: `0e38973`
- Relay fork: `f11f582` (pushed to the user fork)

**The AI-GM is now end-to-end operational on this macOS environment.** A cold
`./start.sh` auto-starts Foundry, launches headless Chrome without orphan/CDP
failures, authenticates the Foundry v14 administrator gate, launches the
configured world (Valdris), logs in as GM, and the Foundry module connects back
to the relay — verified in the logs:

```text
relay: Headless Chrome session active — Foundry connected (clientId=fvtt_8deb0df4eb8a6d2c)
foundry.client: Connected to FoundryVTT relay (attempt 1)
ai-gm: FoundryVTT connected
ai-gm: AI Gamemaster Engine is RUNNING
```

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

### World launch on connect (resolved)

The headless session previously reached the world-selection page and stopped —
no world was launched, so the `/join` GM-login form never rendered and login
timed out with `login form not found`. Fixed (main repo commit `0e38973`): a new
`foundry_world` setting (env `FOUNDRY_WORLD`, default `Valdris`) is threaded from
`ensure_headless_session` → `_launch_headless_session` into the `/start-session`
`worldName`, so the relay selects and launches the world before GM login. The
relay's existing v14 world-list and `/join` selectors were verified against
Foundry's own templates (`li.package.world` / `h3.package-title` /
`a.control.play`; `select[name="userid"]` / `input[name="password"]`).

To point the AI-GM at a different world, set `FOUNDRY_WORLD` (matches the world's
display title, case-insensitive) or change the default in `ai-engine/config.py`.

## Remaining notes

- **Campaign-create path**: `campaign.py` calls `ensure_headless_session(create_world_name=…)`.
  With the new wiring, `ensure_headless_session` uses the created world as the
  launch target (`target_world = world_name or create_world_name or settings.foundry_world`),
  so a freshly built campaign world is also launched after creation. This path
  was not re-exercised live this session — worth a smoke test when next touching
  the campaign builder.
- The world name is a display-title match. If worlds are renamed, update
  `FOUNDRY_WORLD` accordingly.

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

Watch the headless flow specifically — a healthy run logs this sequence in
`data/relay/relay.log`:

```text
Detected page before login   pageType=admin      # admin gate reached
Selecting world              world=Valdris       # world launched
Game canvas detected         selector=#ui-left   # GM logged in, canvas up
Headless session established  clientId=fvtt_…     # Foundry module connected
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
  (`ensure_headless_session`, `_launch_headless_session`, `_ensure_foundry_started`, `_wait_for_foundry_http`)
- AI-GM world/config: `ai-engine/config.py` (`foundry_world`, env `FOUNDRY_WORLD`)
- AI-GM Foundry client: `ai-engine/foundry/client.py`
- Campaign API: `ai-engine/api/routes/campaign.py`
- Campaign builder UI: `ai-engine/admin-panel/src/pages/CampaignBuilder.jsx`
- Launcher: `start.sh`, `run.sh`

## Current conclusion

The full startup path is operational on macOS: the relay auto-starts Foundry,
launches headless Chrome without orphan/CDP failures, authenticates Foundry
v14's administrator gate, launches the configured world (Valdris), logs in as
GM, and the Foundry module connects back to the relay — the AI-GM reports
`FoundryVTT connected` and `AI Gamemaster Engine is RUNNING` with live RPC calls
succeeding. The original HANDOFF blocker and the follow-on admin-gate and
world-launch gaps are all resolved.

Not re-verified this session (pre-existing, out of scope): the campaign-builder
create-world path (`campaign.py`) and gameplay features beyond connection.

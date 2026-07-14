# AI-GM / Relay Handoff

## TL;DR

The AI Gamemaster is **end-to-end operational on this macOS box**: a cold
`./start.sh` auto-starts Foundry, launches headless Chrome, authenticates
Foundry v14's admin gate, launches a world, logs in as GM, and the Foundry
module connects back to the relay. Verified in logs:

```text
relay: Headless Chrome session active — Foundry connected (clientId=fvtt_8deb0df4eb8a6d2c)
foundry.client: Connected to FoundryVTT relay (attempt 1)
ai-gm: FoundryVTT connected
ai-gm: AI Gamemaster Engine is RUNNING
```

**There is ONE open decision awaiting the user** (see "Open decision" below): the
world to launch is currently hardcoded to `"Valdris"` as a checked-in default,
which is a smell. Do not "fix" it unprompted — the user is choosing among three
options.

## Repository state

- App repo branch: `agent/remaining-hardening` — HEAD `59dd441`, **5 commits
  ahead of `origin`, not pushed** (the user only asked to push the relay fork).
- Relay submodule branch: `fix/ws-concurrent-write-panic` — HEAD `f11f582`,
  **pushed** to the user fork `git@github.com:cjkennedy1972/foundryvtt-rest-api-relay.git`.
- Working tree: clean except one pre-existing untracked file `test.txt` (leave it alone).
- Tests green: `366 passed, 1 skipped` (pytest via `.venv-test`); relay
  `go build` / `go vet` / `go test ./...` all clean.

### Commits made this session (app repo, newest first)
- `59dd441` docs: handoff — end-to-end connection working
- `0e38973` feat: launch the configured Foundry world on headless connect
- `5fe9fa4` docs: handoff — admin-gate auth fixed
- `683070c` chore: bump relay submodule for admin-gate auth fix
- `2061cff` fix: clean up orphaned Chrome + wait for Foundry readiness

### Commits made this session (relay fork, newest first, pushed)
- `f11f582` fix: authenticate Foundry v14 administrator gate correctly
- `3ecc52e` fix: stop leaking orphaned Chrome processes across relay restarts

## Open decision (DO NOT resolve unprompted — user is choosing)

`ai-engine/config.py` sets `foundry_world: str = "Valdris"`. This makes every
headless connect launch the world titled *Valdris* (env-overridable via
`FOUNDRY_WORLD`). Hardcoding a specific world title in checked-in source is the
questionable part — though it matches the file's existing pattern of
user-specific defaults (`campaign_vault_path`, `ai_name`, etc.).

The codebase already has the *proper* source of truth: a campaign→world link
stored in the campaign's Obsidian vault metadata, read via
`get_campaign_world(campaign_name)` → `{world_name, world_id}`
(`ai-engine/campaign/obsidian_sync.py:653`). The campaign API even enforces it
(`CAMPAIGN_WORLD_MISMATCH`, `ai-engine/api/routes/campaign.py:738`). Caveat: the
link is established *by connecting* (it reads `game.world.title` from the live
session), so a first-ever connect still needs the world named some other way —
a legitimate role for a config/env value.

Three options presented to the user (awaiting their pick):
- **A (recommended):** change the default to `foundry_world = ""` and set
  `FOUNDRY_WORLD=Valdris` in the user's local `.env`/data dir — keeps the
  specific world name out of the repo, still works locally. One-line change.
- **B:** resolve the world from the default campaign's link
  (`get_campaign_world(settings.default_campaign)`), falling back to
  `FOUNDRY_WORLD` (default `""`) when no campaign is linked. Matches the
  intended design; only helps once `default_campaign` is set + linked
  (`default_campaign` currently defaults to `""`).
- **C:** leave the hardcoded `"Valdris"` default as-is.

## What was fixed this session (all verified)

The prior handoff's blocker was "Chrome DevTools session terminates before
navigation" (`could not dial ws://…: EOF`, `context canceled`). It was **not** a
Chrome/CDP incompatibility. Four distinct defects, fixed in order:

1. **Orphaned Chrome leak** (relay `3ecc52e`, `go-relay/cmd/server/main.go`).
   `HeadlessManager.Shutdown()` existed but was never called on graceful
   shutdown, so the shared Chrome subprocess survived every relay restart;
   restarts piled up dozens of live Chrome instances that starved new launches.
   Fix: wire `Shutdown()` into the shutdown path; also bind the HTTP port
   *before* starting the Chrome warm-up goroutine so a port-bind `log.Fatal`
   can't orphan a just-spawned browser.

2. **Startup hardening** (app `2061cff`, `ai-engine/relay_proc/manager.py`).
   Clear stale Chrome before every relay spawn (covers hard crashes the relay's
   own Shutdown can't run), and wait for Foundry's HTTP port to actually respond
   after auto-start before launching a session (avoids a `login form not found`
   caused by navigating before Foundry is serving).

3. **Foundry v14 admin-gate auth** (relay `f11f582`,
   `go-relay/internal/worker/headless.go`). Three sub-bugs, all surfacing as a
   misleading `login form not found` timeout:
   - `detectPage()` didn't recognize the v14 admin gate — its old selectors
     (`form[action="/auth"]`, `form#setup-authentication`) don't match v14, where
     the form has no `action` and `#setup-authentication` is the wrapping div's
     id. Now matches `input[name="adminPassword"]` (verified against live v14 DOM).
   - `loginToFoundryAdmin()` POSTed `{adminPassword}` **without** the required
     `action:"adminPassword"` field. Foundry's own client sends
     `{action:"adminPassword", adminPassword}` via `game.post` → `POST /auth`.
     Without `action` the server ignored the request but still returned 200, so
     the relay reported a false success and never authenticated.
   - success was read from HTTP status, but a rejected password also returns 200
     (redirect back to `/auth`). Now detected by the response redirecting *away*
     from `/auth`.

4. **World launch on connect** (app `0e38973`, `ai-engine/config.py` +
   `ai-engine/relay_proc/manager.py`). The headless flow never told the relay
   which world to launch, so Chrome parked on the world list and GM login timed
   out (the `/join` password form only exists after a world launches). Added the
   `foundry_world` setting (env `FOUNDRY_WORLD`) and threaded it
   `ensure_headless_session` → `_launch_headless_session` → `/start-session`
   `worldName`. `ensure_headless_session` resolves the target as
   `world_name or create_world_name or settings.foundry_world` — so a
   campaign-builder create also launches the world it just created.

The relay's v14 world-list and `/join` selectors were verified against Foundry's
own on-disk templates and are correct: `li.package.world` / `h3.package-title`
(= `{{package.title}}`) / `a.control.play`; `select[name="userid"]` /
`input[name="password"]`.

## How the headless flow works (mental model)

`ai-engine/main.py` startup → `RelayManager.start()` (auto-starts Foundry,
spawns/adopts the relay) → `ensure_headless_session()`:
1. login to relay, mint scoped key + handshake, POST `/start-session` with
   `worldName`.
2. Relay (`HeadlessManager.LaunchSession`, `go-relay/internal/worker/headless.go`):
   launch shared Chrome → navigate to Foundry → `detectPage()` → if `admin`,
   `loginToFoundryAdmin()` then reload → if `worldName` set and on `worldList`,
   `selectWorld()` (clicks the world's play button) → `loginToFoundry()` (GM
   join) → wait for game canvas → the injected Foundry REST module connects back
   to the relay, which resolves the pending session to a `clientId` (`fvtt_…`).
3. AI-GM's `foundry/client.py` then talks to that live session over the relay WS.

## Environment

- macOS; Foundry app `/Applications/Foundry Virtual Tabletop.app`, **v14 Build 364**.
- Ports: Foundry `30000`, relay `13010`, AI-GM admin/API `18080`.
- Chrome: `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` (v150),
  passed explicitly (relay auto-detect prefers Chromium, which is deprecated).
- Relay DB `data/relay/relay.db`; relay log `data/relay/relay.log`; AI-GM log
  `ai-engine/ai-gm.log`.
- Admin password + Foundry credentials live **only** in the relay's encrypted
  credential DB. Do not add credentials/passwords to this file, source control,
  or diagnostic output. Do not ask the user to paste the admin password — it is
  never needed in plaintext.
- On admin-login / GM-login failure the relay writes a debug screenshot to
  `data/relay/headless-login-debug.png` / `data/relay/headless-debug.png` — read
  these first when the flow breaks; they show exactly which page Chrome landed on.

## Reproduce / verify

```bash
./start.sh                                   # from repo root
tail -100 data/relay/relay.log
tail -80 ai-engine/ai-gm.log
lsof -nP -iTCP:13010 -sTCP:LISTEN
```

A healthy run logs, in `data/relay/relay.log`:

```text
Detected page before login   pageType=admin      # admin gate reached
Selecting world              world=Valdris       # world launched
Game canvas detected         selector=#ui-left   # GM logged in, canvas up
Headless session established  clientId=fvtt_…     # Foundry module connected
```

…and `ai-engine/ai-gm.log` ends with `FoundryVTT connected` +
`AI Gamemaster Engine is RUNNING`.

Tests:

```bash
./.venv-test/bin/python -m pytest -q
cd relay/go-relay && GOCACHE=/private/tmp/go-build-cache go test ./...
```

## Process cleanup (important — trips up naive kills)

The engine process's argv is literally `python main.py` — it does **not**
contain the string "foundryvtt-ai-gm". A `pkill -f 'foundryvtt-ai-gm.*main.py'`
matches nothing, leaving the engine alive and self-healing (relaunching
relay/Chrome). Kill by matching `Python main.py` and filtering on cwd:

```bash
for pid in $(pgrep -f "Python main.py"); do
  cwd=$(lsof -a -p $pid -d cwd -Fn 2>/dev/null | grep ^n | sed 's/^n//')
  case "$cwd" in *foundryvtt-ai-gm*) kill -9 "$pid";; esac
done
pkill -9 -f "/Users/ckennedy/Projects/foundryvtt-ai-gm/bin/relay"
pkill -9 -f "chrome-profile-"
```

Foundry (`Foundry Virtual Tabletop.app`) can be left running; the manager only
shuts down a Foundry instance it started itself. Each relay run uses a
per-PID Chrome profile dir under `data/relay/chrome-profile-<pid>`; these are
disposable.

## Key implementation locations

- Relay headless flow / v14 auth: `relay/go-relay/internal/worker/headless.go`
  (`detectPage`, `loginToFoundryAdmin`, `selectWorld`, `loginToFoundry`, `LaunchSession`, `Shutdown`).
- Relay `/start-session` handler: `relay/go-relay/internal/handler/session.go` (`sessionStartHandler`).
- Relay shutdown / port bind: `relay/go-relay/cmd/server/main.go`.
- AI-GM relay/Foundry lifecycle: `ai-engine/relay_proc/manager.py`
  (`ensure_headless_session`, `_launch_headless_session`, `_ensure_foundry_started`, `_wait_for_foundry_http`).
- AI-GM config / world setting: `ai-engine/config.py` (`foundry_world`, env `FOUNDRY_WORLD`; also `default_campaign`, `campaign_vault_path`).
- Campaign↔world link: `ai-engine/campaign/obsidian_sync.py` (`get_campaign_world`, `link_world_to_campaign`).
- AI-GM Foundry client: `ai-engine/foundry/client.py`.
- Campaign API / create-world path: `ai-engine/api/routes/campaign.py`.
- Launcher: `start.sh`, `run.sh`.

## Not re-verified this session (out of scope; treat as unproven)

- Campaign-builder create-world path (`campaign.py` → `ensure_headless_session(create_world_name=…)`).
  The new wiring should launch a freshly created world (target =
  `create_world_name`), but this was not exercised live — smoke-test before relying on it.
- Any gameplay features beyond establishing the Foundry connection.

## Verified reference facts (for a fresh model)

- The three worlds on this server are **Valdris, Valenthal, Eldoria** (all dnd5e);
  Valdris is what the user chose to auto-launch.
- Foundry v14's admin auth is `POST /auth` JSON `{action:"adminPassword", adminPassword}`,
  success = response redirected away from `/auth`. (Do not revert to the
  action-less form — that was the bug.)
- `foundry_world` matches the world's **display title**, case-insensitive.

# World Template Cloning

New campaigns with **Create world = on** clone a pre-configured template world
instead of Foundry's blank `createWorld`. Module enablement and per-module
settings live in the world's LevelDB `data/settings` store (written on first
launch), *not* in `world.json` — so "same base config every time" means copying
a prepared world, not passing a create flag.

- Clone logic: `ai-engine/foundry/world_template.py` (`clone_world`)
- Wired into: `ai-engine/api/routes/campaign.py` (build path, `create_world=True`)
- Config: `FOUNDRY_DATA_PATH` (default `~/Library/Application Support/FoundryVTT/Data`),
  `FOUNDRY_WORLD_TEMPLATE_ID` (default `_ai-gm-template`)

---

## One-time: prepare the template world

Do this once, and again after any Foundry **core** upgrade (the world data
format is pinned to the core version it was built on).

1. In Foundry setup, **Create World** → title `_ai-gm-template`, system `dnd5e`.
2. **Launch** it (this creates the settings store — required).
3. Enable your base module set (must include **`foundry-rest-api`**).
4. In the REST API module settings: set **Relay URL** to `ws://localhost:13010`,
   and confirm execute-JS is allowed (the AI-GM needs it):
   `allowExecuteJs = true`, `codeExecutionPermission = 4`,
   `allowMacroExecute/Write = true`.
5. **Do NOT pair it.** Leave `apiKey` / `clientId` / `connectionToken` unset —
   pairing is per-world and must never be copied into clones.
6. Keep the **`Gamemaster`** user (role 4). The AI-GM logs in as this account,
   so the relay credential you store per world must be `Gamemaster`.
7. Remove any player users you don't want inherited by every clone (the Users
   tab), then relaunch once so the change persists. Exit to setup.

### Verify the template is ready

Read its settings store with Foundry's bundled LevelDB reader:

```bash
node - <<'EOF'
import { ClassicLevel } from '/Applications/Foundry Virtual Tabletop.app/Contents/Resources/app/node_modules/classic-level/index.js';
import os from 'os'; import path from 'path';
const db = new ClassicLevel(path.join(os.homedir(),
  'Library/Application Support/FoundryVTT/Data/worlds/_ai-gm-template/data/settings'),
  { valueEncoding: 'json', keyEncoding: 'utf8', createIfMissing: false });
await db.open(); const s = {};
for await (const [k, v] of db.iterator()) s[(v&&v.key)||k] = (v&&'value'in v)?v.value:v;
await db.close();
const mc = JSON.parse(s['core.moduleConfiguration']);
console.log('foundry-rest-api enabled:', mc['foundry-rest-api']);
console.log('wsRelayUrl:', s['foundry-rest-api.wsRelayUrl']);
console.log('unpaired:', !s['foundry-rest-api.apiKey'] && !s['foundry-rest-api.clientId']);
EOF
```

Expect: `foundry-rest-api enabled: true`, `wsRelayUrl: "ws://localhost:13010"`,
`unpaired: true`. (Run only while the template world is **not** open.)

---

## Per new campaign

1. **Campaign Builder** → new campaign, **Create world = on**, name it. The
   engine clones the template to `worlds/<slug>/` (folder = slugified name,
   `world.json` title = the campaign name, which is what the relay selects on)
   and launches it headless.
2. In Foundry, enter the new world and finish world-specific setup:
   - Add player user accounts.
   - Create Player Actors / import from D&D Beyond.
   - **Pair** the world: Relay admin UI (`:13010`) → generate a pairing code →
     enter it in the world's REST API module → the world reloads and connects.
   - In the Relay admin UI → Credentials: set the **`Gamemaster`**
     username/password the AI-GM uses, and associate it with this world's
     paired client so the headless session can auto-start.
3. Back in Campaign Builder → build. The campaign is linked to the new world
   (by id) on first successful build.

---

## Notes / gotchas

- **Template is core-version pinned** (currently `14.364`). After a Foundry
  core upgrade, rebuild it. A launch-time version mismatch surfaces clearly
  rather than corrupting data.
- **One template = one system.** Requesting a different `foundry_system_id`
  than the template's system fails fast with an actionable error. A per-system
  template set would key off `FOUNDRY_WORLD_TEMPLATE_ID`.
- **Heavy module sets** slow headless software-rendered canvas ops; the
  `relay_rpc_timeout_canvas` (90s) accommodates this. A leaner template renders
  faster if you hit timeouts.
- The clone strips LevelDB `LOCK` files from the copy (Foundry recreates them),
  and inherits `system` / `coreVersion` / `systemVersion` from the template.

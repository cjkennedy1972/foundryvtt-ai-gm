# Setting Up the AI-GM as the Primary Gamemaster

By default, the AI-GM acts as an assistant to a human Gamemaster. However, you can configure the AI to own the "GM seat," allowing you and your friends to log in as players and experience the world without having a human in the GM role.

## The Setup Runbook

Follow these steps to provision the AI with Gamemaster permissions and shift your account to a player role.

1. **Launch FoundryVTT** and log in with a level-4 (Gamemaster) account.
2. **Create the required PC Actors** for your players; import them from D&D Beyond by ID where known.
3. **Create one Foundry User login per player** and associate each user with their correct PC Actor.
4. **Create an `ai-gm` user** within Foundry and assign it level-4 (Gamemaster) permissions.
5. **Set the engine's login** in `ai-engine/.env`: `FOUNDRY_USERNAME=ai-gm` and `FOUNDRY_PASSWORD` to that user's password. The engine logs in as this user itself. No relay is involved.
6. **Play as a player**: From this point forward, always log in using your player-role user account, not the GM account.

## Important Considerations

### The "Honour System" of Secrecy
This setup is designed to hide GM-only information (like secret plot points or hidden NPCs) from the players. However, this is an **honour-system secrecy**. There is no technical enforcement preventing a user with the correct permissions from seeing this data.

### Maintenance and GM Access
Whenever you need to perform maintenance—such as editing scenes, configuring modules, or managing compendiums—you must **log back in as a human Gamemaster**. Be aware that doing so re-exposes every secret to the active session.

### Alternative: Human GM Vision
If your group prefers that a human retains GM vision and control, simply skip this setup. The AI-GM will continue to operate in its default assistant mode, which is fully supported. There are no flags to enable or disable this behavior.

### Configuration Note: `FOUNDRY_USERNAME`
The `/gm` chat commands can be issued by:

1. **Classic path (primary):** Any Foundry user with role 3 or higher.
2. **Bootstrap fallback:** A user whose Foundry display name matches `FOUNDRY_USERNAME`, used only until the GM-role list has loaded when a session starts.

On the playwright backend, `FOUNDRY_USERNAME` is also the account the engine logs in as (`ai-gm`, a GM-tier account), so the fallback matches the engine's own account. That account is GM-tier, so the match adds no new access. The name is not a player's display name any more. Keeping an operator name separate from the login name is a possible follow-up.

If `FOUNDRY_USERNAME` is empty, only GM-tier users (role 3+) can issue `/gm` commands during the bootstrap window.

**Trust boundary:** The operator's name match is a single factor — there is no second authorization gate. Any player-role user who can read Foundry user settings and knows what name was configured could attempt to exploit this. However, in a self-hosted solo or small-group deployment, you control the world and all user accounts, making this fallback safe in practice.

### Operator Control Panel

When you log in at player role with your configured operator username (or with the admin token set in your browser), you can access the in-Foundry control panel to:

- View the AI engine status
- End the current session
- Issue /gm commands from chat

The control panel button appears in the scene controls only when you are authorized as the operator.

## Verifying Secrecy

The AI-GM's core promise is that it "keeps secrets" — the player-role human should not see unrevealed plot, unmet NPCs, or beyond-vision map data. Verify this works in your Foundry setup by walking through these checks **once against a real world before you begin active play**:

1. **Hidden token.** Have the AI place a token with `hidden: true` (`ai-engine/actions/schemas.py:315` — this is what it does for an ambush). From your player-role client, confirm the token is not drawn on the canvas and does not appear in the token HUD or the scene's token list.

2. **Unrevealed journal.** As `ai-gm`, create a journal entry and leave its ownership at **None** for all players. From your player-role client, confirm the entry does not appear in the Journal sidebar and its contents are not readable.

3. **Vision.** In a scene with walls and fog of war enabled, place your player token in a room with the door closed. From your player-role client, confirm you cannot see terrain, tokens, or map notes beyond your token's line of sight, and that fog does not lift for areas you have not visited.

**If all three pass**, the setup is protecting secrets as intended. Proceed with play.

**If any fails**, the cause is almost always the world's own configuration — token vision disabled on the scene, a journal left at default ownership, or a scene with no walls drawn — rather than a bug in the AI-GM. Fix the world setting and re-run the check.

### What this does not protect against

These checks confirm you are not *shown* secrets during play. They do not make secrets unreachable. You own the server, the world database and the GM password, so you can always log back in as `ai-gm`, open the browser console, or read the journal off disk. That is by design: the value here is not being spoiled by accident, not sealing the world against its own administrator. If you want to stay surprised, the honour system is the mechanism.

# Setting Up the AI-GM as the Primary Gamemaster

By default, the AI-GM acts as an assistant to a human Gamemaster. However, you can configure the AI to own the "GM seat," allowing you and your friends to log in as players and experience the world without having a human in the GM role.

## The Setup Runbook

Follow these steps to provision the AI with Gamemaster permissions and shift your account to a player role.

1. **Launch FoundryVTT** and log in with a level-4 (Gamemaster) account.
2. **Configure the FoundryVTT API Relay** to connect this application to your Foundry instance.
3. **Create the required PC Actors** for your players; import them from D&D Beyond by ID where known.
4. **Create one Foundry User login per player** and associate each user with their correct PC Actor.
5. **Create an `ai-gm` user** within Foundry and assign it level-4 (Gamemaster) permissions.
6. **Register the `ai-gm` user in the Relay Admin UI** and associate it with the current world.
7. **Play as a player**: From this point forward, always log in using your player-role user account, not the GM account.

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
2. **Operator fallback:** A player-role user whose Foundry display name matches the `FOUNDRY_USERNAME` setting in your `.env` file.

In a self-hosted deployment, you (the operator) control all Foundry user accounts. Setting `FOUNDRY_USERNAME` to your player-role user's display name allows you to issue `/gm` commands without needing to be logged in as a GM-tier account. This fallback is useful as a bootstrap mechanism before the cached GM-role list loads when you first start the game session.

If `FOUNDRY_USERNAME` is empty, only GM-tier users (role 3+) can issue `/gm` commands.

**Trust boundary:** The operator's name match is a single factor — there is no second authorization gate. Any player-role user who can read Foundry user settings and knows what name was configured could attempt to exploit this. However, in a self-hosted solo or small-group deployment, you control the world and all user accounts, making this fallback safe in practice.

### Operator Control Panel

When you log in at player role with your configured operator username (or with the admin token set in your browser), you can access the in-Foundry control panel to:

- View the AI engine status
- End the current session
- Issue /gm commands from chat

The control panel button appears in the scene controls only when you are authorized as the operator.

## Verifying Secrecy

The AI-GM's core promise is that it "keeps secrets" — the player-role human should not see unrevealed plot, unmet NPCs, or beyond-vision map data. Verify this works in your Foundry setup by walking through these checks **once against a real world before you begin active play**:

1. **Hidden Token Visibility**: Create a token that the AI has placed with `hidden: true`. Log in as your player-role user and verify the token is NOT visible in the token layer or enumerable from the browser console (`canvas.tokens.placeables` should not include it).

2. **Unrevealed Journal Entries**: Create a journal entry and do NOT grant your player-role user ownership. Log in as your player-role user and verify you cannot read the entry's contents (attempting to access it should show "You do not have permission to view this entry" or similar).

3. **Vision Limits**: In a scene with fog of war enabled, position your player-role token. Move the AI's hidden token to a location beyond your token's vision range. Verify you cannot see that area of the map through fog rendering or any other means.

**If all three checks pass**, your Foundry instance is correctly protecting secrets from player-role accounts. Proceed with play.

**If any check fails**, it indicates a Foundry permission or vision configuration issue — not a bug in the AI-GM code. This is a product-level finding that requires investigation of your Foundry setup (module interactions, permission overrides, custom macros, etc.) before play. Stop and contact your Foundry administrator.

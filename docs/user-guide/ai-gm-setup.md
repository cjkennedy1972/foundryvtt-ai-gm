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

### Known limitation: operator controls are unavailable at player role

Logging in at player role currently costs you the AI-GM operator surface: the in-Foundry control panel does not appear, and `/gm` chat commands are refused. Both are gated on the Foundry GM role today. Use the external Admin panel for operator actions, or log back in as a Gamemaster.

### Configuration Note: `FOUNDRY_USERNAME`
In your `.env` file, the `FOUNDRY_USERNAME` variable is used as a display name for the human GM account. This serves as an optional fallback for `/gm` chat command authorization before the GM-role user list is fully loaded. (Note: Any user with a role 3 or higher is always accepted regardless of this setting).

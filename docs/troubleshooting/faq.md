# FAQ & Troubleshooting

Common questions and solutions for AI-GM.

## General Questions

### What's the difference between AI-GM and a human GM?

AI-GM handles world-building, NPCs, and combat automation. You keep creative control through:
- Setting campaign tone and theme
- Steering the story in play, and correcting the AI when it drifts
- Directing party actions and choices
- Enjoying the narrative together

Think of it as collaborative storytelling where AI handles logistics while you guide the story.

### Can I play AI-GM solo?

Yes! AI-GM works great for solo play. The world is fully functional with one player. You can:
- Play as a party of characters yourself
- Roleplay NPCs and make choices
- Experience the world and quests fully

### What D&D systems does AI-GM support?

AI-GM is designed for **D&D 5e** primarily. Other systems:
- **Pathfinder** — Works with modified rules
- **Custom systems** — Possible with configuration
- **Other fantasy RPGs** — May require adaptation

Check compatibility before starting if you're using a non-5e system.

### How realistic is the AI?

AI-GM uses advanced language models for:
- NPC dialogue and personality
- Quest generation and story
- Combat tactics
- Lore coherence

It's not perfect—it sometimes makes mistakes or produces unusual results—but most players find it compelling and immersive. When something feels wrong, pause the AI and fix it in Foundry; the [action audit trail](../features/action-audit-trail.md) shows exactly what it changed.

## Technical Issues

### AI-GM won't start

**Solutions:**
1. Check that FoundryVTT is running
2. Verify AI-GM module is enabled (Settings > Modules)
3. Look for error messages in the browser console (F12)
4. Restart FoundryVTT
5. Check that port 18080 isn't in use by another app

### API isn't responding

**Solutions:**
1. Verify the API token is correct (Settings > API)
2. Check the base URL (default: http://localhost:18080)
3. Confirm the campaign ID exists
4. Check network connectivity
5. Look at server logs for errors

### Sessions won't save

**Solutions:**
1. Check disk space on your computer
2. Verify FoundryVTT has file permissions
3. Try ending the session again
4. Restart FoundryVTT
5. Back up your campaign data

### NPCs are disappearing

**Solutions:**
1. NPCs might be in other settlements—check their schedule
2. NPCs can die (check session summary)
3. Sometimes NPCs hide or travel—this is intentional
4. If truly missing, check the NPC list in campaign settings

### Combat feels too slow

**Solutions:**
1. Adjust pacing in Settings > Combat > Pacing (set to "Fast")
2. Ask AI-GM to narrate multiple rounds at once
3. Skip lengthy descriptions
4. Use a smaller number of enemies

### Combat is too easy or too hard

**Solutions:**
1. Adjust difficulty (Settings > Campaign > Difficulty)
2. The system auto-adjusts—stick with it for a few sessions
3. Tell the AI-GM your preference in chat—it carries into later encounters
4. Reduce/increase enemy count manually if needed

## Gameplay Questions

### Can my character actually die?

It depends on your settings:
- **Lethal mode** — Yes, death is permanent
- **Heroic mode** — Rare; the story usually continues
- **Milestone mode** — Defeat has consequences beyond death

You can change death settings anytime. If a specific death lands badly, pause and correct it in Foundry—the audit trail records the hit point changes that caused it.

### What if the AI-GM makes a mistake?

This happens. Solutions:
1. Pause the AI, fix the state in Foundry, and resume
2. Tell the AI-GM "I don't think [NPC] would do that" and it learns
3. Check the audit trail to see precisely what it changed
4. Retcon (undo) events if needed—it's collaborative

The system learns from your feedback. Over time, it aligns better with your preferences.

### How does the AI remember things?

The Lore System tracks:
- Character details and relationships
- Settlement history
- Quest progress
- Past events you mention
- Player choices and consequences

If it seems to forget something, mention it again—the system will note it. You can also review the Vault (Settings > Lore) to see what's recorded.

### Can I change campaign settings mid-campaign?

Yes! You can adjust:
- Difficulty
- Death mode
- Tone and theme
- Content filters

Changes take effect immediately. The world adjusts naturally.

### What happens if I don't play for a long time?

When you return:
1. Time has passed in the world (configurable)
2. NPCs have lived their lives
3. Settlements may have changed
4. Relationships have evolved
5. The Lore System refreshes your memory with a session summary

It's like picking up a book after time away—the world has been living without you.

## NPC & World Questions

### Why is an NPC acting differently?

Possible reasons:
1. **Relationships changed** — Your actions affected their mood
2. **Time passed** — NPCs age and develop
3. **External events** — World events shape their behavior
4. **They're hiding something** — NPCs have depth and secrets
5. **It's an error** — Say so in chat, or pause and correct it in Foundry

If it doesn't make sense, ask the AI-GM why. Good questions often spark interesting story developments.

### Can I romance NPCs?

Yes! The system supports:
- Romantic subplots
- Relationships forming naturally
- Marriage and long-term bonds
- Heartbreak and betrayal

Romantic content is optional. If you don't want it, disable it in content filters.

### How much do my choices matter?

A lot. Your choices affect:
- Which quests are available
- NPC relationships and loyalty
- Settlement development
- Quest outcomes
- Major story directions
- World state and history

Canon proposals let you review and reject the lore the AI wants to make permanent, so long-term consequences stay aligned with your vision.

### Can I restart a session?

Yes, with caveats:
1. **Load an earlier session** — Return to where you were before
2. **Retcon events** — Work with the AI-GM to undo things
3. **Start fresh** — New campaign from scratch

Be aware: the Lore System remembers everything. Starting a new session doesn't erase lore—it's always there.

## Combat Questions

### Why did an enemy do that?

AI enemies make tactical decisions. If something seems strange:
- They might have incomplete information
- It could be a bluff or trap
- They might be fleeing
- It could be a mistake (ask the AI-GM)

Ask the AI-GM to explain enemy tactics. This often creates good roleplay moments ("The bandit wanted to draw you toward the cliff...").

### My party feels overpowered

Solutions:
1. Increase difficulty (Settings > Campaign)
2. Add more enemies
3. Use enemy special abilities more
4. Create environmental challenges
5. Remember: not every fight is dangerous

Victory feels better when harder. Trust the difficulty settings.

### My party feels weak

Solutions:
1. Decrease difficulty (Settings > Campaign)
2. Reduce enemy count
3. Give more powerful loot
4. Use easier enemy types
5. Adjust character power if needed

The system can scale difficulty automatically—let it work for a few sessions.

### Can enemies retreat?

Yes! Smart enemies:
- Retreat if badly outnumbered
- Flee if losing too much
- Negotiate when cornered
- Surrender to spare lives
- Regroup for another fight

This creates interesting non-lethal solutions to combat.

## Control & Oversight Questions

### Does the AI ask permission before changing my character?

No — it is designed to run unattended, so there may be nobody to ask. Instead
it is constrained *before* acting (strict action schemas, rules adjudication by
the referee, damage clamping, JavaScript disabled by default) and everything it
does is recorded afterwards. See
**[Action Audit Trail](../features/action-audit-trail.md)**.

### How do I see what the AI did while I was away?

In chat: `/gm session events action_resolved`. In the log:
`grep '[Audit]' ai-gm.log`. Hit point changes, conditions, rests, and
encounters are all flagged as consequential.

### Can I undo something the AI did?

Not automatically, but:
1. Pause the AI and edit the state directly in Foundry
2. Retcon (undo) narratively with the AI-GM
3. Start a new session from an earlier point

### What if I want something to happen that the AI hasn't suggested?

Ask! You can:
- Suggest it in chat mid-session
- Ask the AI-GM directly during sessions
- Propose it as part of character backstory
- Set it up through your actions

The AI-GM responds to what you want. Tell it.

## Performance & Technical

### Campaign is running slowly

Solutions:
1. Reduce number of NPCs (Settings > Performance)
2. Close unused FoundryVTT windows
3. Check your computer's RAM usage
4. Reduce combat effects/animations
5. Disable webhook notifications if using them

### Server errors keep happening

Solutions:
1. Check the error message (write it down)
2. Restart FoundryVTT
3. Check server logs (Settings > Logs)
4. Verify all dependencies are installed correctly
5. Try a clean reinstall of AI-GM module

### API calls are timing out

Solutions:
1. Check network connectivity
2. Reduce request frequency (fewer API calls)
3. Use pagination for large requests
4. Check server load
5. See [API Overview](../api/overview.md) for rate limits

## Getting Help

### Where do I ask questions?

1. **In-game** — Ask the AI-GM directly during sessions
2. **This FAQ** — Check if your question is here
3. **Settings > Help** — Contextual help for features
4. **Community Discord** — Talk to other players
5. **GitHub Issues** — Report bugs or request features

### How do I report a bug?

1. Write down exactly what happened
2. Include steps to reproduce
3. Check if it happens consistently
4. Include your FoundryVTT and AI-GM versions
5. Post on GitHub Issues or Discord

### Can I request features?

Yes! Suggestions are welcome:
1. Discord community
2. GitHub Discussions
3. Feature request form in Settings > Feedback

Popular requests are considered for future releases.

---

**Still stuck?** Check **[Features Overview](../features/overview.md)** for detailed guides on specific systems, or ask the AI-GM directly—it's pretty helpful!

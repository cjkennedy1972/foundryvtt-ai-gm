# Approval Workflow

The Approval Workflow is a safety gate for mechanical character changes. When the AI-GM proposes to grant treasure, level up a character, or unlock abilities, the GM can review and approve or reject the action.

## Why Approval Workflow Exists

Autonomous play means the AI-GM makes many decisions without you—but some decisions are too consequential to skip. Approval gates let you:

- **Prevent unwanted rewards** — reject treasure that doesn't fit your world
- **Control progression** — approve level-ups only when pacing feels right
- **Prevent exploits** — block stat increases that break balance
- **Review unlocks** — ensure new abilities/spells fit the campaign

The system is simple: approve or reject mechanical actions within 20 seconds, or auto-approve if you don't respond.

## Mechanical Action Types

The approval system gates these 10 action types:

1. **grant_treasure** — Award items, gold, or equipment
2. **level_up** — Advance character level
3. **stat_increase** — Boost ability scores (STR, DEX, CON, INT, WIS, CHA)
4. **ability_unlock** — Grant new class abilities or features
5. **feat_grant** — Award D&D feats
6. **spell_grant** — Add spells to character's spell list
7. **status_change** — Apply/remove conditions (blessed, cursed, etc.)
8. **attribute_change** — Modify derived stats (AC, proficiency, initiative bonuses)
9. **skill_unlock** — Grant or enhance skills
10. **inventory_change** — Add/remove items from character inventory

These are mechanical rewards and stat changes—not roleplay decisions. The AI-GM handles narration freely.

## How It Works

### The Approval Flow

1. **Action Triggered** — AI-GM proposes a mechanical change (e.g., "grant Level 5 to Aragorn")
2. **Approval Gate** — GM sees a review dialog with action details
3. **You Choose** — Approve or reject within 20 seconds
4. **Auto-Approve** — If no response, auto-approves after 20 seconds
5. **Execute** — Approved actions apply immediately

### Binary Choices

| Choice | Effect |
|--------|--------|
| **Approve** | Action executes immediately. Character sheet updates. |
| **Reject** — Action is discarded. Character state unchanged. |

No modifications or alternatives—the AI-GM proposed a specific change, and you accept or decline it.

## Example: Level-Up Approval

**Scenario:**
After a major victory, the AI-GM proposes:
> "Party has defeated the Dragon of Ashmore. Level-up from 4 → 5? [APPROVE] [REJECT] — Auto-approve in 20s"

**You respond:**
"APPROVE" — The players level up.

OR you could "REJECT" if you want to space out level-ups or if the challenge didn't warrant advancement.

## Example: Treasure Approval

**Scenario:**
After looting a dungeon:
> "Treasure acquired: +1 Sword of Returning, 500 gold. [APPROVE] [REJECT] — Auto-approve in 20s"

**You respond:**
"REJECT" if the treasure is too powerful or narratively wrong for your world.

"APPROVE" if it fits.

## Example: Spell Grant

**Scenario:**
After a wizard levels up:
> "New spell slot (Lv 3): Fireball. [APPROVE] [REJECT] — Auto-approve in 20s"

Auto-approval happens if you're in a tense moment and can't respond immediately.

## Auto-Approval Timeout

If you don't respond within **20 seconds**, the action **auto-approves**. This keeps the game flowing—you don't have to babysit the approval gate, but you can jump in to reject if needed.

**When auto-approval helps:**
- You're busy with players and miss a dialog
- You're in the middle of a tense scene
- Multiple actions queue up

**When you might reject:**
- Treasure doesn't fit your world
- Level-up seems premature
- Ability unlock breaks game balance
- Stat increase is too generous

## Best Practices

### Set Expectations

Before your campaign starts:
- Explain approval to your players
- Show what actions trigger approvals
- Set a consistent pace (strict review vs. relaxed)

### Monitor Patterns

Over time, notice:
- Which action types trigger rejections
- Whether auto-approval works for your style
- If you need stricter or looser gates

### Trust the System

Remember:
- The AI-GM proposes rewards based on encounter difficulty
- You have final say but don't need to micromanage
- Auto-approval prevents decision paralysis
- Rejections are rare but powerful

## Examples of Use

### Scenario 1: Balanced Progression

**Session 4, party levels up:**
> "Advance to Level 5? [APPROVE]" ← You approve
Party gains new abilities, progresses naturally.

### Scenario 2: Overpowered Loot

**Session 7, after a small victory:**
> "Grant +2 Plate Armor? [REJECT]" ← You reject
The loot doesn't match encounter difficulty. AI-GM will adjust future rewards.

### Scenario 3: Auto-Approval Flow

**Session 10, combat is intense:**
> "Grant Potion of Healing? Auto-approves in 20s..."
You're focused on combat narration, timeout hits, action approves automatically.

### Scenario 4: Unwanted Ability

**Session 6, wizard finishes long quest:**
> "Unlock Metamagic: Twinned Spell? [REJECT]" ← You reject
You want spell variety slower. AI-GM notes this and paces unlocks differently.

## Troubleshooting

**"Too many approvals are interrupting play"**
→ Let auto-approval handle most. Only manually approve/reject when you disagree.

**"I want to review everything"**
→ Set approval mode to **strict** (every action requires explicit approval, no auto-timeout).

**"The AI-GM is granting bad treasures"**
→ Reject treasures that don't fit. The system learns from rejections.

**"I missed an approval timeout"**
→ Normal—auto-approve keeps the game flowing. Review it in the session log later.

## Configuration

**Default behavior:**
- All 10 action types require approval
- 20-second auto-approve timeout
- Reject behavior: action is discarded, no side effects

**Optional modes (if your install supports):**
- **Permissive** — Auto-approve all, only pause on stat increases/unlocks
- **Strict** — Require explicit approval for all; no auto-timeout
- **Custom** — Specify which action types require approval

---

**Next:** Explore **[Features Overview](overview.md)** or jump to **[API Reference](../api/overview.md)**.

# Roll Mechanics — D&D 5e Integration

**Status**: ✅ **Fully Implemented**  
**System**: Foundry VTT integration for d20 system  
**LLM Access**: Via action executors  

---

## 1. Core Dice Rolling

### Roll Action
```
{
  "type": "roll",
  "formula": "2d20kh1",          // Advantage: 2d20kh1, Disadvantage: 2d20kl1
  "speaker": "Grok",              // Token/NPC name
  "advantage": true,              // null = normal, true = advantage, false = disadvantage
  "flavor": "Athletics check"      // Optional reason
}
```

**Features:**
- ✅ Standard d20 rolls
- ✅ **Advantage/Disadvantage** via Foundry's native 2d20kh1/2d20kl1 formulas
- ✅ **Player vs NPC distinction**: Players roll their own dice, GM rolls for NPCs
- ✅ **3D dice animation** support via Foundry Add-on
- ✅ Flavor text in chat for context

**Example Rolls:**
- Basic attack: `1d20+5` 
- Fireball damage: `8d6`
- Advantage roll: `2d20kh1+3` (keep highest)
- Disadvantage roll: `2d20kl1+3` (keep lowest)

---

## 2. Skill Checks

### Skill Check Action
```
{
  "type": "skill_check",
  "actor_uuid": "Actor.abc123",
  "skill": "acrobatics",          // Any d20 system skill
  "dc": 15,                        // Difficulty Class
  "advantage": false,              // Optional advantage/disadvantage
  "reason": "Balance on the rope"  // Optional context
}
```

**Supported Skills:**
All D&D 5e skills with automatic ability score mapping:
- **Strength**: Athletics
- **Dexterity**: Acrobatics, Sleight of Hand, Stealth
- **Constitution**: (rare, endurance checks)
- **Intelligence**: Arcana, History, Investigation, Nature, Religion
- **Wisdom**: Animal Handling, Insight, Medicine, Perception, Survival
- **Charisma**: Deception, Intimidation, Performance, Persuasion

**Mechanics:**
- Automatic ability modifier calculation: `(ability - 10) / 2`
- Proficiency bonus based on character level: `(level + 7) / 4`
- Advantage/disadvantage support
- DC calculation by difficulty: Easy (10), Medium (15), Hard (20), Very Hard (25), Nearly Impossible (30)

**Example:**
```json
{
  "type": "skill_check",
  "actor_uuid": "Actor.fighter-001",
  "skill": "stealth",
  "dc": 18,
  "advantage": true,
  "reason": "Sneaking past the guards"
}
```

---

## 3. Attack Rolls & Combat

### Attack Rolls
- ✅ Attack rolls with modifiers (strength/dexterity + proficiency)
- ✅ Advantage/disadvantage based on conditions (hidden, prone enemy, etc.)
- ✅ Critical hit detection (natural 20)
- ✅ Critical miss handling (natural 1)

### Saving Throws
- ✅ Strength, Dexterity, Constitution, Intelligence, Wisdom, Charisma saves
- ✅ Proficiency-based modifications
- ✅ Spell save DC calculations

### Opportunity Attacks
```
{
  "type": "opportunity_attack",
  "attacker_uuid": "Actor.warrior-001",
  "target_uuid": "Actor.goblin-01",
  "reason": "Enemy left reach without disengaging"
}
```

**Mechanics:**
- ✅ Triggered when creature leaves reach without Disengage action
- ✅ Range calculation: Standard 5 ft melee, extended for reach weapons (10 ft)
- ✅ Auto-detected by position tracking

---

## 4. Advantage/Disadvantage System

### Conditions Granting Advantage
- Invisible attacker
- Prone target (melee only)
- Hidden condition

### Conditions Imposing Disadvantage
- Blinded
- Charmed
- Frightened
- Paralyzed
- Petrified
- Poisoned
- Prone (own attacks)
- Restrained (own attacks)
- Stunned
- Unconscious

**Implementation:**
All rolls use Foundry's native `2d20kh1` (advantage) and `2d20kl1` (disadvantage) formulas for proper 3D dice animation.

---

## 5. Tactical Mechanics

### Flanking
```python
is_flanking(attacker_id, target_id, allies)
```

**Rules:**
- Attacker AND at least one ally must be within 5 ft of target
- Attacker and ally must be on opposite sides of target
- Grants advantage on melee attack roll vs target
- Angular calculation: 90-270° from target's perspective

**Example:**
- Warrior and Rogue both within 5 ft of Goblin King on opposite sides → Advantage on attack

### Cover
**Cover Bonuses:**
- Half cover: +2 AC
- Three-quarters cover: +5 AC
- Full cover: Cannot be targeted

**Mechanics:**
- Automatically detected from token positions and obstacles
- Applies to AC and DEX saves

### Distance & Reach
**Distance Calculation:**
- Euclidean distance between tokens
- Converts grid squares to feet (5 ft/square)

**Reach:**
- Small/Medium: 5 ft (unarmed/melee)
- Small/Medium with reach weapon: 10 ft
- Large creatures: 10 ft natural reach
- Large with reach weapon: 15 ft

---

## 6. Conditions System

### Apply Condition Action
```
{
  "type": "apply_condition",
  "actor_uuid": "Actor.wizard-01",
  "condition": "paralyzed",
  "duration": "until end of next turn"
}
```

**Supported Conditions:**
All D&D 5e conditions with duration tracking:
- Blinded, Charmed, Deafened, Exhaustion (levels 1-6)
- Frightened, Grappled, Incapacitated, Invisible
- Paralyzed, Petrified, Poisoned, Prone, Restrained
- Stunned, Unconscious

**Duration Types:**
- Instantaneous
- Until end of turn
- Until end of next turn
- X rounds/minutes/hours/days
- Concentration (tracked separately)

---

## 7. Spell Mechanics

### Cast Spell Action
```
{
  "type": "cast_spell",
  "caster_uuid": "Actor.wizard-01",
  "spell_name": "Fireball",
  "target_uuid": "Area.50ft-radius",
  "dc": 15,
  "modifier": 4
}
```

**Features:**
- ✅ 500+ D&D 5e spells indexed
- ✅ Level-based spell slot tracking
- ✅ Concentration spell management
- ✅ Spell save DC calculation (8 + ability + proficiency)
- ✅ Spell attack rolls with modifiers
- ✅ Area of effect damage rolls

**Example Spell Data:**
```json
{
  "name": "Fireball",
  "level": 3,
  "school": "evocation",
  "casting_time": "1 action",
  "range": "150 feet",
  "components": "V, S, M (sulfur)",
  "duration": "Instantaneous",
  "concentration": false,
  "damage": "8d6 fire",
  "damage_scaling": "+1d6 per spell level above 3rd"
}
```

---

## 8. Damage System

### Update HP Action (Damage/Healing)
```
{
  "type": "update_hp",
  "actor_uuid": "Actor.goblin-01",
  "damage": 15,           // Positive = damage, negative = healing
  "damage_type": "fire"   // piercing, slashing, bludgeoning, fire, cold, etc.
}
```

**Features:**
- ✅ Direct damage/healing
- ✅ Damage type tracking
- ✅ **Damage clamping**: 0-200 HP per action (safety limit)
- ✅ Resistance/vulnerability calculations
- ✅ Death saving throw triggers at 0 HP

**Damage Types Supported:**
- Weapon: Piercing, Slashing, Bludgeoning
- Elemental: Fire, Cold, Lightning, Acid, Poison, Thunder
- Exotic: Force, Psychic, Radiant, Necrotic

---

## 9. Rules Reference Engine

### Available D&D 5e Data
```python
rules_engine = RulesEngine()
```

**References:**
- **Spells**: 500+ spells with level, school, casting time, range, components, duration, concentration
- **Conditions**: All 15 conditions with descriptions
- **Skills**: 18 skills with ability mappings
- **Classes**: Hit dice, spell slots by level, proficiencies
- **Ability Scores**: Standard array, point-buy, rolling mechanics
- **DC by Difficulty**: Easy (10), Medium (15), Hard (20), Very Hard (25), Nearly Impossible (30)

**Example Query:**
```json
{
  "spell": "Magic Missile",
  "ability": "intelligence",
  "dc": 15,
  "proficiency_bonus": 3,
  "skill_modifier": 5
}
```

---

## 10. Player vs NPC Rolls

### Auto-Defer to Players
When `players_roll_own=true` (default):

**Player Characters:**
- Roll prompted via chat message
- Example: "🎲 **Sarah**, roll `1d20+5` for attack against the goblin."
- Player types result or rolls from character sheet

**NPCs/Monsters:**
- Rolled automatically by AI GM
- Result shown in chat with 3D dice
- Used for NPC attacks, monster spells, etc.

**Rationale:**
Maintains player agency and excitement; removes GM from manipulating d20s.

---

## 11. LLM Integration

### Available to LLM via Actions

The LLM can trigger these mechanics via:

```json
{
  "type": "roll",
  "formula": "1d20+8",
  "speaker": "Grok",
  "flavor": "Strength save vs paralysis"
}
```

All supported actions:
- ✅ `roll` — Free-form dice rolls
- ✅ `skill_check` — D&D skill checks
- ✅ `opportunity_attack` — Reaction attacks
- ✅ `apply_condition` — Apply status effects
- ✅ `cast_spell` — Spellcasting
- ✅ `update_hp` — Damage/healing
- ✅ `tactical_analysis` — Strategic battlefield assessment

---

## 12. Example Combat Sequence

**Setup:**
- Warrior vs 2 Goblins (combat started)
- Initiative rolled and turn order established

**Turn 1 - Warrior's Turn:**
```json
{
  "type": "roll",
  "formula": "1d20+5",
  "speaker": "Aragorn",
  "flavor": "Attack with longsword"
}
// Result: 18 + 5 = 23 → Hit!
// Follow up: Damage roll
{
  "type": "roll",
  "formula": "1d8+3",
  "speaker": "Aragorn",
  "flavor": "Longsword damage"
}
// Result: 7 + 3 = 10 damage
```

**Turn 2 - Goblin's Turn:**
```json
{
  "type": "skill_check",
  "actor_uuid": "Actor.warrior",
  "skill": "dexterity_save",
  "dc": 14,
  "reason": "Dodge poison dart"
}
// Then apply if failed:
{
  "type": "apply_condition",
  "actor_uuid": "Actor.warrior",
  "condition": "poisoned",
  "duration": "1 minute"
}
```

**Combat Continues:**
- All rolls integrated with Foundry's combat tracker
- Chat log shows 3D dice animations
- Token damage updates automatically
- Turn order managed by Foundry

---

## 13. Status

### Fully Implemented ✅
- Core d20 system
- Advantage/disadvantage
- Skill checks
- Saving throws
- Conditions
- Spells (500+ spells)
- Damage system
- Flanking detection
- Cover bonuses
- Distance/reach calculations
- Player vs NPC roll handling

### Tested ✅
- Initiative rolling
- Attack resolution
- Skill check DC calculations
- Condition applications
- Advantage/disadvantage mechanics

---

## Conclusion

The system provides **complete D&D 5e mechanical support** integrated with Foundry VTT. The AI GM can intelligently trigger combat rolls, manage conditions, calculate damage, and assess tactical situations. Players maintain control over their own rolls while the AI handles NPC/environmental mechanics automatically.

# Combat System

AI-GM's combat system uses real artificial intelligence to run enemies. They don't follow pre-written scripts—they think, adapt, and make tactical decisions. This creates unpredictable, engaging fights that feel genuinely challenging.

## How AI Enemies Work

Traditional D&D encounters use fixed patterns: "the orc attacks the nearest enemy" or "the wizard casts *Fireball*." AI-GM enemies are different.

### Real Decision-Making

Each enemy has:

**Tactical awareness:**
- Know where all combatants are
- Understand the terrain and cover
- Track health (theirs and others')
- Consider distance and movement
- Recognize threats and opportunities

**Strategic thinking:**
- Decide when to attack, retreat, or negotiate
- Choose between multiple spells or abilities
- Coordinate with allies
- Use environment tactically
- Adapt to player strategies

**Personality:**
- A cowardly enemy fights differently than a brave one
- A honorable enemy refuses cheap shots
- A cunning enemy uses deception
- A fanatical enemy fights to the death

The result: combats feel alive. Enemies make smart moves, take unexpected approaches, and create moments of genuine drama.

### Difficulty Scaling

AI-GM adjusts combat difficulty automatically based on:

- **Party level** — Higher level parties face smarter enemies
- **Party size** — A solo player gets fewer/weaker enemies
- **Equipment quality** — Well-armed parties face stronger opposition
- **Campaign tone** — Deadly campaigns have harder fights
- **Recent victories** — Winning too often? Difficulty increases

You can also manually adjust difficulty if a campaign is too easy or hard.

## Enemy Tactics

### Types of Tactics

**Aggressive:**
- Focus fire on the most dangerous enemy
- Charge into close combat
- Use area spells to hit multiple targets
- Press advantages aggressively

**Defensive:**
- Protect weaker allies
- Use cover and terrain
- Retreat when badly wounded
- Guard the objective

**Evasive:**
- Stay out of melee range
- Use crowd control spells
- Hit and run tactics
- Escape if possible

**Disruptive:**
- Disable the party's advantages
- Remove the healer from the fight
- Separate party members
- Use crowd control

**Coordinated:**
- Multiple enemies work together
- Flank and surround opponents
- Combine abilities for greater effect
- Communicate (telepathy, signals)

Different enemies use different tactics based on their type and intelligence.

## Combat Features

### Intelligent Positioning

Enemies understand position:
- Stay out of ranged attacks from archers
- Move to high ground if possible
- Use cover against magic and arrows
- Create flanking opportunities
- Block important paths

This creates interesting, dynamic battlefields instead of static encounters.

### Resource Management

Smart enemies manage abilities:
- Save powerful spells for critical moments
- Use potions when badly wounded
- Don't waste limited abilities on trivial fights
- Plan for multi-round encounters
- Retreat if outmatched

### Morale & Surrender

Enemies have morale. When badly losing, they:
- Retreat to safer ground
- Negotiate for safe passage
- Surrender to spare lives
- Rout and scatter
- Fight harder if they believe they can win

This makes victory possible through any approach—combat, intimidation, negotiation.

### Environmental Hazards

Combat locations have:
- Fire, poison, collapsing structures
- Narrow passages and high ground
- Water, ice, magical effects
- Artifacts and traps
- NPCs and innocent bystanders

Enemies use these tactically. So can you.

## Combat Settings

### Difficulty Levels

**Easy** — Enemies are under-equipped and make suboptimal choices
**Moderate** — Smart tactics but some mistakes
**Hard** — Optimized tactics and good choices
**Deadly** — Ruthless, coordinated, intelligent enemies

You can set difficulty per campaign or change it mid-campaign.

### Death & Consequences

**Mode 1: Lethal** — Death is permanent; characters can die
**Mode 2: Heroic** — Characters rarely die (only in dramatic moments)
**Mode 3: Milestone** — Defeat means something other than death (captured, humiliated, etc.)

Choose what feels right for your campaign tone.

### Pacing

**Fast** — Enemies take quick turns, combat moves quickly
**Normal** — Standard pacing, time for tactics
**Slow** — Maximum narration and drama, long encounters

This lets you adjust how much real-world time combat takes.

## Tactical Depth

### Complex Scenarios

AI-GM handles complex situations:

**Multiple enemies coordinating:**
- 8 goblins attack from different angles
- 2 knights + 3 wizards work together
- A squad with different specialists

**Environmental challenges:**
- Fight on narrow bridge (limited positioning)
- Combat in burning building (increasing danger)
- Underwater encounter (different rules)
- Zero-gravity magic duel (creative positioning)

**Non-combat options:**
- Can you negotiate mid-combat?
- Can you run?
- Can you trap enemies?
- Can you use environment against them?

All of these work naturally. Enemies recognize traps, attempt negotiation, and flee if smart.

### Player Flexibility

You're not restricted to "attack" and "cast spell."

**Creative actions:**
- Use terrain (push enemies off cliffs, start fires)
- Distract enemies (perform, trick, confuse)
- Set traps (before or during combat)
- Negotiate (surrender, alliance, parley)
- Escape (run, teleport, hide)

Describe what you do. The AI-GM adjudicates fairly.

## Example Encounter

### Setup
Five bandits ambush your party on a forest road. Narrow path, thick trees on both sides, a cliff edge to the right.

### What Happens

**Round 1:**
- Two bandits move to high ground in trees
- Three stay on the road, blocking retreat
- They expect the party to be disoriented

**Your Response:**
- Wizard casts *Entangle* to restrict movement
- Rogue runs up the cliff
- Barbarian charges the closest bandit

**Round 2:**
- Bandits in trees attempt to flank
- Road bandits (some entangled) focus on the barbarian
- Leader (a bandit captain) emerges, commands others
- Seeing the wizard is isolated, two bandits break entanglement and rush toward them

**Combat continues:**
- You fight tactically, enemies fight intelligently
- Someone gets badly wounded—enemies notice and press advantage
- Wizard retreats to fight alongside others
- As bandits fall, survivors consider surrender
- Final bandit leader offers to yield rather than die

**Result:**
A dynamic, story-rich encounter. Enemies made real choices. Your tactics mattered. It felt like a real struggle, not a scripted scene.

## Tips for Engaging Combat

**Before combat:**
- Describe your surroundings to the AI-GM
- Clarify what you can see
- Set expectations about tone

**During combat:**
- Take your turns fairly but tactically
- Describe your actions, not just mechanics
- Ask about terrain and positioning
- Use the environment creatively

**After combat:**
- Let enemies surrender and be captured
- Loot bodies and search for clues
- Talk to defeated enemies (who might become allies)
- Consider consequences beyond just XP

## Advanced Topics

### Custom Enemy AI

If you're building custom encounters:
- You can customize enemy tactics per enemy
- Set personality and morale
- Choose preferred tactics
- Define special abilities
- Set difficulty individually

### Encounter Builder

For GMs, AI-GM includes tools to:
- Create custom encounters
- Preview difficulty
- Adjust enemy stats and tactics
- Test combat before playing

See **[API Reference](../api/rest-endpoints.md)** for builder details.

---

**Next:** Learn about the **[Living World](living-world.md)** or explore **[Lore System](lore-system.md)**.

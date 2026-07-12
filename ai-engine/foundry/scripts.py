"""Named builders for the JS snippets the engine runs inside Foundry.

Target home for every execute_js payload (architecture plan, Phase 5) so the
scripts are testable and greppable instead of scattered string literals.
Migrate existing inline snippets here opportunistically when touching them.
"""

import json
from typing import Dict, List


def get_multiattack_count(actor_uuid: str) -> str:
    """Check if an NPC has a Multiattack ability and return the attack count.

    Searches the actor's features/traits for text containing "Multiattack" and
    tries to extract a number (e.g., "Multiattack: The dragon makes three
    attacks" → 3). Returns the count or 1 if no multiattack is found.
    """
    actor_uuid_json = json.dumps(actor_uuid)
    return rf"""
const actor = await fromUuid({actor_uuid_json});
if (!actor) return {{count: 1, description: ''}};

// Search for Multiattack in features, traits, or special abilities
let multiattackCount = 1;
let multiattackDescription = '';

for (const item of actor.items) {{
    if (item.type === 'feat' || item.type === 'feature') {{
        const name = item.name.toLowerCase();
        const desc = (item.system?.description?.value || '').toLowerCase();

        if (name.includes('multiattack') || desc.includes('multiattack')) {{
            multiattackDescription = item.name + ': ' + (item.system?.description?.value || '').substring(0, 200);

            // Extract only the attack count from phrases like
            // "makes three attacks"; unrelated numbers in the description
            // (range, damage, DC, etc.) must not affect the result.
            const numRegex = /\b(?:makes?|can make)\s+(one|two|three|four|five|six|seven|eight|[1-9])\s+(?:melee |ranged )?attacks?\b/i;
            const match = desc.match(numRegex);
            if (match) {{
                const wordMap = {{'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8}};
                const numStr = match[1].toLowerCase();
                const num = parseInt(numStr) || wordMap[numStr];
                if (num) multiattackCount = num;
            }}
            break;
        }}
    }}
}}

return {{count: multiattackCount, description: multiattackDescription}};
"""


def resolve_item_attack(
    actor_uuid: str, item_name: str, target_token_id: str,
    advantage: bool = False, disadvantage: bool = False
) -> str:
    """Resolve a real weapon/spell attack: roll attack, check hit vs the
    target's AC, roll damage on a hit, apply it, and post one chat message.

    Bypasses midi-qol's own chat-card workflow (verified live: its config
    overrides for auto-rolling attack+damage work, but auto-*applying*
    damage does not reliably fire headless — the hit-check step never
    populates). Uses the same underlying dnd5e Activity methods
    (activity.rollAttack/.rollDamage — real formulas, real ability/
    proficiency/bonus data) but resolves hit/damage/application directly,
    which is fully within our control and was verified reliable end-to-end
    against a live world (attack roll -> hit vs AC -> damage roll ->
    actor.applyDamage -> HP actually changed, chat card rendered correctly).

    item_name matches case-insensitively, exact first then substring, so
    "cutlass" matches an item named "Rusty Cutlass".

    advantage/disadvantage are applied to the attack roll per 5e rules
    (e.g., from flanking or cover).

    Returns {ok, hit, isCrit, attackTotal, targetAc, damageTotal,
    damageTypes, targetName, targetHpAfter} or {ok: false, error, ...}.
    """
    actor_uuid_json = json.dumps(actor_uuid)
    item_name_json = json.dumps(item_name.strip().lower())
    target_token_id_json = json.dumps(target_token_id)
    advantage_json = json.dumps(bool(advantage))
    disadvantage_json = json.dumps(bool(disadvantage))
    return f"""
const actor = await fromUuid({actor_uuid_json});
if (!actor) return {{ok: false, error: 'actor not found'}};
const wantName = {item_name_json};
const item = actor.items.find(i => i.name.toLowerCase() === wantName)
    ?? actor.items.find(i => i.name.toLowerCase().includes(wantName));
if (!item) {{
    return {{ok: false, error: 'item not found', available: actor.items.filter(i => ['weapon','spell'].includes(i.type)).map(i => i.name)}};
}}
const activity = item.system.activities?.contents?.find(a => a.type === 'attack');
if (!activity) return {{ok: false, error: 'item has no usable attack', itemName: item.name}};

const targetPlaceable = canvas.tokens.placeables.find(t => t.id === {target_token_id_json});
if (!targetPlaceable) return {{ok: false, error: 'target token not found on active scene'}};
const targetActor = targetPlaceable.actor;
const targetAc = targetActor.system.attributes.ac.value;
const hpBefore = targetActor.system.attributes.hp.value;

const attackRolls = await activity.rollAttack({{event: null, advantage: {advantage_json}, disadvantage: {disadvantage_json}}}, {{configure: false, chooseModifier: false}}, {{create: false}});
const attackRoll = Array.isArray(attackRolls) ? attackRolls[0] : attackRolls;
if (!attackRoll) return {{ok: false, error: 'attack roll failed'}};
const attackTotal = attackRoll.total;
const isCrit = attackRoll.isCritical ?? false;
const hit = isCrit || attackTotal >= targetAc;

let damageTotal = 0;
const damageTypes = [];
if (hit) {{
    const damageRolls = await activity.rollDamage({{event: null}}, {{configure: false}}, {{create: false}});
    const rolls = Array.isArray(damageRolls) ? damageRolls : [damageRolls];
    for (const r of rolls) {{
        if (!r) continue;
        damageTotal += r.total;
        const ty = r.options?.type;
        if (ty && !damageTypes.includes(ty)) damageTypes.push(ty);
    }}
    if (damageTotal > 0) {{
        const parts = damageTypes.length
            ? damageTypes.map(ty => ({{value: damageTotal / damageTypes.length, type: ty}}))
            : [{{value: damageTotal, type: 'bludgeoning'}}];
        await targetActor.applyDamage(parts);
    }}
}}

const flavor = hit
    ? `<b>${{actor.name}}</b> attacks with <b>${{item.name}}</b>: ${{isCrit ? 'CRITICAL HIT!' : 'Hit!'}} (${{attackTotal}} vs AC ${{targetAc}}) \\u2014 ${{damageTotal}} ${{damageTypes.join('/')}} damage to ${{targetActor.name}}.`
    : `<b>${{actor.name}}</b> attacks with <b>${{item.name}}</b>: Miss. (${{attackTotal}} vs AC ${{targetAc}})`;
await ChatMessage.create({{content: flavor, speaker: {{alias: actor.name}}}});

return {{
    ok: true, hit, isCrit, attackTotal, targetAc,
    damageTotal, damageTypes, targetName: targetActor.name,
    targetHpBefore: hpBefore, targetHpAfter: targetActor.system.attributes.hp.value,
}};
"""


def resolve_item_save(
    caster_uuid: str, item_name: str, target_token_ids: List[str], auto_resolve_token_ids: List[str]
) -> str:
    """Resolve a save-based item/spell (breath weapon, AoE spell) against targets.

    Mirrors resolve_item_attack's approach (real dnd5e Activity data, damage
    applied directly via actor.applyDamage) but for a 'save' Activity instead
    of 'attack': rolls 1d20 + the target's real save modifier
    (actor.system.abilities[ability].save) against the activity's DC, and on
    a failed save applies full damage, on a success applies half (per the
    activity's onSave setting) — same shape build_save_activity() writes when
    the campaign generator creates monster breath weapons/AoE spells.

    Only targets whose token id is in auto_resolve_token_ids are actually
    rolled — callers exclude player-owned targets so those saves stay with
    the player (returned as {deferred: true} entries instead). ability/dc
    are still read from the item and returned even when every target is
    deferred, so the caller can build the "make your save" chat prompt.

    Unlike resolve_item_attack, this has NOT been verified against a live
    Foundry world yet — test before relying on it in a real session.

    Returns {ok, itemName, ability, dc, results: [{tokenId, targetName,
    deferred} | {tokenId, targetName, ability, dc, saveTotal, success,
    damageDealt}]} or {ok: false, error, ...}.
    """
    caster_uuid_json = json.dumps(caster_uuid)
    item_name_json = json.dumps(item_name.strip().lower())
    target_ids_json = json.dumps(target_token_ids)
    auto_ids_json = json.dumps(auto_resolve_token_ids)
    return f"""
const actor = await fromUuid({caster_uuid_json});
if (!actor) return {{ok: false, error: 'caster actor not found'}};
const wantName = {item_name_json};
const item = actor.items.find(i => i.name.toLowerCase() === wantName)
    ?? actor.items.find(i => i.name.toLowerCase().includes(wantName));
if (!item) {{
    return {{ok: false, error: 'item not found', available: actor.items.filter(i => ['weapon','spell'].includes(i.type)).map(i => i.name)}};
}}
const activity = item.system.activities?.contents?.find(a => a.type === 'save');
if (!activity) return {{ok: false, error: 'item has no save activity', itemName: item.name}};

const ability = activity.save.ability[0];
const dc = Number(activity.save.dc.formula) || 10;
const damageParts = activity.damage?.parts || [];
const onSave = activity.damage?.onSave || 'half';
const autoIds = new Set({auto_ids_json});

const results = [];
for (const tokenId of {target_ids_json}) {{
    const placeable = canvas.tokens.placeables.find(t => t.id === tokenId);
    if (!placeable) {{ results.push({{tokenId, error: 'target token not found on active scene'}}); continue; }}
    const targetActor = placeable.actor ?? null;
    const targetName = targetActor?.name ?? placeable.name;
    if (!autoIds.has(tokenId)) {{
        results.push({{tokenId, targetName, deferred: true}});
        continue;
    }}
    const mod = (targetActor?.system?.abilities?.[ability]?.save) ?? 0;
    const saveRoll = await new Roll(`1d20 + ${{mod}}`).evaluate();
    const total = saveRoll.total;
    const success = total >= dc;

    let damageDealt = 0;
    if (damageParts.length) {{
        const formula = damageParts.map(p => `${{p.number}}d${{p.denomination}}${{p.bonus ? '+' + p.bonus : ''}}`).join(' + ');
        const dmgRoll = await new Roll(formula).evaluate();
        damageDealt = success ? (onSave === 'half' ? Math.floor(dmgRoll.total / 2) : 0) : dmgRoll.total;
        if (damageDealt > 0) await (targetActor ?? targetPlaceable).applyDamage(damageDealt);
    }}
    results.push({{tokenId, targetName, ability, dc, saveTotal: total, success, damageDealt}});
}}

const summary = results.map(r => r.deferred
    ? `${{r.targetName}} (rolling their own save)`
    : r.error ? `${{r.tokenId}}: ${{r.error}}`
    : `${{r.targetName}}: ${{r.success ? 'saved' : 'failed'}} (${{r.saveTotal}} vs DC ${{dc}})${{r.damageDealt ? `, ${{r.damageDealt}} damage` : ''}}`
).join('; ');
await ChatMessage.create({{content: `<b>${{actor.name}}</b> uses <b>${{item.name}}</b>: ${{summary}}`, speaker: {{alias: actor.name}}}});

return {{ok: true, itemName: item.name, ability, dc, results}};
"""


def resolve_environmental_save(
    ability: str, dc: int, damage_formula: str, half_on_save: bool,
    target_token_ids: List[str], auto_resolve_token_ids: List[str]
) -> str:
    """Resolve a trap/hazard saving throw against targets — same shape as
    resolve_item_save but with no caster/item to look up: ability, dc, and
    damage_formula come straight from the LLM's action payload instead of
    a Foundry Activity, since a trap isn't an item on anyone's actor sheet.

    Only targets whose token id is in auto_resolve_token_ids are rolled —
    callers exclude player-owned targets so those saves stay with the
    player (returned as {deferred: true} entries instead).

    Like resolve_item_save, this has NOT been verified against a live
    Foundry world yet — test before relying on it in a real session.

    Returns {ok, ability, dc, results: [{tokenId, targetName, deferred} |
    {tokenId, targetName, ability, dc, saveTotal, success, damageDealt}]}
    or {ok: false, error, ...}.
    """
    ability_json = json.dumps(ability)
    dc_json = json.dumps(int(dc))
    formula_json = json.dumps(damage_formula) if damage_formula else "null"
    half_on_save_json = "true" if half_on_save else "false"
    target_ids_json = json.dumps(target_token_ids)
    auto_ids_json = json.dumps(auto_resolve_token_ids)
    return f"""
const ability = {ability_json};
const dc = {dc_json};
const damageFormula = {formula_json};
const halfOnSave = {half_on_save_json};
const autoIds = new Set({auto_ids_json});

const results = [];
for (const tokenId of {target_ids_json}) {{
    const placeable = canvas.tokens.placeables.find(t => t.id === tokenId);
    if (!placeable) {{ results.push({{tokenId, error: 'target token not found on active scene'}}); continue; }}
    const targetActor = placeable.actor ?? null;
    const targetName = targetActor?.name ?? placeable.name;
    if (!autoIds.has(tokenId)) {{
        results.push({{tokenId, targetName, deferred: true}});
        continue;
    }}
    const mod = (targetActor?.system?.abilities?.[ability]?.save) ?? 0;
    const saveRoll = await new Roll(`1d20 + ${{mod}}`).evaluate();
    const total = saveRoll.total;
    const success = total >= dc;

    let damageDealt = 0;
    if (damageFormula) {{
        const dmgRoll = await new Roll(damageFormula).evaluate();
        damageDealt = success ? (halfOnSave ? Math.floor(dmgRoll.total / 2) : 0) : dmgRoll.total;
        if (damageDealt > 0) await (targetActor ?? targetPlaceable).applyDamage(damageDealt);
    }}
    results.push({{tokenId, targetName, ability, dc, saveTotal: total, success, damageDealt}});
}}

const summary = results.map(r => r.deferred
    ? `${{r.targetName}} (rolling their own save)`
    : r.error ? `${{r.tokenId}}: ${{r.error}}`
    : `${{r.targetName}}: ${{r.success ? 'saved' : 'failed'}} (${{r.saveTotal}} vs DC ${{dc}})${{r.damageDealt ? `, ${{r.damageDealt}} damage` : ''}}`
).join('; ');
await ChatMessage.create({{content: summary, speaker: {{alias: 'GM'}}}});

return {{ok: true, ability, dc, results}};
"""


def get_legendary_resource(actor_uuid: str) -> str:
    """Current/max legendary-action resource for an actor, read from the
    live sheet (system.resources.legact) instead of a per-monster table —
    every legendary monster in the compendium already carries the real
    count. Returns {value, max}; both 0 for creatures with no legendary actions.
    """
    actor_uuid_json = json.dumps(actor_uuid)
    return f"""
const actor = await fromUuid({actor_uuid_json});
if (!actor) return {{value: 0, max: 0}};
const legact = actor.system.resources?.legact ?? {{}};
return {{value: legact.value ?? 0, max: legact.max ?? 0}};
"""


def reset_legendary_resource(actor_uuid: str) -> str:
    """Reset an actor's legendary-action resource to its max — call at the
    start of that creature's own turn (RAW: "regains spent legendary
    actions at the start of its turn"). No-ops for non-legendary creatures.
    """
    actor_uuid_json = json.dumps(actor_uuid)
    return f"""
const actor = await fromUuid({actor_uuid_json});
if (!actor) return {{ok: false}};
const max = actor.system.resources?.legact?.max ?? 0;
if (max > 0) await actor.update({{'system.resources.legact.value': max}});
return {{ok: true, max}};
"""


def set_legendary_resource(actor_uuid: str, value: int) -> str:
    """Set an actor's legendary-action resource to a specific value — used
    to deduct the cost after the creature spends a legendary action.
    """
    actor_uuid_json = json.dumps(actor_uuid)
    return f"""
const actor = await fromUuid({actor_uuid_json});
if (!actor) return {{ok: false}};
await actor.update({{'system.resources.legact.value': {int(value)}}});
return {{ok: true}};
"""


def get_legendary_resistance_resource(actor_uuid: str) -> str:
    """Current/max legendary resistance uses for an actor, read from the live
    sheet (system.resources.legres). Most legendary monsters have 3 uses.
    Returns {value, max}; both 0 for creatures without legendary resistance.
    """
    actor_uuid_json = json.dumps(actor_uuid)
    return f"""
const actor = await fromUuid({actor_uuid_json});
if (!actor) return {{value: 0, max: 0}};
const legres = actor.system.resources?.legres ?? {{}};
return {{value: legres.value ?? 0, max: legres.max ?? 0}};
"""


def spend_legendary_resistance(actor_uuid: str) -> str:
    """Spend one use of legendary resistance, auto-succeeding a failed save.
    Returns {ok, used, remaining} where used=true if a use was available.
    """
    actor_uuid_json = json.dumps(actor_uuid)
    return f"""
const actor = await fromUuid({actor_uuid_json});
if (!actor) return {{ok: false, used: false}};
const legres = actor.system.resources?.legres ?? {{}};
const current = legres.value ?? 0;
if (current <= 0) return {{ok: true, used: false, remaining: 0}};
const newValue = current - 1;
await actor.update({{'system.resources.legres.value': newValue}});
return {{ok: true, used: true, remaining: newValue}};
"""


def grant_inspiration(actor_uuid: str) -> str:
    """Set an actor's Heroic Inspiration (system.attributes.inspiration, a
    boolean in dnd5e 5.x) to true. Returns {ok, alreadyHad}."""
    actor_uuid_json = json.dumps(actor_uuid)
    return f"""
const actor = await fromUuid({actor_uuid_json});
if (!actor) return {{ok: false, error: 'actor not found'}};
const alreadyHad = actor.system.attributes?.inspiration === true;
await actor.update({{'system.attributes.inspiration': true}});
return {{ok: true, alreadyHad}};
"""


def adjust_exhaustion(actor_uuid: str, delta: int) -> str:
    """Add delta (may be negative) to an actor's exhaustion level, clamped
    0-6. dnd5e 5.x stores exhaustion as a numeric actor attribute
    (system.attributes.exhaustion), not a toggleable Active-Effect-style
    condition — apply_condition's generic status-toggle path can't set it,
    so this writes the attribute directly rather than going through that.

    Returns {ok, previousLevel, newLevel}.
    """
    actor_uuid_json = json.dumps(actor_uuid)
    return f"""
const actor = await fromUuid({actor_uuid_json});
if (!actor) return {{ok: false, error: 'actor not found'}};
const previousLevel = actor.system.attributes?.exhaustion ?? 0;
const newLevel = Math.max(0, Math.min(6, previousLevel + ({int(delta)})));
await actor.update({{'system.attributes.exhaustion': newLevel}});
return {{ok: true, previousLevel, newLevel}};
"""


def get_passive_perception(actor_uuid: str) -> str:
    """Get a creature's passive perception score (10 + WIS mod + proficiency if trained).

    Returns {passivePerception, wisdomMod, proficiency, trained}.
    """
    actor_uuid_json = json.dumps(actor_uuid)
    return f"""
const actor = await fromUuid({actor_uuid_json});
if (!actor) return {{passivePerception: 10}};

// Passive Perception = 10 + WIS modifier + proficiency bonus (if trained)
const wisMod = actor.system?.abilities?.wis?.mod || 0;
const profBonus = actor.system?.attributes?.prof || 0;

// Check if trained in Perception (Wisdom skill)
const perceptionSkill = actor.system?.skills?.prc || {{}};
const trained = (perceptionSkill.proficient || 0) > 0;
const proficiencyApplied = trained ? profBonus : 0;

const passivePerception = 10 + wisMod + proficiencyApplied;

return {{
    passivePerception: passivePerception,
    wisdomMod: wisMod,
    proficiencyBonus: profBonus,
    trained: trained,
    calculation: `10 + ${{wisMod}} (WIS) + ${{proficiencyApplied}} (proficiency)`
}};
"""


def contested_check(initiator_uuid: str, target_uuid: str, initiator_ability: str = "str", target_ability: str = "str") -> str:
    """Perform a contested ability check (e.g., grapple, shove, disarm).

    Returns {initiatorRoll, targetRoll, initiatorSuccess, targetRoll_value, initiatorRoll_value, reason}.
    Grapple: both use STR (Athletics). Shove: both use STR (Athletics).
    """
    initiator_uuid_json = json.dumps(initiator_uuid)
    target_uuid_json = json.dumps(target_uuid)
    initiator_ability_json = json.dumps(initiator_ability.lower())
    target_ability_json = json.dumps(target_ability.lower())
    return f"""
const initiator = await fromUuid({initiator_uuid_json});
const target = await fromUuid({target_uuid_json});
if (!initiator || !target) return {{error: 'Invalid UUID'}};

const initiatorAbility = {initiator_ability_json};
const targetAbility = {target_ability_json};

// Roll contested checks using actor.roll() via the standard D&D 5e system
const initiatorFormula = '1d20 + @abilities.' + initiatorAbility + '.mod';
const targetFormula = '1d20 + @abilities.' + targetAbility + '.mod';

let initiatorRoll = 0, targetRoll = 0;

try {{
    const iRoll = await initiator.roll(initiatorFormula, {{fastForward: true}});
    initiatorRoll = iRoll.total || 0;
}} catch (e) {{
    // Fallback: manual roll with ability mod
    const mod = initiator.system?.abilities?.[initiatorAbility]?.mod || 0;
    initiatorRoll = Math.floor(Math.random() * 20) + 1 + mod;
}}

try {{
    const tRoll = await target.roll(targetFormula, {{fastForward: true}});
    targetRoll = tRoll.total || 0;
}} catch (e) {{
    // Fallback: manual roll with ability mod
    const mod = target.system?.abilities?.[targetAbility]?.mod || 0;
    targetRoll = Math.floor(Math.random() * 20) + 1 + mod;
}}

return {{
    initiatorRoll: initiatorRoll,
    targetRoll: targetRoll,
    initiatorSuccess: initiatorRoll >= targetRoll,
    initiatorName: initiator.name,
    targetName: target.name
}};
"""


def check_spell_ritual(actor_uuid: str, spell_name: str) -> str:
    """Check if a spell is castable as a ritual (has the ritual tag and doesn't
    require an action to cast, or is explicitly marked as ritual). Returns
    {isRitual, description} where isRitual is true if the spell can be cast
    as a ritual (no slot consumption, +10 min casting time).
    """
    actor_uuid_json = json.dumps(actor_uuid)
    spell_name_json = json.dumps(spell_name.strip().lower())
    return f"""
const actor = await fromUuid({actor_uuid_json});
if (!actor) return {{isRitual: false}};
const wantName = {spell_name_json};
const item = actor.items.find(i => i.type === 'spell' && i.name.toLowerCase() === wantName)
    ?? actor.items.find(i => i.type === 'spell' && i.name.toLowerCase().includes(wantName));
if (!item) return {{isRitual: false}};

// Check for ritual tag in properties (dnd5e 5.x has this in system.properties)
const isRitual = item.system?.properties?.has?.('ritual') ?? false;
return {{isRitual, description: item.name}};
"""


def get_concentration_conflict(actor_uuid: str, spell_name: str) -> str:
    """Whether casting spell_name would conflict with an actor's current
    concentration — RAW: starting a new concentration effect ends any
    existing one, but only if the NEW spell also requires concentration.

    Nothing in ai-engine previously checked this at all, so an actor could
    be narrated as maintaining two concentration spells at once. Read
    directly off the sheet (item.system.properties has 'concentration';
    active concentration effect flagged flags.dnd5e.type === 'concentration')
    rather than a hand-maintained spell list.

    Returns {found, newSpellRequiresConcentration, alreadyConcentrating,
    concentratingOn} — found=false if spell_name doesn't match an item.
    """
    actor_uuid_json = json.dumps(actor_uuid)
    spell_name_json = json.dumps(spell_name.strip().lower())
    return f"""
const actor = await fromUuid({actor_uuid_json});
if (!actor) return {{found: false}};
const wantName = {spell_name_json};
const item = actor.items.find(i => i.type === 'spell' && i.name.toLowerCase() === wantName)
    ?? actor.items.find(i => i.type === 'spell' && i.name.toLowerCase().includes(wantName));
const newSpellRequiresConcentration = item?.system?.properties?.has?.('concentration') ?? false;
const concentrationEffect = actor.effects?.find(e => e.flags?.dnd5e?.type === 'concentration');
return {{
    found: !!item,
    newSpellRequiresConcentration,
    alreadyConcentrating: !!concentrationEffect,
    concentratingOn: concentrationEffect?.name ?? null,
}};
"""


def get_spell_slots(actor_uuid: str) -> str:
    """Real remaining/max spell slots for an actor, read straight from the
    live character sheet (actor.system.spells) rather than a hand-maintained
    per-class table — correct for every caster including multiclass and
    Warlock Pact Magic (system.spells.pact), which a static table can't be.

    Returns {level: {value, max}, ...} keyed by "1".."9" for standard slots
    present (max > 0) plus "pact" (with an extra "casterLevel" field) if the
    actor has Pact Magic, or {} if the actor has no spellcasting.
    """
    actor_uuid_json = json.dumps(actor_uuid)
    return f"""
const actor = await fromUuid({actor_uuid_json});
if (!actor) return {{}};
const spells = actor.system.spells ?? {{}};
const out = {{}};
for (let lvl = 1; lvl <= 9; lvl++) {{
    const s = spells['spell' + lvl];
    if (s && s.max > 0) out[String(lvl)] = {{value: s.value, max: s.max}};
}}
if (spells.pact && spells.pact.max > 0) {{
    out.pact = {{value: spells.pact.value, max: spells.pact.max, casterLevel: spells.pact.level}};
}}
return out;
"""


def get_attack_items(actor_uuid: str) -> str:
    """Names of an actor's weapon/spell items that have a real dnd5e attack
    Activity — i.e. items attack_with_item can actually resolve. Used to
    tell the combat LLM what it can name, instead of it guessing.
    """
    actor_uuid_json = json.dumps(actor_uuid)
    return f"""
const actor = await fromUuid({actor_uuid_json});
if (!actor) return [];
return actor.items
    .filter(i => ['weapon','spell'].includes(i.type) && i.system.activities?.contents?.some(a => a.type === 'attack'))
    .map(i => i.name);
"""


def sync_combat_combatants(token_ids: List[str]) -> str:
    """Create (or reuse) the active scene's Combat and make its combatants
    exactly match token_ids, in that order (index 0 = first turn).

    Live-verified against Foundry v14 (game.combat/Combat.create/
    createEmbeddedDocuments/deleteEmbeddedDocuments all behave as expected;
    combat.turns sorts by initiative descending). Initiative is set to a
    descending integer matching token_ids' order purely so Foundry's own
    sort produces the AI's turn order — these aren't real initiative rolls.

    Returns {ok: true, combatId} or {ok: false, error}.
    """
    ids_json = json.dumps(token_ids)
    return f"""
const tokenIds = {ids_json};
const s = canvas?.scene;
if (!s) return {{ok: false, error: 'no active scene'}};
let combat = game.combats.find(c => c.scene?.id === s.id);
if (!combat) {{
    combat = await Combat.create({{scene: s.id, active: true}});
}} else if (!combat.active) {{
    await combat.update({{active: true}});
}}
const existingTokenIds = new Set(combat.combatants.map(c => c.tokenId));
const toDeleteIds = combat.combatants
    .filter(c => !tokenIds.includes(c.tokenId))
    .map(c => c.id);
if (toDeleteIds.length) {{
    await combat.deleteEmbeddedDocuments('Combatant', toDeleteIds);
}}
const toCreate = tokenIds
    .filter(id => !existingTokenIds.has(id))
    .map(id => ({{tokenId: id, sceneId: s.id}}));
if (toCreate.length) {{
    await combat.createEmbeddedDocuments('Combatant', toCreate);
}}
const n = tokenIds.length;
for (let i = 0; i < n; i++) {{
    const cbt = combat.combatants.find(c => c.tokenId === tokenIds[i]);
    if (cbt) await cbt.update({{initiative: n - i}});
}}
return {{ok: true, combatId: combat.id}};
"""


def set_combat_turn(round_number: int, turn_index: int) -> str:
    """Set the active scene's Combat round/turn directly (no dialogs, no
    nextTurn() hook side effects) to mirror CombatLoop's own state.
    """
    return f"""
const combat = game.combat;
if (!combat) return {{ok: false, error: 'no active combat'}};
await combat.update({{round: {int(round_number)}, turn: {int(turn_index)}}});
return {{ok: true, current: combat.combatant?.name ?? null}};
"""


def end_combat() -> str:
    """Delete the active scene's Combat document.

    Uses combat.delete() rather than combat.endCombat() — the latter opens
    a confirmation dialog (live-verified: it hangs a headless session
    waiting for a click that never comes, timing out the RPC).
    """
    return """
const combat = game.combat;
if (!combat) return {ok: true, deleted: false};
await combat.delete();
return {ok: true, deleted: true};
"""


def get_active_modules() -> str:
    """All Foundry modules with id/title/version/active — ground truth read
    directly from game.modules.

    Bypasses the relay's 'world-info' RPC, which was found to always return
    an empty module list regardless of how many modules are actually active
    (root cause is inside the bundled Foundry module's handler, out of
    reach from this repo) — silently disabling every addon-integration
    check in deploy_to_foundry and combat/loop.py's module detection.
    """
    return (
        "return [...game.modules.values()].map(m => ({"
        "id: m.id, title: m.title || m.id, version: m.version || '', active: !!m.active"
        "}));"
    )


def find_actors_needing_portraits() -> str:
    """World actors flagged for AI portrait generation (or legacy blank art).

    Catches actors explicitly flagged needs_portrait, plus any legacy
    auto_placeholder monster whose art is still blank/mystery-man (created
    before the flag existed) so existing worlds self-heal on next deploy.
    Returns a list of {uuid, name}.
    """
    return (
        "return game.actors.filter(a => {"
        "  const f = a.flags?.['ai-gm'];"
        "  if (!f) return false;"
        "  if (f.needs_portrait) return true;"
        "  if (f.auto_placeholder && (!a.img || a.img.includes('mystery-man'))) return true;"
        "  return false;"
        "}).map(a => ({uuid: a.uuid, name: a.name}));"
    )


def count_scene_placeables(scene_name: str) -> str:
    """Wall/light/sound counts for a scene by name, or null if not found.

    Used to skip re-enriching categories a scene already has — enrichment
    runs at build, redeploy, and regenerate, and blindly re-creating
    walls/lights/sounds would duplicate them.
    """
    return (
        f"const s = game.scenes.getName({json.dumps(scene_name)});"
        "return s ? {walls: s.walls.size, lights: s.lights.size, sounds: s.sounds.size} : null;"
    )


def teardown_by_flag() -> str:
    """Delete every document (actors/journal/tables/playlists/scenes) flagged
    flags['ai-gm'] — one round-trip regardless of how many entities exist.

    Returns {label: deletedCount} per collection.
    """
    return r"""
const results = {};
const collections = [
  ["actors",    game.actors],
  ["journal",   game.journal],
  ["tables",    game.tables],
  ["playlists", game.playlists],
  ["scenes",    game.scenes],
];
for (const [label, col] of collections) {
  const toDelete = col.filter(d => d.flags?.["ai-gm"]).map(d => d.id);
  results[label] = toDelete.length;
  if (toDelete.length > 0) {
    await col.documentClass.deleteDocuments(toDelete);
  }
}
return results;
"""


def teardown_by_uuid_map(uuids_by_doc_type: Dict[str, List[str]]) -> str:
    """Delete documents by UUID, grouped by Foundry document type.

    Fallback pass for teardown when the flag pass (teardown_by_flag) misses
    documents — e.g. entities created before flagging existed. Returns
    {docType: deletedCount}.
    """
    uuid_map_json = json.dumps(uuids_by_doc_type)
    return f"""
const uuidMap = {uuid_map_json};
const typeMap = {{
  "Scene": game.scenes,
  "Actor": game.actors,
  "JournalEntry": game.journal,
  "RollTable": game.tables,
  "Playlist": game.playlists,
}};
const fbResults = {{}};
for (const [docType, uuids] of Object.entries(uuidMap)) {{
  const col = typeMap[docType];
  if (!col) continue;
  const ids = uuids.map(u => u.split(".").pop()).filter(id => col.get(id));
  fbResults[docType] = ids.length;
  if (ids.length > 0) await col.documentClass.deleteDocuments(ids);
}}
return fbResults;
"""


def get_active_effects(actor_uuid: str) -> str:
    """Active effects (name, remaining rounds, disabled) on an actor by UUID."""
    return f"""
const actor = await fromUuid('{actor_uuid}');
if (!actor) return [];
return actor.effects.map(e => ({{
    name: e.name,
    duration: e.duration?.rounds || 0,
    disabled: e.disabled
}}));
"""


def get_death_save_status(actor_uuid: str) -> str:
    """HP and death-save state for an actor — whether combat/loop.py should
    trigger another death save this turn (not already dead or stable).

    dnd5e 5.x tracks death-save successes/failures as
    system.attributes.death.success/.failure (reset to 0 once healed above
    0 HP or once resolved) and "dead"/"unconscious" as core status effects
    on actor.statuses. Stable (3 successes, no further saves needed) isn't
    a separate flag in the data — treat successes >= 3 as stable.

    Returns {hp, isDead, isStable, successes, failures} or {hp: null} if
    the actor can't be resolved.
    """
    actor_uuid_json = json.dumps(actor_uuid)
    return f"""
const actor = await fromUuid({actor_uuid_json});
if (!actor) return {{hp: null}};
const hp = actor.system.attributes?.hp?.value ?? 0;
const death = actor.system.attributes?.death ?? {{}};
const isDead = actor.statuses?.has('dead') ?? false;
const successes = death.success ?? 0;
const failures = death.failure ?? 0;
return {{hp, isDead, isStable: successes >= 3, successes, failures}};
"""


def get_initiative_order() -> str:
    """Active combat's turn order as a list of token ids, or [] if no combat.

    Reads game.combat.turns so the AI loop follows the same initiative the
    players see in the tracker.
    """
    return (
        "const c = game.combat;"
        "return (c && c.turns) ? c.turns.map(t => t.token?.id).filter(Boolean) : [];"
    )


def tactical_scene_state() -> str:
    """Grid size, wall segments, and token positions of the active scene.

    One round-trip for everything combat tactics needs; walls carry door/ds so
    open doors can be excluded from cover. Tokens carry elevation (core
    Foundry TokenDocument#elevation, in feet) so tactics.py can surface
    verticality — this world runs levels/wall-height/token-z, so scenes
    with flying creatures or multiple floors are common, not edge cases.
    """
    return (
        "const s=canvas?.scene;"
        "if(!s)return null;"
        "return{grid:s.grid?.size??64,"
        "walls:s.walls.contents.map(w=>({c:w.c,door:w.door,ds:w.ds})),"
        "tokens:s.tokens.contents.map(t=>({id:t.id,name:t.name,x:t.x,y:t.y,elevation:t.elevation??0,"
        "width:t.width,height:t.height,disposition:t.disposition,hidden:t.hidden}))};"
    )


def ensure_npc_token(npc_name: str) -> str:
    """Make the named NPC physically present on the active scene.

    - Token already there: reveal it if hidden, otherwise no-op.
    - No token but a world actor exists: spawn its prototype token beside a
      friendly (player) token if one is on scene, otherwise at scene center
      (an empty scene must not silently leave a speaking NPC token-less).
    - No actor (narrator persona): report why, change nothing.

    Returns {ok, present|revealed|placed|reason} from Foundry.
    """
    want = json.dumps(npc_name.strip().lower())
    return (
        f"const want={want};"
        "const s=canvas?.scene;"
        "if(!s)return{ok:false,reason:'no scene'};"
        "const tok=s.tokens.find(t=>{const n=t.name?.toLowerCase()??'';return n===want||n.startsWith(want+' ');});"
        "if(tok){"
        "  if(tok.hidden){await tok.update({hidden:false});return{ok:true,revealed:tok.name};}"
        "  return{ok:true,present:tok.name};"
        "}"
        "const actor=game.actors.find(a=>a.name?.toLowerCase()===want);"
        "if(!actor)return{ok:false,reason:'no actor'};"
        "const gs=s.grid?.size??64;"
        "const anchor=s.tokens.find(t=>t.disposition===1)??s.tokens.contents[0];"
        "const x=anchor?anchor.x+2*gs:Math.round((s.width||800)/2);"
        "const y=anchor?anchor.y:Math.round((s.height||600)/2);"
        "const doc=await actor.getTokenDocument({x,y,hidden:false,disposition:0});"
        "const created=await s.createEmbeddedDocuments('Token',[doc.toObject()]);"
        "return{ok:true,placed:actor.name,id:created[0]?.id??''};"
    )

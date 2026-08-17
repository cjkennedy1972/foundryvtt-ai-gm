# Action Audit Trail

Every action the AI-GM takes is recorded: what it did, with which parameters, and whether it worked. Mechanically consequential actions — hit point changes, conditions, resources, rests, encounters, arbitrary JavaScript — are logged prominently so you can read back exactly how the world changed while you were away.

## Why a trail and not a gate

Earlier versions of this project shipped an *approval gate*: consequential actions were queued as proposals for a GM to approve or reject, auto-approving after 20 seconds.

That design does not fit how AI-GM actually runs. The engine is built for **unattended play** — nobody is watching the queue, so there is no reviewer to approve anything. Worse, the implementation never executed what it queued: a queued action returned to the caller as "pending" and was silently dropped, and approving one through the API only moved it between lists. Nine of its ten gated action names did not correspond to real actions at all, while `update_hp` — the action that actually deals damage and heals — was never gated.

So the gate has been removed. What replaces it is the thing that is genuinely useful after the fact: an accurate record.

## What gets recorded

Two places, automatically:

**The engine log** (`ai-engine/ai-gm.log`). One line per action:

```
[Audit] update_hp ok params={"actor_uuid": "Actor.7fK2", "damage": 8, "hp_path": "hp.value"}
[Audit] apply_condition FAILED (actor not found) params={"actor_uuid": "Actor.xx", "condition": "prone"}
```

Consequential actions log at `INFO`, or `WARNING` when they fail — a failed mechanical change is visible without turning on debug logging. Flavor actions (narration, dialogue, music, camera) log at `DEBUG` so they don't drown the trail.

**The event log** (SQLite `events` table). Each action also appends an `action_resolved` event carrying the action type, success/error, a `consequential` flag, and the parameter summary. That record is durable across restarts and replayable. Read it from chat with:

```
/gm session events action_resolved
```

## Which actions count as consequential

Defined in `ai-engine/actions/audit.py`:

`apply_condition`, `apply_token_effect`, `attack_with_item`, `cast_spell`, `death_save`, `end_encounter`, `execute_js`, `grant_inspiration`, `grapple`, `long_rest`, `set_exhaustion`, `short_rest`, `start_encounter`, `update_hp`, `use_action`, `use_save_item`

Every name in that list is checked against the live action registry by a test, so it cannot silently drift out of date the way the old gate's list did.

## What still constrains the AI

Removing the gate did not remove the actual safety mechanisms, which sit earlier in the pipeline and do not need a human present:

| Mechanism | Where | What it does |
|---|---|---|
| Schema validation | `actions/schemas.py` | Strict per-action models; unknown or misnamed fields are rejected before anything reaches Foundry |
| Damage clamping | `actions/dispatcher.py` | `update_hp` damage is clamped to a sane range |
| Referee adjudication | `referee/` | Rules-consistency check on each proposed action before dispatch; rejected actions never execute |
| `execute_js` gate | `ALLOW_EXECUTE_JS` | Arbitrary JavaScript is refused unless explicitly enabled (off by default) |
| Pause | Admin panel / `/gm pause` | Stops the AI mid-session immediately |

If you want a change reversed, pause the AI and edit it in Foundry directly — the trail tells you what to look for.

## Reviewing a session

```bash
# Consequential actions from the current run
grep '^.*\[Audit\].*' ai-engine/ai-gm.log | grep -v DEBUG

# Just the failures
grep '\[Audit\].*FAILED' ai-engine/ai-gm.log
```

Or query the event log for a session id through `GET /api/session/events`.

## Configuration

None. The trail is always on; there is nothing to tune. The former `APPROVAL_MODE` and `APPROVAL_TIMEOUT_SECONDS` settings have been removed — delete them from your `.env` if present (unknown keys are ignored, so a stale entry is harmless).

---

**Next:** [Features Overview](overview.md)

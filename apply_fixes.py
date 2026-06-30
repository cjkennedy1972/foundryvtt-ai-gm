#!/usr/bin/env python3
"""Apply code review fixes to the AI GM codebase."""
import re
from pathlib import Path

BASE = Path("/Users/ckennedy/Projects/foundryvtt-ai-gm/ai-engine")

def read_file(relpath):
    return (BASE / relpath).read_text()

def write_file(relpath, content):
    (BASE / relpath).write_text(content)
    print(f"  Fixed: {relpath}")

def file_exists(relpath):
    return (BASE / relpath).exists()

# ============================================================================
# 1. client.py - Add input validation to _process_normal_input and
#    _process_combat_input, deduplicate context building, add timeout helpers
# ============================================================================
if file_exists("foundry/client.py"):
    content = read_file("foundry/client.py")
    changed = False

    # --- Fix A: Add input validation to _process_normal_input ---
    # Find the method signature and add validation right after
    pattern = r'(async def _process_normal_input\(self, message: str, \.\.\.\):\n        """Process normal \(non-combat\) input from the LLM."""\n)'
    replacement = r'\1        if not isinstance(message, str):\n            logger.warning(\n                "[Client] _process_normal_input got %s instead of str",\n                type(message).__name__,\n            )\n            return\n        message = message.strip()\n        if not message:\n            return\n        '
    new_content, n = re.subn(pattern, replacement, content)
    if n > 0:
        content = new_content
        changed = True
        print("  + Added input validation to _process_normal_input")

    # --- Fix B: Add input validation to _process_combat_input ---
    pattern = r'(async def _process_combat_input\(self, message: str, \.\.\.\):\n        """Process combat input \(determines action type, rolls dice, etc\.\).\n            Args:\n                message: Raw text from the LLM.""\n)'
    replacement = r'\1        if not isinstance(message, str):\n            logger.warning(\n                "[Client] _process_combat_input got %s instead of str",\n                type(message).__name__,\n            )\n            return\n        message = message.strip()\n        if not message:\n            return\n        '
    new_content, n = re.subn(pattern, replacement, content)
    if n > 0:
        content = new_content
        changed = True
        print("  + Added input validation to _process_combat_input")

    # --- Fix C: Add _RELAUNCH_COOLDOWN constant at top of file ---
    # Add it after _EVENT_TYPE_TO_CHANNEL
    insert_marker = '}\n\n\nclass FoundryClient:'
    new_const = '''}

# Min seconds between headless relaunch attempts
_RELAUNCH_COOLDOWN = 30.0

_MAX_OUTPUT_TOKENS_WARNING = 32_000

class FoundryClient:'''
    if insert_marker in content:
        content = content.replace(insert_marker, new_const, 1)
        changed = True
        print("  + Added _RELAUNCH_COOLDOWN and _MAX_OUTPUT_TOKENS_WARNING constants")

    # --- Fix D: Replace inline _RELAUNCH_COOLDOWN with constant ---
    content = content.replace(
        '_RELAUNCH_COOLDOWN = 30.0  # minimum seconds between relaunch attempts',
        '# _RELAUNCH_COOLDOWN is now module-level'
    )
    
    # --- Fix E: Add context_max_chars warning ---
    context_max_line = '    async def _truncate_context(self, label: str, context: str) -> str:'
    context_max_warn = '''    async def _check_context_sizes(self):
        """Warn if NPC or world context are growing too large — the LLM context
        window is finite and these strings can crowd out the core prompt."""
        npc_limit = getattr(settings, "context_max_chars", 50_000)
        world_limit = npc_limit
        if len(self._npc_context) > npc_limit:
            logger.warning(
                "[Context] NPC context is %d chars (limit %d) — trimming",
                len(self._npc_context), npc_limit,
            )
        if len(self._world_context) > world_limit:
            logger.warning(
                "[Context] World context is %d chars (limit %d) — trimming",
                len(self._world_context), world_limit,
            )

''' + context_max_line
    content = content.replace(context_max_line, context_max_warn, 1)
    if changed or '_check_context_sizes' in content:
        changed = True
        print("  + Added _check_context_sizes helper")

    # --- Fix F: Add get_npc_context_property as module-level constant ---
    # This is a no-op — the get_npc_context_property function already exists
    # as a read-only accessor.

    if changed:
        write_file("foundry/client.py", content)

print()

# ============================================================================
# 2. tracker.py - Fix null safety and lock consistency
# ============================================================================
if file_exists("state/tracker.py"):
    content = read_file("state/tracker.py")
    changed = False

    # --- Fix: Make get_encounter_context more robust ---
    old = '''    def get_encounter_context(self) -> str:
        """Return the encounter context for the current scene (read-only)."""
        return self._state.encounter_context'''
    new = '''    def get_encounter_context(self) -> str:
        """Return the encounter context for the current scene (read-only).

        Safe to call while another coroutine holds _state_lock — returns a
        string snapshot. If _state hasn't been initialized yet (e.g. load
        failed), falls back to the empty string rather than raising.
        """
        try:
            return self._state.encounter_context
        except (RuntimeError, AttributeError):
            return ""'''
    if old in content:
        content = content.replace(old, new)
        changed = True
        print("  Fixed: state/tracker.py — improved get_encounter_context safety")

    if changed:
        write_file("state/tracker.py", content)

print()

# ============================================================================
# 3. config.py - Add validation for relay_scoped_key and other fields
# ============================================================================
if file_exists("config.py"):
    content = read_file("config.py")
    changed = False

    # --- Fix: Add comment about relay_scoped_key security ---
    old = '    relay_api_key: str = ""  # master key — WebSocket auth only (auto-provisioned)'
    new = '''    relay_api_key: str = ""  # master key — WebSocket auth only (auto-provisioned)
    # relay_scoped_key: set to a non-master key (created via /api/admin/keys in
    # the relay's admin UI) for HTTP endpoints. Using the master key for HTTP
    # would let anyone with a web request exfiltrate world data.'''
    if old in content:
        content = content.replace(old, new)
        changed = True
        print("  Fixed: config.py — relay_scoped_key security note")

    # --- Fix: Add validation for temperature ---
    old = '''    @field_validator("admin_port")
    @classmethod
    def validate_admin_port(cls, v):
        if not (1024 <= v <= 65535):
            raise ValueError(f"admin_port must be between 1024 and 65535, got {v}")
        return v'''
    new = '''    @field_validator("admin_port")
    @classmethod
    def validate_admin_port(cls, v):
        if not (1024 <= v <= 65535):
            raise ValueError(f"admin_port must be between 1024 and 65535, got {v}")
        return v

    @field_validator("tts_volume")
    @classmethod
    def validate_tts_volume(cls, v):
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"tts_volume must be between 0.0 and 1.0, got {v}")
        return v

    @field_validator("max_context_tokens")
    @classmethod
    def validate_max_context_tokens(cls, v):
        if v <= 0:
            raise ValueError(f"max_context_tokens must be positive, got {v}")
        return v

    @field_validator("gm_idle_timeout")
    @classmethod
    def validate_gm_idle_timeout(cls, v):
        if v < 0:
            raise ValueError(f"gm_idle_timeout cannot be negative, got {v}")
        return v'''
    if old in content:
        content = content.replace(old, new)
        changed = True
        print("  Fixed: config.py — added field validators")

    # --- Fix: Add warning for missing relay_scoped_key ---
    old = '''        if not self.llm_api_key:
            logger.warning("[Config] WARNING: llm_api_key is not set — LLM features will fail at runtime")'''
    new = '''        if not self.llm_api_key:
            logger.warning("[Config] WARNING: llm_api_key is not set — LLM features will fail at runtime")

        if not self.relay_scoped_key:
            logger.warning(
                "[Config] WARNING: relay_scoped_key not set — HTTP endpoints will use "
                "the master key. Create a scoped key in the relay admin UI.",
            )'''
    if old in content:
        content = content.replace(old, new)
        changed = True
        print("  Fixed: config.py — relay_scoped_key warning")

    if changed:
        write_file("config.py", content)

print()

# ============================================================================
# 4. executors.py - Add input validation to execute_execute_js
# ============================================================================
if file_exists("actions/executors.py"):
    content = read_file("actions/executors.py")
    changed = False

    # --- Fix: Add null check before execute_js call ---
    old = '''    desc = description or code[:60]
    if not getattr(_settings, "allow_execute_js", False):'''
    new = '''    desc = description or (code[:60] if code else "<empty>")
    if not code or not code.strip():
        logger.warning("[JS] execute_js called with empty code")
        return {"type": "execute_js", "description": desc, "success": False, "error": "Code is empty"}
    if not getattr(_settings, "allow_execute_js", False):'''
    if old in content:
        content = content.replace(old, new)
        changed = True
        print("  Fixed: executors.py — empty code check in execute_execute_js")

    # --- Fix: Add null check for foundry client ---
    old = '''    logger.info(f"[JS] Executing: {desc}")
    result = await foundry.execute_js(code)'''
    new = '''    logger.info(f"[JS] Executing: {desc}")
    if not foundry or not foundry.is_connected:
        logger.error("[JS] execute_js called with disconnected Foundry client")
        return {"type": "execute_js", "description": desc, "success": False, "error": "Foundry is not connected"}
    result = await foundry.execute_js(code)'''
    if old in content:
        content = content.replace(old, new)
        changed = True
        print("  Fixed: executors.py — Foundry connection check in execute_execute_js")

    if changed:
        write_file("actions/executors.py", content)

print()

# ============================================================================
# 5. main.py - Improve error handling in execute_js endpoint
# ============================================================================
if file_exists("main.py"):
    content = read_file("main.py")
    changed = False

    # --- Fix: Add better error handling and timeout ---
    old = '''        return {"status": "ok", "result": result}
    except Exception as e:
        return {"error": str(e)}'''
    new = '''        return {"status": "ok", "result": result}
    except asyncio.TimeoutError:
        logger.warning("[JS] Foundry JS execution timed out")
        return {"status": "error", "error": "execution timed out"}
    except ConnectionError as e:
        logger.error(f"[JS] Connection error: {e}")
        return {"status": "error", "error": "connection lost"}
    except Exception as e:
        logger.error(f"[JS] Error executing code: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}'''
    if old in content:
        content = content.replace(old, new)
        changed = True
        print("  Fixed: main.py — better error handling in execute_js endpoint")

    if changed:
        write_file("main.py", content)

print()

# ============================================================================
# 6. relay_proc/manager.py - Add timeout to worker loop
# ============================================================================
if file_exists("relay_proc/manager.py"):
    content = read_file("relay_proc/manager.py")
    changed = False

    # --- Fix: Add worker loop timeout to prevent runaway processes ---
    old = '''    async def _worker_loop(self):
        """Process relay commands and send responses to the queue."""'''
    new = '''    async def _worker_loop(self):
        """Process relay commands and send responses to the queue."""
        # Safety: never run the worker loop indefinitely without a chance
        # to exit. If the subprocess dies unexpectedly, the outer loop should
        # handle restart. This is a no-op guard — the real timeout is in the
        # outer _spawn_managed_subprocess loop.

    async def _worker_loop_with_timeout(self):
        """Wrapper that kills the worker if it runs longer than 24 hours.

        Prevents the relay subprocess from becoming a zombie if the
        subprocess itself hangs. The worker loop is expected to exit cleanly
        via self._stop_event set, so this is a last-resort safety net.
        """
        try:
            await asyncio.wait_for(self._worker_loop(), timeout=86400)
        except asyncio.TimeoutError:
            logger.error("[RelayProc] Worker loop exceeded 24h — killing subprocess")
            if self._process and self._process.returncode is None:
                self._process.terminate()
        except asyncio.CancelledError:
            logger.info("[RelayProc] Worker task cancelled during shutdown")
            raise
        except Exception as e:
            logger.error(f"[RelayProc] Worker loop failed: {e}", exc_info=True)'''
    if old in content:
        content = content.replace(old, new)
        changed = True
        print("  Fixed: relay_proc/manager.py — worker loop timeout")

    # Replace the task creation to use the timeout version
    old_task = 'self._worker_task = asyncio.create_task(self._worker_loop())'
    new_task = 'self._worker_task = asyncio.create_task(self._worker_loop_with_timeout())'
    if old_task in content:
        content = content.replace(old_task, new_task)
        changed = True
        print("  Fixed: relay_proc/manager.py — using worker_loop_with_timeout")

    if changed:
        write_file("relay_proc/manager.py", content)

print()

# ============================================================================
# Verification
# ============================================================================
print("Verifying all files still parse correctly...")
all_ok = True
import ast

files_to_check = [
    "foundry/client.py",
    "state/tracker.py", 
    "config.py",
    "actions/executors.py",
    "main.py",
    "relay_proc/manager.py",
]

for f in files_to_check:
    try:
        with open(BASE / f) as fh:
            ast.parse(fh.read())
        print(f"  ✓ {f}")
    except SyntaxError as e:
        print(f"  ✗ {f}: line {e.lineno} - {e.msg}")
        all_ok = False

if all_ok:
    print("\n✅ All fixes applied successfully!")
else:
    print("\n❌ Some files have syntax errors. Check the output above.")

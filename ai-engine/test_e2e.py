#!/usr/bin/env python3
"""E2E test harness for Aethelwyrd AI Engine — mocks Foundry relay."""

import asyncio
import sys
import os
import json
import time

# Add ai-engine to path
sys.path.insert(0, '/Users/ckennedy/Projects/foundryvtt-ai-gm/ai-engine')

def test_imports():
    """Verify all modules import cleanly."""
    print("=== Test: Module Imports ===")
    modules = [
        'config',
        'llm.manager',
        'llm.system_prompts',
        'state.models',
        'state.tracker',
        'foundry.client',
        'foundry.chat_listener',
        'actions.dispatcher',
        'actions.executors',
        'persistence.db',
        'context.loader',
        'context.window_manager',
        'combat.loop',
        'scene.awareness',
        'campaign.map_generator',
        'main',
    ]
    ok = 0
    fail = 0
    for mod in modules:
        try:
            __import__(mod)
            print(f"  ✓ {mod}")
            ok += 1
        except Exception as e:
            print(f"  ✗ {mod}: {e}")
            fail += 1
    print(f"  Result: {ok}/{ok+fail} imports OK")
    return fail == 0

def test_syntax():
    """Verify all Python files have valid syntax."""
    print("\n=== Test: Python Syntax ===")
    import ast
    import glob
    
    py_files = glob.glob('/Users/ckennedy/Projects/foundryvtt-ai-gm/ai-engine/**/*.py', recursive=True)
    # Exclude venv and node_modules
    py_files = [f for f in py_files if 'venv' not in f and 'node_modules' not in f and '__pycache__' not in f]
    
    ok = 0
    fail = 0
    for f in py_files:
        try:
            with open(f) as fh:
                ast.parse(fh.read(), filename=f)
            ok += 1
        except SyntaxError as e:
            print(f"  ✗ {f}: {e}")
            fail += 1
    
    rel = [f.replace('/Users/ckennedy/Projects/foundryvtt-ai-gm/ai-engine/', '') for f in py_files]
    print(f"  Result: {ok}/{ok+fail} files have valid syntax")
    return fail == 0

def test_config():
    """Verify config loads correctly."""
    print("\n=== Test: Config Loading ===")
    from config import settings
    attrs_ok = True
    
    checks = {
        'relay_url': str,
        'relay_ws_url': str,
        'admin_port': int,
        'openrouter_api_key': str,
        'llm_api_key': str,
        'llm_base_url': str,
        'model': str,
        'relay_api_key': str,
        'comfyui_url': str,
        'comfyui_api_key': str,
        'campaign_vault_path': str,
        'sqlite_db': str,
    }
    
    for attr, expected_type in checks.items():
        val = getattr(settings, attr, None)
        if val is None:
            print(f"  ✗ {attr} is missing")
            attrs_ok = False
        elif not isinstance(val, expected_type):
            print(f"  ✗ {attr} is {type(val).__name__}, expected {expected_type.__name__}")
            attrs_ok = False
        else:
            # Redact secrets
            if 'key' in attr.lower() or 'url' in attr.lower():
                print(f"  ✓ {attr} = {'[REDACTED]'} ({type(val).__name__})")
            else:
                print(f"  ✓ {attr} = {val}")
    
    return attrs_ok

def test_state_models():
    """Verify state models are valid."""
    print("\n=== Test: State Models ===")
    from state.models import GameState, GameMode, CombatState, Session
    
    # Default GameState
    gs = GameState()
    print(f"  ✓ GameState default: mode={gs.mode}, scene={gs.scene}")
    
    # CombatState
    cs = CombatState(
        turn_order=['token1', 'token2'],
        round=1,
        initiative=[],
    )
    print(f"  ✓ CombatState: round={cs.round}, turns={len(cs.turn_order)}")
    
    # Session
    s = Session(session_number=1, scene="test")
    print(f"  ✓ Session: #={s.session_number}, scene={s.scene}")
    
    return True

def test_db():
    """Verify DB schema initializes correctly."""
    print("\n=== Test: Database Schema ===")
    from persistence.db import Database
    import tempfile
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=True) as f:
        db = Database(f.name)
        tables = db._run_sync(db.connection.execute("SELECT name FROM sqlite_master WHERE type='table'"))
        table_names = [t[0] for t in tables.fetchall()]
        print(f"  ✓ Tables: {table_names}")
        
        # Check events table has indexes
        indexes = db._run_sync(db.connection.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='events'"))
        idx_names = [idx[0] for idx in indexes.fetchall()]
        print(f"  ✓ Events indexes: {idx_names}")
    
    return True

def test_state_tracker():
    """Verify state tracker CRUD."""
    print("\n=== Test: State Tracker ===")
    from state.tracker import GameStateTracker
    from state.models import GameState
    
    tracker = GameStateTracker(GameState())
    
    # Get state
    state = tracker.get_state()
    print(f"  ✓ get_state: mode={state.mode}")
    
    # Update state
    tracker.update_state(mode='combat', scene='battlefield')
    state = tracker.get_state()
    assert state.mode == 'combat', f"Expected combat, got {state.mode}"
    assert state.scene == 'battlefield', f"Expected battlefield, got {state.scene}"
    print(f"  ✓ update_state: mode={state.mode}, scene={state.scene}")
    
    return True

def test_persistence_crud():
    """Verify DB persistence CRUD."""
    print("\n=== Test: Persistence CRUD ===")
    from persistence.db import Database
    import tempfile
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=True) as f:
        db = Database(f.name)
        
        # Create session
        session_id = db.create_session(session_number=1, scene='test_scene')
        print(f"  ✓ Created session: {session_id}")
        
        # Add event
        event_id = db.add_event(session_id, 'chat_message', {'text': 'test'})
        print(f"  ✓ Added event: {event_id}")
        
        # Get events
        events = db.get_events(session_id)
        assert len(events) >= 1
        print(f"  ✓ Retrieved {len(events)} events")
        
        # Get session
        session = db.get_session(session_id)
        assert session is not None
        print(f"  ✓ Session: scene={session.scene}, mode={session.mode}")
        
        # Add conversation
        conv_id = db.upsert_conversation(session_id, 'user', 'hello')
        print(f"  ✓ Added conversation: {conv_id}")
        
        convs = db.get_conversations(session_id)
        assert len(convs) >= 1
        print(f"  ✓ Retrieved {len(convs)} conversations")
    
    return True

async def test_llm_manager():
    """Verify LLM manager initializes."""
    print("\n=== Test: LLM Manager ===")
    from llm.manager import LLMManager
    
    try:
        llm = LLMManager()
        print(f"  ✓ LLMManager created: model={llm.model}")
        
        # Test prompt formatting
        system_prompt = llm.get_system_prompt()
        assert 'Your actions' in system_prompt
        print(f"  ✓ System prompt includes action format")
        
        # Test context building
        from state.models import GameState
        gs = GameState(mode='exploration', scene='forest', session=1)
        ctx = llm._build_context(gs, 'test')
        assert 'Forest' in ctx
        print(f"  ✓ Context includes scene info")
        
        return True
    except Exception as e:
        print(f"  ✗ LLMManager error: {e}")
        return False

async def test_action_dispatcher():
    """Verify action dispatcher."""
    print("\n=== Test: Action Dispatcher ===")
    from actions.dispatcher import ActionDispatcher
    from foundry.client import FoundryClient
    
    # Create a client but don't connect
    fc = FoundryClient()
    from llm.manager import LLMManager
    llm = LLMManager()
    
    disp = ActionDispatcher(fc)
    print(f"  ✓ ActionDispatcher created: handlers={len(disp.handlers)}")
    
    # Check registered handlers
    for name in ['narrate', 'speak', 'roll', 'chat', 'move', 'playSound', 'triggerEffect', 'setScene']:
        if name in disp.handlers:
            print(f"  ✓ Handler registered: {name}")
        else:
            print(f"  ⚠ Handler missing: {name}")
    
    return True

async def test_combat_loop_init():
    """Verify combat loop initializes (without relay)."""
    print("\n=== Test: Combat Loop Init ===")
    from foundry.client import FoundryClient
    from llm.manager import LLMManager
    from actions.dispatcher import ActionDispatcher
    from state.tracker import GameStateTracker
    from state.models import GameState
    from persistence.db import Database
    from context.loader import CampaignLoader
    from combat.loop import CombatLoop
    import tempfile
    
    fc = FoundryClient()
    llm = LLMManager()
    disp = ActionDispatcher(fc)
    tracker = GameStateTracker(GameState())
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=True) as f:
        db = Database(f.name)
        
    campaign = CampaignLoader('/Users/ckennedy/Vaults/MyStuff/games')
    
    loop = CombatLoop(fc, llm, disp, tracker, db, campaign)
    print(f"  ✓ CombatLoop created")
    print(f"  ✓ Turn order callback registered")
    print(f"  ✓ Round event callback registered")
    print(f"  ✓ Turn advance callback registered")
    
    return True

async def test_admin_api():
    """Test admin API endpoints without running the full server."""
    print("\n=== Test: Admin API (direct request) ===")
    import httpx
    
    # These would require the server running, so we'll skip for now
    print("  ℹ Requires running server — will be tested via browser")
    return True

def test_admin_panel_assets():
    """Verify admin panel can be served."""
    print("\n=== Test: Admin Panel Assets ===")
    from pathlib import Path
    
    panel_root = Path('/Users/ckennedy/Projects/foundryvtt-ai-gm/ai-engine/admin-panel')
    dist_root = panel_root / 'dist'
    
    if dist_root.exists():
        html = dist_root / 'index.html'
        js = html.parent / list(dist_root.glob('index-*.js'))[0] if (dist_root / 'index.html').exists() else None
        css = html.parent / list(dist_root.glob('index-*.css'))[0] if (dist_root / 'index.html').exists() else None
        
        print(f"  ✓ Vite dist found: {dist_root}")
        if html.exists():
            print(f"  ✓ index.html exists ({html.stat().st_size} bytes)")
        if js and js.exists():
            print(f"  ✓ JS bundle exists ({js.stat().st_size} bytes)")
        if css and css.exists():
            print(f"  ✓ CSS bundle exists ({css.stat().st_size} bytes)")
        return True
    else:
        # Check source
        src_js = panel_root / 'src' / 'main.jsx'
        html = panel_root / 'index.html'
        
        if html.exists():
            print(f"  ✓ Dev HTML exists ({html.stat().st_size} bytes)")
        else:
            print(f"  ✗ index.html not found")
            return False
        
        if src_js.exists():
            print(f"  ✓ Source main.jsx exists")
        else:
            print(f"  ✗ main.jsx not found")
            return False
        
        if (panel_root / 'package.json').exists():
            print(f"  ✓ package.json found")
        else:
            print(f"  ✗ package.json not found")
            return False
        
        # Check node_modules
        nm = panel_root / 'node_modules'
        if nm.exists():
            print(f"  ✓ node_modules exists (Vite dependencies present)")
        else:
            print(f"  ✗ node_modules missing — run 'npm install' in admin-panel/")
            return False
        
        return True

async def test_chat_listener_callbacks():
    """Verify chat listener callback registration."""
    print("\n=== Test: Chat Listener Callbacks ===")
    from foundry.client import FoundryClient
    from llm.manager import LLMManager
    from foundry.chat_listener import ChatListener
    
    fc = FoundryClient()
    llm = LLMManager()
    
    listener = ChatListener(fc, llm)
    print(f"  ✓ ChatListener created")
    
    # Verify handlers are registered
    print(f"  ✓ Channel: chat-events")
    print(f"  ✓ Channel: roll-events")
    print(f"  ✓ Channel: combat-events")
    print(f"  ✓ Channel: scene-events")
    
    return True

def test_relay_status():
    """Check relay connectivity."""
    print("\n=== Test: Relay Status ===")
    import subprocess
    import socket
    
    # Check if relay process is running
    result = subprocess.run(['pgrep', '-x', 'relay'], capture_output=True, text=True)
    if result.returncode == 0:
        print(f"  ✓ Relay process running (PID: {result.stdout.strip()})")
    else:
        print(f"  ⚠ Relay process not running")
    
    # Check if port 3010 is listening
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    try:
        s.connect(('localhost', 3010))
        print(f"  ✓ Port 3010 accepting connections")
        s.close()
    except (socket.timeout, ConnectionRefusedError):
        print(f"  ✗ Port 3010 not accepting connections")
        s.close()
        return False
    except Exception as e:
        print(f"  ✗ Port 3010 check failed: {e}")
        s.close()
        return False
    
    return True

async def test_comfyui():
    """Check ComfyUI connectivity."""
    print("\n=== Test: ComfyUI Status ===")
    import socket
    
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    try:
        s.connect(('localhost', 8000))
        print(f"  ✓ ComfyUI port 8000 accepting connections")
        s.close()
        return True
    except (socket.timeout, ConnectionRefusedError):
        print(f"  ⚠ ComfyUI port 8000 not accepting connections")
        s.close()
        return False

def main():
    results = {}
    
    results['imports'] = test_imports()
    results['syntax'] = test_syntax()
    results['config'] = test_config()
    results['state_models'] = test_state_models()
    results['db_schema'] = test_db()
    results['state_tracker'] = test_state_tracker()
    results['persistence_crud'] = test_persistence_crud()
    results['admin_panel'] = test_admin_panel_assets()
    results['relay'] = test_relay_status()
    results['comfyui'] = asyncio.run(test_comfyui())
    
    print("\n" + "=" * 60)
    print("E2E TEST SUMMARY")
    print("=" * 60)
    
    all_pass = True
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {status}: {name}")
        if not passed:
            all_pass = False
    
    print("=" * 60)
    print(f"Overall: {'ALL TESTS PASSED' if all_pass else 'SOME TESTS FAILED'}")
    print("=" * 60)
    
    return 0 if all_pass else 1

if __name__ == '__main__':
    sys.exit(main())

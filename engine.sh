#!/usr/bin/env bash
# ============================================
# FoundryVTT AI Gamemaster Engine - lifecycle
#
#   ./engine.sh status     show whether the engine is running and what it reports
#   ./engine.sh start      start the engine in the background
#   ./engine.sh stop       stop it (SIGTERM, waits up to 30 s)
#   ./engine.sh restart    stop, then start
#
# The engine is identified by what listens on its admin port, and only counts as ours when that
# process is `main.py` running from ai-engine/. Other listeners (ComfyUI, the Foundry server) are
# never touched.
#
# Environment:
#   ADMIN_PORT   port the engine listens on (default 18080, as in ai-engine/config.py)
#   PYTHON       interpreter to run the engine with (default ai-engine/venv/bin/python)
#
# The engine's own log is ai-engine/ai-gm.log. Start-up output goes to ai-engine/engine.out.
# ============================================
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENGINE_DIR="$SCRIPT_DIR/ai-engine"
PORT="${ADMIN_PORT:-18080}"
PYTHON="${PYTHON:-$ENGINE_DIR/venv/bin/python}"
OUT="$ENGINE_DIR/engine.out"
STOP_WAIT=30
START_WAIT=60

say() { printf '%s\n' "$*"; }
die() { printf 'engine: %s\n' "$*" >&2; exit "${2:-1}"; }

# PID of the process listening on the engine's port, or nothing.
listener_pid() {
    lsof -nP -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | head -n 1
}

# True when $1 is the engine: main.py run from ai-engine/.
is_engine() {
    local pid="$1" cmd cwd
    cmd="$(ps -o command= -p "$pid" 2>/dev/null)"
    [[ "$cmd" == *"main.py"* ]] || return 1
    cwd="$(lsof -nP -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1)"
    [[ "$cwd" == "$ENGINE_DIR" ]]
}

# The engine's pid, or empty. Fails when the port is held by something else.
engine_pid() {
    local pid
    pid="$(listener_pid)"
    [[ -z "$pid" ]] && return 0
    if is_engine "$pid"; then
        printf '%s\n' "$pid"
        return 0
    fi
    die "port $PORT is held by pid $pid, which is not the engine. Leaving it alone." 2
}

health() {
    curl -s -m 3 "http://localhost:$PORT/health" 2>/dev/null
}

cmd_status() {
    local pid
    pid="$(engine_pid)"
    if [[ -z "$pid" ]]; then
        say "engine: not running (nothing listening on port $PORT)"
        return 1
    fi
    say "engine: running, pid $pid, port $PORT"
    local h
    h="$(health)"
    if [[ -z "$h" ]]; then
        say "health: no answer yet (still starting, or hung)"
        return 1
    fi
    say "health: $h"
    local status
    status="$(curl -s -m 3 "http://localhost:$PORT/api/status" 2>/dev/null)"
    if [[ -n "$status" ]]; then
        "$PYTHON" - "$status" <<'PY' 2>/dev/null || say "status: $status"
import json, sys
d = json.loads(sys.argv[1])
keys = ("connected", "ai_running", "campaign", "session", "scene")
print("status: " + ", ".join(f"{k}={d.get(k)}" for k in keys))
PY
    fi
    return 0
}

cmd_start() {
    local pid
    pid="$(engine_pid)"
    if [[ -n "$pid" ]]; then
        say "engine: already running, pid $pid"
        say "admin UI: http://localhost:$PORT/admin"
        return 0
    fi
    [[ -x "$PYTHON" ]] || die "no interpreter at $PYTHON. Set PYTHON, or run ./run.sh first." 3
    "$PYTHON" -c "import playwright" 2>/dev/null \
        || die "$PYTHON has no playwright. Use the venv that does, or set PYTHON." 3

    # Start-up output is appended to engine.out. Show the log lines from this start as they appear.
    local shown=0
    [[ -f "$OUT" ]] && shown="$(wc -l < "$OUT" | tr -d ' ')"
    ( cd "$ENGINE_DIR" && nohup "$PYTHON" main.py >>"$OUT" 2>&1 & )
    say "engine: starting…"

    local waited=0 now
    while (( waited < START_WAIT )); do
        now="$(wc -l < "$OUT" 2>/dev/null | tr -d ' ')"
        now="${now:-0}"
        if (( now > shown )); then
            sed -n "$((shown + 1)),${now}p" "$OUT" | grep -E '\[(INFO|WARNING|ERROR)\]' | cut -c1-160 | sed 's/^/  | /'
            shown=$now
        fi
        if [[ -n "$(health)" ]]; then
            print_ready
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done
    say "engine: no answer on port $PORT after ${START_WAIT}s. Last lines of ai-engine/engine.out:" >&2
    tail -n 15 "$OUT" >&2
    exit 4
}

# The final state after a start: where to go, and what the engine is connected to.
print_ready() {
    local pid
    pid="$(listener_pid)"
    say ""
    say "engine: running, pid ${pid:-?}"
    say "admin UI: http://localhost:$PORT/admin"
    say "API:      http://localhost:$PORT/api"
    say "log:      ai-engine/ai-gm.log"
    say "Foundry:  not connected yet. Deploy and Start connect it on demand."
}

cmd_stop() {
    local pid
    pid="$(engine_pid)"
    if [[ -z "$pid" ]]; then
        say "engine: not running"
        return 0
    fi
    kill -TERM "$pid" 2>/dev/null || true
    local waited=0
    while (( waited < STOP_WAIT )); do
        if ! kill -0 "$pid" 2>/dev/null; then
            say "engine: stopped (pid $pid)"
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done
    die "pid $pid did not exit within ${STOP_WAIT}s after SIGTERM. Check ai-engine/ai-gm.log before forcing it." 5
}

cmd_restart() {
    cmd_stop || return $?
    # Wait for the port to be free before starting, so the new engine doesn't see the old one.
    local waited=0
    while [[ -n "$(listener_pid)" ]] && (( waited < 10 )); do
        sleep 1
        waited=$((waited + 1))
    done
    cmd_start
}

case "${1:-}" in
    status)  cmd_status ;;
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    restart) cmd_restart ;;
    *)
        say "usage: $(basename "$0") {status|start|stop|restart}"
        exit 64
        ;;
esac

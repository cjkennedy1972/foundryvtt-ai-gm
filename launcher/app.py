"""
AI GM — macOS menu bar app.

Manages the FoundryVTT AI GM engine as a background process and provides
start / stop / restart controls plus a filtered status log window.
"""

import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import rumps

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
ENGINE_DIR = PROJECT_ROOT / "ai-engine"
VENV_PYTHON = ENGINE_DIR / "venv" / "bin" / "python"
MAIN_PY = ENGINE_DIR / "main.py"
LOG_VIEWER = Path(__file__).parent / "log_viewer.py"
ADMIN_PORT = 18080
ADMIN_URL = f"http://localhost:{ADMIN_PORT}"

# Engine writes ai-gm.log relative to CWD (ENGINE_DIR when started here)
_LOG_CANDIDATES = [ENGINE_DIR / "ai-gm.log", PROJECT_ROOT / "ai-gm.log"]


def _active_log() -> Path:
    existing = [p for p in _LOG_CANDIDATES if p.exists()]
    if not existing:
        return _LOG_CANDIDATES[0]
    return max(existing, key=lambda p: p.stat().st_mtime)


class AIGMApp(rumps.App):
    def __init__(self):
        super().__init__(title="🎲", quit_button=None)
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._viewer: subprocess.Popen | None = None

        self._status_item = rumps.MenuItem("○ Engine Stopped")
        self._status_item.set_callback(None)

        self._start_item = rumps.MenuItem("Start", callback=self.on_start)
        self._stop_item = rumps.MenuItem("Stop", callback=self.on_stop)
        self._restart_item = rumps.MenuItem("Restart", callback=self.on_restart)

        self.menu = [
            self._status_item,
            None,
            rumps.MenuItem("Open Admin Panel", callback=self.on_admin),
            None,
            self._start_item,
            self._stop_item,
            self._restart_item,
            None,
            rumps.MenuItem("Show Logs", callback=self.on_logs),
            None,
            rumps.MenuItem("Quit", callback=self.on_quit),
        ]

        self._timer = rumps.Timer(self._poll, 2)
        self._timer.start()

    # ── process helpers ───────────────────────────────────────────────────────

    def _owns_engine(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    @staticmethod
    def _port_busy() -> bool:
        """Whether anything is listening on the engine's admin port.

        The Popen handle alone is not enough: a relaunched launcher (or a manual
        ./start.sh) leaves an engine we no longer track, and spawning a second
        one just fails to bind.
        """
        with socket.socket() as s:
            s.settimeout(0.25)
            return s.connect_ex(("127.0.0.1", ADMIN_PORT)) == 0

    def _is_running(self) -> bool:
        return self._owns_engine() or self._port_busy()

    def _start_engine(self):
        with self._lock:
            if self._proc and self._proc.poll() is None:
                return
            self._proc = subprocess.Popen(
                [str(VENV_PYTHON), str(MAIN_PY)],
                cwd=str(ENGINE_DIR),
            )

    def _stop_engine(self, timeout: int = 30):
        # Longer than the relay's 20s SIGTERM drain, so a slow-but-working
        # shutdown is not SIGKILLed (which orphans the relay child).
        with self._lock:
            if not self._proc or self._proc.poll() is not None:
                self._proc = None
                return
            self._proc.terminate()
            try:
                self._proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
            self._proc = None

    # ── status poll ───────────────────────────────────────────────────────────

    def _poll(self, _):
        if self._is_running():
            self._status_item.title = "● Engine Running"
        else:
            self._status_item.title = "○ Engine Stopped"
            with self._lock:
                if self._proc is not None and self._proc.poll() is not None:
                    self._proc = None

    # ── menu callbacks ────────────────────────────────────────────────────────

    def on_start(self, _):
        if self._owns_engine():
            rumps.notification("AI GM", "", "Engine is already running.")
            return
        if self._port_busy():
            rumps.notification(
                "AI GM", "",
                f"Port {ADMIN_PORT} is in use by an engine this launcher does "
                f"not own. Run: lsof -ti:{ADMIN_PORT} | xargs kill",
            )
            return
        self._start_engine()
        rumps.notification("AI GM", "", "Engine starting…")

    def on_stop(self, _):
        if not self._owns_engine():
            if self._port_busy():
                rumps.notification(
                    "AI GM", "",
                    f"An engine this launcher does not own holds port "
                    f"{ADMIN_PORT}. Run: lsof -ti:{ADMIN_PORT} | xargs kill",
                )
            else:
                rumps.notification("AI GM", "", "Engine is not running.")
            return
        threading.Thread(target=self._stop_engine, daemon=True).start()
        rumps.notification("AI GM", "", "Engine stopping…")

    def on_restart(self, _):
        def _do():
            self._stop_engine()
            time.sleep(1)
            self._start_engine()
        threading.Thread(target=_do, daemon=True).start()
        rumps.notification("AI GM", "", "Restarting engine…")

    def on_admin(self, _):
        subprocess.run(["open", ADMIN_URL])

    def on_logs(self, _):
        # Close stale viewer if it exited
        if self._viewer and self._viewer.poll() is not None:
            self._viewer = None
        if self._viewer is None:
            log_path = str(_active_log())
            self._viewer = subprocess.Popen(
                [str(VENV_PYTHON), str(LOG_VIEWER), log_path]
            )

    def on_quit(self, _):
        if self._owns_engine():
            resp = rumps.alert(
                title="Quit AI GM",
                message="The AI GM engine is running. Stop it before quitting?",
                ok="Stop & Quit",
                cancel="Leave Running",
            )
            if resp == 1:
                self._stop_engine()
        rumps.quit_application()


if __name__ == "__main__":
    AIGMApp().run()

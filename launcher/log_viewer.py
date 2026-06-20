"""
Standalone filtered log viewer — launched as a subprocess by the menu bar app
so it runs in its own process and avoids AppKit/tkinter main-thread conflicts.

Usage: python log_viewer.py <log_file_path>
"""

import sys
import tkinter as tk
from pathlib import Path

# Accept log path from command line (passed by app.py)
_LOG_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else None

# Add launcher dir to path so log_filter is importable
sys.path.insert(0, str(Path(__file__).parent))
from log_filter import is_safe_line, log_parts

_BG = "#1a1a2e"
_FG = "#c8c8d0"
_COLORS = {
    "INFO":    "#6a9fb5",
    "WARNING": "#e5c07b",
    "ERROR":   "#e06c75",
    "CRITICAL":"#ff5c5c",
    "DEBUG":   "#555566",
}
_REFRESH_MS = 3000


def _load_filtered(path: Path, max_lines: int = 300) -> list[str]:
    if not path or not path.exists():
        return []
    with open(path, encoding="utf-8", errors="replace") as f:
        return [l for l in f.readlines() if is_safe_line(l)][-max_lines:]


def main():
    root = tk.Tk()
    root.title("AI GM — Status Log")
    root.geometry("760x420")
    root.configure(bg=_BG)
    root.resizable(True, True)

    frame = tk.Frame(root, bg=_BG)
    frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    text = tk.Text(
        frame,
        bg=_BG, fg=_FG,
        font=("Menlo", 11),
        wrap=tk.WORD,
        state=tk.DISABLED,
        relief=tk.FLAT,
        borderwidth=0,
        selectbackground="#2d2d44",
    )
    scrollbar = tk.Scrollbar(frame, command=text.yview, bg=_BG, troughcolor="#2a2a3d")
    text.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    for level, color in _COLORS.items():
        text.tag_configure(level, foreground=color)
    text.tag_configure("DIM", foreground="#444466")

    def refresh():
        lines = _load_filtered(_LOG_PATH)
        text.config(state=tk.NORMAL)
        text.delete("1.0", tk.END)
        for line in lines:
            parts = log_parts(line)
            tag = _COLORS.get(parts[0], "INFO") if parts else "INFO"
            text.insert(tk.END, line, parts[0] if parts else None)
        text.config(state=tk.DISABLED)
        text.see(tk.END)
        root.after(_REFRESH_MS, refresh)

    refresh()
    root.mainloop()


if __name__ == "__main__":
    main()

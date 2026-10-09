"""Minimize only the Terminal window that launched this JARVIS process."""
from __future__ import annotations

import re
import subprocess


SCRIPT = '''on run argv
    set targetTTY to item 1 of argv
    tell application "Terminal"
        repeat with w in windows
            try
                repeat with t in tabs of w
                    if (tty of t) is targetTTY then
                        set miniaturized of w to true
                        return "minimized"
                    end if
                end repeat
            end try
        end repeat
    end tell
    return "not_found"
end run'''


def minimize_launch_terminal(target_tty: str) -> bool:
    if not re.fullmatch(r"/dev/ttys[0-9a-zA-Z]+", target_tty or ""):
        return False
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", SCRIPT, target_tty],
            capture_output=True, text=True, timeout=6, check=False,
        )
        return result.returncode == 0 and result.stdout.strip() == "minimized"
    except (OSError, subprocess.TimeoutExpired):
        return False

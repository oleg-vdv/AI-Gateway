"""Уведомления пользователю: десктоп-уведомление (best-effort) + лог.

notify-send (Linux), osascript (macOS), PowerShell toast (Windows).
Ошибки уведомлений не должны ломать защитный контур — всё best-effort.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys

logger = logging.getLogger("aigate.agent")


def notify(title: str, message: str) -> None:
    logger.info("%s: %s", title, message)
    try:
        if sys.platform == "darwin":
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'display notification "{message}" with title "{title}"',
                ],
                timeout=5,
                check=False,
                capture_output=True,
            )
        elif sys.platform.startswith("win"):
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"[System.Windows.Forms.MessageBox] | Out-Null; "
                    f"Write-Host '{title}: {message}'",
                ],
                timeout=5,
                check=False,
                capture_output=True,
            )
        elif shutil.which("notify-send"):
            subprocess.run(
                ["notify-send", "--app-name=AI-Gate", title, message],
                timeout=5,
                check=False,
                capture_output=True,
            )
    except Exception:
        pass  # уведомление — не критический путь

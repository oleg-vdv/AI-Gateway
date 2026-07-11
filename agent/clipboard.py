"""Кроссплатформенный доступ к буферу обмена без внешних зависимостей.

Бэкенды: macOS — pbpaste/pbcopy; Windows — PowerShell
Get-Clipboard/Set-Clipboard; Linux — wl-paste/wl-copy (Wayland),
xclip или xsel (X11). Для тестов — FakeClipboard.
"""

from __future__ import annotations

import shutil
import subprocess
import sys


class ClipboardError(Exception):
    pass


class ClipboardBackend:
    """Интерфейс бэкенда буфера обмена."""

    def read(self) -> str:
        raise NotImplementedError

    def write(self, text: str) -> None:
        raise NotImplementedError


class CommandClipboard(ClipboardBackend):
    """Бэкенд на внешних командах (pbpaste/xclip/wl-paste/powershell)."""

    def __init__(self, read_cmd: list[str], write_cmd: list[str]):
        self._read_cmd = read_cmd
        self._write_cmd = write_cmd

    def read(self) -> str:
        try:
            out = subprocess.run(
                self._read_cmd, capture_output=True, timeout=5, check=False
            )
            return out.stdout.decode("utf-8", errors="replace")
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ClipboardError(f"Не удалось прочитать буфер: {e}") from e

    def write(self, text: str) -> None:
        try:
            subprocess.run(
                self._write_cmd, input=text.encode(), timeout=5, check=True
            )
        except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
            raise ClipboardError(f"Не удалось записать буфер: {e}") from e


class FakeClipboard(ClipboardBackend):
    """Буфер в памяти — для тестов."""

    def __init__(self, initial: str = ""):
        self.content = initial
        self.history: list[str] = []

    def read(self) -> str:
        return self.content

    def write(self, text: str) -> None:
        self.content = text
        self.history.append(text)


def detect_backend() -> ClipboardBackend:
    """Выбрать бэкенд под текущую платформу."""
    platform = sys.platform
    if platform == "darwin":
        return CommandClipboard(["pbpaste"], ["pbcopy"])
    if platform.startswith("win"):
        ps = ["powershell", "-NoProfile", "-Command"]
        return CommandClipboard(
            ps + ["Get-Clipboard -Raw"], ps + ["$input | Set-Clipboard"]
        )
    # Linux / *BSD
    if shutil.which("wl-paste") and shutil.which("wl-copy"):
        return CommandClipboard(["wl-paste", "--no-newline"], ["wl-copy"])
    if shutil.which("xclip"):
        return CommandClipboard(
            ["xclip", "-selection", "clipboard", "-o"],
            ["xclip", "-selection", "clipboard"],
        )
    if shutil.which("xsel"):
        return CommandClipboard(
            ["xsel", "--clipboard", "--output"], ["xsel", "--clipboard", "--input"]
        )
    raise ClipboardError(
        "Не найден инструмент буфера обмена: установите xclip, xsel или wl-clipboard"
    )

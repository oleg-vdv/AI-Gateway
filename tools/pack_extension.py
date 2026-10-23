"""Сборка архивов расширения для магазинов.

    python -I tools/pack_extension.py

Результат в dist/:
  aigate-dlp-chrome-<версия>.zip   — Chrome Web Store и Edge Add-ons: из
      манифеста убраны поля Firefox (background.scripts, browser_specific_settings),
      на которые Chrome выдаёт предупреждения при проверке;
  aigate-dlp-firefox-<версия>.zip  — addons.mozilla.org, манифест как есть.
CHANGELOG.md в архивы не попадает.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "extension"
DIST = ROOT / "dist"
SKIP = {"CHANGELOG.md"}


def _files() -> list[Path]:
    return sorted(
        p for p in EXT.rglob("*") if p.is_file() and p.name not in SKIP and p.name != "manifest.json"
    )


def _write(zip_path: Path, manifest: dict) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for path in _files():
            zf.write(path, path.relative_to(EXT).as_posix())


def main() -> None:
    manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    DIST.mkdir(exist_ok=True)

    chrome = json.loads(json.dumps(manifest))
    chrome["background"].pop("scripts", None)
    chrome.pop("browser_specific_settings", None)
    chrome_zip = DIST / f"aigate-dlp-chrome-{version}.zip"
    _write(chrome_zip, chrome)

    firefox_zip = DIST / f"aigate-dlp-firefox-{version}.zip"
    _write(firefox_zip, manifest)

    for z in (chrome_zip, firefox_zip):
        print(f"{z}  {z.stat().st_size // 1024} КБ")


if __name__ == "__main__":
    main()

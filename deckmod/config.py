"""Filesystem locations used by DeckMod."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

HOME = Path.home()


def data_dir() -> Path:
    if os.environ.get("DECKMOD_HOME"):
        base = Path(os.environ["DECKMOD_HOME"])
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local" / "share") / "deckmod"
    base.mkdir(parents=True, exist_ok=True)
    return base


def game_dir(game_id: str) -> Path:
    return data_dir() / "games" / safe_name(game_id)


def mods_dir(game_id: str) -> Path:
    return game_dir(game_id) / "mods"


def backups_dir(game_id: str) -> Path:
    return game_dir(game_id) / "backups"


def downloads_dir() -> Path:
    d = data_dir() / "downloads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_name(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-.")
    return text or "unnamed"


def read_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)

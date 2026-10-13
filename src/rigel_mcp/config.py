"""Paths, read from environment variables (mcp 2.x no longer reads MCP_* or .env for us)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HUB = "D:/02_Learning_Knowledge"


def env_path(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser() if raw else None


@dataclass(frozen=True)
class Config:
    home: Path  # state: study.db, protocol.md, exports/, activity.jsonl, library.json
    library: Path  # classic mode: lesson files laid out as <library>/<course>/<lesson>/<files>
    notes: Path  # where save_note writes (point it at an Obsidian vault if you like)
    map_path: Path | None = None  # explicit library.json; else env RIGEL_LIBRARY_MAP, else <home>/library.json
    hub: Path | None = None  # explicit hub root; else env RIGEL_HUB, else the map's "hub", else DEFAULT_HUB

    @property
    def db_path(self) -> Path:
        return self.home / "study.db"

    @property
    def protocol_path(self) -> Path:
        return self.home / "protocol.md"

    @property
    def exports(self) -> Path:
        return self.home / "exports"

    @property
    def activity_path(self) -> Path:
        return self.home / "activity.jsonl"

    @property
    def library_map(self) -> Path:
        """Where the library map lives. The file may not exist: then the classic folder mode is used."""
        return self.map_path or env_path("RIGEL_LIBRARY_MAP") or self.home / "library.json"

    def hub_dir(self, map_hub: str | None = None) -> Path:
        """Hub root: explicit setting, then env RIGEL_HUB, then the map's `hub`, then the default."""
        if self.hub is not None:
            return self.hub
        from_env = env_path("RIGEL_HUB")
        if from_env is not None:
            return from_env
        return Path(map_hub).expanduser() if map_hub else Path(DEFAULT_HUB)


def load_config() -> Config:
    home = Path(os.environ.get("RIGEL_HOME", "~/.rigel")).expanduser()
    library = Path(os.environ.get("RIGEL_LIBRARY", "~/rigel-library")).expanduser()
    notes = Path(os.environ.get("RIGEL_NOTES", str(home / "notes"))).expanduser()
    return Config(
        home=home,
        library=library,
        notes=notes,
        map_path=env_path("RIGEL_LIBRARY_MAP"),
        hub=env_path("RIGEL_HUB"),
    )

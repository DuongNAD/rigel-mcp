"""Thin wrapper around the `smart-drive search` CLI (the learner's SSD index).

The hub holds 500k+ files on an exFAT SSD, so lesson files are found through the index instead of a
recursive walk. Everything here is read-only. The command is only a source of candidate paths: callers
re-check containment, exclusion rules and file existence themselves.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .libmap import is_within, norm_path

LIMIT = 5000  # results requested per query
TIMEOUT = 30  # seconds per query
DEFAULT_ROOT = "D:/"
OFF_VALUES = {"off", "false", "0", "no"}


class SmartDriveError(RuntimeError):
    """The index could not be queried (command missing, failed, timed out or printed no JSON)."""


@dataclass(frozen=True)
class IndexResult:
    files: list[Path]  # absolute paths, exactly as the index reported them (drive root + result path)
    raw_count: int  # entries the index returned before any filtering
    hit_limit: bool  # raw_count reached the requested limit: the list may be incomplete


def parse_cmd(raw: str) -> list[str]:
    """Turn RIGEL_SMARTDRIVE_CMD into an argv. A bare existing path (even with spaces) is used as is;
    a .py script is run with this interpreter."""
    raw = raw.strip()
    if not raw:
        return []
    if os.path.isfile(raw):
        parts = [raw]
    else:
        try:
            tokens = shlex.split(raw, posix=False)
        except ValueError:
            tokens = [raw]
        parts = [t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'" else t for t in tokens]
    if parts and parts[0].lower().endswith(".py"):
        parts = [sys.executable, "-B", *parts]
    return parts


@dataclass(frozen=True)
class SmartDrive:
    cmd: list[str]
    root: Path  # the drive root the index paths are relative to

    @classmethod
    def from_env(cls) -> "SmartDrive | None":
        """None when SmartDrive is switched off (RIGEL_SMARTDRIVE=off|false|0|no) or not installed."""
        if os.environ.get("RIGEL_SMARTDRIVE", "").strip().lower() in OFF_VALUES:
            return None
        cmd = parse_cmd(os.environ.get("RIGEL_SMARTDRIVE_CMD", ""))
        if not cmd:
            exe = shutil.which("smart-drive")
            if not exe:
                return None
            cmd = [exe]
        root = norm_path(os.environ.get("RIGEL_SMARTDRIVE_ROOT", "").strip() or DEFAULT_ROOT)
        return cls(cmd, root)

    def covers(self, folder: Path) -> bool:
        """The folder is below the index root (same drive), so the index can know about its files."""
        return folder != self.root and is_within(folder, self.root)

    def query(self, folder: Path, exts: Sequence[str], limit: int | None = None) -> IndexResult:
        limit = limit or LIMIT
        rel = folder.relative_to(self.root).as_posix()
        args = [*self.cmd, "search", "--dir", rel, "--ext", ",".join(exts), "--json", "--limit", str(limit)]
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            # stdin must be detached: this process speaks MCP over stdio and a child must never read it.
            proc = subprocess.run(
                args, capture_output=True, stdin=subprocess.DEVNULL, timeout=TIMEOUT, env=env, creationflags=flags
            )
        except subprocess.TimeoutExpired:
            raise SmartDriveError(f"smart-drive không trả lời sau {TIMEOUT}s") from None
        except OSError as e:
            raise SmartDriveError(f"không chạy được lệnh smart-drive: {e}") from None
        if proc.returncode != 0:
            tail = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()[-1:] or [""]
            raise SmartDriveError(f"smart-drive thoát với mã {proc.returncode}: {tail[0][:160]}")
        rows = _parse_rows(proc.stdout.decode("utf-8", errors="replace"))
        files: list[Path] = []
        for row in rows:
            rel_path = row.get("path") if isinstance(row, dict) else None
            if isinstance(rel_path, str) and rel_path.strip():
                files.append(norm_path(str(self.root / rel_path.replace("\\", "/").lstrip("/"))))
        return IndexResult(files, len(rows), len(rows) >= limit)


def _parse_rows(text: str) -> list:
    text = text.lstrip("\ufeff").strip()
    for candidate in (text, text[text.find("[") : text.rfind("]") + 1] if "[" in text else ""):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            data = data.get("results", [])
        if isinstance(data, list):
            return data
    raise SmartDriveError("smart-drive không in ra JSON hợp lệ")

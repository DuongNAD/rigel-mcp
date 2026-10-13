"""Activity log: one JSON line per Rigel tool call, as evidence for Sirius (the learner's execution tracker).

Line shape:
  {"v":1,"t":"2026-10-13T09:30:00+07:00","tool":"search_material","course":"GCI","lesson":"02_NumPy",
   "subject":"GCI_World_2026_September","coverage":"strong","hits":4}
`coverage` appears for searches only and `cards` for flashcard exports only. The log records THAT and WHERE the
learner studied, never WHAT: no query, note or card text is ever written. A failure to log never breaks a tool.
"""

from __future__ import annotations

import json
import sys
import threading
from datetime import datetime
from pathlib import Path

LOGGED_TOOLS = (
    "search_material",
    "read_chunks",
    "lesson_outline",
    "sample_material",
    "export_flashcards",
    "save_note",
    "scope_for_path",
)

_LOCK = threading.Lock()
_warned = False


def record(
    path: Path,
    tool: str,
    *,
    course: str | None = None,
    lesson: str | None = None,
    subject: str | None = None,
    coverage: str | None = None,
    hits: int = 0,
    cards: int | None = None,
) -> None:
    global _warned
    try:
        entry: dict = {
            "v": 1,
            "t": datetime.now().astimezone().isoformat(timespec="seconds"),
            "tool": tool,
            "course": course,
            "lesson": lesson,
            "subject": subject,
        }
        if coverage is not None:
            entry["coverage"] = coverage
        entry["hits"] = int(hits)
        if cards is not None:
            entry["cards"] = int(cards)
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                f.write(line)
    except Exception as e:  # noqa: BLE001 - logging must never break a tool
        if not _warned:
            _warned = True
            print(f"rigel: không ghi được {path}: {e}", file=sys.stderr)

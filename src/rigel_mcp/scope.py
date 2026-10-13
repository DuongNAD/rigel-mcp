"""Work out which course and lesson a file or folder belongs to (used by scope_for_path).

Coding agents usually know the path of the file the learner is editing; mapping it to a scope makes
grounding automatic: the next search is restricted to that lesson without anyone naming it.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .discover import is_hidden, skip_name
from .ingest import GENERAL_LESSON
from .libmap import LibMap, is_within, norm_path, path_key
from .store import Store
from .text import fold

_WSL = re.compile(r"^/mnt/([A-Za-z])(?:/(.*))?$")
_GIT_BASH = re.compile(r"^/([A-Za-z])(?:/(.*))?$")


@dataclass(frozen=True)
class PathMatch:
    course: str
    lesson: str | None  # None: the path is inside the course but not inside a lesson
    via: str  # why it matched (Vietnamese, shown to the learner)


def windows_style(text: str) -> str:
    """'/d/x' (Git Bash) and '/mnt/d/x' (WSL) become 'D:/x'. Anything else is returned unchanged."""
    m = _WSL.match(text) or _GIT_BASH.match(text)
    return f"{m.group(1).upper()}:/{m.group(2) or ''}" if m else text


def normalize_input(text: str, windows: bool | None = None) -> Path | None:
    """The path the agent sent, as a normalised absolute Path (None when blank)."""
    s = (text or "").strip().strip("\"'")
    if not s:
        return None
    if windows is None:
        windows = os.name == "nt"
    if windows:
        s = windows_style(s)
    return Path(os.path.normpath(os.path.abspath(os.path.expanduser(s))))


def exact_name(name: str, options: Iterable[str]) -> str | None:
    """The option equal to `name` ignoring case and diacritics (never a partial match)."""
    wanted = fold(name).strip()
    return next((o for o in options if fold(o) == wanted), None)


def _is_dir(p: Path) -> bool:
    try:
        return p.is_dir()
    except OSError:
        return False


def _from_map(p: Path, libmap: LibMap, hub: Path) -> PathMatch | None:
    best: tuple[int, int, PathMatch] | None = None

    def offer(depth: int, rank: int, match: PathMatch) -> None:
        nonlocal best
        if best is None or (depth, rank) > (best[0], best[1]):
            best = (depth, rank, match)  # the deepest container wins; a root beats its subject folder

    for course in libmap.courses:
        for root in course.roots:
            depth = len(root.path.parts)
            if root.lessons == "file":
                if p == root.path:
                    offer(depth, 2, PathMatch(course.name, root.lesson, f'file "{root.path.name}"'))
                continue
            if not is_within(p, root.path):
                continue
            if root.lessons == "single":
                offer(depth, 2, PathMatch(course.name, root.lesson, f'thư mục "{root.path.as_posix()}"'))
                continue
            rel = p.relative_to(root.path).parts
            lesson = None
            if rel:
                if len(rel) >= 2 or _is_dir(p):
                    lesson = None if skip_name(rel[0], course.exclude) else rel[0]
                else:
                    lesson = GENERAL_LESSON  # a loose file directly in the root
            offer(depth, 2, PathMatch(course.name, lesson, f'thư mục gốc "{root.path.as_posix()}"'))
        if course.subject:
            subject_dir = norm_path(str(hub / course.subject))
            if is_within(p, subject_dir):
                offer(len(subject_dir.parts), 1, PathMatch(course.name, None, f'thư mục môn "{subject_dir.as_posix()}"'))
    return best[2] if best else None


def _from_library(p: Path, library: Path, store: Store) -> PathMatch | None:
    root = norm_path(str(library))
    if p == root or not is_within(p, root):
        return None
    rel = p.relative_to(root).parts
    if is_hidden(rel[0]):
        return None
    course = exact_name(rel[0], store.courses())
    if course is None:
        return None
    if len(rel) == 1:
        lesson = None
    elif len(rel) == 2 and not _is_dir(p):
        lesson = GENERAL_LESSON
    else:
        lesson = rel[1]
    return PathMatch(course, lesson, f'thư viện "{root.as_posix()}"')


def _from_docs(p: Path, store: Store) -> PathMatch | None:
    key = path_key(p)
    for row in store.docs_with_path():
        if path_key(row["abs_path"]) == key:
            return PathMatch(row["course"], row["lesson"], "file đã nạp")
    return None


def resolve_path(p: Path, *, libmap: LibMap | None, hub: Path, library: Path, store: Store) -> PathMatch | None:
    """Course (and lesson) containing `p`, or None. Map mode looks at the map's roots and subject folders,
    classic mode at <library>/<course>/<lesson>; a file added by add_material matches by its exact path."""
    found = _from_map(p, libmap, hub) if libmap is not None else _from_library(p, library, store)
    return found or _from_docs(p, store)

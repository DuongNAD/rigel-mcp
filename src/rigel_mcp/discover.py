"""Find the lesson files named by library.json without ever walking the hub recursively.

For every lesson folder the candidate files are the union of
  (a) the SmartDrive index (one query per root, split by lesson; per lesson when a root is too big), and
  (b) a NON-recursive listing of the lesson folder itself, which catches new top-level files the index
      has not seen yet.
Without SmartDrive (not installed, switched off, failed, or the folder is on another drive) a bounded
`os.scandir` walk is used instead: at most WALK_MAX_DEPTH levels, WALK_MAX_FILES files per lesson,
excludes applied before descending, and symlinks and junctions are never followed.
"""

from __future__ import annotations

import fnmatch
import os
import stat
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

from . import smartdrive
from .ingest import GENERAL_LESSON, KIND_BY_SUFFIX
from .libmap import CourseMap, LibMap, RootSpec, is_within, path_key
from .smartdrive import SmartDrive, SmartDriveError

WALK_MAX_DEPTH = 4
WALK_MAX_FILES = 3000
SUPPORTED_EXTS = sorted(s.lstrip(".") for s in KIND_BY_SUFFIX)
_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT: symlinks, junctions, cloud placeholders


# ---- name rules ----------------------------------------------------------------------------


def is_hidden(name: str) -> bool:
    return name.startswith((".", "~$"))


def is_excluded(name: str, patterns) -> bool:
    low = name.casefold()
    return any(fnmatch.fnmatchcase(low, pat.casefold()) for pat in patterns)


def skip_name(name: str, patterns) -> bool:
    """Hidden names are always skipped; the rest follow the map's exclude patterns (names or globs)."""
    return is_hidden(name) or is_excluded(name, patterns)


def is_supported(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in KIND_BY_SUFFIX


# ---- one non-recursive directory listing ---------------------------------------------------


@dataclass(frozen=True)
class Entry:
    name: str
    path: Path
    is_dir: bool
    is_file: bool
    link: bool  # symlink, junction or other reparse point: never followed


def _is_link(entry: os.DirEntry) -> bool:
    try:
        if entry.is_symlink():
            return True
        return bool(getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & _REPARSE_POINT)
    except OSError:
        return True


def scan(folder: Path) -> list[Entry] | None:
    """Entries of ONE folder, sorted by name; None when it cannot be read."""
    try:
        out: list[Entry] = []
        with os.scandir(folder) as it:
            for e in it:
                try:
                    is_dir, is_file = e.is_dir(follow_symlinks=False), e.is_file(follow_symlinks=False)
                except OSError:
                    is_dir = is_file = False
                out.append(Entry(e.name, Path(e.path), is_dir, is_file, _is_link(e)))
    except OSError:
        return None
    out.sort(key=lambda x: x.name.casefold())
    return out


# ---- results -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Item:
    course: str
    lesson: str
    rel: str  # path inside the lesson folder, forward slashes (the file name for "file" roots)
    path: Path
    size: int
    mtime: float
    via: str  # "smartdrive" | "listing" | "walk" | "file"


@dataclass
class Discovery:
    items: list[Item] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    protected: list[Path] = field(default_factory=list)  # unreachable sources: their docs must be kept
    counts: Counter = field(default_factory=Counter)  # via -> number of files
    index_roots: list[str] = field(default_factory=list)  # folders answered by SmartDrive


class _Run:
    def __init__(self, libmap: LibMap, sd: SmartDrive | None):
        self.libmap = libmap
        self.sd = sd
        self.rep = Discovery()
        self.max_bytes = int(libmap.max_file_mb * 1024 * 1024)
        self.oversize: list[str] = []

    # -- plumbing ----------------------------------------------------------------------------

    def protect(self, path: Path, message: str) -> None:
        self.rep.protected.append(path)
        self.rep.warnings.append(message)

    def add(self, course: str, lesson: str, rel: str, path: Path, via: str) -> None:
        try:
            st = path.stat()
        except OSError:
            return  # reported by a stale index, or removed in the meantime
        if not stat.S_ISREG(st.st_mode):
            return
        if st.st_size > self.max_bytes:
            self.oversize.append(f"{course}/{lesson}/{rel}")
            return
        self.rep.items.append(Item(course, lesson, rel, path, st.st_size, st.st_mtime, via))
        self.rep.counts[via] += 1

    def index(self, folder: Path) -> tuple[list[Path] | None, bool]:
        """(files, hit_limit) from SmartDrive, or (None, False) when the index cannot answer for this folder."""
        sd = self.sd
        if sd is None or not sd.covers(folder):
            return None, False
        try:
            res = sd.query(folder, SUPPORTED_EXTS)
        except SmartDriveError as e:
            self.rep.warnings.append(
                f"SmartDrive lỗi ({e}); chuyển sang duyệt thư mục có giới hạn (tối đa {WALK_MAX_DEPTH} cấp)."
            )
            self.sd = None
            return None, False
        label = folder.as_posix()
        if label not in self.rep.index_roots:
            self.rep.index_roots.append(label)
        return res.files, res.hit_limit

    def cap_warning(self, folder: Path) -> None:
        self.rep.warnings.append(
            f"{folder.as_posix()}: chỉ mục trả về đủ {smartdrive.LIMIT} kết quả nên có thể thiếu file "
            "(thu hẹp thư mục hoặc thêm exclude)."
        )

    def walk(self, folder: Path, top: list[Entry], patterns, label: str) -> list[Path]:
        out: list[Path] = []
        queue: deque = deque([(folder, 0, top)])
        too_deep = False
        while queue:
            current, depth, entries = queue.popleft()
            if entries is None:
                entries = scan(current)
                if entries is None:
                    continue
            for e in entries:
                if e.link or skip_name(e.name, patterns):
                    continue
                if e.is_dir:
                    if depth < WALK_MAX_DEPTH:
                        queue.append((e.path, depth + 1, None))
                    else:
                        too_deep = True
                elif e.is_file and is_supported(e.name):
                    if len(out) >= WALK_MAX_FILES:
                        self.rep.warnings.append(f"{label}: hơn {WALK_MAX_FILES} file, chỉ lấy {WALK_MAX_FILES} file đầu tiên")
                        return out
                    out.append(e.path)
        if too_deep:
            self.rep.warnings.append(f"{label}: có thư mục con sâu hơn {WALK_MAX_DEPTH} cấp chưa được duyệt")
        return out

    def collect(self, folder: Path, patterns, indexed: list[Path] | None, label: str) -> list[tuple[Path, str]] | None:
        """(path, via) for every supported file of one lesson folder; None when the folder is unreadable."""
        top = scan(folder)
        if top is None:
            self.protect(folder, f"{label}: không đọc được thư mục {folder.as_posix()} — giữ nguyên tài liệu đã nạp từ đó")
            return None
        found: dict[str, tuple[Path, str]] = {}
        if indexed is None:
            for p in self.walk(folder, top, patterns, label):
                found[path_key(p)] = (p, "walk")
        else:
            for p in indexed:
                if p != folder and is_within(p, folder):
                    parts = p.relative_to(folder).parts
                    if is_supported(parts[-1]) and not any(skip_name(part, patterns) for part in parts):
                        found[path_key(p)] = (p, "smartdrive")
            for e in top:  # top-level files the index may not know yet
                if e.is_file and not e.link and not skip_name(e.name, patterns) and is_supported(e.name):
                    found.setdefault(path_key(e.path), (e.path, "listing"))
        return sorted(found.values(), key=lambda t: t[0].relative_to(folder).as_posix().casefold())

    # -- the three kinds of root -------------------------------------------------------------

    def file_root(self, course: CourseMap, root: RootSpec) -> None:
        path = root.path
        try:
            ok = path.is_file()
        except OSError:
            ok = False
        if not ok:
            self.protect(path, f"{course.name}: không thấy file {path.as_posix()} — giữ nguyên tài liệu đã nạp từ đó")
        elif not is_supported(path.name):
            self.rep.warnings.append(f"{course.name}: {path.name} có định dạng chưa hỗ trợ nên bị bỏ qua")
        else:
            self.add(course.name, root.lesson or path.stem, path.name, path, "file")

    def single_root(self, course: CourseMap, root: RootSpec) -> None:
        lesson = root.lesson or root.path.name
        indexed, hit = self.index(root.path)
        if hit:
            self.cap_warning(root.path)
        found = self.collect(root.path, course.exclude, indexed, f"{course.name}/{lesson}")
        for path, via in found or []:
            self.add(course.name, lesson, path.relative_to(root.path).as_posix(), path, via)

    def subfolder_root(self, course: CourseMap, root: RootSpec) -> None:
        patterns = course.exclude
        entries = scan(root.path)
        if entries is None:
            self.protect(
                root.path,
                f"{course.name}: không đọc được thư mục {root.path.as_posix()} — giữ nguyên tài liệu đã nạp từ đó",
            )
            return
        for e in entries:  # loose files directly in the root form the general lesson
            if e.is_file and not e.link and not skip_name(e.name, patterns) and is_supported(e.name):
                self.add(course.name, GENERAL_LESSON, e.name, e.path, "listing")
        lessons = [e for e in entries if e.is_dir and not e.link and not skip_name(e.name, patterns)]

        # One index query for the whole root, split by lesson. If the root is so big that the index
        # answer is capped, fall back to one query per lesson folder so each lesson gets its own cap.
        partition: dict[str, list[Path]] | None = None
        everything, capped = self.index(root.path)
        if everything is not None and not capped:
            partition = defaultdict(list)
            for p in everything:
                if is_within(p, root.path):
                    parts = p.relative_to(root.path).parts
                    if len(parts) >= 2:
                        partition[parts[0].casefold()].append(p)

        for lesson_dir in lessons:
            if partition is not None:
                indexed = partition.get(lesson_dir.name.casefold(), [])
            else:
                indexed, hit = self.index(lesson_dir.path)
                if hit:
                    self.cap_warning(lesson_dir.path)
            found = self.collect(lesson_dir.path, patterns, indexed, f"{course.name}/{lesson_dir.name}")
            for path, via in found or []:
                self.add(course.name, lesson_dir.name, path.relative_to(lesson_dir.path).as_posix(), path, via)

    def finish(self) -> None:
        if self.oversize:
            shown = ", ".join(self.oversize[:5]) + (f" … (+{len(self.oversize) - 5})" if len(self.oversize) > 5 else "")
            self.rep.warnings.append(f"bỏ qua {len(self.oversize)} file lớn hơn {self.libmap.max_file_mb:g} MB: {shown}")


def discover(libmap: LibMap, sd: SmartDrive | None) -> Discovery:
    """Every file the map points at, with the lesson it belongs to. Never walks a drive recursively."""
    run = _Run(libmap, sd)
    for course in libmap.courses:
        for root in course.roots:
            if root.lessons == "file":
                run.file_root(course, root)
            elif root.lessons == "single":
                run.single_root(course, root)
            else:
                run.subfolder_root(course, root)
    run.finish()
    return run.rep

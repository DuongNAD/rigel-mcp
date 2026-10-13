"""library.json: the learner's map of where lesson materials live.

Instead of copying files into one library folder, the map points at the folders where the lessons
already are (for example the learning hub on the SSD). Every error is reported in Vietnamese with
the JSON path of the offending field, so the learner can fix the file without guessing.

Schema (v1):
    {"v": 1, "hub": "D:/hub", "exclude": [...], "maxFileMB": 40,
     "courses": [{"name": "GCI", "subject": "GCI_World_2026_September", "aliases": ["gci"],
                  "roots": [{"path": "D:/hub/GCI/03_Materials", "lessons": "subfolders"},
                            {"path": "D:/hub/GCI/ROADMAP.md", "lessons": "file", "lesson": "_lo_trinh"},
                            {"path": "D:/hub/GCI/notes", "lessons": "single", "lesson": "Bai_giang"}],
                  "exclude": ["dap_an"]}]}
"""

from __future__ import annotations

import difflib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .text import fold

DEFAULT_EXCLUDE = ("__pycache__", ".venv", "venv", "node_modules", ".git", ".ipynb_checkpoints")
DEFAULT_MAX_MB = 40.0
LESSON_MODES = ("subfolders", "single", "file")


# ---- path helpers --------------------------------------------------------------------------


def norm_path(text: str) -> Path:
    """expanduser + normpath, so `..` and mixed slashes never defeat containment checks."""
    return Path(os.path.normpath(os.path.expanduser(text)))


def path_key(p: Path | str) -> str:
    """Stable dictionary key for a path (case-insensitive on Windows)."""
    return os.path.normcase(os.path.normpath(str(p)))


def is_within(child: Path, parent: Path) -> bool:
    """True when `child` is `parent` or lies anywhere below it (component-wise, never string prefix)."""
    return child == parent or parent in child.parents


def norm_key(text: str) -> str:
    """Folded name for matching: no case, no diacritics, '_' '-' and spaces are the same separator."""
    return re.sub(r"[\s_\-]+", " ", fold(text)).strip()


# ---- the validated structures the rest of the code uses ------------------------------------


@dataclass(frozen=True)
class RootSpec:
    path: Path
    lessons: str  # "subfolders" | "single" | "file"
    lesson: str | None  # resolved lesson name for "single" and "file"; None for "subfolders"


@dataclass(frozen=True)
class CourseMap:
    name: str
    subject: str | None
    aliases: tuple[str, ...]
    roots: tuple[RootSpec, ...]
    exclude: tuple[str, ...]  # global patterns plus this course's own

    def keys(self) -> list[str]:
        return [k for k in (norm_key(t) for t in (self.name, self.subject or "", *self.aliases)) if k]


@dataclass(frozen=True)
class LibMap:
    source: Path
    hub: str | None
    max_file_mb: float
    exclude: tuple[str, ...]
    courses: tuple[CourseMap, ...]

    def match_course(self, text: str) -> list[CourseMap]:
        """Courses addressed by `text`: exact name/subject/alias first, else a unique-looking substring.

        Case, diacritics and the separators ' ', '_' and '-' are ignored. More than one result means the
        text is ambiguous; the caller decides what to tell the learner.
        """
        wanted = norm_key(text)
        if not wanted:
            return []
        exact = [c for c in self.courses if wanted in c.keys()]
        if exact:
            return exact
        return [c for c in self.courses if any(wanted in k for k in c.keys())]

    def course_named(self, name: str) -> CourseMap | None:
        wanted = norm_key(name)
        return next((c for c in self.courses if norm_key(c.name) == wanted), None)

    def subject_of(self, course: str | None) -> str | None:
        found = self.course_named(course) if course else None
        return found.subject if found else None


@dataclass(frozen=True)
class MapState:
    path: Path
    exists: bool
    map: LibMap | None = None
    errors: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return self.map is not None


class MapError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


# ---- pydantic schema -----------------------------------------------------------------------


class _Root(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1)
    lessons: Literal["subfolders", "single", "file"] = "subfolders"
    lesson: str | None = None


class _Course(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    subject: str | None = None
    aliases: list[str] = Field(default_factory=list)
    roots: list[_Root] = Field(min_length=1)
    exclude: list[str] = Field(default_factory=list)


class _Map(BaseModel):
    model_config = ConfigDict(extra="forbid")
    v: Literal[1]
    hub: str | None = None
    exclude: list[str] = Field(default_factory=lambda: list(DEFAULT_EXCLUDE))
    maxFileMB: float = Field(default=DEFAULT_MAX_MB, gt=0)
    courses: list[_Course] = Field(min_length=1)


_KNOWN_KEYS = ("v", "hub", "exclude", "maxFileMB", "courses", "name", "subject", "aliases", "roots", "path", "lessons", "lesson")
_OPTIONAL_TEXT = ("hub", "subject", "lesson")  # an explicit null means "not set"


def _drop_nulls(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _drop_nulls(v) for k, v in node.items() if not (v is None and k in _OPTIONAL_TEXT)}
    if isinstance(node, list):
        return [_drop_nulls(v) for v in node]
    return node


def _loc(loc: tuple) -> str:
    out = ""
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else (("." if out else "") + str(part))
    return out or "(gốc file)"


def _short(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else f'"{value}"'
    return text if len(text) <= 40 else text[:37] + "..."


def _describe(err: dict) -> str:
    kind, ctx, value = err["type"], err.get("ctx") or {}, err.get("input")
    if kind == "missing":
        return "thiếu trường bắt buộc"
    if kind == "extra_forbidden":
        key = str(err["loc"][-1])
        near = difflib.get_close_matches(key, _KNOWN_KEYS, n=1, cutoff=0.6)
        return f'trường "{key}" không được hỗ trợ' + (f' (ý bạn là "{near[0]}"?)' if near else "")
    if kind == "literal_error":
        expected = str(ctx.get("expected", "")).replace(" or ", " hoặc ")
        return f"giá trị không hợp lệ (đang là {_short(value)}); cần: {expected}"
    if kind == "string_type":
        return f"phải là chuỗi (đang là {_short(value)})"
    if kind == "string_too_short":
        return "không được để trống"
    if kind == "too_short":
        return f"cần ít nhất {ctx.get('min_length', 1)} phần tử"
    if kind == "list_type":
        return f"phải là một danh sách [...] (đang là {_short(value)})"
    if kind in ("dict_type", "model_type", "model_attributes_type"):
        return "phải là một đối tượng JSON {...}"
    if kind in ("float_type", "float_parsing", "int_type", "int_parsing"):
        return f"phải là một số (đang là {_short(value)})"
    if kind == "greater_than":
        return f"phải lớn hơn {ctx.get('gt', 0)} (đang là {_short(value)})"
    return str(err.get("msg", kind))


def _pydantic_errors(exc: ValidationError) -> list[str]:
    out: list[str] = []
    for err in exc.errors():
        loc = tuple(err["loc"])
        if err["type"] == "none_required":  # the "None" half of `str | None`: noise
            continue
        if len(loc) >= 2 and loc[-1] == "str" and loc[-2] in _OPTIONAL_TEXT:  # the "str" half of the same union
            loc = loc[:-1]
        line = f"{_loc(loc)}: {_describe(err)}"
        if line not in out:
            out.append(line)
    return out


def _semantic_errors(model: _Map) -> list[str]:
    errors: list[str] = []

    def check_patterns(prefix: str, items: list[str]) -> None:
        for k, pat in enumerate(items):
            if not pat.strip():
                errors.append(f"{prefix}[{k}]: không được để trống")
            elif "/" in pat or "\\" in pat:
                errors.append(f'{prefix}[{k}]: "{pat}" chỉ được là tên hoặc mẫu glob của MỘT thư mục/file (không chứa / hoặc \\)')

    if model.hub is not None and not Path(os.path.expanduser(model.hub)).is_absolute():
        errors.append(f'hub: phải là đường dẫn tuyệt đối (đang là "{model.hub}")')
    check_patterns("exclude", model.exclude)

    claims: dict[str, tuple[int, str]] = {}
    for i, course in enumerate(model.courses):
        names = [("name", course.name), ("subject", course.subject)]
        names += [(f"aliases[{k}]", alias) for k, alias in enumerate(course.aliases)]
        for where, text in names:
            if text is None:
                continue
            key = norm_key(text)
            if not key:
                errors.append(f"courses[{i}].{where}: không được để trống")
                continue
            prev = claims.get(key)
            if prev is not None and prev[0] != i:
                errors.append(
                    f'courses[{i}].{where}: "{text}" trùng với {prev[1]} — mỗi tên, subject và bí danh chỉ thuộc một môn'
                )
            else:
                claims.setdefault(key, (i, f"courses[{i}].{where}"))
        check_patterns(f"courses[{i}].exclude", course.exclude)
        for j, root in enumerate(course.roots):
            base = f"courses[{i}].roots[{j}]"
            if not root.path.strip() or not Path(os.path.expanduser(root.path)).is_absolute():
                errors.append(f'{base}.path: phải là đường dẫn tuyệt đối (đang là "{root.path}")')
            if root.lesson is not None:
                if root.lessons == "subfolders":
                    errors.append(
                        f'{base}.lesson: chỉ dùng khi lessons là "single" hoặc "file" '
                        '(với "subfolders" mỗi thư mục con là một bài)'
                    )
                elif not root.lesson.strip():
                    errors.append(f"{base}.lesson: không được để trống")
    return errors


def _build(model: _Map, source: Path) -> LibMap:
    global_exclude = tuple(model.exclude)
    courses = []
    for c in model.courses:
        roots = []
        for r in c.roots:
            path = norm_path(r.path)
            if r.lessons == "file":
                lesson = (r.lesson or "").strip() or path.stem or path.name
            elif r.lessons == "single":
                lesson = (r.lesson or "").strip() or path.name or "bai"
            else:
                lesson = None
            roots.append(RootSpec(path, r.lessons, lesson))
        courses.append(
            CourseMap(
                name=c.name.strip(),
                subject=(c.subject or "").strip() or None,
                aliases=tuple(a.strip() for a in c.aliases),
                roots=tuple(roots),
                exclude=tuple(dict.fromkeys([*global_exclude, *c.exclude])),
            )
        )
    return LibMap(
        source=source,
        hub=(model.hub or "").strip() or None,
        max_file_mb=float(model.maxFileMB),
        exclude=global_exclude,
        courses=tuple(courses),
    )


# ---- loading -------------------------------------------------------------------------------


def parse_map(data: Any, source: Path) -> LibMap:
    """Validate parsed JSON. Raises MapError with one Vietnamese message per problem."""
    if not isinstance(data, dict):
        raise MapError(["(gốc file): phải là một đối tượng JSON { ... }"])
    try:
        model = _Map.model_validate(_drop_nulls(data))
    except ValidationError as exc:
        raise MapError(_pydantic_errors(exc)) from None
    problems = _semantic_errors(model)
    if problems:
        raise MapError(problems)
    return _build(model, source)


def load_map(path: Path) -> MapState:
    """Read and validate the map. A missing file is not an error (classic folder mode is used instead)."""
    try:
        st = path.stat()
    except OSError:
        return MapState(path, False)
    if not stat.S_ISREG(st.st_mode):
        return MapState(path, False)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        return MapState(path, True, None, (f"{path.name} không phải JSON hợp lệ (dòng {e.lineno}, cột {e.colno}): {e.msg}",))
    except (OSError, UnicodeDecodeError) as e:
        return MapState(path, True, None, (f"không đọc được {path.name}: {e}",))
    try:
        return MapState(path, True, parse_map(data, path))
    except MapError as e:
        return MapState(path, True, None, tuple(e.errors))

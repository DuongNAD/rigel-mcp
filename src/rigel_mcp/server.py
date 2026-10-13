"""MCP server: tools, prompts and a resource for studying from your own lesson documents.

No `from __future__ import annotations` here: the SDK reads the real annotations to build schemas.
"""

import json
import os
import re
import sys
import threading
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, Field

from . import __version__, activity, vega
from . import ingest as ing
from .config import Config, load_config
from .libmap import LibMap, MapState, load_map
from .mapsync import sync_map
from .protocol import load_protocol, protocol_reminder
from .scope import exact_name, normalize_input, resolve_path
from .smartdrive import SmartDrive
from .store import Store, make_ref, parse_ref
from .text import fold, query_terms

MIN_COVERAGE = 0.5  # a hit must contain at least this share of the query terms
STRONG_COVERAGE = 0.75

INSTRUCTIONS = """\
Server này giúp người học hỏi–đáp bám sát tài liệu bài học đã nạp (PDF, PPTX, DOCX, Markdown, notebook, code).

Với MỌI câu hỏi học tập:
1. Gọi search_material trước (2–4 truy vấn từ khóa ngắn, viết bằng ngôn ngữ của tài liệu). Không trả lời từ trí nhớ khi chưa tra.
2. Chỉ khẳng định điều nằm trong các đoạn trả về; gắn [ref] (ví dụ [D3#12]) sau mỗi ý chính. Phần suy ra thêm ghi "(ngoài tài liệu)".
3. coverage là weak hoặc none: nói rõ "tài liệu bài học chưa nói rõ phần này" TRƯỚC, rồi mới bổ sung từ kiến thức chung hoặc MCP khác (tra docs thư viện, web...) và ghi nhãn "(nguồn ngoài)".
4. Trả lời đúng trọng tâm câu hỏi, theo giao thức học đính kèm kết quả tìm kiếm (cũng có ở resource rigel://protocol).
5. Thiếu ngữ cảnh: read_chunks(context=1). Cần bản đồ bài: lesson_outline. Muốn ôn: sample_material.
6. Cuối buổi, đề nghị save_note (Obsidian/NotebookLM) cho phần vừa học.
Chưa thấy tài liệu: gọi library_status, rồi sync_library.

Trong hệ sinh thái học tập của người học:
- Khóa phạm vi trước khi tra: scope_for_path với file hoặc thư mục người học đang làm việc, hoặc set_scope với tên môn/bí danh họ nhắc (GCI, ML, PM, QC, AMD...).
- Câu hỏi học tập về các môn của hub: search_material trước khi trả lời, không bao giờ trả lời từ trí nhớ trước.
- Sau buổi học: export_flashcards { to: "vega" } để thẻ vào hàng đợi ôn tập duy nhất của Vega (Vega nạp ở lần vega_sync kế tiếp); to="anki" chỉ khi người học muốn file Anki.
- Code tìm thấy trong tài liệu có thể trace từng dòng bằng Mira (mira_record) khi người học hỏi nó chạy thế nào.
- Dựng fact Polaris hoặc nguồn slide Aurora: dùng [ref] + vị trí (file · slide/trang/mục) làm locator bằng chứng.
- Dạy học: theo giao thức học đính kèm mỗi kết quả tìm kiếm (thang gợi ý của người học); không viết lời giải bài tập thay họ.
"""

Where = Literal["lesson", "course", "all"]
WHERE_DOC = "lesson = bài đang khóa (set_scope); course = cả môn đang khóa; all = toàn thư viện."
ExportTo = Literal["anki", "vega", "both"]
EXPORT_TO_DOC = (
    'anki = file TSV để nhập Anki (mặc định); vega = một file trong hộp thư của Vega (hàng đợi ôn tập duy nhất, '
    "FSRS); both = cả hai."
)
ROOT_MODE_LABEL = {
    "subfolders": "mỗi thư mục con là một bài",
    "single": "cả thư mục là một bài",
    "file": "một file là một bài",
}


class Card(BaseModel):
    front: str = Field(description="Mặt trước: câu hỏi ngắn, một ý.")
    back: str = Field(description="Mặt sau: đáp án ngắn gọn.")
    ref: str = Field(description="ref của đoạn tài liệu làm căn cứ, ví dụ D3#12.")
    tags: list[str] = Field(default_factory=list, description="Thẻ tùy chọn (môn và bài được tự thêm).")


# ---- helpers -------------------------------------------------------------------------------


def _reply(text: str, data: dict | None = None) -> CallToolResult:
    """Text for the model, plus optional structured data for programmatic clients."""
    return CallToolResult(content=[TextContent(type="text", text=text)], structured_content=data)


def _safe_name(s: str, fallback: str = "_chung") -> str:
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "-", s).strip(" .-")
    return s[:80] or fallback


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(s)).strip("-")[:60] or "untitled"


def _tag(s: str) -> str:
    return re.sub(r"\s+", "_", s.strip())


def _unique_path(folder: Path, stem: str, suffix: str) -> Path:
    path = folder / f"{stem}{suffix}"
    n = 2
    while path.exists():
        path = folder / f"{stem}-{n}{suffix}"
        n += 1
    return path


def _match_name(name: str, options: list[str]) -> str | None:
    """Exact match ignoring case and diacritics, else a unique substring match."""
    wanted = fold(name).strip()
    exact = [o for o in options if fold(o) == wanted]
    if exact:
        return exact[0]
    partial = [o for o in options if wanted and wanted in fold(o)]
    return partial[0] if len(partial) == 1 else None


def _hit_block(h: dict) -> str:
    head = f"[{h['ref']}] {h['file']} · {h['locator']}"
    if h["heading"] and h["heading"] not in h["locator"]:
        head += f" · {h['heading']}"
    head += f"  ({h['course']}/{h['lesson']})"
    if h.get("path") and h.get("kind") in ("code", "notebook"):
        head += f"\nfile: {h['path']}"  # lets an agent open the code, or trace it with Mira
    return f"{head}\n{h['text']}"


def _meta(h: dict) -> dict:
    out = {
        k: v
        for k, v in h.items()
        if k not in ("text", "matched", "votes", "chunk_id") and not (k == "path" and v is None)
    }
    out["preview"] = h["text"][:160]
    return out


def _common(values) -> str | None:
    """The single value shared by all items, else None."""
    unique = {v for v in values}
    return unique.pop() if len(unique) == 1 else None


# ---- server --------------------------------------------------------------------------------


def build_server(cfg: Config | None = None) -> MCPServer:
    cfg = cfg or load_config()
    store = Store(cfg.db_path)
    load_protocol(cfg)  # creates protocol.md on first run so the learner can find and edit it
    sync_lock = threading.Lock()  # a client that timed out may call sync_library again while the first run goes on

    mcp = MCPServer(
        name="rigel",
        title="Rigel",
        description="Hỏi–đáp và ôn tập bám sát tài liệu bài học của bạn.",
        instructions=INSTRUCTIONS,
        version=__version__,
    )

    def current_map() -> MapState:
        return load_map(cfg.library_map)  # re-read every time: the file is tiny and the learner edits it freely

    def active_map() -> LibMap | None:
        return current_map().map

    def scope_for(where: str) -> tuple[str | None, str | None, str]:
        scope = store.get_scope()
        if where == "all" or not scope:
            return None, None, "toàn thư viện"
        course, lesson = scope["course"], scope.get("lesson")
        if where == "course" or not lesson:
            return course, None, course
        return course, lesson, f"{course} / {lesson}"

    def scope_label() -> str:
        scope = store.get_scope()
        if not scope:
            return "chưa khóa (tìm toàn thư viện)"
        return scope["course"] + (f" / {scope['lesson']}" if scope.get("lesson") else " (cả môn)")

    def log(tool: str, *, course=None, lesson=None, coverage=None, hits=0, cards=None) -> None:
        """Evidence line for Sirius. Never carries query, note or card text; never raises."""
        try:
            libmap = active_map()
            subject = libmap.subject_of(course) if libmap and course else None
        except Exception:  # noqa: BLE001 - logging must never break a tool
            subject = None
        activity.record(
            cfg.activity_path, tool, course=course, lesson=lesson, subject=subject, coverage=coverage, hits=hits, cards=cards
        )

    def course_list(courses: list[str]) -> str:
        libmap = active_map()
        out = []
        for name in courses:
            course = libmap.course_named(name) if libmap else None
            out.append(f"{name} (bí danh: {', '.join(course.aliases)})" if course and course.aliases else name)
        return ", ".join(out) or "(trống — chạy sync_library)"

    def resolve_course(text: str) -> str:
        """Course name stored in the library for `text`: a map course by name, subject folder or alias
        (case, diacritics and ' _ -' ignored), else an exact or unique-substring match on the loaded courses."""
        courses = store.courses()
        libmap = active_map()
        if libmap is not None:
            matches = libmap.match_course(text)
            if len(matches) > 1:
                raise ToolError(f"'{text}' khớp nhiều môn: {', '.join(m.name for m in matches)}. Dùng tên đầy đủ hơn.")
            if matches:
                name = exact_name(matches[0].name, courses)
                if name is None:
                    raise ToolError(
                        f"Môn '{matches[0].name}' có trong library.json nhưng chưa có tài liệu nào được nạp. "
                        "Chạy sync_library trước."
                    )
                return name
        name = _match_name(text, courses)
        if name is None:
            raise ToolError(f"Không thấy môn '{text}'. Hiện có: {course_list(courses)}")
        return name

    def lock_scope(course_name: str, lesson_name: str | None) -> tuple[str, dict, int]:
        store.set_scope(course_name, lesson_name)
        rows = [r for r in store.summary() if r["course"] == course_name and lesson_name in (None, r["lesson"])]
        docs, chunks = sum(r["docs"] for r in rows), sum(r["chunks"] for r in rows)
        label = course_name + (f" / {lesson_name}" if lesson_name else " (cả môn)")
        return f"Đã khóa phạm vi: {label} — {docs} file, {chunks} đoạn.", {"course": course_name, "lesson": lesson_name}, docs

    # -- library ---------------------------------------------------------------------------

    @mcp.tool()
    def library_status() -> CallToolResult:
        """Xem thư viện bài học đã nạp (môn → bài → số file/đoạn), chế độ đang dùng (bản đồ library.json hoặc thư mục),
        phạm vi đang khóa và đường dẫn cấu hình."""
        state = current_map()
        libmap = state.map
        rows = store.summary()
        if libmap is not None:
            lines = [
                f"Chế độ: bản đồ thư viện (library.json) — {state.path.as_posix()}",
                f"Hub: {cfg.hub_dir(libmap.hub).as_posix()}",
            ]
        else:
            lines = [f"Thư viện: {cfg.library}"]
        lines += [f"Giao thức học: {cfg.protocol_path}", f"Phạm vi hiện tại: {scope_label()}"]
        scope = store.get_scope()
        if scope and not any(
            r["course"] == scope["course"] and scope.get("lesson") in (None, r["lesson"]) for r in rows
        ):
            lines.append("Chú ý: phạm vi này không còn tài liệu nào (đã gỡ hoặc đổi tên). Dùng set_scope hoặc scope_for_path.")

        data: dict = {"scope": scope, "library": rows, "mode": "map" if libmap else "folder", "map": None, "map_errors": []}
        if libmap is not None:
            data["map"] = state.path.as_posix()
        elif state.exists:
            data["map_errors"] = list(state.errors)
            lines.append(f"library.json có nhưng KHÔNG hợp lệ ({state.path.as_posix()}):")
            lines += [f"  - {e}" for e in state.errors]
            lines.append("Sửa file này rồi gọi sync_library; trong lúc đó chế độ thư mục vẫn được dùng để tra cứu.")
        elif os.environ.get("RIGEL_LIBRARY_MAP", "").strip():
            lines.append(f"RIGEL_LIBRARY_MAP trỏ tới file không tồn tại: {state.path.as_posix()} (đang dùng chế độ thư mục).")
        else:
            lines.append(
                f"Chế độ thư mục. Chưa có bản đồ thư viện tại {state.path.as_posix()} (mẫu: examples/library.example.json)."
            )

        if not rows:
            lines.append(
                "Chưa có tài liệu. "
                + ("Chạy sync_library để nạp theo bản đồ." if libmap else "Đặt file vào <thư viện>/<môn>/<bài>/ rồi gọi sync_library.")
            )

        by_course: dict[str, list] = {}
        for r in rows:
            by_course.setdefault(r["course"], []).append(r)
        course_info = []
        if libmap is not None:
            counts = store.origin_counts()
            if counts:
                lines.append("Nguồn tài liệu đã nạp: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
            for course in libmap.courses:
                head = course.name + (f"  [subject: {course.subject}]" if course.subject else "")
                if course.aliases:
                    head += f"  [bí danh: {', '.join(course.aliases)}]"
                lines.append(f"\n{head}")
                for root in course.roots:
                    kind = ROOT_MODE_LABEL[root.lessons] + (f" '{root.lesson}'" if root.lessons != "subfolders" else "")
                    lines.append(f"  gốc: {root.path.as_posix()}  [{kind}]")
                loaded = by_course.pop(course.name, [])
                if not loaded:
                    lines.append("  (chưa nạp — chạy sync_library)")
                lines += [f"  - {r['lesson']}: {r['docs']} file, {r['chunks']} đoạn" for r in loaded]
                course_info.append(
                    {
                        "name": course.name,
                        "subject": course.subject,
                        "aliases": list(course.aliases),
                        "roots": [{"path": r.path.as_posix(), "lessons": r.lessons, "lesson": r.lesson} for r in course.roots],
                        "lessons": [{"lesson": r["lesson"], "docs": r["docs"], "chunks": r["chunks"]} for r in loaded],
                    }
                )
            if by_course:
                lines.append("\nNgoài bản đồ (nạp thủ công hoặc từ thư mục cũ):")
            for name, loaded in by_course.items():
                lines.append(f"\n{name}")
                lines += [f"  - {r['lesson']}: {r['docs']} file, {r['chunks']} đoạn" for r in loaded]
            data["courses"] = course_info
        else:
            current = None
            for r in rows:
                if r["course"] != current:
                    lines.append(f"\n{r['course']}")
                    current = r["course"]
                lines.append(f"  - {r['lesson']}: {r['docs']} file, {r['chunks']} đoạn")
        return _reply("\n".join(lines), data)

    @mcp.tool()
    def sync_library() -> CallToolResult:
        """Nạp tài liệu vào chỉ mục: theo bản đồ library.json nếu có (tìm file qua chỉ mục SmartDrive, không quét đệ quy),
        không thì quét thư mục thư viện (<môn>/<bài>/file). Nạp file mới hoặc đã sửa (so size/mtime rồi sha256),
        gỡ file đã xóa. Gọi sau khi thêm/sửa tài liệu."""
        state = current_map()
        if state.exists and not state.valid:
            raise ToolError(
                f"library.json không hợp lệ ({state.path.as_posix()}):\n"
                + "\n".join(f"- {e}" for e in state.errors)
                + "\nSửa file rồi gọi lại sync_library."
            )
        if state.map is None and not cfg.library.is_dir():
            raise ToolError(
                f"Không thấy thư mục thư viện: {cfg.library}. Tạo thư mục này (hoặc đặt RIGEL_LIBRARY) rồi thử lại. "
                f"Hoặc khai báo bản đồ thư viện tại {state.path.as_posix()} (xem examples/library.example.json)."
            )
        with sync_lock:  # one sync at a time; a second call waits, then finds almost nothing left to do
            if state.map is not None:
                report = sync_map(store, state.map, SmartDrive.from_env())
            else:
                report = ing.sync_library(store, cfg.library)
        return _reply(report.render(), report.as_dict())

    @mcp.tool()
    def add_material(
        path: Annotated[str, Field(description="Đường dẫn file trên máy chạy server.")],
        course: Annotated[str, Field(description="Tên môn, ví dụ PRJ301.")],
        lesson: Annotated[str, Field(description="Tên bài, ví dụ L03-OOP.")],
    ) -> CallToolResult:
        """Nạp một file nằm ngoài thư viện (pdf, pptx, docx, md, txt, ipynb, code) vào môn/bài chỉ định."""
        try:
            file = Path(path).expanduser().resolve(strict=True)
        except OSError:
            raise ToolError(f"Không thấy file: {path}") from None
        if not file.is_file():
            raise ToolError(f"Không phải file: {path}")
        if any(part.startswith(".") for part in file.parts[1:]):
            raise ToolError("Không nạp file nằm trong thư mục ẩn (.ssh, .config...).")
        if file.suffix.lower() not in ing.KIND_BY_SUFFIX:
            raise ToolError(f"Định dạng {file.suffix or '(không đuôi)'} chưa hỗ trợ. Hỗ trợ: {', '.join(sorted(ing.KIND_BY_SUFFIX))}")
        course, lesson = course.strip(), lesson.strip()
        if not course or not lesson:
            raise ToolError("Cần cả course và lesson.")
        try:
            result = ing.ingest_file(
                store, file, course, lesson, origin="manual", rel_path=f"@{file.name}", abs_path=file.as_posix()
            )
        except ing.NoTextError as e:
            raise ToolError(str(e)) from None
        verb = "Đã nạp" if result.created else "Đã cập nhật"
        text = f"{verb} {file.name} vào {course}/{lesson}: {result.n_chunks} đoạn."
        if result.warnings:
            text += "\nCảnh báo: " + "; ".join(result.warnings)
        return _reply(text, {"doc_id": result.doc_id, "chunks": result.n_chunks, "warnings": result.warnings})

    @mcp.tool()
    def set_scope(
        course: Annotated[
            str,
            Field(
                description="Tên môn, tên thư mục subject của môn hoặc bí danh trong library.json "
                '(không phân biệt hoa/thường, dấu). Truyền "*" để bỏ khóa.'
            ),
        ],
        lesson: Annotated[str | None, Field(description="Tên bài (hoặc một phần tên, nếu duy nhất); bỏ trống để khóa cả môn.")] = None,
    ) -> CallToolResult:
        """Khóa phạm vi tìm kiếm vào một môn (và một bài) để câu trả lời không lan sang tài liệu khác."""
        if course.strip() == "*":
            store.clear_scope()
            return _reply("Đã bỏ khóa phạm vi: tìm trong toàn thư viện.", {"scope": None})
        course_name = resolve_course(course)
        lesson_name = None
        if lesson and lesson.strip() not in ("", "*"):
            lessons = store.lessons(course_name)
            lesson_name = _match_name(lesson, lessons)
            if lesson_name is None:
                raise ToolError(f"Không thấy bài '{lesson}' trong {course_name}. Hiện có: {', '.join(lessons)}")
        text, scope, _ = lock_scope(course_name, lesson_name)
        return _reply(text, {"scope": scope})

    @mcp.tool()
    def scope_for_path(
        path: Annotated[
            str,
            Field(
                description="Đường dẫn tuyệt đối của file hoặc thư mục người học đang làm việc, ví dụ "
                "D:/02_Learning_Knowledge/Machine_Learning/02_Supervised_Learning/01_Regression/regression.py"
            ),
        ],
    ) -> CallToolResult:
        """Tự khóa phạm vi theo file hoặc thư mục người học đang mở: tìm môn chứa đường dẫn đó và bài chứa nó (nếu có),
        rồi khóa như set_scope. Gọi ở đầu phiên học hoặc khi đổi file, trước khi search_material. Không khớp môn nào
        thì phạm vi giữ nguyên và tool liệt kê các môn hiện có."""
        p = normalize_input(path)
        if p is None:
            raise ToolError("Cần đường dẫn của file hoặc thư mục đang làm việc.")
        libmap = active_map()
        match = resolve_path(
            p, libmap=libmap, hub=cfg.hub_dir(libmap.hub if libmap else None), library=cfg.library, store=store
        )
        courses = store.courses()
        if match is None:
            log("scope_for_path", hits=0)
            return _reply(
                f"Không có môn nào chứa đường dẫn: {p.as_posix()}. Phạm vi giữ nguyên ({scope_label()}).\n"
                f"Các môn hiện có: {course_list(courses)}.\n"
                "Dùng đường dẫn tuyệt đối của file đang mở, hoặc set_scope với tên môn.",
                {"matched": False, "path": p.as_posix(), "scope": store.get_scope(), "courses": courses},
            )
        course_name = exact_name(match.course, courses)
        if course_name is None:
            raise ToolError(
                f"Đường dẫn thuộc môn '{match.course}' nhưng môn này chưa có tài liệu nào được nạp. Chạy sync_library trước."
            )
        lesson_name, note = None, None
        if match.lesson is not None:
            lesson_name = exact_name(match.lesson, store.lessons(course_name))
            if lesson_name is None:
                note = f'Bài "{match.lesson}" chưa có tài liệu đã nạp (chạy sync_library nếu vừa thêm file), nên khóa cả môn.'
        text, scope, docs = lock_scope(course_name, lesson_name)
        text += f"\nKhớp theo {match.via}."
        if note:
            text += "\n" + note
        log("scope_for_path", course=course_name, lesson=lesson_name, hits=docs)
        return _reply(text, {"matched": True, "path": p.as_posix(), "scope": scope, "via": match.via, "note": note})

    # -- reading ---------------------------------------------------------------------------

    @mcp.tool()
    def search_material(
        queries: Annotated[
            list[str],
            Field(
                description="2–4 truy vấn từ khóa ngắn (2–6 từ), viết bằng ngôn ngữ của tài liệu; nếu chưa chắc, "
                "thêm bản tiếng Anh/Việt. Dùng thuật ngữ cốt lõi của khái niệm, không dùng cả câu hỏi."
            ),
        ],
        limit: Annotated[int, Field(ge=1, le=12, description="Số đoạn tối đa.")] = 6,
        where: Annotated[Where, Field(description=WHERE_DOC)] = "lesson",
    ) -> CallToolResult:
        """Tra tài liệu bài học trước khi giải thích. Trả về các đoạn nguyên văn kèm [ref], vị trí (slide/trang/mục),
        nhãn coverage (strong/weak/none) và giao thức trả lời của người học. Luôn gọi tool này trước khi trả lời
        câu hỏi học tập."""
        prepared = [(q, query_terms(q)) for q in queries[:5]]
        prepared = [(q, terms) for q, terms in prepared if terms]
        if not prepared:
            raise ToolError("Truy vấn rỗng hoặc chỉ gồm từ phổ biến. Dùng 2–4 từ khóa cốt lõi của khái niệm cần tra.")
        course, lesson, label = scope_for(where)

        merged: dict[str, dict] = {}
        for q, terms in prepared:
            for hit in store.search(terms, course, lesson, limit * 3):
                if hit["coverage"] < MIN_COVERAGE:
                    continue
                prev = merged.get(hit["ref"])
                if prev is None:
                    merged[hit["ref"]] = {**hit, "votes": 1}
                else:  # same chunk found by another query: keep its best score and best coverage
                    prev["votes"] += 1
                    prev["score"] = max(prev["score"], hit["score"])
                    prev["coverage"] = max(prev["coverage"], hit["coverage"])
        hits = sorted(merged.values(), key=lambda h: h["score"] * (1 + 0.25 * (h["votes"] - 1)), reverse=True)[:limit]
        top = max((h["coverage"] for h in hits[:3]), default=0.0)
        level = "none" if not hits else "strong" if top >= STRONG_COVERAGE else "weak"

        if not hits:
            body = (
                f"coverage: none — không đoạn nào khớp đủ trong phạm vi: {label}.\n"
                'Thử: đổi từ khóa, dịch sang ngôn ngữ của tài liệu, hoặc where="course"/"all". Nếu vẫn không có, '
                'nói rõ tài liệu bài học không đề cập phần này, rồi mới dùng nguồn ngoài và ghi nhãn "(nguồn ngoài)".'
            )
        else:
            body = f"coverage: {level} — {len(hits)} đoạn (phạm vi: {label})\n\n" + "\n\n".join(
                _hit_block(h) for h in hits
            )
            body += (
                "\n\nChỉ khẳng định điều có trong các đoạn trên, gắn [ref] sau mỗi ý; điều suy ra thêm ghi "
                '"(ngoài tài liệu)". Cần rộng hơn → read_chunks(refs, context=1).'
            )
            if level == "weak":
                body += "\ncoverage yếu: nói rõ phần tài liệu chưa nói rõ trước khi bổ sung từ nguồn khác."
        body += "\n\n---\nGiao thức trả lời của người học:\n" + protocol_reminder(load_protocol(cfg))
        log("search_material", course=course, lesson=lesson, coverage=level, hits=len(hits))
        return _reply(body, {"coverage": level, "scope": label, "hits": [_meta(h) for h in hits]})

    @mcp.tool()
    def read_chunks(
        refs: Annotated[list[str], Field(description="Danh sách ref, ví dụ ['D3#12', 'D3#13'].")],
        context: Annotated[int, Field(ge=0, le=3, description="Số đoạn liền kề lấy thêm ở mỗi phía.")] = 0,
    ) -> CallToolResult:
        """Đọc nguyên văn các đoạn theo ref, kèm đoạn liền kề để có đủ ngữ cảnh (slide trước/sau, mục kế tiếp)."""
        wanted: list[tuple[int, int]] = []
        bad: list[str] = []
        for ref in refs[:20]:
            parsed = parse_ref(ref)
            if parsed:
                wanted.append(parsed)
            else:
                bad.append(ref)
        if bad or not wanted:
            raise ToolError(f"ref không hợp lệ: {', '.join(bad) or '(rỗng)'}. Dạng đúng: D3#12.")
        found: dict[str, dict] = {}
        for doc_id, seq in wanted:
            for h in store.window(doc_id, max(1, seq - context), seq + context):
                found[h["ref"]] = h
        requested = {make_ref(*p) for p in wanted}
        missing = sorted(requested - found.keys())
        if missing:
            raise ToolError(
                f"Không thấy: {', '.join(missing)}. ref có thể đã đổi sau khi file bị gỡ — tìm lại bằng search_material."
            )
        ordered = sorted(found.values(), key=lambda h: parse_ref(h["ref"]))
        text = "\n\n".join(("► " if h["ref"] in requested else "") + _hit_block(h) for h in ordered)
        course = _common(h["course"] for h in ordered)
        log("read_chunks", course=course, lesson=_common(h["lesson"] for h in ordered) if course else None, hits=len(ordered))
        return _reply(text, {"hits": [_meta(h) for h in ordered]})

    @mcp.tool()
    def lesson_outline(
        where: Annotated[Where, Field(description=WHERE_DOC)] = "lesson",
        max_items: Annotated[int, Field(ge=20, le=400, description="Số mục tối đa.")] = 150,
    ) -> CallToolResult:
        """Mục lục của phạm vi đang khóa: từng file với các slide/trang/mục theo thứ tự. Dùng để thấy bài gồm gì
        trước khi tóm tắt hoặc chọn phần cần đọc."""
        course, lesson, label = scope_for(where)
        rows = store.outline(course, lesson)
        if not rows:
            raise ToolError(f"Không có tài liệu trong phạm vi: {label}. Chạy sync_library hoặc đổi where.")
        lines = [f"Mục lục — {label}"]
        last_doc, last_heading, shown = None, None, 0
        for r in rows:
            if r["doc_id"] != last_doc:
                lines.append(f"\n{r['course']}/{r['lesson']}/{r['rel_path']}  [D{r['doc_id']}]")
                last_doc, last_heading = r["doc_id"], None
            heading = r["heading"] or "(không tiêu đề)"
            if heading == last_heading:
                continue
            last_heading = heading
            if shown >= max_items:
                lines.append("  … (còn nữa — dùng where hẹp hơn hoặc search_material)")
                break
            lines.append(f"  {make_ref(r['doc_id'], r['seq'])} · {r['locator']} · {heading}")
            shown += 1
        log("lesson_outline", course=course, lesson=lesson, hits=shown)
        return _reply("\n".join(lines), {"scope": label, "items": shown})

    @mcp.tool()
    def sample_material(
        n: Annotated[int, Field(ge=1, le=10, description="Số đoạn lấy ngẫu nhiên.")] = 5,
        where: Annotated[Where, Field(description=WHERE_DOC)] = "lesson",
        seed: Annotated[int | None, Field(description="Hạt giống để lặp lại đúng bộ đoạn (tùy chọn).")] = None,
    ) -> CallToolResult:
        """Lấy ngẫu nhiên vài đoạn của bài để ra câu hỏi ôn tập/quiz bám tài liệu (không cần truy vấn)."""
        course, lesson, label = scope_for(where)
        hits = store.sample(course, lesson, n, seed)
        if not hits:
            raise ToolError(f"Không có đoạn nào trong phạm vi: {label}. Chạy sync_library hoặc đổi where.")
        text = f"{len(hits)} đoạn ngẫu nhiên (phạm vi: {label})\n\n" + "\n\n".join(_hit_block(h) for h in hits)
        log("sample_material", course=course, lesson=lesson, hits=len(hits))
        return _reply(text, {"scope": label, "hits": [_meta(h) for h in hits]})

    # -- handoff to other tools ------------------------------------------------------------

    @mcp.tool()
    def export_flashcards(
        deck: Annotated[str, Field(description="Tên bộ thẻ, ví dụ 'PRJ301-L03'.")],
        cards: list[Card],
        to: Annotated[ExportTo, Field(description=EXPORT_TO_DOC)] = "anki",
    ) -> CallToolResult:
        """Xuất flashcard. to="anki" (mặc định): file TSV để nhập vào Anki (File → Import). to="vega": một file trong
        hộp thư của Vega (hàng đợi ôn tập duy nhất, lịch FSRS), Vega nạp ở lần vega_sync kế tiếp. to="both": cả hai.
        Mỗi thẻ PHẢI có ref hợp lệ; thẻ không có căn cứ trong tài liệu bị từ chối. Nguồn được tự gắn vào mặt sau."""
        found, _ = store.resolve_refs([c.ref for c in cards])
        rows: list[str] = []
        accepted: list[dict] = []
        sources: list[dict] = []
        rejected: list[dict] = []
        for card in cards:
            front, back = card.front.strip(), card.back.strip()
            parsed = parse_ref(card.ref)
            key = make_ref(*parsed) if parsed else None
            if not front or not back:
                rejected.append({"ref": card.ref, "reason": "front/back rỗng"})
                continue
            if key not in found:
                rejected.append({"ref": card.ref, "reason": "ref không tồn tại; mỗi thẻ phải bám một đoạn tài liệu"})
                continue
            src = found[key]
            source = f"{src['file']} · {src['locator']}"
            tags = list(dict.fromkeys(t for t in (_tag(src["course"]), _tag(src["lesson"])) + tuple(_tag(t) for t in card.tags) if t))
            front_html = escape(front).replace("\n", "<br>").replace("\t", " ")
            back_html = (escape(back).replace("\n", "<br>") + f"<br><small>Nguồn: {escape(source)}</small>").replace("\t", " ")
            rows.append("\t".join([front_html, back_html, " ".join(tags)]))
            accepted.append({"front": front, "back": back, "ref": key, "source": source, "tags": tags})
            sources.append(src)
        if not rows:
            raise ToolError("Không thẻ nào hợp lệ: " + "; ".join(f"{r['ref']}: {r['reason']}" for r in rejected))

        want_anki, want_vega = to in ("anki", "both"), to in ("vega", "both")
        vega_lines: list[dict] = []
        vega_rejected: list[dict] = []
        inbox_path: Path | None = None
        if want_vega:
            libmap = active_map()
            hub = cfg.hub_dir(libmap.hub if libmap else None)
            if not hub.is_dir():
                raise ToolError(
                    f"Không thấy thư mục hub: {hub}. Đặt biến RIGEL_HUB (hoặc \"hub\" trong library.json) rồi thử lại."
                )
            now = datetime.now().astimezone().isoformat(timespec="seconds")
            seen_keys: set[str] = set()
            written: list[int] = []  # indexes (in `accepted`) of the cards that became inbox lines
            for index, (card, src) in enumerate(zip(accepted, sources)):
                origin_path = src["path"] or src["file"]  # absolute when known, else relative to the lesson folder
                if len(origin_path) > 400:
                    origin_path = src["file"]
                line = vega.build_line(
                    now=now,
                    course=src["course"],
                    subject=libmap.subject_of(src["course"]) if libmap else None,
                    deck=deck,
                    front=card["front"],
                    back=card["back"],
                    ref=card["ref"],
                    file_label=src["file"],
                    locator=src["locator"],
                    path=origin_path,
                    tags=card["tags"],
                )
                problems = vega.validate_line(line)
                if not problems and line["key"] in seen_keys:
                    problems = ["trùng thẻ trong cùng lô (cùng ref và mặt trước)"]
                if problems:
                    vega_rejected.append({"ref": card["ref"], "reason": "; ".join(problems)})
                    continue
                seen_keys.add(line["key"])
                vega_lines.append(line)
                written.append(index)
            if not vega_lines and not want_anki:
                raise ToolError(
                    "Không thẻ nào hợp lệ cho Vega: " + "; ".join(f"{r['ref']}: {r['reason']}" for r in vega_rejected)
                )
            if not want_anki:  # Vega only: report as accepted just what really went to the inbox
                accepted = [accepted[i] for i in written]
                sources = [sources[i] for i in written]
            if vega_lines:
                try:
                    inbox_path = vega.write_inbox(hub, vega_lines)
                except (OSError, UnicodeError) as e:
                    raise ToolError(f"Không ghi được hộp thư Vega ({hub / '.vega' / 'inbox'}): {e}") from None

        path: Path | None = None
        if want_anki:
            cfg.exports.mkdir(parents=True, exist_ok=True)
            path = _unique_path(cfg.exports, _slug(deck), ".tsv")
            header = ["#separator:tab", "#html:true", "#tags column:3"]
            path.write_text("\n".join(header + rows) + "\n", encoding="utf-8")

        lines = []
        if path is not None:
            lines.append(f"Đã xuất {len(rows)} thẻ → {path}")
        if inbox_path is not None:
            lines.append(f"Đã ghi {len(vega_lines)} thẻ vào hộp thư Vega → {inbox_path}")
        if rejected:
            lines.append(f"Từ chối {len(rejected)} thẻ: " + "; ".join(f"{r['ref']} ({r['reason']})" for r in rejected))
        if vega_rejected:
            lines.append(
                f"Vega từ chối {len(vega_rejected)} thẻ: " + "; ".join(f"{r['ref']} ({r['reason']})" for r in vega_rejected)
            )
        if path is not None:
            lines.append("Anki: File → Import, chọn file này (đã khai báo tab, HTML, cột tag).")
        if inbox_path is not None:
            lines.append("Vega nạp file này ở lần vega_sync kế tiếp (hoặc vega_inbox với action ingest).")
        course = _common(s["course"] for s in sources)
        log(
            "export_flashcards",
            course=course,
            lesson=_common(s["lesson"] for s in sources) if course else None,
            hits=len({a["ref"] for a in accepted}),
            cards=len(accepted),
        )
        return _reply(
            "\n".join(lines),
            {
                "path": str(path) if path else None,
                "accepted": accepted,
                "rejected": rejected,
                "to": to,
                "inbox": str(inbox_path) if inbox_path else None,
                "vega_rejected": vega_rejected,
            },
        )

    @mcp.tool()
    def save_note(
        title: Annotated[str, Field(description="Tiêu đề ghi chú.")],
        markdown: Annotated[str, Field(description="Nội dung ghi chú (Markdown).")],
        refs: Annotated[list[str] | None, Field(description="ref các đoạn tài liệu làm nguồn (khuyến nghị).")] = None,
    ) -> CallToolResult:
        """Lưu ghi chú học (Markdown, có front matter và nguồn) vào thư mục notes/<môn>/<bài>/. Trỏ RIGEL_NOTES
        vào vault Obsidian hoặc thư mục NotebookLM/Filesystem MCP đang đọc để chuyển tiếp."""
        if not title.strip() or not markdown.strip():
            raise ToolError("Cần cả title và markdown.")
        found, missing = store.resolve_refs(refs or [])
        if missing:
            raise ToolError(f"ref không tồn tại: {', '.join(missing)}")
        scope = store.get_scope() or {}
        folder = cfg.notes / _safe_name(scope.get("course") or "") / _safe_name(scope.get("lesson") or "")
        folder.mkdir(parents=True, exist_ok=True)
        path = _unique_path(folder, _slug(title), ".md")
        dump = lambda s: json.dumps(s, ensure_ascii=False)  # noqa: E731 - valid YAML double-quoted string
        front = [
            "---",
            f"title: {dump(title.strip())}",
            f"course: {dump(scope.get('course') or '')}",
            f"lesson: {dump(scope.get('lesson') or '')}",
            f"created: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        ]
        if found:
            front.append("sources:")
            for ref, h in found.items():
                front.append("  - " + dump(f"{h['file']} · {h['locator']} [{ref}]"))
        front.append("---")
        path.write_text("\n".join(front) + "\n\n" + markdown.strip() + "\n", encoding="utf-8")
        log("save_note", course=scope.get("course"), lesson=scope.get("lesson"), hits=len(found))
        return _reply(f"Đã lưu ghi chú → {path}", {"path": str(path), "sources": list(found)})

    # -- prompts and resource --------------------------------------------------------------

    @mcp.prompt(title="Học một khái niệm theo tài liệu bài học")
    def learn(topic: str) -> str:
        return (
            f"Giải thích giúp mình: {topic}\n\n"
            "Làm đúng thứ tự:\n"
            "1. Gọi search_material (2–4 truy vấn từ khóa ngắn, viết bằng ngôn ngữ của tài liệu). Chưa trả lời từ trí nhớ.\n"
            "2. Trả lời đúng trọng tâm câu hỏi, theo giao thức bên dưới, gắn [ref] cho mỗi ý chính.\n"
            '3. coverage weak/none: nói rõ tài liệu chưa nói rõ phần nào trước khi dùng nguồn ngoài (ghi nhãn "(nguồn ngoài)").\n\n'
            "--- Giao thức học ---\n" + load_protocol(cfg)
        )

    @mcp.prompt(title="Kiểm tra nhanh theo bài đang học")
    def quiz(n: str = "5") -> str:
        try:
            count = max(1, min(10, int(n)))
        except ValueError:
            count = 5
        return (
            f"Kiểm tra mình {count} câu về phạm vi đang khóa.\n\n"
            f"1. Gọi sample_material(n={count}) để lấy đoạn nguồn.\n"
            "2. Hỏi từng câu một, đợi mình trả lời rồi mới chấm. Câu hỏi bám đúng đoạn; ưu tiên 'làm / viết / dự đoán' hơn học thuộc.\n"
            "3. Chấm xong giải thích ngắn kèm [ref].\n"
            '4. Cuối buổi gom các câu mình sai và đề nghị export_flashcards (to="vega" để vào hàng đợi ôn tập).'
        )

    @mcp.prompt(title="Tóm tắt bài đang học")
    def recap() -> str:
        return (
            "Tóm tắt bài đang khóa cho mình trong một trang.\n\n"
            "1. Gọi lesson_outline để thấy bài gồm những mục nào.\n"
            "2. Với 3–5 mục quan trọng nhất, gọi search_material để đọc nội dung thật.\n"
            "3. Viết tóm tắt theo thứ tự của bài, gắn [ref]; cuối cùng nêu 3 điểm dễ nhầm và 1 bài tập làm ngay.\n\n"
            "--- Giao thức học ---\n" + protocol_reminder(load_protocol(cfg))
        )

    @mcp.resource(
        "rigel://protocol",
        name="protocol",
        title="Giao thức học",
        description="Cách AI cần giải thích và trả lời; sửa file protocol.md để đổi.",
        mime_type="text/markdown",
    )
    def protocol_resource() -> str:
        return load_protocol(cfg)

    return mcp


def main() -> None:
    cfg = load_config()
    server = build_server(cfg)
    print(
        f"rigel {__version__} | library={cfg.library} | map={cfg.library_map} | home={cfg.home}",
        file=sys.stderr,
    )
    server.run(transport="stdio")


if __name__ == "__main__":
    main()

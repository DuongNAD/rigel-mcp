import asyncio
import os

import pytest
from mcp import Client

from rigel_mcp.config import Config
from rigel_mcp.scope import normalize_input, windows_style
from rigel_mcp.server import build_server


def text(r):
    return r.content[0].text


def run(cfg, flow):
    async def go():
        async with Client(build_server(cfg)) as c:
            return await flow(c)

    return asyncio.run(go())


def scope_of(r):
    return r.structured_content["scope"]


# ---- set_scope: course name, subject folder, aliases ----------------------------------------


def test_set_scope_accepts_name_subject_and_aliases_ignoring_case_and_diacritics(map_cfg):
    async def flow(c):
        assert not (await c.call_tool("sync_library", {})).is_error
        for name in ("GCI", "gci", "GCI World", "GCI_Subject", "gci subject", "Gci_World", " gci-world "):
            r = await c.call_tool("set_scope", {"course": name})
            assert not r.is_error and scope_of(r) == {"course": "GCI", "lesson": None}, name
            assert "Đã khóa phạm vi: GCI (cả môn)" in text(r)
        for name in ("Học máy", "hoc may", "HOC_MAY", "ml", "Machine_Learning"):
            r = await c.call_tool("set_scope", {"course": name})
            assert not r.is_error and scope_of(r) == {"course": "ML", "lesson": None}, name

        r = await c.call_tool("set_scope", {"course": "học máy", "lesson": "classif"})  # lesson: unique substring
        assert scope_of(r) == {"course": "ML", "lesson": "02_Classification"} and "ML / 02_Classification" in text(r)
        r = await c.call_tool("set_scope", {"course": "gci", "lesson": "NUMPY"})
        assert scope_of(r) == {"course": "GCI", "lesson": "02_NumPy"}

        r = await c.call_tool("set_scope", {"course": "gci", "lesson": "khong-co"})
        assert r.is_error and "Không thấy bài" in text(r) and "02_NumPy" in text(r)
        r = await c.call_tool("set_scope", {"course": "zzz"})
        assert r.is_error and "Không thấy môn 'zzz'" in text(r) and "GCI (bí danh: GCI World, gci)" in text(r)
        r = await c.call_tool("set_scope", {"course": "i"})  # a substring of both GCI and Machine_Learning
        assert r.is_error and "khớp nhiều môn" in text(r)
        r = await c.call_tool("set_scope", {"course": "*"})
        assert scope_of(r) is None

    run(map_cfg, flow)


def test_set_scope_on_a_map_course_that_is_not_synced_says_so(map_cfg, hub):
    data = hub.map_dict()
    data["courses"].append(
        {"name": "PM", "aliases": ["python master"], "roots": [{"path": (hub.root / "Python_Master").as_posix()}]}
    )
    hub.write_map(data)

    async def flow(c):
        await c.call_tool("sync_library", {})
        r = await c.call_tool("set_scope", {"course": "python master"})
        assert r.is_error and "chưa có tài liệu nào được nạp" in text(r) and "sync_library" in text(r)

    run(map_cfg, flow)


# ---- scope_for_path -------------------------------------------------------------------------


def test_scope_for_path_finds_course_and_lesson(map_cfg, hub):
    (hub.materials / "05_Empty").mkdir()
    (hub.materials / "05_Empty" / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")  # a lesson folder with no docs
    ml = hub.root / "Machine_Learning" / "02_Supervised_Learning"

    async def flow(c):
        r = await c.call_tool("scope_for_path", {"path": str(hub.materials / "02_NumPy" / "notes.md")})
        assert r.is_error and "chưa có tài liệu nào được nạp" in text(r)  # nothing synced yet
        await c.call_tool("sync_library", {})

        async def where(path):
            r = await c.call_tool("scope_for_path", {"path": str(path)})
            assert not r.is_error, text(r)
            return r

        # a file inside a lesson, a folder that is a lesson, a nested file
        r = await where(hub.materials / "02_NumPy" / "notes.md")
        assert scope_of(r) == {"course": "GCI", "lesson": "02_NumPy"} and r.structured_content["matched"] is True
        assert "Đã khóa phạm vi: GCI / 02_NumPy — 1 file" in text(r) and "Khớp theo thư mục gốc" in text(r)
        assert scope_of(await where(hub.materials / "02_NumPy")) == {"course": "GCI", "lesson": "02_NumPy"}
        assert scope_of(await where(hub.materials / "01_Intro" / "code" / "hello.py")) == {"course": "GCI", "lesson": "01_Intro"}
        # the ml example of the spec: a file in a lesson of another course
        r = await where(ml / "01_Regression" / "regression.py")
        assert scope_of(r) == {"course": "ML", "lesson": "01_Regression"}

        # a file directly in the root belongs to the general lesson; the root folder itself to the whole course
        assert scope_of(await where(hub.materials / "overview.md")) == {"course": "GCI", "lesson": "_chung"}
        assert scope_of(await where(hub.materials)) == {"course": "GCI", "lesson": None}
        # single and file roots
        assert scope_of(await where(hub.root / "GCI_Subject" / "06_Notes" / "extra" / "more.md"))["lesson"] == "Bai_giang"
        assert scope_of(await where(hub.root / "GCI_Subject" / "roadmap" / "ROADMAP.md"))["lesson"] == "_lo_trinh"
        # inside the subject folder but outside every root: the course, no lesson
        r = await where(hub.root / "GCI_Subject" / "04_Assignments" / "hw.py")
        assert scope_of(r) == {"course": "GCI", "lesson": None} and "thư mục môn" in text(r)
        # the subject folder itself is the course, with no lesson
        assert scope_of(await where(hub.root / "GCI_Subject")) == {"course": "GCI", "lesson": None}
        # an excluded folder is not a lesson
        assert scope_of(await where(hub.materials / "node_modules" / "x.md")) == {"course": "GCI", "lesson": None}
        # a lesson folder that has no loaded docs: whole course, with an explanation
        r = await where(hub.materials / "05_Empty" / "data.csv")
        assert scope_of(r) == {"course": "GCI", "lesson": None} and '"05_Empty" chưa có tài liệu đã nạp' in text(r)
        if os.name == "nt":  # Windows paths ignore case and accept either slash
            r = await where(str(hub.materials / "02_NumPy" / "notes.md").upper().replace("/", "\\"))
            assert scope_of(r) == {"course": "GCI", "lesson": "02_NumPy"}

        # the scope really changed: searches are now limited to it
        await c.call_tool("scope_for_path", {"path": str(ml / "02_Classification" / "notes.md")})
        r = await c.call_tool("search_material", {"queries": ["logistic regression"]})
        assert "notes.md" in text(r) and "(ML/02_Classification)" in text(r)

        # nothing matches: say so, list the courses, keep the scope
        r = await c.call_tool("scope_for_path", {"path": str(hub.tmp / "elsewhere" / "x.py")})
        assert not r.is_error and r.structured_content["matched"] is False
        assert "Không có môn nào chứa đường dẫn" in text(r) and "GCI (bí danh: GCI World, gci)" in text(r) and "ML" in text(r)
        assert "Phạm vi giữ nguyên (ML / 02_Classification)" in text(r)
        status = await c.call_tool("library_status", {})
        assert status.structured_content["scope"] == {"course": "ML", "lesson": "02_Classification"}

        r = await c.call_tool("scope_for_path", {"path": "  "})
        assert r.is_error and "Cần đường dẫn" in text(r)

    run(map_cfg, flow)


@pytest.mark.skipif(os.name != "nt", reason="Git Bash and WSL style paths are converted on Windows only")
def test_scope_for_path_understands_git_bash_and_wsl_paths(map_cfg, hub):
    drive = hub.root.drive[0].lower()
    rest = hub.root.as_posix()[2:]
    notes = "/GCI_Subject/03_Materials/02_NumPy/notes.md"

    async def flow(c):
        await c.call_tool("sync_library", {})
        for path in (f"/{drive}{rest}{notes}", f"/mnt/{drive}{rest}{notes}", f"{hub.root}{notes}".replace("/", "\\")):
            r = await c.call_tool("scope_for_path", {"path": path})
            assert not r.is_error and scope_of(r) == {"course": "GCI", "lesson": "02_NumPy"}, path

    run(map_cfg, flow)


def test_path_normalisation_helpers():
    assert windows_style("/d/02_Learning_Knowledge/x.py") == "D:/02_Learning_Knowledge/x.py"
    assert windows_style("/mnt/c/Users/Admin/x.py") == "C:/Users/Admin/x.py"
    assert windows_style("/d") == "D:/"
    for untouched in ("/home/admin/x.py", "D:/x/y.py", "C:\\x\\y.py", "relative/x.py", "/etc"):
        assert windows_style(untouched) == untouched
    assert normalize_input("") is None and normalize_input('  ""  ') is None
    if os.name == "nt":
        assert normalize_input('"D:\\hub\\a\\..\\b.py"', windows=True).as_posix().lower().endswith("hub/b.py")


def test_scope_for_path_in_classic_folder_mode_and_for_added_files(cfg, tmp_path):
    (cfg.library / "PRJ301" / "loose.md").write_text("# Chung\n\nghi chú chung của môn.\n", encoding="utf-8")
    extra = tmp_path / "elsewhere" / "extra.md"
    extra.parent.mkdir()
    extra.write_text("# Interface\n\nHợp đồng mà lớp phải tuân theo.\n", encoding="utf-8")
    lesson = cfg.library / "PRJ301" / "L03-OOP"

    async def flow(c):
        await c.call_tool("sync_library", {})
        r = await c.call_tool("scope_for_path", {"path": str(lesson / "Lecture3.pptx")})
        assert scope_of(r) == {"course": "PRJ301", "lesson": "L03-OOP"} and "thư viện" in text(r)
        assert scope_of(await c.call_tool("scope_for_path", {"path": str(lesson)})) == {"course": "PRJ301", "lesson": "L03-OOP"}
        assert scope_of(await c.call_tool("scope_for_path", {"path": str(cfg.library / "PRJ301" / "loose.md")}))["lesson"] == "_chung"
        assert scope_of(await c.call_tool("scope_for_path", {"path": str(cfg.library / "PRJ301")})) == {
            "course": "PRJ301",
            "lesson": None,
        }
        r = await c.call_tool("scope_for_path", {"path": str(cfg.library / "Khac" / "x.md")})
        assert r.structured_content["matched"] is False

        # a file added with add_material is found by its exact path
        await c.call_tool("add_material", {"path": str(extra), "course": "PRJ301", "lesson": "L03-OOP"})
        await c.call_tool("set_scope", {"course": "*"})
        r = await c.call_tool("scope_for_path", {"path": str(extra)})
        assert scope_of(r) == {"course": "PRJ301", "lesson": "L03-OOP"} and "file đã nạp" in text(r)

    run(cfg, flow)


def test_library_status_shows_mode_roots_lessons_and_problems(map_cfg, hub, tmp_path):
    async def flow(c):
        r = await c.call_tool("library_status", {})
        body = text(r)
        assert r.structured_content["mode"] == "map" and "Chế độ: bản đồ thư viện (library.json)" in body
        assert hub.map_file.as_posix() in body and f"Hub: {hub.root.as_posix()}" in body
        assert "Chưa có tài liệu" in body and "(chưa nạp — chạy sync_library)" in body
        assert "GCI  [subject: GCI_Subject]  [bí danh: GCI World, gci]" in body
        assert f"gốc: {hub.materials.as_posix()}  [mỗi thư mục con là một bài]" in body
        assert "[một file là một bài '_lo_trinh']" in body and "[cả thư mục là một bài 'Bai_giang']" in body

        await c.call_tool("sync_library", {})
        await c.call_tool("add_material", {"path": str(hub.root / "GCI_Subject" / "06_Notes" / "lecture.txt"), "course": "Tay", "lesson": "L"})
        await c.call_tool("set_scope", {"course": "gci", "lesson": "numpy"})
        r = await c.call_tool("library_status", {})
        body = text(r)
        assert "Phạm vi hiện tại: GCI / 02_NumPy" in body and "- 02_NumPy: 1 file" in body
        assert "Nguồn tài liệu đã nạp: manual 1, map 9" in body
        assert "Ngoài bản đồ" in body and "\nTay\n" in body
        assert {c["name"] for c in r.structured_content["courses"]} == {"GCI", "ML"}

        # the file behind the scope disappears: status says so
        (hub.materials / "02_NumPy" / "notes.md").unlink()
        await c.call_tool("sync_library", {})
        assert "phạm vi này không còn tài liệu nào" in text(await c.call_tool("library_status", {}))

    run(map_cfg, flow)


def test_a_broken_map_is_reported_not_silently_ignored(tmp_path, cfg):
    broken = tmp_path / "home" / "library.json"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text('{"v": 1, "courses": [{"name": "A", "roots": [{"path": "D:/x", "lessons": "folder"}]}]}', encoding="utf-8")
    with_map = Config(home=cfg.home, library=cfg.library, notes=cfg.notes, map_path=broken)

    async def flow(c):
        r = await c.call_tool("sync_library", {})
        assert r.is_error and "library.json không hợp lệ" in text(r)
        assert "courses[0].roots[0].lessons" in text(r) and "subfolders" in text(r)  # the exact field, in Vietnamese
        r = await c.call_tool("library_status", {})
        assert not r.is_error and "KHÔNG hợp lệ" in text(r) and "courses[0].roots[0].lessons" in text(r)
        assert r.structured_content["mode"] == "folder" and r.structured_content["map_errors"]

    run(with_map, flow)


def test_concurrent_sync_calls_never_overlap(map_cfg, monkeypatch):
    import threading
    import time

    from rigel_mcp import server

    state = {"now": 0, "max": 0}
    guard = threading.Lock()
    real = server.sync_map

    def slow(*args, **kwargs):
        with guard:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(0.25)
        try:
            return real(*args, **kwargs)
        finally:
            with guard:
                state["now"] -= 1

    monkeypatch.setattr(server, "sync_map", slow)

    async def flow(c):
        results = await asyncio.gather(*(c.call_tool("sync_library", {}) for _ in range(3)))
        return [r.structured_content for r in results]

    reports = run(map_cfg, flow)
    assert state["max"] == 1  # a client that timed out and retried cannot start a second, racing sync
    assert sum(len(r["added"]) for r in reports) == 9 and sum(r["unchanged"] for r in reports) == 18


def test_folder_mode_status_says_where_the_map_would_be(cfg):
    async def flow(c):
        r = await c.call_tool("library_status", {})
        body = text(r)
        assert r.structured_content["mode"] == "folder" and r.structured_content["map"] is None
        assert f"Thư viện: {cfg.library}" in body and "Chưa có tài liệu" in body
        assert f"Chưa có bản đồ thư viện tại {(cfg.home / 'library.json').as_posix()}" in body

    run(cfg, flow)


def test_library_map_variable_pointing_nowhere_is_called_out(cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("RIGEL_LIBRARY_MAP", str(tmp_path / "missing" / "library.json"))

    async def flow(c):
        r = await c.call_tool("library_status", {})
        assert not r.is_error and "RIGEL_LIBRARY_MAP trỏ tới file không tồn tại" in text(r)
        assert r.structured_content["mode"] == "folder"
        assert not (await c.call_tool("sync_library", {})).is_error  # and the classic mode keeps working

    run(cfg, flow)


def test_code_hits_carry_the_absolute_path_so_agents_can_trace_them_with_mira(map_cfg, hub):
    hello = hub.materials / "01_Intro" / "code" / "hello.py"

    async def flow(c):
        await c.call_tool("sync_library", {})
        r = await c.call_tool("search_material", {"queries": ["hello"], "where": "all"})
        hit = next(h for h in r.structured_content["hits"] if h["file"] == "code/hello.py")
        assert (hit["kind"], hit["path"]) == ("code", hello.as_posix())
        assert f"file: {hello.as_posix()}" in text(r)
        r = await c.call_tool("search_material", {"queries": ["broadcasting"], "where": "all"})
        assert "file: " not in text(r)  # prose documents get no path line, only code and notebooks
        assert r.structured_content["hits"][0]["kind"] == "text"

    run(map_cfg, flow)


def test_missing_library_folder_error_points_at_the_map(tmp_path):
    nothing = Config(home=tmp_path / "home", library=tmp_path / "nope", notes=tmp_path / "notes")

    async def flow(c):
        r = await c.call_tool("sync_library", {})
        assert r.is_error and "Không thấy thư mục thư viện" in text(r) and "library.json" in text(r)

    run(nothing, flow)

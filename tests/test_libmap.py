import copy
import json
from pathlib import Path

import pytest

from rigel_mcp.config import DEFAULT_HUB, Config, load_config
from rigel_mcp.libmap import DEFAULT_EXCLUDE, MapError, load_map, norm_key, parse_map

PROJECT = Path(__file__).resolve().parents[1]
SRC = Path("library.json")


def good(hub) -> dict:
    return copy.deepcopy(hub.map_dict())


def errors_of(data) -> list[str]:
    with pytest.raises(MapError) as info:
        parse_map(data, SRC)
    return info.value.errors


def has(errors: list[str], *fragments: str) -> bool:
    return any(all(f in e for f in fragments) for e in errors)


def test_example_map_in_the_repo_is_valid():
    state = load_map(PROJECT / "examples" / "library.example.json")
    assert state.valid, state.errors
    assert {c.name for c in state.map.courses} == {"GCI", "ML", "PM", "QC", "AMD"}


def test_valid_map_is_resolved_with_lesson_names_and_merged_excludes(hub):
    libmap = parse_map(good(hub), SRC)
    gci, ml = libmap.courses
    assert (gci.name, gci.subject, gci.aliases) == ("GCI", "GCI_Subject", ("GCI World", "gci"))
    assert [(r.lessons, r.lesson) for r in gci.roots] == [("subfolders", None), ("file", "_lo_trinh"), ("single", "Bai_giang")]
    assert gci.exclude == ("__pycache__", "node_modules", "dap_an")  # global first, then the course's own
    assert ml.exclude == ("__pycache__", "node_modules")
    assert libmap.max_file_mb == 40 and libmap.hub == hub.root.as_posix()


def test_defaults_when_optional_keys_are_missing(tmp_path):
    root = tmp_path.as_posix()
    data = {
        "v": 1,
        "courses": [
            {
                "name": "X",
                "roots": [
                    {"path": f"{root}/notes/ROADMAP.md", "lessons": "file"},
                    {"path": f"{root}/docs", "lessons": "single"},
                    {"path": f"{root}/lessons"},
                ],
            }
        ],
    }
    libmap = parse_map(data, SRC)
    course = libmap.courses[0]
    assert [r.lesson for r in course.roots] == ["ROADMAP", "docs", None]  # file stem, folder name, n/a
    assert [r.lessons for r in course.roots] == ["file", "single", "subfolders"]  # subfolders is the default
    assert course.exclude == DEFAULT_EXCLUDE and libmap.max_file_mb == 40 and libmap.hub is None and course.subject is None


def test_explicit_null_means_not_set(hub):
    data = good(hub)
    data["hub"] = None
    data["courses"][0]["subject"] = None
    data["courses"][0]["roots"][1]["lesson"] = None
    libmap = parse_map(data, SRC)
    assert libmap.hub is None and libmap.courses[0].subject is None
    assert libmap.courses[0].roots[1].lesson == "ROADMAP"


def test_json_syntax_error_reports_line_and_column(tmp_path):
    f = tmp_path / "library.json"
    f.write_text('{\n  "v": 1,\n  "courses": [\n}', encoding="utf-8")
    state = load_map(f)
    assert state.exists and not state.valid
    assert "không phải JSON hợp lệ" in state.errors[0] and "dòng 4" in state.errors[0]


def test_missing_file_is_not_an_error_and_bom_is_tolerated(tmp_path, hub):
    assert not load_map(tmp_path / "nope.json").exists
    assert not load_map(tmp_path).exists  # a folder is not a map
    bom = tmp_path / "bom.json"
    bom.write_bytes(b"\xef\xbb\xbf" + json.dumps(good(hub)).encode("utf-8"))
    assert load_map(bom).valid


def test_shape_errors_name_the_json_path_in_vietnamese(hub):
    assert has(errors_of([1, 2]), "(gốc file)", "đối tượng JSON")

    d = good(hub)
    del d["v"]
    assert has(errors_of(d), "v:", "thiếu trường bắt buộc")

    d = good(hub)
    d["courses"] = []
    assert has(errors_of(d), "courses:", "cần ít nhất 1 phần tử")

    d = good(hub)
    d["courses"][0]["roots"][1]["lessons"] = "folder"
    errs = errors_of(d)
    assert has(errs, "courses[0].roots[1].lessons:", "giá trị không hợp lệ", "subfolders", "hoặc")

    d = good(hub)
    d["maxFileMb"] = 10
    assert has(errors_of(d), 'trường "maxFileMb" không được hỗ trợ', 'ý bạn là "maxFileMB"')

    d = good(hub)
    d["courses"][0]["aliases"] = "gci"
    assert has(errors_of(d), "courses[0].aliases:", "phải là một danh sách")

    d = good(hub)
    d["courses"][1]["roots"][0]["path"] = ""
    assert has(errors_of(d), "courses[1].roots[0].path:", "không được để trống")

    d = good(hub)
    d["maxFileMB"] = 0
    assert has(errors_of(d), "maxFileMB:", "phải lớn hơn 0")

    d = good(hub)
    d["hub"] = 5
    errs = errors_of(d)
    assert has(errs, "hub:", "phải là chuỗi") and not has(errs, "none")  # no noise from the str|None union


def test_semantic_errors_are_reported_together(hub):
    d = good(hub)
    d["courses"][1]["name"] = "gci"  # same as course 0 once case is ignored
    d["courses"][1]["aliases"] = ["GCI_World"]  # collides with alias "GCI World" of course 0
    d["courses"][0]["roots"][0]["path"] = "relative/folder"
    d["courses"][0]["roots"][2]["lesson"] = "  "
    d["courses"][0]["roots"][0]["lesson"] = "x"
    d["exclude"] = ["a/b", ""]
    d["courses"][0]["exclude"] = ["ok", "c\\d"]
    d["hub"] = "relative-hub"
    errs = errors_of(d)
    assert has(errs, "courses[1].name", "trùng với courses[0].name")
    assert has(errs, "courses[1].aliases[0]", "trùng với courses[0].aliases[0]")
    assert has(errs, "courses[0].roots[0].path", "đường dẫn tuyệt đối")
    assert has(errs, "courses[0].roots[0].lesson", 'chỉ dùng khi lessons là "single" hoặc "file"')
    assert has(errs, "courses[0].roots[2].lesson", "không được để trống")
    assert has(errs, "exclude[0]", "MỘT thư mục/file") and has(errs, "exclude[1]", "không được để trống")
    assert has(errs, "courses[0].exclude[1]", "MỘT thư mục/file")
    assert has(errs, "hub:", "đường dẫn tuyệt đối")
    assert len(errs) >= 8


def test_load_map_wraps_validation_errors_in_the_state(tmp_path, hub):
    d = good(hub)
    d["courses"][0]["roots"][0]["lessons"] = "oops"
    f = tmp_path / "library.json"
    f.write_text(json.dumps(d), encoding="utf-8")
    state = load_map(f)
    assert state.exists and not state.valid and state.map is None
    assert state.errors and "courses[0].roots[0].lessons" in state.errors[0]


def test_match_course_ignores_case_diacritics_and_separators(hub):
    libmap = parse_map(good(hub), SRC)
    for text in ("GCI", "gci", "GCI World", "gci_world", "GCI-WORLD", "GCI_Subject", "gci subject"):
        assert [c.name for c in libmap.match_course(text)] == ["GCI"], text
    for text in ("Học máy", "hoc may", "HOC_MAY", "ML", "Machine_Learning", "machine learning"):
        assert [c.name for c in libmap.match_course(text)] == ["ML"], text
    assert [c.name for c in libmap.match_course("supervis")] == []  # not a name, subject or alias part
    assert [c.name for c in libmap.match_course("Machine")] == ["ML"]  # unique substring of a subject
    assert libmap.match_course("") == [] and libmap.match_course("zzz") == []
    assert libmap.subject_of("gci") == "GCI_Subject" and libmap.subject_of("nope") is None
    assert norm_key("  Đa_hình-Java ") == "da hinh java"


def test_substring_that_hits_two_courses_is_ambiguous(hub):
    libmap = parse_map(good(hub), SRC)
    names = sorted(c.name for c in libmap.match_course("i"))  # "gci"/"gci world"/... and "machine learning"/... contain "i"
    assert names == ["GCI", "ML"]


def test_config_resolution_of_map_and_hub(tmp_path, monkeypatch):
    base = Config(home=tmp_path / "home", library=tmp_path / "lib", notes=tmp_path / "notes")
    assert base.library_map == tmp_path / "home" / "library.json"
    assert base.hub_dir() == Path(DEFAULT_HUB)
    assert base.hub_dir("D:/from-map") == Path("D:/from-map")

    monkeypatch.setenv("RIGEL_LIBRARY_MAP", str(tmp_path / "env-map.json"))
    monkeypatch.setenv("RIGEL_HUB", str(tmp_path / "env-hub"))
    assert base.library_map == tmp_path / "env-map.json"
    assert base.hub_dir("D:/from-map") == tmp_path / "env-hub"  # env beats the map's hub

    explicit = Config(home=tmp_path, library=tmp_path, notes=tmp_path, map_path=tmp_path / "x.json", hub=tmp_path / "h")
    assert explicit.library_map == tmp_path / "x.json" and explicit.hub_dir("D:/m") == tmp_path / "h"

    monkeypatch.setenv("RIGEL_HOME", str(tmp_path / "H"))
    loaded = load_config()
    assert loaded.home == tmp_path / "H" and loaded.map_path == tmp_path / "env-map.json" and loaded.hub == tmp_path / "env-hub"

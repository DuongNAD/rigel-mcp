import os
import sqlite3
import subprocess
import types

import pytest

from rigel_mcp import discover
from rigel_mcp import ingest as ing
from rigel_mcp.libmap import load_map
from rigel_mcp.mapsync import sync_map
from rigel_mcp.store import Store
from rigel_mcp.text import query_terms


def libmap_of(hub, data=None):
    hub.write_map(data)
    state = load_map(hub.map_file)
    assert state.valid, state.errors
    return state.map


def new_store(tmp_path):
    return Store(tmp_path / "db" / "study.db")


def keys(store):
    return set(store.docs_index())


def test_subfolders_single_and_file_lessons_are_mapped(tmp_path, hub):
    store = new_store(tmp_path)
    rep = sync_map(store, libmap_of(hub), None)

    assert keys(store) == hub.expected  # lesson names: folders, "_chung", "_lo_trinh", "Bai_giang"
    assert len(rep.added) == 9 and not rep.errors and not rep.updated and rep.mode == "map"
    # no SmartDrive here: lessons are found by the bounded walk, loose root files by listing, the roadmap as a file root
    assert rep.discovery == {"walk": 7, "listing": 1, "file": 1}
    assert "Cách tìm file:" in rep.render() and "duyệt thư mục có giới hạn 7" in rep.render()

    doc = store.docs_index()[("GCI", "02_NumPy", "notes.md")]
    f = hub.materials / "02_NumPy" / "notes.md"
    st = f.stat()
    assert doc["origin"] == "map" and doc["abs_path"] == f.as_posix()
    assert doc["size"] == st.st_size and doc["mtime"] == st.st_mtime

    hit = store.search(query_terms("broadcasting"), "GCI", None, 5)[0]
    assert (hit["course"], hit["lesson"], hit["file"], hit["path"]) == ("GCI", "02_NumPy", "notes.md", f.as_posix())
    assert store.search(query_terms("broadcasting"), "ML", None, 5) == []  # courses stay separate


def test_hidden_names_are_always_skipped_and_hidden_lessons_are_not_lessons(tmp_path, hub):
    (hub.materials / ".git").mkdir()
    (hub.materials / ".git" / "config.md").write_text("# git\n\nkhông phải bài\n", encoding="utf-8")
    (hub.materials / ".loose.md").write_text("# ẩn\n\nfile ẩn ở gốc\n", encoding="utf-8")
    (hub.materials / "~$overview.md").write_text("# khóa\n\nfile khóa\n", encoding="utf-8")
    data = hub.map_dict()
    data["exclude"] = []  # even with no excludes at all, hidden names stay out
    data["courses"][0]["exclude"] = []
    store = new_store(tmp_path)
    sync_map(store, libmap_of(hub, data), None)
    rels = {rel for _, _, rel in keys(store)}
    assert not any(".hidden" in r or "~$" in r or ".loose" in r or "config.md" in r for r in rels)
    assert not any(lesson in (".git", ".hidden") for _, lesson, _ in keys(store))
    assert ("GCI", "01_Intro", "slides.md") in keys(store)


def test_global_and_per_course_excludes_each_take_effect(tmp_path, hub):
    store = new_store(tmp_path)
    sync_map(store, libmap_of(hub), None)
    assert ("GCI", "node_modules", "pkg.md") not in keys(store)  # global exclude: node_modules is not a lesson
    assert ("GCI", "02_NumPy", "dap_an/answers.md") not in keys(store)  # course exclude
    assert ("GCI", "01_Intro", "__pycache__/junk.py") not in keys(store)

    # drop each list in turn and the excluded files show up, so each list really was responsible
    data = hub.map_dict()
    data["exclude"] = ["__pycache__"]
    data["courses"][0]["exclude"] = []
    store2 = new_store(tmp_path / "second")
    sync_map(store2, libmap_of(hub, data), None)
    assert ("GCI", "node_modules", "pkg.md") in keys(store2)
    assert ("GCI", "02_NumPy", "dap_an/answers.md") in keys(store2)
    assert ("GCI", "01_Intro", "__pycache__/junk.py") not in keys(store2)  # still in the global list

    data["exclude"] = ["__pycache__", "*.TXT", "NODE_MODULES"]  # globs, and case never matters
    data["courses"][0]["exclude"] = ["DAP_*"]
    store3 = new_store(tmp_path / "third")
    sync_map(store3, libmap_of(hub, data), None)
    k3 = keys(store3)
    assert ("GCI", "Bai_giang", "lecture.txt") not in k3 and ("GCI", "node_modules", "pkg.md") not in k3
    assert ("GCI", "02_NumPy", "dap_an/answers.md") not in k3


def test_oversize_files_are_skipped_with_a_warning(tmp_path, hub):
    big = hub.materials / "02_NumPy" / "big.md"
    big.write_text("# Lớn\n\n" + "từ " * 2000, encoding="utf-8")  # several KB
    data = hub.map_dict()
    data["maxFileMB"] = 0.001  # about 1 KB: the small test files fit, big.md does not
    store = new_store(tmp_path)
    rep = sync_map(store, libmap_of(hub, data), None)
    assert ("GCI", "02_NumPy", "big.md") not in keys(store)
    assert ("GCI", "02_NumPy", "notes.md") in keys(store)
    assert any("big.md" in w and "lớn hơn 0.001 MB" in w for w in rep.warnings)


def test_unchanged_files_are_not_hashed_again(tmp_path, hub, monkeypatch):
    calls: list = []
    real = ing.sha256_file

    def counting(path):
        calls.append(path)
        return real(path)

    monkeypatch.setattr(ing, "sha256_file", counting)
    store = new_store(tmp_path)
    libmap = libmap_of(hub)

    first = sync_map(store, libmap, None)
    assert len(first.added) == 9 and len(calls) == 9  # one hash per new file (shared with the ingest step)

    calls.clear()
    second = sync_map(store, libmap, None)
    assert calls == [] and second.unchanged == 9 and not second.updated  # same size and mtime: no read at all

    # touched but identical content: hashed once, not re-ingested, new mtime remembered
    notes = hub.materials / "02_NumPy" / "notes.md"
    ids_before = {k: v["id"] for k, v in store.docs_index().items()}
    stamp = notes.stat().st_mtime + 120
    os.utime(notes, (stamp, stamp))
    calls.clear()
    third = sync_map(store, libmap, None)
    assert calls == [notes] and third.unchanged == 9 and not third.updated and not third.added
    assert store.docs_index()[("GCI", "02_NumPy", "notes.md")]["mtime"] == notes.stat().st_mtime
    calls.clear()
    assert sync_map(store, libmap, None).unchanged == 9 and calls == []  # and now it is skipped again

    # really changed content: re-ingested under the same doc id (refs stay valid)
    notes.write_text(notes.read_text(encoding="utf-8") + "\n## Thêm\n\nMột mục mới.\n", encoding="utf-8")
    fourth = sync_map(store, libmap, None)
    assert [p.rsplit("/", 1)[-1] for p in fourth.updated] == ["notes.md"] and fourth.unchanged == 8
    assert {k: v["id"] for k, v in store.docs_index().items()} == ids_before


def test_removal_only_touches_docs_of_the_active_mode(tmp_path, hub):
    store = new_store(tmp_path)
    chunk = ing.Chunk("đầu file", "", "nội dung thủ công đủ dài để tìm kiếm được.")
    for course, origin in (("Tay", "manual"), ("Cu", "library")):
        store.upsert_doc(
            course=course, lesson="L", rel_path="@x.md", kind="text", sha256="0" * 64, origin=origin, chunks=[chunk]
        )
    libmap = libmap_of(hub)
    sync_map(store, libmap, None)
    assert ("Tay", "L", "@x.md") in keys(store) and ("Cu", "L", "@x.md") in keys(store)  # not map docs: left alone

    (hub.materials / "02_NumPy" / "notes.md").unlink()
    rep = sync_map(store, libmap, None)
    assert rep.removed == ["GCI/02_NumPy/notes.md"]
    assert ("Tay", "L", "@x.md") in keys(store) and ("Cu", "L", "@x.md") in keys(store)

    # a course dropped from the map loses its map docs, nothing else
    data = hub.map_dict()
    data["courses"] = data["courses"][:1]
    rep = sync_map(store, libmap_of(hub, data), None)
    assert sorted(rep.removed) == ["ML/01_Regression/regression.py", "ML/02_Classification/notes.md"]
    assert ("Tay", "L", "@x.md") in keys(store)

    # and the classic mode only ever removes classic docs
    empty_library = tmp_path / "empty-lib"
    empty_library.mkdir()
    classic = ing.sync_library(store, empty_library)
    assert classic.removed == ["Cu/L/@x.md"]
    assert ("Tay", "L", "@x.md") in keys(store) and ("GCI", "01_Intro", "slides.md") in keys(store)


def test_unreachable_source_keeps_its_docs_and_refs(tmp_path, hub):
    store = new_store(tmp_path)
    libmap = libmap_of(hub)
    sync_map(store, libmap, None)
    ids_before = {k: v["id"] for k, v in store.docs_index().items()}

    parked = hub.materials.with_name("03_Materials_parked")
    hub.materials.rename(parked)  # like an unplugged SSD or a renamed folder
    rep = sync_map(store, libmap, None)
    assert rep.removed == [] and rep.kept == 4  # overview.md, 01_Intro/slides.md, 01_Intro/code/hello.py, 02_NumPy/notes.md
    assert any("không đọc được thư mục" in w for w in rep.warnings)
    assert "Giữ nguyên 4 tài liệu" in rep.render()
    # the other roots of the same course are still synced normally
    assert ("GCI", "Bai_giang", "lecture.txt") in keys(store)

    parked.rename(hub.materials)
    rep = sync_map(store, libmap, None)
    assert rep.unchanged == 9 and not rep.added and rep.kept == 0
    assert {k: v["id"] for k, v in store.docs_index().items()} == ids_before  # refs never moved


def test_same_lesson_and_name_from_two_roots_is_reported_not_duplicated(tmp_path, hub):
    other = hub.root / "GCI_Subject" / "07_More"
    other.mkdir()
    (other / "lecture.txt").write_text("Một file khác cùng tên.\n", encoding="utf-8")
    data = hub.map_dict()
    data["courses"][0]["roots"].append({"path": other.as_posix(), "lessons": "single", "lesson": "Bai_giang"})
    store = new_store(tmp_path)
    rep = sync_map(store, libmap_of(hub, data), None)
    assert keys(store) == hub.expected  # first root wins
    assert any("trùng tên" in w and "lecture.txt" in w for w in rep.warnings)


def test_missing_file_root_is_a_warning_not_a_crash(tmp_path, hub):
    store = new_store(tmp_path)
    libmap = libmap_of(hub)
    sync_map(store, libmap, None)
    (hub.root / "GCI_Subject" / "roadmap" / "ROADMAP.md").unlink()
    rep = sync_map(store, libmap, None)
    assert rep.removed == [] and rep.kept == 1 and any("không thấy file" in w for w in rep.warnings)


# ---- the bounded walk -----------------------------------------------------------------------


def make_deep(root, levels):
    folder = root
    for i in range(1, levels + 1):
        folder = folder / f"d{i}"
        folder.mkdir()
        (folder / f"level{i}.md").write_text(f"# Cấp {i}\n\nfile ở cấp {i}.\n", encoding="utf-8")
    return folder


def test_fallback_walk_stops_at_depth_four(tmp_path, hub):
    lesson = hub.materials / "03_Deep"
    lesson.mkdir()
    (lesson / "top.md").write_text("# Gốc\n\nfile ở gốc bài.\n", encoding="utf-8")
    make_deep(lesson, 6)
    store = new_store(tmp_path)
    rep = sync_map(store, libmap_of(hub), None)
    found = {rel for course, lsn, rel in keys(store) if lsn == "03_Deep"}
    assert found == {"top.md", "d1/level1.md", "d1/d2/level2.md", "d1/d2/d3/level3.md", "d1/d2/d3/d4/level4.md"}
    assert any("sâu hơn 4 cấp" in w for w in rep.warnings)


def test_fallback_walk_caps_files_per_lesson(tmp_path, hub, monkeypatch):
    monkeypatch.setattr(discover, "WALK_MAX_FILES", 5)
    lesson = hub.materials / "03_Many"
    lesson.mkdir()
    for i in range(12):
        (lesson / f"f{i:02d}.md").write_text(f"# F{i}\n\nnội dung {i}\n", encoding="utf-8")
    store = new_store(tmp_path)
    rep = sync_map(store, libmap_of(hub), None)
    assert len([1 for _, lsn, _ in keys(store) if lsn == "03_Many"]) == 5
    assert any("03_Many" in w and "chỉ lấy 5 file" in w for w in rep.warnings)


@pytest.mark.skipif(os.name != "nt", reason="directory junctions are a Windows feature")
def test_junctions_are_never_followed(tmp_path, hub):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.md").write_text("# Ngoài\n\nkhông được nạp qua junction.\n", encoding="utf-8")
    lesson = hub.materials / "02_NumPy"
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(lesson / "link"), str(outside)], capture_output=True)
    if made.returncode != 0:
        pytest.skip("cannot create a junction here")
    store = new_store(tmp_path)
    sync_map(store, libmap_of(hub), None)
    assert not any("leak.md" in rel for _, _, rel in keys(store))
    assert ("GCI", "02_NumPy", "notes.md") in keys(store)


def test_symlinked_files_and_folders_are_never_followed(tmp_path, hub):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.md").write_text("# Ngoài\n\nkhông được nạp qua symlink.\n", encoding="utf-8")
    lesson = hub.materials / "02_NumPy"
    try:
        os.symlink(outside, lesson / "dir_link", target_is_directory=True)
        os.symlink(outside / "leak.md", lesson / "file_link.md")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need extra privileges on this machine")
    store = new_store(tmp_path)
    sync_map(store, libmap_of(hub), None)
    assert not any("leak.md" in rel or "link" in rel for _, _, rel in keys(store))


class StubEntry:
    """Just enough of os.DirEntry for discover._is_link."""

    def __init__(self, symlink=False, attributes=0, broken=False):
        self._symlink, self._attributes, self._broken = symlink, attributes, broken

    def is_symlink(self):
        return self._symlink

    def stat(self, follow_symlinks=True):
        if self._broken:
            raise OSError("cannot stat")
        return types.SimpleNamespace(st_file_attributes=self._attributes)


def test_link_detection_covers_symlinks_junctions_and_unreadable_entries():
    assert discover._is_link(StubEntry(symlink=True))
    assert discover._is_link(StubEntry(attributes=0x400))  # reparse point: a junction or a cloud placeholder
    assert discover._is_link(StubEntry(attributes=0x410))  # directory + reparse point
    assert not discover._is_link(StubEntry(attributes=0x10))  # a plain directory
    assert not discover._is_link(StubEntry())
    assert discover._is_link(StubEntry(broken=True))  # when in doubt, do not follow


# ---- database migration ---------------------------------------------------------------------

OLD_SCHEMA = """
CREATE TABLE docs (
    id INTEGER PRIMARY KEY, course TEXT NOT NULL, lesson TEXT NOT NULL, rel_path TEXT NOT NULL,
    kind TEXT NOT NULL, sha256 TEXT NOT NULL, origin TEXT NOT NULL DEFAULT 'library',
    n_chunks INTEGER NOT NULL DEFAULT 0, ingested_at TEXT NOT NULL, UNIQUE (course, lesson, rel_path));
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY, doc_id INTEGER NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL, locator TEXT NOT NULL, heading TEXT NOT NULL DEFAULT '', text TEXT NOT NULL,
    UNIQUE (doc_id, seq));
CREATE VIRTUAL TABLE chunks_fts USING fts5(heading, body, tokenize = 'porter unicode61');
CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def test_database_from_0_1_0_is_migrated_in_place(tmp_path, cfg):
    note = cfg.library / "PRJ301" / "L03-OOP" / "notes.md"
    db = tmp_path / "old" / "study.db"
    db.parent.mkdir()
    conn = sqlite3.connect(db)
    conn.executescript(OLD_SCHEMA)
    conn.execute(
        "INSERT INTO docs (id, course, lesson, rel_path, kind, sha256, origin, n_chunks, ingested_at)"
        " VALUES (7, 'PRJ301', 'L03-OOP', 'notes.md', 'text', ?, 'library', 1, '2026-10-01T00:00:00+00:00')",
        (ing.sha256_file(note),),
    )
    conn.execute("INSERT INTO chunks (id, doc_id, seq, locator, heading, text) VALUES (1, 7, 1, 'đầu file', '', 'cũ')")
    conn.commit()
    conn.close()

    store = Store(db)  # migrates
    with sqlite3.connect(db) as check:
        columns = {r[1] for r in check.execute("PRAGMA table_info(docs)")}
    assert {"size", "mtime", "abs_path"} <= columns
    old = store.docs_index()[("PRJ301", "L03-OOP", "notes.md")]
    assert old["id"] == 7 and old["size"] is None and old["abs_path"] is None  # data kept, new columns empty

    Store(db)  # opening it again must be harmless
    rep = ing.sync_library(store, cfg.library)
    assert "L03-OOP/notes.md" not in " ".join(rep.updated)  # same sha: no re-ingest, the old doc id stays valid
    now = store.docs_index()[("PRJ301", "L03-OOP", "notes.md")]
    assert now["id"] == 7 and now["size"] == note.stat().st_size and now["abs_path"] == note.as_posix()

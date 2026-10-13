from rigel_mcp import ingest as ing
from rigel_mcp.store import Store, make_ref, parse_ref
from rigel_mcp.text import query_terms

LESSON_DIR = ("PRJ301", "L03-OOP")


def search(store, q, course=None, lesson=None, limit=5):
    return store.search(query_terms(q), course, lesson, limit)


def test_parsers_extract_text_headings_and_locators(library):
    lesson = library.joinpath(*LESSON_DIR)

    _, pptx = ing.parse_file(lesson / "Lecture3.pptx")
    assert [c.locator for c in pptx.chunks] == ["slide 1", "slide 2"]
    assert pptx.chunks[0].heading == "Polymorphism"
    assert "Overriding vs overloading" in pptx.chunks[0].text
    assert "Ghi chú: Nhấn mạnh dynamic dispatch" in pptx.chunks[0].text

    _, docx = ing.parse_file(lesson / "Handout.docx")
    assert [c.heading for c in docx.chunks] == ["Lập trình hướng đối tượng", "Lập trình hướng đối tượng > Đa hình"]
    assert "Overload | Cùng tên, khác tham số" in docx.chunks[1].text

    _, pdf = ing.parse_file(lesson / "Slides.pdf")
    assert [(c.locator, c.heading) for c in pdf.chunks] == [("trang 1", "Inheritance"), ("trang 2", "Abstraction")]

    _, md = ing.parse_file(lesson / "notes.md")
    assert [c.heading for c in md.chunks] == ["Đa hình", "Đa hình > Overload"]
    assert "# comment này không phải heading" in md.chunks[0].text  # inside a code fence: not a heading

    _, java = ing.parse_file(lesson / "Shape.java")
    assert java.chunks[0].locator == "dòng 1–3"
    assert java.chunks[0].heading.startswith("public class Shape")


def test_sync_lifecycle_add_update_remove_and_bad_file(cfg):
    store = Store(cfg.db_path)
    lesson = cfg.library.joinpath(*LESSON_DIR)

    first = ing.sync_library(store, cfg.library)
    assert len(first.added) == 5 and not first.errors and not first.updated
    assert ing.sync_library(store, cfg.library).unchanged == 5

    ref_before = search(store, "overriding")[0]["ref"]
    notes = lesson / "notes.md"
    notes.write_text(notes.read_text(encoding="utf-8") + "\n## Mới\n\nThêm một mục.\n", encoding="utf-8")
    second = ing.sync_library(store, cfg.library)
    assert [p.split("/")[-1] for p in second.updated] == ["notes.md"] and second.unchanged == 4
    assert search(store, "overriding")[0]["ref"] == ref_before  # refs stay stable across re-ingest

    (lesson / "Shape.java").unlink()
    (lesson / "broken.pdf").write_bytes(b"this is not a pdf")
    third = ing.sync_library(store, cfg.library)
    assert [p.split("/")[-1] for p in third.removed] == ["Shape.java"]
    assert len(third.errors) == 1 and "broken.pdf" in third.errors[0]
    assert search(store, "polymorphism")  # the other documents are untouched


def test_search_without_diacritics_scope_and_no_match(cfg):
    store = Store(cfg.db_path)
    ing.sync_library(store, cfg.library)

    assert search(store, "da hinh")[0]["file"] in {"Handout.docx", "notes.md"}
    top = search(store, "polymorphism")[0]
    assert (top["file"], top["locator"]) == ("Lecture3.pptx", "slide 1")
    assert search(store, "interfaces contracts", *LESSON_DIR)[0]["file"] == "Slides.pdf"
    assert search(store, "polymorphism", course="Khác") == []
    assert search(store, "quantum chromodynamics") == []


def test_coverage_is_honest_and_english_inflection_is_stemmed(cfg):
    store = Store(cfg.db_path)
    ing.sync_library(store, cfg.library)

    # "con" is a common Vietnamese word ("lớp con"), but "trỏ" and "null" appear nowhere:
    # whatever matches must show low coverage so the server can reject it.
    assert all(h["coverage"] < 0.5 for h in search(store, "con trỏ null"))
    assert search(store, "da hinh")[0]["coverage"] == 1.0

    # porter stemming: plural and -ing forms find the same chunks
    assert {h["file"] for h in search(store, "overrides")} >= {"Handout.docx", "notes.md", "Lecture3.pptx"}
    assert search(store, "classes")[0]["coverage"] == 1.0


def test_refs_windows_outline_and_sample(cfg):
    store = Store(cfg.db_path)
    ing.sync_library(store, cfg.library)

    assert parse_ref("d3#12") == (3, 12) and make_ref(3, 12) == "D3#12" and parse_ref("x") is None

    hit = search(store, "polymorphism")[0]
    found, missing = store.resolve_refs([hit["ref"], "D999#1", "oops"])
    assert list(found) == [hit["ref"]] and missing == ["D999#1", "oops"]

    doc_id, seq = parse_ref(hit["ref"])
    assert [h["locator"] for h in store.window(doc_id, seq, seq + 1)] == ["slide 1", "slide 2"]

    outline = store.outline("PRJ301", None)
    assert {r["rel_path"] for r in outline} == {"Lecture3.pptx", "Handout.docx", "Slides.pdf", "notes.md", "Shape.java"}
    assert len(store.sample("PRJ301", "L03-OOP", 2, seed=1)) == 2
    assert store.sample("Khác", None, 2) == []

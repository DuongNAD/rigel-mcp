import asyncio
import hashlib
import json
import re
from datetime import datetime

import pytest
from mcp import Client

from rigel_mcp import vega
from rigel_mcp.config import Config
from rigel_mcp.server import build_server

FILE_NAME = re.compile(r"^rigel-\d{8}T\d{6}-[0-9a-f]{6}\.jsonl$")
CHECK_MARK = chr(0x2705)  # an emoji, built from its code point so this file stays emoji-free


def text(r):
    return r.content[0].text


def run(cfg, flow):
    async def go():
        async with Client(build_server(cfg)) as c:
            return await flow(c)

    return asyncio.run(go())


def good_line(**over):
    line = vega.build_line(
        now="2026-10-13T09:30:00+07:00",
        course="GCI",
        subject="GCI_Subject",
        deck="Buổi 2",
        front="NumPy ndarray là gì?",
        back="Mảng nhiều chiều cùng kiểu dữ liệu.",
        ref="D3#12",
        file_label="notes.md",
        locator="slide 7",
        path="D:/hub/GCI_Subject/notes.md",
        tags=["GCI", "02_NumPy"],
    )
    line.update(over)
    return line


# ---- the strict validator -------------------------------------------------------------------


def test_built_line_follows_the_contract():
    line = good_line()
    assert vega.validate_line(line) == []
    front = "NumPy ndarray là gì?"
    assert line == {
        "v": 1,
        "t": "2026-10-13T09:30:00+07:00",
        "producer": "rigel",
        "key": f"rigel:GCI_Subject:D3#12:{hashlib.sha1(front.encode('utf-8')).hexdigest()[:8]}",
        "subject": "GCI_Subject",
        "deck": "Buổi 2",
        "kind": "basic",
        "front": front,
        "back": "Mảng nhiều chiều cùng kiểu dữ liệu.\nNguồn: notes.md · slide 7",
        "origin": {"kind": "rigel-ref", "path": "D:/hub/GCI_Subject/notes.md", "locator": "D3#12 · slide 7"},
        "tags": ["GCI", "02_NumPy"],
        "reason": "Thẻ từ tài liệu bài học (Rigel)",
    }
    assert vega.build_line(**{**_args(), "subject": None})["subject"] == "GCI"  # no subject in the map: the course name
    assert "deck" not in vega.build_line(**{**_args(), "deck": "  "})


def _args():
    return dict(
        now="2026-10-13T09:30:00+07:00", course="GCI", subject="S", deck="d", front="f?", back="b", ref="D1#1",
        file_label="a.md", locator="slide 1", path="D:/a.md", tags=[],
    )  # fmt: skip


@pytest.mark.parametrize(
    "change,fragment",
    [
        ({"extra": 1}, 'trường lạ "extra"'),
        ({"v": 2}, "v phải là số nguyên 1"),
        ({"v": True}, "v phải là số nguyên 1"),
        ({"t": "2026-10-13T09:30:00"}, "múi giờ"),
        ({"t": "2026-10-13"}, "múi giờ"),
        ({"t": "2026-10-13 09:30:00+07:00"}, "múi giờ"),
        ({"t": 5}, "múi giờ"),
        ({"producer": "nobody"}, "producer"),
        ({"producer": ["rigel"]}, "producer"),
        ({"key": "polaris:GCI:D1#1:abcd1234"}, "key phải bắt đầu bằng rigel:"),
        ({"key": "rigel:" + "x" * 200}, "key dài"),
        ({"key": ""}, "key dài 0"),
        ({"subject": ""}, "subject dài 0"),
        ({"subject": "s" * 121}, "subject dài 121"),
        ({"deck": "d" * 121}, "deck dài 121"),
        ({"kind": "weird"}, "kind phải là"),
        ({"front": ""}, "front dài 0"),
        ({"front": "   "}, "front không được chỉ gồm khoảng trắng"),
        ({"front": "q" * 601}, "front dài 601"),
        ({"front": "Đúng rồi " + CHECK_MARK}, "front chứa emoji"),
        ({"front": "Cười \U0001F600"}, "front chứa emoji"),
        ({"front": "Cờ \U0001F1FB\U0001F1F3"}, "front chứa emoji"),
        ({"back": "b" * 1201}, "back dài 1201"),
        ({"back": 5}, "back phải là chuỗi"),
        ({"hint": "h" * 201}, "hint dài 201"),
        ({"hook": "h" * 201}, "hook dài 201"),
        ({"reason": "r" * 201}, "reason dài 201"),
        ({"origin": "x"}, "origin phải là một đối tượng"),
        ({"origin": {"kind": "rigel-ref", "path": "p", "extra": 1}}, 'origin có trường lạ "extra"'),
        ({"origin": {"kind": "nope", "path": "p"}}, "origin.kind"),
        ({"origin": {"kind": "rigel-ref", "path": ""}}, "origin.path dài 0"),
        ({"origin": {"kind": "rigel-ref", "path": "p" * 401}}, "origin.path dài 401"),
        ({"origin": {"kind": "rigel-ref", "path": "p", "locator": "l" * 121}}, "origin.locator dài 121"),
        ({"origin": {"kind": "rigel-ref", "path": "p", "href": "h" * 401}}, "origin.href dài 401"),
        ({"tags": "a"}, "tags phải là danh sách"),
        ({"tags": [f"t{i}" for i in range(11)]}, "tags có 11 mục"),
        ({"tags": ["t" * 41]}, "tags[0]"),
        ({"tags": ["ok", ""]}, "tags[1]"),
        ({"tags": ["ok", 3]}, "tags[1]"),
    ],
)
def test_validator_rejects_every_contract_violation(change, fragment):
    problems = vega.validate_line(good_line(**change))
    assert problems and any(fragment in p for p in problems), problems


def test_validator_accepts_the_edges_of_every_limit():
    edge = good_line(
        front="q" * 600, back="", hint="h" * 200, hook="h" * 200, reason="r" * 200, subject="s" * 120,
        key="rigel:" + "k" * 194, deck="d" * 120, kind="cloze", t="2026-10-13T02:30:00Z",
        tags=["t" * 40] * 10,
        origin={"kind": "rigel-ref", "path": "p" * 400, "locator": "l" * 120, "href": "h" * 400},
    )  # fmt: skip
    assert vega.validate_line(edge) == []
    for kind in vega.KINDS:
        assert vega.validate_line(good_line(kind=kind)) == []
    for origin_kind in vega.ORIGIN_KINDS:
        assert vega.validate_line(good_line(origin={"kind": origin_kind, "path": "p"})) == []
    assert vega.validate_line("not a dict") and vega.validate_line(None) and vega.validate_line([])
    for producer in vega.PRODUCERS:
        assert vega.validate_line(good_line(producer=producer, key=f"{producer}:k")) == []


def test_tags_are_clipped_deduplicated_and_capped_for_the_contract():
    tags = vega.clean_tags(["GCI", " GCI ", "x" * 60, "", "  "] + [f"t{i}" for i in range(20)])
    assert tags[0] == "GCI" and tags[1] == "x" * 40 and len(tags) == 10 and len(set(tags)) == 10
    line = vega.build_line(**{**_args(), "tags": ["x" * 60] * 3 + [f"t{i}" for i in range(15)]})
    assert vega.validate_line(line) == []
    long_locator = vega.build_line(**{**_args(), "locator": "§ " + "Tiêu đề rất dài " * 20})
    assert len(long_locator["origin"]["locator"]) == 120 and vega.validate_line(long_locator) == []


def test_inbox_file_is_written_atomically_and_never_reused(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    hub.mkdir()
    lines = [good_line(), good_line(front="Câu khác?", key="rigel:S:D1#2:aaaaaaaa")]
    first = vega.write_inbox(hub, lines, datetime(2026, 10, 13, 9, 30, 5))
    assert first.parent == hub / ".vega" / "inbox" and FILE_NAME.match(first.name)
    assert first.name.startswith("rigel-20261013T093005-")
    raw = first.read_bytes()
    assert raw.endswith(b"\n") and b"\r" not in raw  # LF, trailing newline
    assert [json.loads(x) for x in raw.decode("utf-8").splitlines()] == lines
    assert "Mảng nhiều chiều" in raw.decode("utf-8")  # real UTF-8, not \u escapes
    second = vega.write_inbox(hub, lines, datetime(2026, 10, 13, 9, 30, 5))  # same second: still a new file
    assert second != first and first.read_bytes() == raw
    assert sorted(p.name for p in first.parent.iterdir()) == sorted([first.name, second.name])  # no *.tmp left

    def fail(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(vega.os, "replace", fail)
    with pytest.raises(OSError):
        vega.write_inbox(hub, lines)
    assert sorted(p.name for p in first.parent.iterdir()) == sorted([first.name, second.name])  # temp file cleaned up


# ---- export_flashcards through the server ---------------------------------------------------


async def get_ref(c):
    await c.call_tool("sync_library", {})
    r = await c.call_tool("search_material", {"queries": ["broadcasting"], "where": "all"})
    hit = r.structured_content["hits"][0]
    return hit["ref"], hit


def deck_cards(ref):
    return [
        {"front": "NumPy broadcasting là gì?", "back": "Cơ chế căn chỉnh hình dạng mảng khi tính toán.", "ref": ref, "tags": ["numpy core"]},
        {"front": "Thẻ không căn cứ", "back": "x", "ref": "D999#1"},
        {"front": f"Thẻ có emoji {CHECK_MARK} ở mặt trước", "back": "x", "ref": ref},
        {"front": "Thẻ có mặt sau quá dài", "back": "y" * 1300, "ref": ref},
    ]


def inbox_files(hub):
    folder = hub.root / ".vega" / "inbox"
    return sorted(folder.iterdir()) if folder.exists() else []


def test_export_to_vega_writes_one_valid_inbox_file(map_cfg, hub):
    async def flow(c):
        ref, hit = await get_ref(c)
        r = await c.call_tool("export_flashcards", {"deck": "GCI Buổi 2", "cards": deck_cards(ref), "to": "vega"})
        assert not r.is_error, text(r)
        return ref, hit, r

    ref, hit, r = run(map_cfg, flow)
    files = inbox_files(hub)
    assert len(files) == 1 and FILE_NAME.match(files[0].name)  # exactly one file, no *.tmp left over

    lines = [json.loads(x) for x in files[0].read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1  # the other three cards were rejected
    line = lines[0]
    assert vega.validate_line(line) == []
    notes = hub.materials / "02_NumPy" / "notes.md"
    front = "NumPy broadcasting là gì?"
    assert line["producer"] == "rigel" and line["subject"] == "GCI_Subject" and line["deck"] == "GCI Buổi 2"
    assert line["key"] == f"rigel:GCI_Subject:{ref}:{hashlib.sha1(front.encode('utf-8')).hexdigest()[:8]}"
    assert line["front"] == front and line["kind"] == "basic"
    assert line["back"] == f"Cơ chế căn chỉnh hình dạng mảng khi tính toán.\nNguồn: notes.md · {hit['locator']}"
    assert line["origin"] == {"kind": "rigel-ref", "path": notes.as_posix(), "locator": f"{ref} · {hit['locator']}"}
    assert line["tags"] == ["GCI", "02_NumPy", "numpy_core"] and line["reason"] == "Thẻ từ tài liệu bài học (Rigel)"
    assert datetime.fromisoformat(line["t"]).tzinfo is not None

    data = r.structured_content
    assert data["to"] == "vega" and data["inbox"] == str(files[0]) and data["path"] is None
    assert [a["ref"] for a in data["accepted"]] == [ref]  # only what went to the inbox
    assert [x["ref"] for x in data["rejected"]] == ["D999#1"]  # rejected refs: exactly as the Anki export does it
    assert data["rejected"][0]["reason"] == "ref không tồn tại; mỗi thẻ phải bám một đoạn tài liệu"
    reasons = {x["reason"] for x in data["vega_rejected"]}
    assert len(data["vega_rejected"]) == 2 and any("emoji" in x for x in reasons) and any("back dài" in x for x in reasons)
    body = text(r)
    assert str(files[0]) in body and "vega_sync" in body and "Anki: File" not in body
    assert not map_cfg.exports.exists() or not list(map_cfg.exports.iterdir())  # to="vega" writes no TSV


def test_default_export_is_anki_only_and_never_touches_the_inbox(map_cfg, hub):
    async def flow(c):
        ref, _ = await get_ref(c)
        r = await c.call_tool("export_flashcards", {"deck": "GCI", "cards": deck_cards(ref)[:2]})
        assert not r.is_error and r.structured_content["to"] == "anki" and r.structured_content["inbox"] is None
        assert "vega_sync" not in text(r) and "Anki: File" in text(r)

    run(map_cfg, flow)
    assert not (hub.root / ".vega").exists()  # not even the folder
    assert [p.name for p in map_cfg.exports.iterdir()] == ["gci.tsv"]


def test_export_to_both_writes_the_tsv_and_the_inbox(map_cfg, hub):
    async def flow(c):
        ref, _ = await get_ref(c)
        r = await c.call_tool("export_flashcards", {"deck": "GCI", "cards": deck_cards(ref), "to": "both"})
        assert not r.is_error
        return r

    r = run(map_cfg, flow)
    data = r.structured_content
    assert (map_cfg.exports / "gci.tsv").exists() and len(inbox_files(hub)) == 1
    assert len(data["accepted"]) == 3 and len(data["vega_rejected"]) == 2  # Anki takes the emoji and long cards
    assert len((map_cfg.exports / "gci.tsv").read_text(encoding="utf-8").splitlines()) == 3 + 3
    assert "Đã xuất 3 thẻ" in text(r) and "hộp thư Vega" in text(r) and "Vega từ chối 2 thẻ" in text(r)


def test_nothing_valid_for_vega_is_an_error_and_writes_nothing(map_cfg, hub):
    async def flow(c):
        ref, _ = await get_ref(c)
        r = await c.call_tool("export_flashcards", {"deck": "x", "cards": deck_cards(ref)[1:], "to": "vega"})
        assert r.is_error and "Không thẻ nào hợp lệ cho Vega" in text(r) and "emoji" in text(r)
        r = await c.call_tool("export_flashcards", {"deck": "x", "cards": deck_cards(ref)[1:2], "to": "vega"})
        assert r.is_error and "Không thẻ nào hợp lệ" in text(r)  # a bad ref is rejected before Vega is even considered

    run(map_cfg, flow)
    assert inbox_files(hub) == []


def test_two_exports_make_two_files_and_duplicates_in_a_batch_are_flagged(map_cfg, hub):
    async def flow(c):
        ref, _ = await get_ref(c)
        card = deck_cards(ref)[0]
        r = await c.call_tool("export_flashcards", {"deck": "a", "cards": [card, card], "to": "vega"})
        assert [x["ref"] for x in r.structured_content["vega_rejected"]] == [ref]
        assert "trùng thẻ" in r.structured_content["vega_rejected"][0]["reason"]
        await c.call_tool("export_flashcards", {"deck": "a", "cards": [card], "to": "vega"})

    run(map_cfg, flow)
    files = inbox_files(hub)
    assert len(files) == 2 and files[0] != files[1]
    keys = [json.loads(f.read_text(encoding="utf-8").strip())["key"] for f in files]
    assert keys[0] == keys[1]  # same card exported twice: same key, so Vega de-duplicates it


def test_hub_resolution_env_then_map_then_default(map_cfg, hub, tmp_path, monkeypatch):
    other = tmp_path / "other-hub"
    other.mkdir()
    no_explicit = Config(home=map_cfg.home, library=map_cfg.library, notes=map_cfg.notes, map_path=hub.map_file)

    async def flow(c):
        ref, _ = await get_ref(c)
        return await c.call_tool("export_flashcards", {"deck": "a", "cards": deck_cards(ref)[:1], "to": "vega"})

    run(no_explicit, flow)  # nothing set: the map's own "hub"
    assert len(inbox_files(hub)) == 1
    monkeypatch.setenv("RIGEL_HUB", str(other))
    run(no_explicit, flow)  # RIGEL_HUB beats the map
    assert len(inbox_files(hub)) == 1 and len(list((other / ".vega" / "inbox").iterdir())) == 1


def test_missing_hub_folder_is_an_error_and_is_not_created(map_cfg, hub, tmp_path):
    ghost = tmp_path / "unplugged-ssd" / "hub"
    cfg = Config(home=map_cfg.home, library=map_cfg.library, notes=map_cfg.notes, map_path=hub.map_file, hub=ghost)

    async def flow(c):
        ref, _ = await get_ref(c)
        r = await c.call_tool("export_flashcards", {"deck": "a", "cards": deck_cards(ref)[:1], "to": "vega"})
        assert r.is_error and "Không thấy thư mục hub" in text(r) and "RIGEL_HUB" in text(r)

    run(cfg, flow)
    assert not ghost.exists() and not ghost.parent.exists()


def test_classic_mode_uses_the_course_name_as_subject_and_the_relative_path(cfg, tmp_path):
    hub_dir = tmp_path / "hub"
    hub_dir.mkdir()
    cfg = Config(home=cfg.home, library=cfg.library, notes=cfg.notes, hub=hub_dir)

    async def flow(c):
        await c.call_tool("sync_library", {})
        r = await c.call_tool("search_material", {"queries": ["polymorphism"], "where": "all"})
        ref = r.structured_content["hits"][0]["ref"]
        r = await c.call_tool("export_flashcards", {"deck": "PRJ301", "to": "vega", "cards": [{"front": "Đa hình là gì?", "back": "Nhiều dạng.", "ref": ref}]})
        assert not r.is_error, text(r)

    run(cfg, flow)
    (path,) = (hub_dir / ".vega" / "inbox").iterdir()
    line = json.loads(path.read_text(encoding="utf-8"))
    assert line["subject"] == "PRJ301" and line["key"].startswith("rigel:PRJ301:D")
    lecture = cfg.library / "PRJ301" / "L03-OOP" / "Lecture3.pptx"
    assert line["origin"]["path"] == lecture.as_posix() and line["tags"][:2] == ["PRJ301", "L03-OOP"]
    assert vega.validate_line(line) == []

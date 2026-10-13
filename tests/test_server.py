import asyncio

from mcp import Client

from rigel_mcp.server import build_server

EXPECTED_TOOLS = {
    "library_status",
    "sync_library",
    "add_material",
    "set_scope",
    "search_material",
    "read_chunks",
    "lesson_outline",
    "sample_material",
    "export_flashcards",
    "save_note",
}


def text(result):
    return result.content[0].text


async def flow(cfg, tmp_path):
    async with Client(build_server(cfg)) as c:
        tools = {t.name for t in (await c.list_tools()).tools}
        assert EXPECTED_TOOLS <= tools
        assert "search_material" in c.instructions

        r = await c.call_tool("library_status", {})
        assert "Chưa có tài liệu" in text(r)

        r = await c.call_tool("sync_library", {})
        assert not r.is_error and len(r.structured_content["added"]) == 5

        r = await c.call_tool("set_scope", {"course": "prj301", "lesson": "l03"})
        assert not r.is_error and "PRJ301 / L03-OOP" in text(r)
        r = await c.call_tool("set_scope", {"course": "nope"})
        assert r.is_error and "Không thấy môn" in text(r)

        r = await c.call_tool("search_material", {"queries": ["đa hình", "polymorphism"]})
        assert not r.is_error
        body = text(r)
        assert "coverage: strong" in body and "Giao thức trả lời" in body and "Lecture3.pptx" in body
        ref = r.structured_content["hits"][0]["ref"]
        assert ref.startswith("D") and "text" not in r.structured_content["hits"][0]

        # one noisy query (coverage 0.5, higher score) + one clean query (coverage 1.0) for the same slide
        r = await c.call_tool("search_material", {"queries": ["polymorphism overriding foo bar", "polymorphism"]})
        assert "coverage: strong" in text(r)

        for off_topic in (["quantum chromodynamics"], ["con trỏ null"]):  # the 2nd shares only "con" with the docs
            r = await c.call_tool("search_material", {"queries": off_topic})
            assert "coverage: none" in text(r) and r.structured_content["hits"] == []
        r = await c.call_tool("search_material", {"queries": ["the of"]})
        assert r.is_error

        r = await c.call_tool("read_chunks", {"refs": [ref], "context": 1})
        assert not r.is_error and "►" in text(r)
        r = await c.call_tool("read_chunks", {"refs": ["D999#1"]})
        assert r.is_error

        r = await c.call_tool("lesson_outline", {})
        assert "Lecture3.pptx" in text(r) and "slide 2 · Encapsulation" in text(r)
        r = await c.call_tool("sample_material", {"n": 2, "seed": 1})
        assert not r.is_error and "đoạn ngẫu nhiên" in text(r)

        cards = [
            {"front": "Đa hình là gì?", "back": "Một lời gọi, nhiều hành vi <tùy kiểu thực tế>", "ref": ref, "tags": ["oop core"]},
            {"front": "Thẻ không căn cứ", "back": "x", "ref": "D999#1"},
        ]
        r = await c.call_tool("export_flashcards", {"deck": "PRJ301 L03", "cards": cards})
        assert not r.is_error and len(r.structured_content["rejected"]) == 1
        tsv = (cfg.exports / "prj301-l03.tsv").read_text(encoding="utf-8").splitlines()
        assert tsv[:3] == ["#separator:tab", "#html:true", "#tags column:3"] and len(tsv) == 4
        assert "&lt;tùy kiểu thực tế&gt;" in tsv[3] and "oop_core" in tsv[3] and "PRJ301" in tsv[3]
        r = await c.call_tool("export_flashcards", {"deck": "x", "cards": cards[1:]})
        assert r.is_error

        r = await c.call_tool("save_note", {"title": "Đa hình – tóm tắt", "markdown": "## Ý chính\nMột lời gọi, nhiều hành vi.", "refs": [ref]})
        assert not r.is_error
        note = (cfg.notes / "PRJ301" / "L03-OOP" / "da-hinh-tom-tat.md").read_text(encoding="utf-8")
        assert note.startswith("---\ntitle:") and "sources:" in note and ref in note
        r = await c.call_tool("save_note", {"title": "t", "markdown": "m", "refs": ["D999#1"]})
        assert r.is_error

        extra = tmp_path / "extra.md"
        extra.write_text("# Interface\n\nHợp đồng mà lớp phải tuân theo, không có thân hàm.\n", encoding="utf-8")
        r = await c.call_tool("add_material", {"path": str(extra), "course": "PRJ301", "lesson": "L03-OOP"})
        assert not r.is_error and "1 đoạn" in text(r)
        hidden = tmp_path / ".secret" / "x.txt"
        hidden.parent.mkdir()
        hidden.write_text("khóa bí mật", encoding="utf-8")
        r = await c.call_tool("add_material", {"path": str(hidden), "course": "A", "lesson": "B"})
        assert r.is_error and "thư mục ẩn" in text(r)
        r = await c.call_tool("sync_library", {})
        assert r.structured_content["removed"] == []  # manually added docs survive a sync

        prompts = {p.name for p in (await c.list_prompts()).prompts}
        assert {"learn", "quiz", "recap"} <= prompts
        got = await c.get_prompt("learn", {"topic": "đa hình"})
        assert "đa hình" in got.messages[0].content.text and "Giao thức học" in got.messages[0].content.text
        got = await c.get_prompt("quiz", {"n": "3"})
        assert "sample_material(n=3)" in got.messages[0].content.text
        res = await c.read_resource("rigel://protocol")
        assert "Giao thức học" in res.contents[0].text


def test_full_tool_flow_in_process(cfg, tmp_path):
    asyncio.run(flow(cfg, tmp_path))

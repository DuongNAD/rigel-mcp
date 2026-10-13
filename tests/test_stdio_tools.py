import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from rigel_mcp import __version__

SRC = str(Path(__file__).resolve().parents[1] / "src")


def test_real_stdio_process_lists_the_new_tool_and_runs_the_map_flow(map_cfg, hub):
    """`python -m rigel_mcp` the way an agent launches it, configured only through environment variables."""

    async def main():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-B", "-m", "rigel_mcp"],
            env={
                **os.environ,
                "PYTHONPATH": SRC,
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUTF8": "1",
                "RIGEL_HOME": str(map_cfg.home),
                "RIGEL_LIBRARY": str(map_cfg.library),
                "RIGEL_NOTES": str(map_cfg.notes),
                "RIGEL_LIBRARY_MAP": str(hub.map_file),
                "RIGEL_HUB": str(hub.root),
            },
        )
        async with Client(params) as c:
            assert c.server_info.version == __version__ == "0.2.0"
            for line in ("scope_for_path", 'export_flashcards { to: "vega" }', "set_scope", "Mira", "Polaris", "locator"):
                assert line in c.instructions
            assert c.instructions.index("search_material") < c.instructions.index("scope_for_path")  # grounding rules come first

            tools = {t.name: t for t in (await c.list_tools()).tools}
            assert {"library_status", "sync_library", "add_material", "set_scope", "scope_for_path", "search_material",
                    "read_chunks", "lesson_outline", "sample_material", "export_flashcards", "save_note"} <= set(tools)  # fmt: skip
            assert tools["scope_for_path"].input_schema["required"] == ["path"]
            to = tools["export_flashcards"].input_schema["properties"]["to"]
            assert to["enum"] == ["anki", "vega", "both"] and to["default"] == "anki"

            r = await c.call_tool("sync_library", {})
            assert not r.is_error and len(r.structured_content["added"]) == 9 and r.structured_content["mode"] == "map"
            notes = hub.materials / "02_NumPy" / "notes.md"
            r = await c.call_tool("scope_for_path", {"path": str(notes)})
            assert not r.is_error and r.structured_content["scope"] == {"course": "GCI", "lesson": "02_NumPy"}
            r = await c.call_tool("search_material", {"queries": ["broadcasting"]})
            assert "notes.md" in r.content[0].text and "coverage: strong" in r.content[0].text
            ref = r.structured_content["hits"][0]["ref"]
            cards = [{"front": "Broadcasting là gì?", "back": "Căn chỉnh hình dạng mảng.", "ref": ref}]
            r = await c.call_tool("export_flashcards", {"deck": "GCI", "cards": cards, "to": "vega"})
            assert not r.is_error and Path(r.structured_content["inbox"]).parent == hub.root / ".vega" / "inbox"
            return r.structured_content["inbox"]

    inbox_file = asyncio.run(main())
    line = json.loads(Path(inbox_file).read_text(encoding="utf-8"))
    assert line["subject"] == "GCI_Subject" and line["producer"] == "rigel"
    assert len(map_cfg.activity_path.read_text(encoding="utf-8").splitlines()) == 3  # scope_for_path, search, export

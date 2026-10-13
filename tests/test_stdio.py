import asyncio
import os
import sys
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters

SRC = str(Path(__file__).resolve().parents[1] / "src")


def test_real_stdio_process(cfg):
    """Spawn `python -m rigel_mcp` exactly like Claude Desktop / Claude Code would."""

    async def main():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "rigel_mcp"],
            env={
                **os.environ,
                "PYTHONPATH": SRC,
                "RIGEL_HOME": str(cfg.home),
                "RIGEL_LIBRARY": str(cfg.library),
                "RIGEL_NOTES": str(cfg.notes),
            },
        )
        async with Client(params) as c:
            assert not (await c.call_tool("sync_library", {})).is_error
            r = await c.call_tool("search_material", {"queries": ["polymorphism"]})
            assert "Lecture3.pptx" in r.content[0].text

    asyncio.run(main())

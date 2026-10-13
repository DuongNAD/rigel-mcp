import asyncio
import json
import threading
from datetime import datetime

from mcp import Client

from rigel_mcp import activity
from rigel_mcp.server import build_server

ALLOWED_KEYS = {"v", "t", "tool", "course", "lesson", "subject", "coverage", "hits", "cards"}
SECRETS = [
    "ZEBRAQUERYSENTINEL",
    "ZEBRATITLESENTINEL",
    "ZEBRANOTESENTINEL",
    "ZEBRAFRONTSENTINEL",
    "ZEBRABACKSENTINEL",
    "ZEBRATAGSENTINEL",
]


def text(r):
    return r.content[0].text


def run(cfg, flow):
    async def go():
        async with Client(build_server(cfg)) as c:
            return await flow(c)

    return asyncio.run(go())


def read_log(cfg) -> list[dict]:
    path = cfg.activity_path
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_every_rigel_tool_leaves_one_line_of_evidence_and_never_any_text(map_cfg, hub):
    notes = hub.materials / "02_NumPy" / "notes.md"

    async def flow(c):
        await c.call_tool("sync_library", {})  # not a logged Rigel action: nothing recorded
        await c.call_tool("scope_for_path", {"path": str(notes)})
        r = await c.call_tool("search_material", {"queries": [f"broadcasting {SECRETS[0]}"]})
        assert not r.is_error
        ref = r.structured_content["hits"][0]["ref"]
        await c.call_tool("search_material", {"queries": ["quantum chromodynamics"]})
        r = await c.call_tool("search_material", {"queries": ["the of"]})  # an error: nothing happened, nothing logged
        assert r.is_error
        await c.call_tool("read_chunks", {"refs": [ref], "context": 1})
        await c.call_tool("lesson_outline", {})
        await c.call_tool("sample_material", {"n": 2, "seed": 1})
        r = await c.call_tool(
            "export_flashcards",
            {
                "deck": "d",
                "cards": [
                    {"front": f"{SECRETS[3]}?", "back": SECRETS[4], "ref": ref, "tags": [SECRETS[5]]},
                    {"front": "Thẻ hỏng", "back": "x", "ref": "D999#1"},
                ],
            },
        )
        assert not r.is_error
        r = await c.call_tool("save_note", {"title": SECRETS[1], "markdown": f"## Ghi chú\n{SECRETS[2]}", "refs": [ref]})
        assert not r.is_error
        await c.call_tool("library_status", {})  # not logged either
        await c.call_tool("add_material", {"path": str(notes), "course": "Tay", "lesson": "L"})  # nor this

    run(map_cfg, flow)
    lines = read_log(map_cfg)

    assert [x["tool"] for x in lines] == [
        "scope_for_path",
        "search_material",
        "search_material",
        "read_chunks",
        "lesson_outline",
        "sample_material",
        "export_flashcards",
        "save_note",
    ]
    for x in lines:
        assert set(x) <= ALLOWED_KEYS and {"v", "t", "tool", "course", "lesson", "subject", "hits"} <= set(x)
        assert x["v"] == 1 and isinstance(x["hits"], int)
        assert datetime.fromisoformat(x["t"]).utcoffset() is not None  # ISO 8601 with an offset
        assert (x["course"], x["lesson"], x["subject"]) == ("GCI", "02_NumPy", "GCI_Subject")
        assert ("coverage" in x) == (x["tool"] == "search_material")  # coverage: searches only
        assert ("cards" in x) == (x["tool"] == "export_flashcards")  # cards: exports only
    scope, weak, none, read, outline, sample, export, note = lines
    assert scope["hits"] == 1  # one document in the lesson
    assert weak["coverage"] == "weak" and weak["hits"] >= 1  # the sentinel word is not in any lesson: half the terms match
    assert none["coverage"] == "none" and none["hits"] == 0
    assert read["hits"] == 1 and outline["hits"] >= 1 and sample["hits"] >= 1
    assert export["cards"] == 1 and export["hits"] == 1  # one card accepted, the bad ref was rejected
    assert note["hits"] == 1

    raw = map_cfg.activity_path.read_text(encoding="utf-8")
    for secret in SECRETS:
        assert secret not in raw
    assert "broadcasting" not in raw and "quantum" not in raw and "Thẻ hỏng" not in raw  # no query or card text at all
    assert "\r" not in raw and raw.endswith("\n")


def test_unscoped_and_unmapped_calls_log_nulls(cfg):
    async def flow(c):
        await c.call_tool("sync_library", {})
        await c.call_tool("search_material", {"queries": ["polymorphism"]})
        await c.call_tool("scope_for_path", {"path": str(cfg.home / "nowhere" / "x.py")})
        await c.call_tool("set_scope", {"course": "PRJ301", "lesson": "L03"})
        await c.call_tool("search_material", {"queries": ["polymorphism"]})

    run(cfg, flow)
    unscoped, nowhere, scoped = read_log(cfg)
    assert (unscoped["course"], unscoped["lesson"], unscoped["subject"]) == (None, None, None)
    assert unscoped["coverage"] == "strong" and unscoped["hits"] >= 1
    assert nowhere["tool"] == "scope_for_path" and nowhere["course"] is None and nowhere["hits"] == 0
    assert (scoped["course"], scoped["lesson"], scoped["subject"]) == ("PRJ301", "L03-OOP", None)  # no map: no subject


def test_a_logging_failure_never_breaks_a_tool(cfg):
    cfg.home.mkdir(parents=True, exist_ok=True)
    cfg.activity_path.mkdir()  # the log path is a folder: every write fails

    async def flow(c):
        await c.call_tool("sync_library", {})
        r = await c.call_tool("search_material", {"queries": ["polymorphism"]})
        assert not r.is_error and "coverage: strong" in text(r)
        r = await c.call_tool("sample_material", {"n": 1})
        assert not r.is_error

    run(cfg, flow)
    assert cfg.activity_path.is_dir()


def test_concurrent_writers_never_interleave_lines(tmp_path):
    path = tmp_path / "activity.jsonl"

    def worker(n):
        for i in range(40):
            activity.record(path, "search_material", course=f"C{n}", lesson=f"L{i}", subject=None, coverage="strong", hits=i)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 8 * 40 and all(r["tool"] == "search_material" for r in rows)


def test_log_line_shape_matches_the_spec_exactly(tmp_path):
    path = tmp_path / "deep" / "folder" / "activity.jsonl"  # parent folders are created
    activity.record(path, "export_flashcards", course="GCI", lesson=None, subject="GCI_World", hits=2, cards=3)
    activity.record(path, "search_material", course="GCI", lesson="02_NumPy", subject="GCI_World", coverage="weak", hits=4)
    first, second = (json.loads(x) for x in path.read_text(encoding="utf-8").splitlines())
    assert list(first) == ["v", "t", "tool", "course", "lesson", "subject", "hits", "cards"] and first["lesson"] is None
    assert list(second) == ["v", "t", "tool", "course", "lesson", "subject", "coverage", "hits"]
    assert ", " not in path.read_text(encoding="utf-8")  # compact separators, one object per line
    assert set(activity.LOGGED_TOOLS) == {
        "search_material", "read_chunks", "lesson_outline", "sample_material", "export_flashcards", "save_note", "scope_for_path",
    }  # fmt: skip

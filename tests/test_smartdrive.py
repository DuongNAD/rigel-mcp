import json
import subprocess
import sys
from pathlib import Path

import pytest

from rigel_mcp import smartdrive
from rigel_mcp.libmap import load_map, norm_path
from rigel_mcp.mapsync import sync_map
from rigel_mcp.smartdrive import SmartDrive, SmartDriveError, parse_cmd
from rigel_mcp.store import Store

# A stand-in for `smart-drive search`: reads results.json, honours --dir (substring, like the real CLI),
# --ext and --limit, and records every call so tests can check what was asked.
FAKE_SCRIPT = '''\
import json, os, sys, time

here = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[1:]
with open(os.path.join(here, "calls.log"), "a", encoding="utf-8") as log:
    log.write(json.dumps(args, ensure_ascii=False) + "\\n")
mode_file = os.path.join(here, "mode.txt")
mode = open(mode_file, encoding="utf-8").read().strip() if os.path.exists(mode_file) else "ok"
if mode == "fail":
    sys.stderr.write("boom: index unavailable\\n")
    sys.exit(3)
if mode == "garbage":
    print("not json at all")
    sys.exit(0)
if mode == "sleep":
    time.sleep(5)
with open(os.path.join(here, "results.json"), encoding="utf-8") as f:
    rows = json.load(f)
d = args[args.index("--dir") + 1] if "--dir" in args else ""
limit = int(args[args.index("--limit") + 1]) if "--limit" in args else 5000
exts = set(args[args.index("--ext") + 1].split(",")) if "--ext" in args else None
rows = [r for r in rows if d in r["path"] and (exts is None or r["path"].rsplit(".", 1)[-1].lower() in exts)]
print(json.dumps(rows[:limit], ensure_ascii=False))
'''


class FakeIndex:
    def __init__(self, folder: Path, drive_root: Path):
        self.folder, self.root = folder, drive_root

    def set_rows(self, *files: Path) -> None:
        rows = [{"path": f.relative_to(self.root).as_posix(), "size": 1, "mtime": 1.0} for f in files]
        (self.folder / "results.json").write_text(json.dumps(rows), encoding="utf-8")

    def set_mode(self, mode: str) -> None:
        (self.folder / "mode.txt").write_text(mode, encoding="utf-8")

    def calls(self) -> list[list[str]]:
        log = self.folder / "calls.log"
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []

    def dirs(self) -> list[str]:
        return [c[c.index("--dir") + 1] for c in self.calls()]


@pytest.fixture
def fake_index(tmp_path, monkeypatch):
    folder = tmp_path / "fake-sd"
    folder.mkdir()
    script = folder / "fake_smart_drive.py"
    script.write_text(FAKE_SCRIPT, encoding="utf-8")
    monkeypatch.setenv("RIGEL_SMARTDRIVE", "on")
    monkeypatch.setenv("RIGEL_SMARTDRIVE_CMD", str(script))
    monkeypatch.setenv("RIGEL_SMARTDRIVE_ROOT", str(tmp_path))
    return FakeIndex(folder, tmp_path)


def run_sync(tmp_path, hub):
    hub.write_map()
    libmap = load_map(hub.map_file).map
    store = Store(tmp_path / "db" / "study.db")
    return store, sync_map(store, libmap, SmartDrive.from_env())


def the_index_of_a_synced_hub(hub):
    m, r = hub.materials, hub.root
    return [
        m / "01_Intro" / "slides.md",
        m / "01_Intro" / "code" / "hello.py",
        m / "01_Intro" / "__pycache__" / "junk.py",  # excluded by name
        m / "01_Intro" / ".hidden" / "secret.md",  # hidden
        m / "02_NumPy" / "notes.md",
        m / "02_NumPy" / "dap_an" / "answers.md",  # excluded for the course
        m / "node_modules" / "pkg.md",  # an excluded folder is not a lesson
        m / "overview.md",
        r / "GCI_Subject" / "03_Materials_old" / "x.md",  # matches the --dir substring but is a different folder
        r / "GCI_Subject" / "06_Notes" / "lecture.txt",
        r / "GCI_Subject" / "06_Notes" / "extra" / "more.md",
        r / "Machine_Learning" / "02_Supervised_Learning" / "01_Regression" / "regression.py",
        r / "Machine_Learning" / "02_Supervised_Learning" / "02_Classification" / "notes.md",
        m / "02_NumPy" / "gone.md",  # in a stale index, deleted from the disk
    ]


def test_discovery_goes_through_the_index_and_unions_the_top_level_listing(tmp_path, hub, fake_index):
    (hub.root / "GCI_Subject" / "03_Materials_old").mkdir()
    (hub.root / "GCI_Subject" / "03_Materials_old" / "x.md").write_text("# cũ\n\nthư mục anh em\n", encoding="utf-8")
    fake_index.set_rows(*the_index_of_a_synced_hub(hub))
    # two files the index has not seen yet: a new top-level one (the listing catches it) and a new nested one (it cannot)
    (hub.materials / "01_Intro" / "new_top.md").write_text("# Mới\n\nfile mới ở cấp ngoài cùng.\n", encoding="utf-8")
    (hub.materials / "01_Intro" / "code" / "deep_new.py").write_text("x = 1\n", encoding="utf-8")

    store, rep = run_sync(tmp_path, hub)

    assert set(store.docs_index()) == hub.expected | {("GCI", "01_Intro", "new_top.md")}
    assert rep.discovery == {"smartdrive": 7, "listing": 2, "file": 1}  # 7 from the index; overview.md + new_top.md listed
    assert not rep.errors and not any("SmartDrive lỗi" in w for w in rep.warnings)

    # one query per root (not per lesson); the file root needs none
    root = hub.root.relative_to(tmp_path).as_posix()
    assert sorted(fake_index.dirs()) == sorted(
        [f"{root}/GCI_Subject/03_Materials", f"{root}/GCI_Subject/06_Notes", f"{root}/Machine_Learning/02_Supervised_Learning"]
    )
    first = fake_index.calls()[0]
    assert first[0] == "search" and "--json" in first and first[first.index("--limit") + 1] == "5000"
    exts = first[first.index("--ext") + 1].split(",")
    assert {"pdf", "pptx", "docx", "ipynb", "md", "py", "java"} <= set(exts) and not any(e.startswith(".") for e in exts)

    # the reminder about files the index has not seen yet, once per folder asked
    assert len(rep.notes) == 3 and all('smart-drive update "' in n and "rồi sync_library" in n for n in rep.notes)
    assert "File mới ở thư mục con chưa có trong chỉ mục: chạy smart-drive update" in rep.render()
    assert "chỉ mục SmartDrive 7" in rep.render() and "liệt kê thư mục 2" in rep.render()
    assert not any("deep_new" in rel for _, _, rel in store.docs_index())


def test_index_answer_that_hits_the_limit_is_redone_per_lesson(tmp_path, hub, fake_index, monkeypatch):
    monkeypatch.setattr(smartdrive, "LIMIT", 3)
    m = hub.materials
    for name in ("a", "b", "c"):
        (m / "01_Intro" / f"{name}.md").write_text(f"# {name}\n\ntệp {name}\n", encoding="utf-8")
    fake_index.set_rows(
        m / "01_Intro" / "slides.md",
        m / "01_Intro" / "a.md",
        m / "01_Intro" / "b.md",
        m / "01_Intro" / "code" / "hello.py",
        m / "02_NumPy" / "notes.md",
    )
    store, rep = run_sync(tmp_path, hub)

    assert any(d.endswith("03_Materials") for d in fake_index.dirs())  # first the whole root ...
    assert any(d.endswith("03_Materials/01_Intro") for d in fake_index.dirs())  # ... then each lesson on its own
    assert any(d.endswith("03_Materials/02_NumPy") for d in fake_index.dirs())
    assert any("01_Intro" in w and "chỉ mục trả về đủ 3 kết quả" in w for w in rep.warnings)  # honest about the cap
    docs = {rel for _, lesson, rel in store.docs_index() if lesson == "01_Intro"}
    assert {"slides.md", "a.md", "b.md", "c.md"} <= docs  # c.md was cut by the cap, the listing recovered it
    assert "code/hello.py" not in docs  # nested and cut: this is what the warning is about


@pytest.mark.parametrize("mode,fragment", [("fail", "thoát với mã 3"), ("garbage", "không in ra JSON")])
def test_a_failing_index_falls_back_to_the_walk_and_is_not_asked_again(tmp_path, hub, fake_index, mode, fragment):
    fake_index.set_rows()
    fake_index.set_mode(mode)
    store, rep = run_sync(tmp_path, hub)
    assert set(store.docs_index()) == hub.expected  # everything found by the bounded walk
    failures = [w for w in rep.warnings if "SmartDrive lỗi" in w]
    assert len(failures) == 1 and fragment in failures[0]
    assert len(fake_index.calls()) == 1  # switched off for the rest of this sync
    assert rep.discovery.get("walk") == 7 and not rep.notes  # no "run smart-drive update" reminder without the index


def test_a_slow_index_times_out(tmp_path, hub, fake_index, monkeypatch):
    monkeypatch.setattr(smartdrive, "TIMEOUT", 0.5)
    fake_index.set_rows()
    fake_index.set_mode("sleep")
    store, rep = run_sync(tmp_path, hub)
    assert set(store.docs_index()) == hub.expected
    assert any("không trả lời sau 0.5s" in w for w in rep.warnings)


def test_folders_on_another_drive_never_ask_the_index(tmp_path, hub, fake_index, monkeypatch):
    monkeypatch.setenv("RIGEL_SMARTDRIVE_ROOT", str(tmp_path / "another-drive"))
    fake_index.set_rows(*the_index_of_a_synced_hub(hub))
    store, rep = run_sync(tmp_path, hub)
    assert fake_index.calls() == []
    assert set(store.docs_index()) == hub.expected and rep.discovery.get("walk") == 7


def test_smartdrive_off_values_and_missing_command(monkeypatch, tmp_path):
    for value in ("off", "OFF", "false", "0", "no", " No "):
        monkeypatch.setenv("RIGEL_SMARTDRIVE", value)
        assert SmartDrive.from_env() is None, value
    monkeypatch.setenv("RIGEL_SMARTDRIVE", "on")
    monkeypatch.setattr(smartdrive.shutil, "which", lambda name: None)
    assert SmartDrive.from_env() is None  # not installed: the walk is used
    monkeypatch.setattr(smartdrive.shutil, "which", lambda name: str(tmp_path / "smart-drive.exe"))
    sd = SmartDrive.from_env()
    assert sd is not None and sd.cmd == [str(tmp_path / "smart-drive.exe")]
    assert sd.root == norm_path(smartdrive.DEFAULT_ROOT)  # D:/ unless RIGEL_SMARTDRIVE_ROOT says otherwise
    monkeypatch.setenv("RIGEL_SMARTDRIVE_ROOT", str(tmp_path))
    sd = SmartDrive.from_env()
    assert sd.root == norm_path(str(tmp_path))
    assert sd.covers(tmp_path / "x") and not sd.covers(tmp_path)  # the drive root itself is never queried


def test_parse_cmd_shapes(tmp_path):
    exe = tmp_path / "with space" / "smart-drive.exe"
    exe.parent.mkdir()
    exe.write_text("", encoding="utf-8")
    assert parse_cmd("") == [] and parse_cmd("   ") == []
    assert parse_cmd(str(exe)) == [str(exe)]  # an existing path is used as is, spaces and all
    assert parse_cmd('"C:/Program Files/sd/smart-drive.exe" --verbose') == ["C:/Program Files/sd/smart-drive.exe", "--verbose"]
    script = tmp_path / "fake.py"
    script.write_text("", encoding="utf-8")
    assert parse_cmd(str(script)) == [sys.executable, "-B", str(script)]  # a .py script runs under this interpreter


def test_query_detaches_stdin_forces_utf8_and_has_a_timeout(tmp_path, monkeypatch):
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"], seen["kwargs"] = args, kwargs
        out = json.dumps([{"path": "sub/x.md"}, {"path": "sub/y.pdf"}, {"nopath": 1}]).encode("utf-8")
        return subprocess.CompletedProcess(args, 0, out, b"")

    monkeypatch.setattr(smartdrive.subprocess, "run", fake_run)
    sd = SmartDrive(["smart-drive"], tmp_path)
    res = sd.query(tmp_path / "sub", ["md", "pdf"])
    assert seen["kwargs"]["stdin"] is subprocess.DEVNULL  # a child must never read this process's MCP stdio
    assert seen["kwargs"]["timeout"] == smartdrive.TIMEOUT == 30
    assert seen["kwargs"]["env"]["PYTHONUTF8"] == "1"
    assert seen["args"] == ["smart-drive", "search", "--dir", "sub", "--ext", "md,pdf", "--json", "--limit", "5000"]
    assert [p.name for p in res.files] == ["x.md", "y.pdf"] and res.raw_count == 3 and not res.hit_limit
    assert sd.query(tmp_path / "sub", ["md"], limit=3).hit_limit  # as many rows as asked: possibly truncated


def test_query_errors_become_smartdrive_errors(tmp_path, monkeypatch):
    sd = SmartDrive(["definitely-not-installed-xyz"], tmp_path)
    with pytest.raises(SmartDriveError, match="không chạy được"):
        sd.query(tmp_path / "sub", ["md"])
    assert smartdrive._parse_rows('log line\n[{"path": "a.md"}]\n') == [{"path": "a.md"}]  # noise around the JSON
    assert smartdrive._parse_rows('{"results": [{"path": "a.md"}]}') == [{"path": "a.md"}]
    with pytest.raises(SmartDriveError):
        smartdrive._parse_rows("nothing here")

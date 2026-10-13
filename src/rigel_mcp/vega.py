"""Vega card inbox: Rigel's side of the shared contract `vega-card-inbox.v1`.

Vega is the learner's single FSRS review queue. Producers never touch Vega's own files; they drop ONE
batch file per call into `<hub>/.vega/inbox/` and Vega validates, de-duplicates (by `key`) and schedules the
cards on its next `vega_sync`. The amendment of 2026-10-13 added producer `rigel` and origin kind `rigel-ref`.

Every line is checked here with a small strict validator before anything is written, so a bad card is
rejected with a reason instead of poisoning the batch.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

PRODUCER = "rigel"
PRODUCERS = frozenset({"polaris", "orion", "mira", "aurora", "sirius", "manual", "rigel"})
KINDS = frozenset({"basic", "cloze", "reverse"})
ORIGIN_KINDS = frozenset(
    {
        "polaris-fact",
        "polaris-errorlog",
        "polaris-selfcheck",
        "aurora-quiz",
        "aurora-predict",
        "orion-attempt",
        "mira-stop",
        "manual",
        "rigel-ref",
    }
)
LINE_FIELDS = frozenset(
    {"v", "t", "producer", "key", "subject", "deck", "kind", "front", "back", "hint", "hook", "origin", "tags", "reason"}
)
ORIGIN_FIELDS = frozenset({"kind", "path", "locator", "href"})
REASON = "Thẻ từ tài liệu bài học (Rigel)"

# Code point ranges of emoji and pictographs; the contract bans them in the card front.
# Built from numbers on purpose, so this source file contains no emoji itself.
_EMOJI_RANGES = (
    (0x1F000, 0x1FAFF),  # pictographs, emoticons, transport, flags, symbols and pictographs extended
    (0x2600, 0x27BF),  # miscellaneous symbols and dingbats
    (0x2B50, 0x2B50),  # star
    (0x2B55, 0x2B55),  # heavy circle
    (0x2B1B, 0x2B1C),  # large squares
    (0x231A, 0x231B),  # watch, hourglass
    (0x23E9, 0x23F3),  # media control symbols
    (0x23F8, 0x23FA),  # media control symbols
    (0xFE0F, 0xFE0F),  # emoji presentation selector
    (0x20E3, 0x20E3),  # combining enclosing keycap
)
_EMOJI = re.compile("[" + "".join(f"{re.escape(chr(a))}-{re.escape(chr(b))}" for a, b in _EMOJI_RANGES) + "]")


# ---- strict validator ----------------------------------------------------------------------


def _text(errors: list[str], obj: dict, key: str, lo: int, hi: int, *, required: bool = False) -> str | None:
    if key not in obj:
        if required:
            errors.append(f"thiếu {key}")
        return None
    value = obj[key]
    if not isinstance(value, str):
        errors.append(f"{key} phải là chuỗi")
        return None
    n = len(value)
    if n < lo or n > hi:
        errors.append(f"{key} dài {n} ký tự, cần {lo}-{hi}" if lo else f"{key} dài {n} ký tự, tối đa {hi}")
    elif lo >= 1 and not value.strip():
        errors.append(f"{key} không được chỉ gồm khoảng trắng")
    return value


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or len(value) < 11 or value[10] not in "Tt":
        return False
    try:
        return datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        return False


def _one_of(value: Any, allowed: frozenset[str]) -> bool:
    return isinstance(value, str) and value in allowed


def validate_line(obj: Any) -> list[str]:
    """Problems of one inbox line against the contract; an empty list means the line is valid."""
    if not isinstance(obj, dict):
        return ["dòng phải là một đối tượng JSON"]
    errors = [f'trường lạ "{k}" (hợp đồng từ chối trường không biết)' for k in obj if k not in LINE_FIELDS]
    version = obj.get("v")
    if isinstance(version, bool) or version != 1:
        errors.append("v phải là số nguyên 1")
    if not _valid_timestamp(obj.get("t")):
        errors.append("t phải là ngày giờ ISO 8601 có múi giờ (offset hoặc Z)")
    producer = obj.get("producer")
    if not _one_of(producer, PRODUCERS):
        errors.append("producer không thuộc danh sách của hợp đồng")
    key = _text(errors, obj, "key", 1, 200, required=True)
    if key is not None and _one_of(producer, PRODUCERS) and not key.startswith(f"{producer}:"):
        errors.append(f"key phải bắt đầu bằng {producer}:")
    _text(errors, obj, "subject", 1, 120, required=True)
    _text(errors, obj, "deck", 0, 120)
    if "kind" in obj and not _one_of(obj["kind"], KINDS):
        errors.append("kind phải là basic, cloze hoặc reverse")
    front = _text(errors, obj, "front", 1, 600, required=True)
    if front is not None and _EMOJI.search(front):
        errors.append("front chứa emoji (hợp đồng cấm)")
    _text(errors, obj, "back", 0, 1200, required=True)
    _text(errors, obj, "hint", 0, 200)
    _text(errors, obj, "hook", 0, 200)
    _text(errors, obj, "reason", 0, 200)

    origin = obj.get("origin")
    if not isinstance(origin, dict):
        errors.append("origin phải là một đối tượng")
    else:
        errors += [f'origin có trường lạ "{k}"' for k in origin if k not in ORIGIN_FIELDS]
        if not _one_of(origin.get("kind"), ORIGIN_KINDS):
            errors.append("origin.kind không thuộc danh sách của hợp đồng")
        sub: list[str] = []
        _text(sub, origin, "path", 1, 400, required=True)
        _text(sub, origin, "locator", 0, 120)
        _text(sub, origin, "href", 0, 400)
        errors += [f"origin.{e}" for e in sub]

    if "tags" in obj:
        tags = obj["tags"]
        if not isinstance(tags, list):
            errors.append("tags phải là danh sách")
        else:
            if len(tags) > 10:
                errors.append(f"tags có {len(tags)} mục, tối đa 10")
            for i, tag in enumerate(tags):
                if not isinstance(tag, str) or not 1 <= len(tag) <= 40 or not tag.strip():
                    errors.append(f"tags[{i}] phải là chuỗi 1-40 ký tự")
    return errors


# ---- building and writing ------------------------------------------------------------------


def front_hash(front: str) -> str:
    return hashlib.sha1(front.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]


def clean_tags(tags: Iterable[str]) -> list[str]:
    """At most 10 distinct tags of at most 40 characters (the contract's limits)."""
    out: list[str] = []
    for tag in tags:
        tag = tag.strip()[:40].strip()
        if tag and tag not in out:
            out.append(tag)
    return out[:10]


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_line(
    *,
    now: str,
    course: str,
    subject: str | None,
    deck: str,
    front: str,
    back: str,
    ref: str,
    file_label: str,
    locator: str,
    path: str,
    tags: Iterable[str],
) -> dict:
    """One card as an inbox line. `subject` is the course's hub subject folder when the library map names one."""
    subj = subject or course
    line: dict[str, Any] = {
        "v": 1,
        "t": now,
        "producer": PRODUCER,
        "key": f"{PRODUCER}:{subj}:{ref}:{front_hash(front)}",
        "subject": subj,
    }
    if deck.strip():
        line["deck"] = _clip(deck.strip(), 120)
    line.update(
        kind="basic",
        front=front,
        back=f"{back}\nNguồn: {file_label} · {locator}",
        origin={"kind": "rigel-ref", "path": path, "locator": _clip(f"{ref} · {locator}", 120)},
        tags=clean_tags(tags),
        reason=REASON,
    )
    return line


def write_inbox(hub: Path, lines: list[dict], now: datetime | None = None) -> Path:
    """Write ONE new batch file `rigel-<YYYYMMDDTHHmmss>-<6 hex>.jsonl` into <hub>/.vega/inbox/.

    Atomic: the lines go to `<name>.jsonl.tmp` first and the file is renamed once complete, so Vega
    (which ignores *.tmp) never sees a half-written batch. An existing inbox file is never touched.
    """
    folder = hub / ".vega" / "inbox"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%dT%H%M%S")
    for _ in range(20):
        final = folder / f"{PRODUCER}-{stamp}-{secrets.token_hex(3)}.jsonl"
        if final.exists():
            continue
        tmp = final.with_name(final.name + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                for line in lines:
                    f.write(json.dumps(line, ensure_ascii=False) + "\n")
            os.replace(tmp, final)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return final
    raise OSError("không tạo được tên file duy nhất trong hộp thư Vega")

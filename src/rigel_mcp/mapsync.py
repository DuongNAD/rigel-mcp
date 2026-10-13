"""Library sync driven by library.json (see libmap.py and discover.py)."""

from __future__ import annotations

from .discover import discover
from .ingest import SyncReport, sync_one
from .libmap import LibMap, is_within, norm_path
from .smartdrive import SmartDrive
from .store import Store

ORIGIN = "map"
MAX_REMINDERS = 3


def sync_map(store: Store, libmap: LibMap, sd: SmartDrive | None) -> SyncReport:
    """Ingest what the map points at. Only docs with origin "map" are ever removed (never manual or classic
    library docs), and docs whose source folder cannot be read right now (for example the SSD is unplugged)
    are kept, so refs stay stable."""
    rep = SyncReport(mode="map", source=libmap.source.as_posix())
    found = discover(libmap, sd)
    rep.warnings.extend(found.warnings)
    known = store.docs_index()
    seen: set[tuple[str, str, str]] = set()

    for item in found.items:
        key = (item.course, item.lesson, item.rel)
        if key in seen:
            rep.warnings.append(
                f"{item.course}/{item.lesson}/{item.rel}: trùng tên với file đã nạp từ gốc khác, bỏ qua {item.path.as_posix()}"
            )
            continue
        seen.add(key)
        sync_one(store, rep, known, f=item.path, course=item.course, lesson=item.lesson, rel=item.rel, origin=ORIGIN)

    for doc in store.list_docs(ORIGIN):
        if (doc["course"], doc["lesson"], doc["rel_path"]) in seen:
            continue
        where = norm_path(doc["abs_path"]) if doc["abs_path"] else None
        if where is not None and any(is_within(where, root) for root in found.protected):
            rep.kept += 1
            continue
        store.delete_doc(doc["id"])
        rep.removed.append(f"{doc['course']}/{doc['lesson']}/{doc['rel_path']}")

    rep.discovery = dict(found.counts)
    for folder in found.index_roots[:MAX_REMINDERS]:
        rep.notes.append(
            f'File mới ở thư mục con chưa có trong chỉ mục: chạy smart-drive update "{folder}" rồi sync_library.'
        )
    if len(found.index_roots) > MAX_REMINDERS:
        rep.notes.append(f"(và {len(found.index_roots) - MAX_REMINDERS} thư mục khác đã tra bằng chỉ mục SmartDrive)")
    return rep

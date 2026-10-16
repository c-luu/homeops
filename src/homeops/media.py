from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path


@dataclass
class MediaFile:
    path: str
    name: str
    size: int | None
    accessible: bool
    error: str | None = None
    sha256: str | None = None

    def json(self) -> dict[str, object]:
        return asdict(self)


def inventory_local(root: Path) -> list[MediaFile]:
    results: list[MediaFile] = []
    if not root.exists():
        return [MediaFile(str(root), root.name, None, False, "path does not exist")]
    try:
        paths: Iterable[Path] = root.rglob("*")
        for path in paths:
            try:
                if not path.is_file():
                    continue
                results.append(MediaFile(str(path), path.name, path.stat().st_size, True))
            except OSError as exc:
                results.append(MediaFile(str(path), path.name, None, False, str(exc)))
    except OSError as exc:
        results.append(MediaFile(str(root), root.name, None, False, str(exc)))
    return results


def hash_file(item: MediaFile, chunk_size: int = 1024 * 1024) -> MediaFile:
    if not item.accessible:
        return item
    path = Path(item.path)
    digest = sha256()
    try:
        before = path.stat()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(chunk_size), b""):
                digest.update(chunk)
        after = path.stat()
        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            item.accessible = False
            item.error = "file changed during hashing"
            return item
        item.sha256 = digest.hexdigest()
    except OSError as exc:
        item.accessible = False
        item.error = str(exc)
    return item


def match_files(sources: list[MediaFile], backups: list[MediaFile]) -> list[dict[str, object]]:
    source_sizes = {item.size for item in sources if item.accessible and item.size is not None}
    hashed_backups = [
        hash_file(item)
        for item in backups
        if item.accessible and item.size in source_sizes
    ]
    results: list[dict[str, object]] = []
    for source in sources:
        matching_size = [
            backup for backup in hashed_backups
            if source.accessible and backup.accessible and source.size == backup.size
        ]
        source = hash_file(source) if matching_size else source
        candidates = [
            backup for backup in hashed_backups
            if source.accessible and backup.accessible
            and source.size == backup.size
        ]
        same_hash = [backup for backup in candidates if backup.sha256 == source.sha256]
        status = "verified" if len(same_hash) == 1 else (
            "ambiguous" if len(same_hash) > 1 else "unmatched"
        )
        results.append({"source": source.json(), "candidates": [b.json() for b in same_hash], "status": status})
    return results

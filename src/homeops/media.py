from __future__ import annotations

import os
import re
import stat
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path

VIDEO_SUFFIXES = frozenset({".mp4", ".m4v", ".mov", ".mkv", ".webm", ".3gp", ".3g2"})
DIGEST = re.compile(r"[0-9a-f]{64}")


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


@dataclass
class LocalInventory:
    root: Path
    scope: Path
    within: str
    recursive: bool
    files: list[MediaFile] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    scanned: int = 0


def contained_path(path: Path, root: Path) -> Path:
    """Check lexical containment and every descendant without following symlinks."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("path must be absolute without parent traversal")
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"symlinks are not supported: {current}")
    if not path.resolve().is_relative_to(root):
        raise ValueError("path escapes configured destination")
    return path


def resolve_scope(root: Path, within: str | None = None) -> tuple[Path, Path, str]:
    root = root.expanduser().resolve(strict=True)
    if not root.is_dir() or not os.access(root, os.R_OK | os.X_OK):
        raise ValueError("LocalSend destination must be an existing readable directory")
    relative = Path(within or ".")
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("--within must be a contained relative subdirectory")
    scope = contained_path(root / relative, root)
    if not scope.is_dir() or not os.access(scope, os.R_OK | os.X_OK):
        raise ValueError("--within must name an existing readable directory")
    return root, scope, str(relative)


def scan_local(
    root: Path,
    *,
    within: str | None = None,
    recursive: bool = True,
    videos_only: bool = True,
) -> LocalInventory:
    root, scope, relative = resolve_scope(root, within)
    inventory = LocalInventory(root, scope, relative, recursive)

    def visit(directory: Path) -> None:
        try:
            contained_path(directory, root)
            with os.scandir(directory) as stream:
                entries = sorted(stream, key=lambda item: item.name)
        except (OSError, ValueError) as exc:
            inventory.errors.append({"path": str(directory), "error": str(exc)})
            return
        for entry in entries:
            path = Path(entry.path)
            try:
                metadata = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(metadata.st_mode):
                    if recursive:
                        visit(path)
                    continue
                if not stat.S_ISREG(metadata.st_mode):
                    inventory.skipped.append(
                        {"path": str(path), "reason": "symlink or non-regular"}
                    )
                    continue
                inventory.scanned += 1
                reason = None
                if videos_only and path.suffix.lower() not in VIDEO_SUFFIXES:
                    reason = "unsupported video or partial-transfer suffix"
                elif videos_only and metadata.st_size == 0:
                    reason = "empty file"
                if reason:
                    inventory.skipped.append({"path": str(path), "reason": reason})
                else:
                    inventory.files.append(MediaFile(str(path), path.name, metadata.st_size, True))
            except OSError as exc:
                inventory.errors.append({"path": str(path), "error": str(exc)})
                if path.suffix.lower() in VIDEO_SUFFIXES:
                    inventory.files.append(MediaFile(str(path), path.name, None, False, str(exc)))

    visit(scope)
    return inventory


def inventory_local(root: Path) -> list[MediaFile]:
    """Compatibility helper for generic regular-file inventories in library callers."""
    try:
        return scan_local(root, videos_only=False).files
    except (OSError, ValueError) as exc:
        return [MediaFile(str(root), root.name, None, False, str(exc))]


def _identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        metadata.st_mode,
    )


def hash_file(item: MediaFile, chunk_size: int = 1024 * 1024) -> MediaFile:
    item.sha256 = None
    if not item.accessible:
        return item
    path = Path(item.path)
    digest = sha256()
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("not a regular file")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if _identity(before) != _identity(opened) or opened.st_size != item.size:
                raise ValueError("file changed since inventory")
            for chunk in iter(lambda: handle.read(chunk_size), b""):
                digest.update(chunk)
            after = os.fstat(handle.fileno())
        if _identity(before) != _identity(after) or _identity(before) != _identity(path.lstat()):
            raise ValueError("file changed during hashing")
        item.sha256 = digest.hexdigest()
    except (OSError, ValueError) as exc:
        item.accessible = False
        item.error = str(exc)
    return item


def match_files(
    sources: list[MediaFile],
    backups: list[MediaFile],
    *,
    source_hasher: Callable[[MediaFile], MediaFile] | None = None,
    on_progress: Callable[[list[dict[str, object]]], None] | None = None,
) -> list[dict[str, object]]:
    # Mac candidates drive both the results and the size buckets hashed on Android.
    sources = list({item.path: item for item in sources}.values())
    backups = list({item.path: item for item in backups}.values())
    sizes = {item.size for item in backups if item.accessible and item.size is not None}
    remote_hash = source_hasher or hash_file
    for item in sources:
        if item.accessible and item.size in sizes:
            remote_hash(item)
            if not item.sha256 or not DIGEST.fullmatch(item.sha256):
                item.accessible = False
                item.error = item.error or "missing or invalid remote SHA-256"
    remote_sizes = {item.size for item in sources}
    for item in backups:
        if item.accessible and item.size in remote_sizes:
            hash_file(item)
    local_groups = Counter(
        (item.size, item.sha256)
        for item in backups
        if item.accessible and item.sha256 and DIGEST.fullmatch(item.sha256)
    )
    results: list[dict[str, object]] = []
    for local in backups:
        candidates = [
            remote
            for remote in sources
            if remote.accessible
            and remote.size == local.size
            and remote.sha256
            and remote.sha256 == local.sha256
            and DIGEST.fullmatch(remote.sha256)
        ]
        uncertain = any(
            not remote.accessible and remote.size in {None, local.size} for remote in sources
        )
        uncertain_local = any(
            not other.accessible and other.size in {None, local.size} for other in backups
        )
        count = local_groups[(local.size, local.sha256)]
        reason = None
        if not local.accessible or uncertain or uncertain_local:
            status = "error"
            reason = local.error or "unreadable evidence might conceal a duplicate"
        elif candidates and (len(candidates) != 1 or count != 1):
            status, reason = "ambiguous", "duplicate content on Mac or Pixel"
        elif len(candidates) == 1:
            status = "verified"
        else:
            status, reason = "unmatched", "no byte-identical Pixel original"
        failures = [
            item.json()
            for item in sources
            if not item.accessible and item.size in {None, local.size}
        ]
        results.append(
            {
                "local": local.json(),
                "sources": [item.json() for item in candidates],
                "source_errors": failures,
                "local_count": count,
                "remote_count": len(candidates),
                "status": status,
                "eligible": status == "verified",
                "reason": reason,
            }
        )
        if on_progress:
            on_progress(results)
    return results

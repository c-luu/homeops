from __future__ import annotations

import json
import posixpath
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


class CleanupError(ValueError):
    """Raised when a cleanup safety precondition fails."""


@dataclass(frozen=True)
class PlanEntry:
    source: str
    destination: str
    size: int
    sha256: str


def create_plan(report: dict[str, object], *, destination_root: Path) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    source_roots = tuple(str(root).strip("/") for root in report.get("source_roots", []))
    device = report.get("device")
    if (
        not isinstance(device, str)
        or not device
        or device == "[redacted]"
        or not source_roots
    ):
        return {
            "schema": "homeops.cleanup-plan.v1",
            "verification_schema": report.get("schema"),
            "device": device,
            "destination_root": str(destination_root.resolve()),
            "source_roots": list(source_roots),
            "files": [],
        }
    for item in report.get("files", []):
        if not isinstance(item, dict) or item.get("status") != "verified":
            continue
        candidates = item.get("candidates", [])
        source = item.get("source", {})
        if not isinstance(source, dict) or not isinstance(candidates, list) or len(candidates) != 1:
            continue
        destination = candidates[0]
        source_path = str(source.get("path", "")) if isinstance(source, dict) else ""
        if not _authorized_source(source_path, source_roots):
            continue
        if not isinstance(destination, dict) or not destination.get("sha256"):
            continue
        path = Path(str(destination.get("path", "")))
        try:
            path.relative_to(destination_root.resolve())
            stat = path.stat()
        except (OSError, ValueError):
            continue
        if (
            stat.st_size != destination.get("size")
            or source.get("sha256") != destination.get("sha256")
            or _hash(path) != destination.get("sha256")
        ):
            continue
        entries.append(
            {
                "source": source_path,
                "destination": str(path),
                "size": stat.st_size,
                "sha256": destination["sha256"],
            }
        )
    return {
        "schema": "homeops.cleanup-plan.v1",
        "verification_schema": report.get("schema"),
        "device": report.get("device"),
        "destination_root": str(destination_root.resolve()),
        "source_roots": list(source_roots),
        "files": entries,
    }


def _authorized_source(path: str, roots: tuple[str, ...]) -> bool:
    normalized = posixpath.normpath(path)
    if not normalized.startswith("/sdcard/") or "\x00" in normalized:
        return False
    if not roots:
        return True
    return any(
        normalized == f"/sdcard/{root}" or normalized.startswith(f"/sdcard/{root}/")
        for root in roots
    )


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def revalidate(
    plan: dict[str, object],
    *,
    source_check: Callable[[str, int, str], bool],
) -> list[PlanEntry]:
    root = Path(str(plan.get("destination_root", ""))).resolve()
    source_roots = tuple(str(root).strip("/") for root in plan.get("source_roots", []))
    device = plan.get("device")
    if "device" in plan and device in {None, "", "[redacted]"}:
        raise CleanupError("cleanup plan has no exact Android device identity")
    entries: list[PlanEntry] = []
    for raw in plan.get("files", []):
        if not isinstance(raw, dict):
            raise CleanupError("cleanup plan contains an invalid entry")
        destination = Path(str(raw.get("destination", ""))).resolve()
        try:
            destination.relative_to(root)
            size = int(raw["size"])
            expected = str(raw["sha256"])
            if destination.stat().st_size != size or _hash(destination) != expected:
                raise CleanupError(f"destination changed: {destination}")
            source = str(raw["source"])
            if not _authorized_source(source, source_roots):
                raise CleanupError(f"source path is outside configured roots: {source}")
            if not source_check(source, size, expected):
                raise CleanupError(f"source failed revalidation: {source}")
        except (KeyError, OSError, ValueError) as exc:
            raise CleanupError(f"invalid cleanup entry: {destination}") from exc
        entries.append(PlanEntry(source, str(destination), size, expected))
    return entries


def persist_result(path: Path, entries: Iterable[dict[str, object]], *, complete: bool) -> None:
    path.write_text(
        json.dumps(
            {"schema": "homeops.cleanup-result.v1", "complete": complete, "files": list(entries)},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

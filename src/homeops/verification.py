from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .adb import Adb, AdbError
from .config import Profile
from .media import MediaFile, match_files, scan_local


def verify_media(
    profile: Profile,
    adb: Adb,
    *,
    within: str | None = None,
    checkpoint: Callable[[dict], None] | None = None,
) -> dict:
    """Read-only workflow shared by guided cleanup and advanced verification."""
    local = scan_local(profile.destination, within=within, recursive=profile.recursive)
    device = adb.find_single_authorized()
    report = {
        "schema": "homeops.verification.v2",
        "run_id": uuid4().hex,
        "created_at": datetime.now(UTC).isoformat(),
        "complete": False,
        "device": device.serial,
        "profile": profile.name,
        "delete_mode": profile.delete_mode,
        "destination_root": str(local.root),
        "source_roots": list(profile.roots),
        "scope": {"within": local.within, "path": str(local.scope), "recursive": local.recursive},
        "coverage": [],
        "coverage_complete": False,
        "files": [],
        "scanned_mac_files": local.scanned,
        "skipped": local.skipped,
        "inventory_errors": list(local.errors),
    }
    if checkpoint:
        checkpoint(report)
    if local.files:
        remote = adb.inventory_with_coverage(device.serial, profile.roots)
        report["coverage"] = remote.coverage
        for root in remote.coverage:
            if root.get("status") not in {"complete", "absent-optional"}:
                report["inventory_errors"].append(root)
        sources = [
            MediaFile(
                item.path,
                Path(item.path).name,
                item.size,
                item.size is not None,
                "unknown remote size" if item.size is None else None,
            )
            for item in remote.files
        ]

        def remote_hash(item: MediaFile) -> MediaFile:
            try:
                item.sha256 = adb.hash_file(device.serial, item.path)
                if adb.file_size(device.serial, item.path) != item.size:
                    raise AdbError("remote size changed since inventory")
            except AdbError as exc:
                item.accessible, item.sha256, item.error = False, None, str(exc)
            return item

        def progress(entries: list[dict]) -> None:
            report["files"] = entries
            if checkpoint:
                checkpoint(report)

        report["files"] = match_files(
            sources, local.files, source_hasher=remote_hash, on_progress=progress
        )
        if any(item.size is None for item in remote.files):
            report["inventory_errors"].append({"error": "unknown remote sizes may hide originals"})
    report["coverage_complete"] = not report["inventory_errors"]
    if not report["coverage_complete"]:
        for entry in report["files"]:
            entry["eligible"] = False
            entry["reason"] = "incomplete inventory may hide matching or duplicate originals"
    report["complete"] = True
    if checkpoint:
        checkpoint(report)
    return report

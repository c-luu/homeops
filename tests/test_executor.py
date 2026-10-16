from hashlib import sha256
from pathlib import Path

import pytest

from homeops.cleanup import CleanupError
from homeops.executor import apply_plan


def test_cleanup_dry_run_does_not_require_confirmation(tmp_path: Path) -> None:
    destination = tmp_path / "backup.mp4"
    destination.write_bytes(b"video")
    digest = sha256(b"video").hexdigest()
    plan = {
        "destination_root": str(tmp_path),
        "files": [
            {
                "source": "/sdcard/DCIM/backup.mp4",
                "destination": str(destination),
                "size": 5,
                "sha256": digest,
            }
        ],
    }
    results = apply_plan(
        plan,
        tmp_path / "result.json",
        source_check=lambda *_: True,
        delete_source=lambda _: None,
        confirm=False,
        permanent_acknowledged=False,
    )
    assert results[0]["status"] == "dry-run"


def test_cleanup_rechecks_each_entry_before_deletion(tmp_path: Path) -> None:
    destinations = [tmp_path / "one.mp4", tmp_path / "two.mp4"]
    for destination in destinations:
        destination.write_bytes(b"video")
    digest = sha256(b"video").hexdigest()
    plan = {
        "destination_root": str(tmp_path),
        "files": [
            {
                "source": f"/sdcard/DCIM/{destination.name}",
                "destination": str(destination),
                "size": 5,
                "sha256": digest,
            }
            for destination in destinations
        ],
    }
    checks: list[str] = []

    def source_check(source: str, _size: int, _digest: str) -> bool:
        checks.append(source)
        return source.endswith("one.mp4")

    with pytest.raises(CleanupError, match="source failed revalidation"):
        apply_plan(
            plan,
            tmp_path / "result.json",
            source_check=source_check,
            delete_source=lambda _: None,
            confirm=True,
            permanent_acknowledged=True,
            dry_run=False,
        )
    assert checks == ["/sdcard/DCIM/one.mp4", "/sdcard/DCIM/two.mp4"]


def test_cleanup_executes_only_after_permanent_acknowledgement(tmp_path: Path) -> None:
    destination = tmp_path / "backup.mp4"
    destination.write_bytes(b"video")
    digest = sha256(b"video").hexdigest()
    plan = {
        "destination_root": str(tmp_path),
        "files": [{"source": "/sdcard/DCIM/backup.mp4", "destination": str(destination), "size": 5, "sha256": digest}],
    }
    deleted: list[str] = []
    results = apply_plan(
        plan,
        tmp_path / "result.json",
        source_check=lambda *_: True,
        delete_source=deleted.append,
        confirm=True,
        permanent_acknowledged=True,
        dry_run=False,
    )
    assert results[0]["status"] == "deleted"
    assert deleted == ["/sdcard/DCIM/backup.mp4"]


def test_permanent_cleanup_requires_explicit_confirmation(tmp_path: Path) -> None:
    destination = tmp_path / "backup.mp4"
    destination.write_bytes(b"video")
    digest = sha256(b"video").hexdigest()
    plan = {
        "destination_root": str(tmp_path),
        "files": [
            {
                "source": "/sdcard/DCIM/backup.mp4",
                "destination": str(destination),
                "size": 5,
                "sha256": digest,
            }
        ],
    }
    with pytest.raises(CleanupError, match="confirmation"):
        apply_plan(
            plan,
            tmp_path / "result.json",
            source_check=lambda *_: True,
            delete_source=lambda _: None,
            confirm=False,
            permanent_acknowledged=True,
            dry_run=False,
        )


def test_uncertain_delete_stops_and_requires_fresh_plan(tmp_path: Path) -> None:
    destination = tmp_path / "backup.mp4"
    destination.write_bytes(b"video")
    digest = sha256(b"video").hexdigest()
    plan = {
        "destination_root": str(tmp_path),
        "files": [{"source": "/sdcard/DCIM/backup.mp4", "destination": str(destination), "size": 5, "sha256": digest}],
    }
    result_path = tmp_path / "result.json"
    results = apply_plan(
        plan,
        result_path,
        source_check=lambda *_: True,
        delete_source=lambda _: (_ for _ in ()).throw(RuntimeError("disconnect")),
        confirm=True,
        permanent_acknowledged=True,
        dry_run=False,
    )
    assert results[0]["status"] == "unknown"
    with pytest.raises(CleanupError, match="uncertain"):
        apply_plan(
            plan,
            result_path,
            source_check=lambda *_: True,
            delete_source=lambda _: None,
            confirm=True,
            permanent_acknowledged=True,
            dry_run=False,
        )


def test_recovery_skips_deleted_entry_before_revalidation(tmp_path: Path) -> None:
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"video")
    second.write_bytes(b"video")
    digest = sha256(b"video").hexdigest()
    plan = {
    "destination_root": str(tmp_path),
    "files": [
        {"source": "/sdcard/DCIM/first.mp4", "destination": str(first), "size": 5, "sha256": digest},
        {"source": "/sdcard/DCIM/second.mp4", "destination": str(second), "size": 5, "sha256": digest},
    ],
    }
    result_path = tmp_path / "result.json"
    result_path.write_text(
    __import__("json").dumps(
        {
            "complete": False,
            "files": [
                {
                    "source": "/sdcard/DCIM/first.mp4",
                    "destination": str(first),
                    "status": "deleted",
                }
            ],
        }
    ),
    encoding="utf-8",
    )
    deleted: list[str] = []
    results = apply_plan(
    plan,
    result_path,
    source_check=lambda source, *_: source.endswith("second.mp4"),
    delete_source=deleted.append,
    confirm=True,
    permanent_acknowledged=True,
    dry_run=False,
    )
    assert results[0]["status"] == "already-deleted"
    assert deleted == ["/sdcard/DCIM/second.mp4"]

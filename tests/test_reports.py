import json
from pathlib import Path

from homeops.cleanup import CleanupError, create_plan, revalidate
from homeops.reports import write_checkpoint, write_report


def test_reports_have_schema_timestamp_and_incomplete_checkpoint(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    checkpoint = tmp_path / "checkpoint.json"
    entries = [{"status": "unmatched", "source": {"path": "clip.mp4"}}]
    write_report(entries, report)
    write_checkpoint(entries, checkpoint)
    assert json.loads(report.read_text())["created_at"]
    assert json.loads(checkpoint.read_text())["complete"] is False


def test_cleanup_plan_rejects_changed_destination(tmp_path: Path) -> None:
    destination = tmp_path / "clip.mp4"
    destination.write_bytes(b"video")
    digest = "0" * 64
    report = {
        "schema": "homeops.verification.v1",
        "files": [
            {
                "status": "verified",
                "source": {"path": "/sdcard/DCIM/clip.mp4", "sha256": digest},
                "candidates": [{"path": str(destination), "size": 5, "sha256": digest}],
            }
        ],
    }
    assert create_plan(report, destination_root=tmp_path)["files"] == []
    try:
        revalidate(
            {"destination_root": str(tmp_path), "files": [{"source": "x", "destination": str(destination), "size": 5, "sha256": digest}]},
            source_check=lambda *_: True,
        )
    except CleanupError:
        pass
    else:
        raise AssertionError("changed destination must be rejected")

from hashlib import sha256
from pathlib import Path

import pytest

from homeops.cleanup import CleanupError
from homeops.executor import apply_plan


def test_cleanup_requires_confirmation_and_can_dry_run(tmp_path: Path) -> None:
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
        apply_plan(plan, tmp_path / "result.json", source_check=lambda *_: True, delete_source=lambda _: None, confirm=False, permanent_acknowledged=False)

from pathlib import Path

from homeops.media import inventory_local, match_files


def test_inventory_and_renamed_hash_match(tmp_path: Path) -> None:
    source = tmp_path / "android" / "DCIM" / "clip one.mp4"
    backup = tmp_path / "backup" / "renamed.mp4"
    source.parent.mkdir(parents=True)
    backup.parent.mkdir()
    source.write_bytes(b"video")
    backup.write_bytes(b"video")
    results = match_files(inventory_local(source.parent), inventory_local(backup.parent))
    assert results[0]["status"] == "verified"


def test_duplicate_hash_is_ambiguous(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"same")
    left = tmp_path / "left.mp4"
    right = tmp_path / "right.mp4"
    left.write_bytes(b"same")
    right.write_bytes(b"same")
    results = match_files(inventory_local(tmp_path), inventory_local(tmp_path))
    assert any(item["status"] == "ambiguous" for item in results)

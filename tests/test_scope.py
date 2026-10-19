import pytest

from homeops.media import resolve_scope, scan_local


def test_scope_video_selection_and_depth(tmp_path):
    (tmp_path / "batch").mkdir()
    (tmp_path / "batch" / "clip.MP4").write_bytes(b"video")
    (tmp_path / "clip.mp4.part").write_bytes(b"partial")
    (tmp_path / "empty.mp4").touch()
    (tmp_path / "note.txt").write_text("note")
    (tmp_path / "link.mp4").symlink_to(tmp_path / "batch" / "clip.MP4")
    result = scan_local(tmp_path)
    assert result.scanned == 4
    assert len(result.files) == 1
    assert len(result.skipped) == 4
    assert scan_local(tmp_path, recursive=False).files == []
    narrowed = scan_local(tmp_path, within="batch", recursive=False)
    assert len(narrowed.files) == 1
    assert narrowed.within == "batch"
    assert not narrowed.errors


@pytest.mark.parametrize("within", ["../elsewhere", "/tmp", "missing", "link"])
def test_scope_does_not_fall_back_or_follow_symlink(tmp_path, within):
    (tmp_path / "link").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises((ValueError, OSError)):
        resolve_scope(tmp_path, within)


def test_inventory_records_enumeration_failure(tmp_path, monkeypatch):
    def denied(_):
        raise PermissionError("denied")

    monkeypatch.setattr("homeops.media.os.scandir", denied)
    result = scan_local(tmp_path)
    assert result.errors and not result.files

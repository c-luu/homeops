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


def test_matching_hashes_only_hash_size_candidates(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.mp4"
    matching = tmp_path / "matching.mp4"
    different = tmp_path / "different.mp4"
    source.write_bytes(b"video")
    matching.write_bytes(b"video")
    different.write_bytes(b"other-size")
    original = __import__("homeops.media", fromlist=["hash_file"]).hash_file
    hashed: list[str] = []

    def tracked_hash(item, *args, **kwargs):
        hashed.append(item.path)
        return original(item, *args, **kwargs)

    monkeypatch.setattr("homeops.media.hash_file", tracked_hash)
    results = match_files(
        [
            __import__("homeops.media", fromlist=["MediaFile"]).MediaFile(
                str(source), source.name, source.stat().st_size, True
            )
        ],
        inventory_local(tmp_path),
    )
    by_path = {item["local"]["path"]: item for item in results}
    assert by_path[str(matching)]["status"] == "ambiguous"
    assert by_path[str(different)]["status"] == "unmatched"
    assert str(different) not in hashed


def test_pixel_duplicates_and_repeated_observations(tmp_path: Path) -> None:
    from homeops.media import MediaFile

    local = tmp_path / "local.mp4"
    local.write_bytes(b"video")
    digest = __import__("hashlib").sha256(b"video").hexdigest()

    def remote_hash(item):
        item.sha256 = digest
        return item

    first = MediaFile("/sdcard/DCIM/first", "first", 5, True)
    second = MediaFile("/sdcard/Movies/renamed", "renamed", 5, True)
    result = match_files([first, second], inventory_local(tmp_path), source_hasher=remote_hash)
    assert result[0]["status"] == "ambiguous"
    assert result[0]["remote_count"] == 2
    assert not result[0]["eligible"]
    unique = match_files([first, first], inventory_local(tmp_path), source_hasher=remote_hash)
    assert unique[0]["status"] == "verified"


def test_narrow_local_scope_does_not_narrow_pixel_lookup(tmp_path: Path) -> None:
    from homeops.media import MediaFile, scan_local

    (tmp_path / "batch").mkdir()
    (tmp_path / "batch" / "renamed.mp4").write_bytes(b"video")
    (tmp_path / "outside.mp4").write_bytes(b"video")
    sources = [MediaFile(str(tmp_path / "outside.mp4"), "remote", 5, True)]
    assert match_files(sources, scan_local(tmp_path, within="batch").files)[0]["eligible"]
    assert all(not item["eligible"] for item in match_files(sources, scan_local(tmp_path).files))


def test_local_replacement_during_hash_is_unverified(tmp_path, monkeypatch):
    from hashlib import sha256

    from homeops.media import hash_file

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video")
    item = inventory_local(tmp_path)[0]

    class ReplacingDigest:
        def __init__(self):
            self.inner = sha256()

        def update(self, chunk):
            replacement = tmp_path / "replacement"
            replacement.write_bytes(b"other")
            replacement.replace(clip)
            self.inner.update(chunk)

        def hexdigest(self):
            return self.inner.hexdigest()

    monkeypatch.setattr("homeops.media.sha256", ReplacingDigest)
    result = hash_file(item)
    assert not result.accessible and result.sha256 is None


def test_missing_digests_never_match(tmp_path):
    from homeops.media import MediaFile

    (tmp_path / "clip.mp4").write_bytes(b"video")
    result = match_files(
        [MediaFile("/sdcard/DCIM/clip", "clip", 5, True)],
        inventory_local(tmp_path),
        source_hasher=lambda item: item,
    )
    assert not result[0]["eligible"]


def test_changed_inventory_size_is_rejected(tmp_path):
    from homeops.media import hash_file

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video")
    item = inventory_local(tmp_path)[0]
    clip.write_bytes(b"larger video")
    assert not hash_file(item).accessible

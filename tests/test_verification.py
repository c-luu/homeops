from hashlib import sha256

import pytest

from homeops.adb import AdbError, Device, RemoteFile, RemoteInventory
from homeops.config import Profile
from homeops.verification import verify_media


class FakeAdb:
    def __init__(self, files=None, coverage=None, content=b"video"):
        self.files = files if files is not None else [RemoteFile("/sdcard/DCIM/original", 5)]
        self.coverage = (
            coverage
            if coverage is not None
            else [
                {"root": "DCIM", "status": "complete"},
                {"root": "Movies", "status": "absent-optional"},
            ]
        )
        self.content = content
        self.hashed = []

    def find_single_authorized(self):
        return Device("fixture-device", "device")

    def inventory_with_coverage(self, serial, roots):
        return RemoteInventory(self.files, self.coverage)

    def hash_file(self, serial, path):
        self.hashed.append(path)
        return sha256(self.content).hexdigest()

    def file_size(self, serial, path):
        return len(self.content)

    def evidence(self, serial, path):
        return self.file_size(serial, path), self.hash_file(serial, path), "1:2:5:100:100:81a4"

    def is_absent(self, serial, path):
        return False

    def delete_file(self, *args):
        raise AssertionError("read-only verification must not delete")


def test_absent_optional_root_does_not_block(tmp_path):
    (tmp_path / "renamed.mp4").write_bytes(b"video")
    result = verify_media(Profile("pixel", tmp_path, roots=("DCIM", "Movies")), FakeAdb())
    assert result["complete"] and result["coverage_complete"]
    assert result["files"][0]["eligible"]
    assert not result["inventory_errors"]


@pytest.mark.parametrize(
    "coverage,files",
    [
        ([{"root": "Movies", "status": "error-incomplete", "error": "permission denied"}], None),
        ([{"root": "Movies", "status": "error-incomplete", "error": "partial enumeration"}], None),
        (
            None,
            [RemoteFile("/sdcard/DCIM/original", 5), RemoteFile("/sdcard/Movies/unknown", None)],
        ),
    ],
)
def test_unknown_coverage_cannot_manufacture_unique_match(tmp_path, coverage, files):
    (tmp_path / "clip.mp4").write_bytes(b"video")
    result = verify_media(Profile("pixel", tmp_path), FakeAdb(files, coverage))
    assert not result["coverage_complete"]
    assert all(not entry["eligible"] for entry in result["files"])


def test_unreadable_possible_duplicate_is_not_discarded(tmp_path):
    (tmp_path / "clip.mp4").write_bytes(b"video")
    adb = FakeAdb()

    def failed(*args):
        raise AdbError("cannot read")

    adb.hash_file = failed
    result = verify_media(Profile("pixel", tmp_path), adb)
    assert result["files"][0]["status"] == "error"
    assert not result["files"][0]["eligible"]


def test_no_local_candidates_never_triggers_remote_hashing(tmp_path):
    (tmp_path / "note.txt").write_bytes(b"not a video")
    adb = FakeAdb()
    result = verify_media(Profile("pixel", tmp_path), adb)
    assert not result["files"] and not adb.hashed


class MemoryAdb(FakeAdb):
    def __init__(self, data=None):
        super().__init__()
        self.data = (
            data
            if data is not None
            else {
                "/sdcard/DCIM/first.mp4": b"video",
                "/sdcard/Movies/second.mp4": b"second",
            }
        )
        self.deleted = []
        self.serial = "fixture-device"

    def find_single_authorized(self):
        return Device(self.serial, "device")

    def inventory_with_coverage(self, serial, roots):
        return RemoteInventory(
            [RemoteFile(path, len(data)) for path, data in self.data.items()],
            [{"root": root, "status": "complete"} for root in roots],
        )

    def file_size(self, serial, path):
        if path not in self.data:
            raise AdbError("missing source")
        return len(self.data[path])

    def hash_file(self, serial, path):
        if path not in self.data:
            raise AdbError("missing source")
        return sha256(self.data[path]).hexdigest()

    def evidence(self, serial, path):
        return (
            self.file_size(serial, path),
            self.hash_file(serial, path),
            (
                f"1:{int(sha256(path.encode()).hexdigest()[:8], 16)}:{self.file_size(serial, path)}"
                ":100:100:81a4"
            ),
        )

    def is_absent(self, serial, path):
        return path not in self.data

    def delete_file(self, serial, path, *, expected_metadata=None):
        assert expected_metadata == self.evidence(serial, path)[2]
        self.deleted.append(path)
        del self.data[path]

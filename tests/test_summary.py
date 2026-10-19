from hashlib import sha256

from test_verification import FakeAdb

from homeops.adb import AdbError, RemoteFile, RemoteInventory
from homeops.cleanup import create_plan
from homeops.config import Profile
from homeops.reports import human_report, summary_lines
from homeops.verification import verify_media


def test_summary_counts_units_and_details(tmp_path):
    for name, content in {
        "unique.mp4": b"video",
        "missing.mp4": b"unmatched!",
        "dup-a.mp4": b"dupe",
        "dup-b.mp4": b"dupe",
        "error.mp4": b"broken",
        "note.txt": b"note",
    }.items():
        (tmp_path / name).write_bytes(content)
    contents = {
        "/sdcard/DCIM/original": b"video",
        "/sdcard/DCIM/duplicate": b"dupe",
        "/sdcard/DCIM/error": b"broken",
    }

    class ContentAdb(FakeAdb):
        def inventory_with_coverage(self, serial, roots):
            return RemoteInventory(
                [RemoteFile(path, len(data)) for path, data in contents.items()], self.coverage
            )

        def hash_file(self, serial, path):
            if path.endswith("/error"):
                raise AdbError("fixture read failure")
            return sha256(contents[path]).hexdigest()

        def file_size(self, serial, path):
            return len(contents[path])

    profile = Profile("pixel", tmp_path, roots=("DCIM", "Movies"))
    report = verify_media(profile, ContentAdb())
    plan = create_plan(report, destination_root=tmp_path, profile=profile)
    text = "\n".join(summary_lines(report, plan))
    assert "Scanned Mac files: 6; supported videos: 5; skipped entries: 1" in text
    assert "Verified matches: 1; unmatched Mac videos: 1; ambiguous Mac videos: 2" in text
    assert "Errors: 1 video files; 0 inventory/root errors" in text
    assert "absent optional Android roots: 1" in text
    assert "Deletion-eligible Pixel originals: 1" in text and "(5 bytes)" in text
    details = human_report(report, plan)
    assert "/sdcard/DCIM/error" in details and "fixture read failure" in details
    assert "note.txt" in details and "unsupported" in details

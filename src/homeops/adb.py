from __future__ import annotations

import posixpath
import re
import shlex
import stat
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from hashlib import sha256

from .media import DIGEST


@dataclass(frozen=True)
class Device:
    serial: str
    state: str


@dataclass(frozen=True)
class RemoteFile:
    path: str
    size: int | None


@dataclass
class RemoteInventory:
    files: list[RemoteFile]
    coverage: list[dict]


class AdbError(RuntimeError):
    pass


def validate_roots(roots: Sequence[str]) -> tuple[str, ...]:
    if not roots or isinstance(roots, (str, bytes)):
        raise ValueError("at least one relative Android media root is required")
    for root in roots:
        if (
            not isinstance(root, str)
            or not root
            or root.startswith("/")
            or any(part in {"", ".", ".."} for part in root.split("/"))
            or any(ord(char) < 32 or ord(char) == 127 for char in root)
        ):
            raise ValueError("unsafe Android media root")
    return tuple(dict.fromkeys(roots))


# Only these static programs cross the remote shell boundary. Paths arrive via stdin.
_READ_PATH = r"""
IFS= read -r path || exit 80
base=$(cd /sdcard && pwd -P) || exit 81
resolved=$(realpath -- "$path") || exit 82
if [ "$path" = /sdcard ]; then
    [ "$resolved" = "$base" ] || exit 83
else
    [ "$resolved" = "$base/${path#/sdcard/}" ] || exit 83
fi
"""
_LIST = (
    _READ_PATH
    + r"""
[ -d "$path" ] && [ -r "$path" ] && [ -x "$path" ] || exit 84
cd "$path" || exit 85
printf 'DIRECTORY\000'
find . -mindepth 1 -maxdepth 1 -print0
"""
)
_INVENTORY = (
    _READ_PATH
    + r"""
[ -d "$path" ] && [ -r "$path" ] && [ -x "$path" ] || exit 84
cd "$path" || exit 85
printf 'FILES\000'
find . -type f -print0
"""
)
_STAT_FORMAT = "%d:%i:%s:%Y:%Z:%f|%y|%z"
_STAT = (
    _READ_PATH
    + r"""
[ -f "$path" ] && [ ! -L "$path" ] || exit 86
stat -c '%d:%i:%s:%Y:%Z:%f|%y|%z' -- "$path"
"""
)
_HASH = (
    _READ_PATH
    + r"""
[ -f "$path" ] && [ ! -L "$path" ] || exit 86
before=$(stat -c '%d:%i:%s:%Y:%Z:%f|%y|%z' -- "$path") || exit 87
digest=$(sha256sum < "$path") || exit 88
after=$(stat -c '%d:%i:%s:%Y:%Z:%f|%y|%z' -- "$path") || exit 89
[ "$before" = "$after" ] || exit 90
printf '%s\000%s\000%s\000' "$before" "$digest" "$after"
"""
)

_DELETE = (
    _READ_PATH
    + r"""
IFS= read -r expected || exit 91
[ -f "$path" ] && [ ! -L "$path" ] || exit 86
current=$(stat -c '%d:%i:%s:%Y:%Z:%f|%y|%z' -- "$path") || exit 87
[ "$current" = "$expected" ] || exit 90
rm -- "$path"
"""
)

# Metadata requires stat access, not permission to read a system file's contents.
# SHA-256 is checked independently using known, non-personal stdin data.
_PROTOCOL_CHECK = (
    _READ_PATH
    + r"""
sample=
failure=
for candidate in /system/bin/toybox /system/bin/sh /system/bin/mksh; do
    test -f "$candidate" || continue
    if ! target=$(realpath -- "$candidate" 2>&1); then
        failure="system metadata sample: realpath: $target"
        continue
    fi
    if ! sample=$(stat -c '%d:%i:%s:%Y:%Z:%f|%y|%z' -- "$target" 2>&1); then
        failure="system metadata sample: stat: $sample"
        sample=
        continue
    fi
    break
done
if [ -z "$sample" ]; then
    if [ -n "$failure" ]; then
        printf '%s\n' "$failure" >&2
        exit 87
    fi
    exit 92
fi
known=$(printf 'homeops-protocol\n' | sha256sum) || exit 88
printf 'PROTOCOL\000%s\000%s\000' "$sample" "$known"
"""
)


def _command_failure(operation: str, result: subprocess.CompletedProcess[str]) -> AdbError:
    reasons = {
        80: "missing path input",
        81: "shared storage is inaccessible",
        82: "path resolution failed",
        83: "canonical path safety check failed",
        84: "directory is unavailable or access permission is denied",
        85: "could not enter directory",
        86: "file is unavailable, non-regular, or a symlink",
        87: "metadata stat or system sample resolution failed",
        88: "SHA-256 command failed",
        89: "post-hash metadata stat failed",
        90: "file changed; metadata revalidation failed",
        91: "missing expected metadata",
        92: "no available regular system file for metadata probe",
        126: "command could not be executed; check permissions and executable format",
        127: "required command is unavailable",
    }
    if result.returncode:
        reason = reasons.get(result.returncode, "command or ADB transport failure")
        status = f"failed (exit {result.returncode}: {reason})"
    else:
        status = "reported stderr (exit 0; response rejected)"
    detail = result.stderr.strip() or "no stderr"
    if "permission denied" in detail.lower():
        detail = "permission failure: " + detail
    return AdbError(f"{operation} {status}: {detail}")


class Adb:
    def __init__(
        self,
        executable: str = "adb",
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ):
        self.executable = executable
        self.runner = runner or subprocess.run

    def devices(self) -> list[Device]:
        try:
            result = self.runner(
                [self.executable, "devices"], capture_output=True, text=True, check=False
            )
        except OSError as exc:
            raise AdbError(f"ADB is unavailable: {exc}") from exc
        if result.returncode:
            raise _command_failure("ADB device discovery", result)
        devices = []
        for line in result.stdout.splitlines():
            if not line.strip() or line.startswith("List of devices"):
                continue
            parts = line.split()
            if len(parts) != 2:
                raise AdbError("unsupported ADB device discovery output")
            devices.append(Device(*parts))
        return devices

    def find_single_authorized(self) -> Device:
        devices = self.devices()
        ready = [device for device in devices if device.state == "device"]
        if len(ready) != 1 or len(devices) != 1:
            if any(device.state == "unauthorized" for device in devices):
                raise AdbError("Android device is unauthorized; unlock it and accept USB debugging")
            if len(devices) > 1:
                raise AdbError("multiple Android devices connected; leave only the intended Pixel")
            raise AdbError("no authorized Android device connected")
        return ready[0]

    @staticmethod
    def _validate_media_path(path: str, *, allow_base: bool = False) -> None:
        if (
            not isinstance(path, str)
            or any(ord(c) < 32 or ord(c) == 127 for c in path)
            or posixpath.normpath(path) != path
            or (path != "/sdcard" or not allow_base)
            and not path.startswith("/sdcard/")
        ):
            raise AdbError("unsafe or unsupported Android media path")

    def _program(self, serial: str, program: str, path: str, extra: str = "") -> str:
        self._validate_media_path(path, allow_base=True)
        if not serial or any(ord(char) < 33 for char in serial):
            raise AdbError("invalid Android device identity")
        try:
            result = self.runner(
                [self.executable, "-s", serial, "shell", "-T", "sh", "-c", shlex.quote(program)],
                input=path + "\n" + extra,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            raise AdbError(f"ADB operation failed: {exc}") from exc
        if result.returncode or result.stderr:
            operation = {
                _LIST: "Android directory enumeration",
                _INVENTORY: "Android media inventory",
                _STAT: "Android file metadata",
                _HASH: "Android SHA-256 verification",
                _DELETE: "Android deletion",
                _PROTOCOL_CHECK: "Android protocol probe",
            }.get(program, "Android shell command")
            raise _command_failure(operation, result)
        return result.stdout

    def _paths(self, serial: str, path: str, *, recursive: bool) -> list[str]:
        output = self._program(serial, _INVENTORY if recursive else _LIST, path)
        parts = output.split("\x00")
        expected = "FILES" if recursive else "DIRECTORY"
        if len(parts) < 2 or parts[0] != expected or parts[-1] != "":
            raise AdbError("unsupported framed Android inventory output")
        results = []
        for relative in parts[1:-1]:
            if not relative.startswith("./") or not relative[2:]:
                raise AdbError("invalid Android inventory path")
            if not recursive and "/" in relative[2:]:
                raise AdbError("invalid parent inventory path")
            absolute = path + "/" + relative[2:]
            self._validate_media_path(absolute)
            results.append(absolute)
        return results

    def _root_exists(self, serial: str, root: str) -> bool:
        # Successful complete parent enumeration proves absence; a failed test -e cannot.
        parent = "/sdcard"
        for component in root.split("/"):
            children = self._paths(serial, parent, recursive=False)
            parent += "/" + component
            if parent not in children:
                return False
        return True

    def inventory_with_coverage(self, serial: str, roots: Sequence[str]) -> RemoteInventory:
        roots = validate_roots(roots)
        files: dict[str, RemoteFile] = {}
        coverage = []
        for root in roots:
            try:
                if not self._root_exists(serial, root):
                    coverage.append({"root": root, "status": "absent-optional"})
                    continue
                paths = self._paths(serial, "/sdcard/" + root, recursive=True)
                for path in paths:
                    try:
                        size = self.file_size(serial, path)
                    except AdbError:
                        size = None
                    files[path] = RemoteFile(path, size)
                coverage.append({"root": root, "status": "complete"})
            except AdbError as exc:
                coverage.append({"root": root, "status": "error-incomplete", "error": str(exc)})
        return RemoteInventory(list(files.values()), coverage)

    def inventory(self, serial: str, roots: Sequence[str]) -> list[RemoteFile]:
        result = self.inventory_with_coverage(serial, roots)
        if any(root["status"] == "error-incomplete" for root in result.coverage):
            raise AdbError("Android inventory is incomplete; inspect configured roots and access")
        return result.files

    def check_protocol(self, serial: str) -> None:
        """Read-only preflight: framing, system metadata, and known-input SHA-256."""
        self._paths(serial, "/sdcard", recursive=False)
        parts = self._program(serial, _PROTOCOL_CHECK, "/sdcard").split("\x00")
        if len(parts) != 4 or parts[0] != "PROTOCOL" or parts[-1]:
            raise AdbError("unsupported framed Android protocol probe response")
        self._metadata(parts[1])
        fields = parts[2].split()
        if len(fields) != 2 or fields[1] != "-" or not DIGEST.fullmatch(fields[0]):
            raise AdbError("unsupported Android SHA-256 stdin format")
        if fields[0] != sha256(b"homeops-protocol\n").hexdigest():
            raise AdbError("Android SHA-256 known-input check failed")

    @staticmethod
    def _metadata(raw: str) -> tuple[int, ...]:
        try:
            parts = raw.split("|")
            timestamp = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{1,9} [+-]\d{4}"
            if len(parts) != 3 or any(not re.fullmatch(timestamp, part) for part in parts[1:]):
                raise ValueError
            fields = parts[0].split(":")
            if len(fields) != 6:
                raise ValueError
            numbers = tuple(
                int(item, 16 if index == 5 else 10) for index, item in enumerate(fields)
            )
            if numbers[2] < 0 or not stat.S_ISREG(numbers[5]):
                raise ValueError
            return numbers
        except ValueError as exc:
            raise AdbError("unsupported Android stat output or non-regular file") from exc

    def evidence(self, serial: str, path: str) -> tuple[int, str, str]:
        self._validate_media_path(path)
        parts = self._program(serial, _HASH, path).split("\x00")
        if len(parts) != 4 or parts[-1] or parts[0] != parts[2]:
            raise AdbError("remote file changed during hashing or unsupported hash response")
        metadata = self._metadata(parts[0])
        digest_fields = parts[1].split()
        if (
            len(digest_fields) != 2
            or digest_fields[1] != "-"
            or not DIGEST.fullmatch(digest_fields[0])
        ):
            raise AdbError("invalid SHA-256 response")
        return metadata[2], digest_fields[0], parts[0]

    def hash_file(self, serial: str, path: str) -> str:
        return self.evidence(serial, path)[1]

    def file_size(self, serial: str, path: str) -> int:
        self._validate_media_path(path)
        return self._metadata(self._program(serial, _STAT, path).strip())[2]

    def is_absent(self, serial: str, path: str) -> bool:
        self._validate_media_path(path)
        return not self._root_exists(serial, path.removeprefix("/sdcard/"))

    def delete_file(self, serial: str, path: str, *, expected_metadata: str | None = None) -> None:
        self._validate_media_path(path)
        if expected_metadata is None:
            raise AdbError("deletion requires freshly revalidated source metadata")
        self._metadata(expected_metadata)
        self._program(serial, _DELETE, path, expected_metadata + "\n")

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Device:
    serial: str
    state: str


@dataclass(frozen=True)
class RemoteFile:
    path: str
    size: int | None


class AdbError(RuntimeError):
    pass


class Adb:
    def __init__(self, executable: str = "adb", runner: Callable[..., subprocess.CompletedProcess[str]] | None = None):
        self.executable = executable
        self.runner = runner or subprocess.run

    def devices(self) -> list[Device]:
        try:
            result = self.runner(
                [self.executable, "devices"], capture_output=True, text=True, check=False
            )
        except OSError as exc:
            raise AdbError(f"ADB is unavailable: {exc}") from exc
        if result.returncode != 0:
            raise AdbError(result.stderr.strip() or "ADB device discovery failed")
        devices: list[Device] = []
        for line in result.stdout.splitlines():
            if not line.strip() or line.startswith("List of devices"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                devices.append(Device(parts[0], parts[1]))
        return devices

    def find_single_authorized(self) -> Device:
        devices = self.devices()
        ready = [device for device in devices if device.state == "device"]
        if len(ready) != 1:
            if any(device.state == "unauthorized" for device in devices):
                raise AdbError("Android device is unauthorized; unlock it and accept USB debugging")
            if len(ready) > 1:
                raise AdbError("multiple authorized Android devices connected; select one explicitly")
            raise AdbError("no authorized Android device connected")
        return ready[0]

    def shell(self, serial: str, args: Sequence[str]) -> str:
        result = self.runner(
            [self.executable, "-s", serial, "shell", *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise AdbError(result.stderr.strip() or "ADB shell command failed")
        return result.stdout

    def inventory(self, serial: str, roots: Sequence[str]) -> list[RemoteFile]:
        files: list[RemoteFile] = []
        for root in roots:
            output = self.shell(serial, ["find", f"/sdcard/{root}", "-type", "f", "-print"])
            for path in output.splitlines():
                path = path.strip()
                if not path:
                    continue
                try:
                    size = int(self.shell(serial, ["stat", "-c", "%s", path]).strip())
                except (ValueError, AdbError):
                    size = None
                files.append(RemoteFile(path, size))
        return files

    def hash_file(self, serial: str, path: str) -> str:
        result = self.runner(
            [self.executable, "-s", serial, "shell", "sha256sum", path],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise AdbError(result.stderr.strip() or f"unable to hash remote file: {path}")
        digest = result.stdout.split()[0] if result.stdout.split() else ""
        if len(digest) != 64:
            raise AdbError(f"invalid SHA-256 response for remote file: {path}")
        return digest

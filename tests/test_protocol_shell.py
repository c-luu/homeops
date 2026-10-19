"""Run the static probe in a shell with a restricted, synthetic Android filesystem."""

import os
import shlex
import subprocess
from hashlib import sha256
from subprocess import CompletedProcess

import pytest
from test_adb import META
from typer.testing import CliRunner

from homeops.adb import _LIST, _PROTOCOL_CHECK, Adb
from homeops.cli import app

# Commands outside this small read-only interface cannot access a real device.
# System-file contents are never exposed; build.prop is explicitly unreadable.
SHELL_FIXTURE = r"""
cd() { [ "$1" = /sdcard ]; }
pwd() { printf '/storage/emulated/0\n'; }
realpath() {
    case "$2" in
        /sdcard) printf '/storage/emulated/0\n' ;;
        /system/bin/sh) printf '/system/bin/mksh\n' ;;
        /system/bin/toybox|/system/bin/mksh) printf '%s\n' "$2" ;;
        *) return 73 ;;
    esac
}
test() {
    case "$1:$2" in
        -r:/system/build.prop) return 1 ;;
        -f:/system/build.prop) return 0 ;;
        -f:/system/bin/toybox) [ "$TOYBOX_PRESENT" = 1 ] ;;
        -f:/system/bin/sh|-f:/system/bin/mksh) [ "$SHELL_PRESENT" = 1 ] ;;
        *) return 74 ;;
    esac
}
stat() {
    [ "$1" = -c ] && [ "$3" = -- ] || return 75
    if [ "$STAT_DENIED" = all ] || {
        [ "$STAT_DENIED" = toybox ] && [ "$4" = /system/bin/toybox ];
    }; then
        printf 'stat: Permission denied\n' >&2
        return 1
    fi
    case "$4" in
        /system/bin/toybox|/system/bin/mksh) printf '%s\n' "$SAMPLE_METADATA" ;;
        *) return 76 ;;
    esac
}
sha256sum() {
    [ "$#" = 0 ] || return 77
    IFS= read -r line || return 78
    [ "$line" = homeops-protocol ] || return 79
    printf '%s  -\n' "$KNOWN_HASH"
}
"""


@pytest.mark.parametrize(
    "toybox,shell,denied,metadata,expected",
    [
        ("1", "1", "none", META, "compatible"),
        ("0", "1", "none", META, "compatible"),
        ("1", "1", "toybox", META, "compatible"),
        ("0", "0", "none", META, "exit 92"),
        ("1", "1", "all", META, "permission failure"),
        ("1", "1", "none", "invalid stat format", "unsupported Android stat"),
    ],
)
def test_doctor_shell_probe_handles_restricted_pixel_and_unavailable_candidates(
    monkeypatch, toybox, shell, denied, metadata, expected
):
    calls = []
    environment = dict(
        os.environ,
        TOYBOX_PRESENT=toybox,
        SHELL_PRESENT=shell,
        STAT_DENIED=denied,
        SAMPLE_METADATA=metadata,
        KNOWN_HASH=sha256(b"homeops-protocol\n").hexdigest(),
    )

    def runner(args, **kwargs):
        calls.append(args)
        if args == ["adb", "devices"]:
            return CompletedProcess(args, 0, "List of devices attached\nfixture device\n", "")
        assert kwargs["input"] == "/sdcard\n"
        if args[-1] == shlex.quote(_LIST):
            return CompletedProcess(args, 0, "DIRECTORY\x00./DCIM\x00", "")
        assert args[-1] == shlex.quote(_PROTOCOL_CHECK)
        # Execute the production shell body with controlled command/permission responses.
        assert kwargs.pop("check") is False
        return subprocess.run(
            ["/bin/sh", "-c", SHELL_FIXTURE + _PROTOCOL_CHECK],
            env=environment,
            check=False,
            **kwargs,
        )

    monkeypatch.setattr("homeops.cli.Adb", lambda: Adb(runner=runner))
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert expected in result.output
    assert len(calls) == 3
    if expected != "compatible":
        assert "check failed" in result.output
        assert "compatible" not in result.output
    assert "fixture" not in result.output
    assert "/system/build.prop" not in _PROTOCOL_CHECK
    assert "sha256sum <" not in _PROTOCOL_CHECK

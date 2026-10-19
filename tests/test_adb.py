from hashlib import sha256
from subprocess import CompletedProcess

import pytest

from homeops.adb import _INVENTORY, _LIST, _STAT, Adb, AdbError

META = "1:2:5:100:100:81a4" + "|2026-01-01 00:00:00.000000001 +0000" * 2


def test_adb_uses_argument_vector():
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        return CompletedProcess(args, 0, "List of devices attached\nfixture unauthorized\n", "")

    assert Adb(runner=runner).devices()[0].state == "unauthorized"
    assert calls == [["adb", "devices"]]


def test_remote_inventory_paths_are_data_and_framed():
    import shlex

    calls = []
    name = " clip ' $(literal); &.mp4"

    def runner(args, **kwargs):
        calls.append((args, kwargs["input"]))
        assert args[-1] in {shlex.quote(_LIST), shlex.quote(_INVENTORY), shlex.quote(_STAT)}
        if args[-1] == shlex.quote(_LIST):
            output = "DIRECTORY\x00./DCIM\x00"
        elif args[-1] == shlex.quote(_INVENTORY):
            output = "FILES\x00./" + name + "\x00"
        else:
            output = META + "\n"
        return CompletedProcess(args, 0, output, "")

    result = Adb(runner=runner).inventory_with_coverage("fixture", ["DCIM", "Movies"])
    assert result.files[0].path == "/sdcard/DCIM/" + name
    assert result.files[0].size == 5
    assert result.coverage[1]["status"] == "absent-optional"
    assert calls[-1][1] == "/sdcard\n"
    assert all(name not in argument for args, _ in calls for argument in args)


@pytest.mark.parametrize(
    "returncode,stdout,stderr",
    [
        (1, "", "permission denied"),
        (1, "", ""),
        (0, "", ""),
        (0, "DIRECTORY\x00./DCIM", ""),
        (0, "DIRECTORY\x00../escape\x00", ""),
        (0, "DIRECTORY\x00./DCIM\x00", "find: failed"),
    ],
)
def test_failed_parent_probe_is_never_optional_absence(returncode, stdout, stderr):
    adb = Adb(runner=lambda args, **kwargs: CompletedProcess(args, returncode, stdout, stderr))
    result = adb.inventory_with_coverage("fixture", ["DCIM"])
    assert result.coverage[0]["status"] == "error-incomplete"


@pytest.mark.parametrize(
    "path",
    [
        "/data/local/tmp/file",
        "/sdcard/DCIM/../file",
        "/sdcard/DCIM/new\nline",
        "/sdcard/DCIM/tab\t",
    ],
)
def test_rejects_unrepresentable_or_escaping_paths(path):
    with pytest.raises(AdbError, match="unsafe"):
        Adb(runner=lambda *args, **kwargs: pytest.fail("must reject before transport")).hash_file(
            "fixture", path
        )


def test_hash_brackets_metadata_and_parses_digest():
    digest = sha256(b"video").hexdigest()
    adb = Adb(
        runner=lambda args, **kwargs: CompletedProcess(
            args, 0, META + "\x00" + digest + "  -\x00" + META + "\x00", ""
        )
    )
    assert adb.evidence("fixture", "/sdcard/DCIM/renamed") == (5, digest, META)


@pytest.mark.parametrize(
    "response",
    [
        META + "\x00" + "g" * 64 + "  -\x00" + META + "\x00",
        META + "\x00" + "0" * 64 + "  -\x00" + META.replace(":5:", ":6:") + "\x00",
        "malformed",
    ],
)
def test_invalid_hash_or_changed_metadata_is_rejected(response):
    adb = Adb(runner=lambda args, **kwargs: CompletedProcess(args, 0, response, ""))
    with pytest.raises(AdbError):
        adb.hash_file("fixture", "/sdcard/DCIM/clip")


def test_delete_rejects_paths_outside_shared_storage():
    with pytest.raises(AdbError, match="unsafe"):
        Adb(runner=lambda *args, **kwargs: None).delete_file("fixture", "/data/local/tmp/file")


def test_delete_uses_static_program_and_revalidated_metadata_as_data():
    import shlex

    from homeops.adb import _DELETE

    path = "/sdcard/DCIM/ clip ' $(literal); &.mp4"
    calls = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return CompletedProcess(args, 0, "", "")

    adb = Adb(runner=runner)
    adb.delete_file("fixture", path, expected_metadata=META)
    assert calls[0][0][-1] == shlex.quote(_DELETE)
    assert calls[0][1]["input"] == path + "\n" + META + "\n"
    assert all(path not in arg for arg in calls[0][0])
    with pytest.raises(AdbError, match="freshly"):
        adb.delete_file("fixture", path)


def test_remote_shell_serialization_keeps_metacharacters_literal():
    import subprocess

    def runner(args, **kwargs):
        # Model ADB's remote command joining with a real local shell, without a device.
        check = kwargs.pop("check", False)
        return subprocess.run(["/bin/sh", "-c", " ".join(args[5:])], check=check, **kwargs)

    adb = Adb(runner=runner)
    path = "/sdcard/DCIM/ $(printf expanded); ' literal &.mp4"
    program = "IFS= read -r path; printf '%s' \"$path\""
    assert adb._program("fixture", program, path) == path


def test_same_second_remote_mutation_is_detected():
    digest = sha256(b"video").hexdigest()
    changed = META.replace(".000000001", ".000000002", 1)
    adb = Adb(
        runner=lambda args, **kwargs: CompletedProcess(
            args, 0, META + "\x00" + digest + "  -\x00" + changed + "\x00", ""
        )
    )
    with pytest.raises(AdbError, match="changed"):
        adb.hash_file("fixture", "/sdcard/DCIM/clip")

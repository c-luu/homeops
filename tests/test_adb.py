from subprocess import CompletedProcess

from homeops.adb import Adb


def test_adb_uses_argument_vector() -> None:
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        return CompletedProcess(args, 0, "List of devices attached\nserial unauthorized\n", "")

    assert Adb(runner=runner).devices()[0].state == "unauthorized"
    assert calls[0] == ["adb", "devices"]


def test_remote_inventory_uses_argument_vector() -> None:
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        if "find" in args:
            return CompletedProcess(args, 0, "/sdcard/DCIM/clip one.mp4\n", "")
        return CompletedProcess(args, 0, "12\n", "")

    files = Adb(runner=runner).inventory("serial", ["DCIM"])
    assert files[0].path.endswith("clip one.mp4")
    assert files[0].size == 12
    assert all(isinstance(arg, str) for call in calls for arg in call)

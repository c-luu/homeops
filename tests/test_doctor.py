import pytest
from typer.testing import CliRunner

from homeops.adb import AdbError, Device
from homeops.cli import app


@pytest.mark.parametrize("connected", [False, True])
def test_doctor_only_probes_one_authorized_device_without_media_operations(monkeypatch, connected):
    calls = []

    class FakeAdb:
        def devices(self):
            return [Device("fixture", "device")] if connected else []

        def check_protocol(self, serial):
            calls.append(serial)

        def inventory_with_coverage(self, *args):
            pytest.fail("doctor must not inventory videos")

        def delete_file(self, *args):
            pytest.fail("doctor must never delete")

    monkeypatch.setattr("homeops.cli.Adb", FakeAdb)
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert calls == (["fixture"] if connected else [])
    assert ("compatible" if connected else "not checked") in result.output
    assert "fixture" not in result.output


def test_doctor_reports_protocol_failure_without_fallback(monkeypatch):
    class FakeAdb:
        def devices(self):
            return [Device("fixture", "device")]

        def check_protocol(self, serial):
            raise AdbError("unsupported Android stat output")

    monkeypatch.setattr("homeops.cli.Adb", FakeAdb)
    result = CliRunner().invoke(app, ["doctor"])
    assert "unsupported Android stat output" in result.output
    assert "compatible" not in result.output

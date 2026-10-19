import json
from pathlib import Path

from typer.testing import CliRunner

from homeops.adb import Device
from homeops.cli import app
from homeops.config import load_config


def test_dry_run_needs_no_confirmation_and_prints_summary(tmp_path: Path, monkeypatch) -> None:
    destination = tmp_path / "backup"
    destination.mkdir()
    config = tmp_path / "state" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        f'[backups.pixel]\ndestination = "{destination}"\nroots = ["DCIM"]\n',
        encoding="utf-8",
    )
    plan = tmp_path / "state" / "cleanup-plan.json"

    class FakeAdb:
        def find_single_authorized(self):
            return Device("pixel", "device")

    from homeops.cleanup import create_plan
    from homeops.verification import verify_media

    selected = load_config(config)[0].profiles["pixel"]
    report = verify_media(selected, FakeAdb())
    payload = create_plan(report, destination_root=destination, profile=selected)
    plan.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr("homeops.cli.Adb", FakeAdb)
    result = CliRunner().invoke(
        app,
        ["android", "cleanup", "apply", "--config", str(config), "--plan", str(plan)],
    )
    assert result.exit_code == 0, result.output
    assert "Cleanup plan contains no eligible files" in result.output


def test_explicit_config_keeps_reports_with_that_machine_state(tmp_path: Path) -> None:
    config = tmp_path / "machine-a" / "config.toml"
    config.parent.mkdir()
    config.write_text("[defaults]\n", encoding="utf-8")
    settings, source = load_config(config)
    assert source == str(config)
    assert settings.profiles == {}

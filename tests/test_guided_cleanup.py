import json

import pytest
from test_verification import FakeAdb
from typer.testing import CliRunner

from homeops.cli import app


def machine(tmp_path, name="mini", mode="disabled"):
    media = tmp_path / name / "received"
    (media / "batch").mkdir(parents=True)
    (media / "batch" / "renamed.mp4").write_bytes(b"video")
    (media / "outside.mp4").write_bytes(b"other-size")
    config = tmp_path / name / "state" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        "[defaults]\ndry_run = true\n[backups.pixel]\n"
        f"destination = {json.dumps(str(media))}\n"
        f'delete_mode = {json.dumps(mode)}\nroots = ["DCIM", "Movies"]\n'
    )
    return media, config


@pytest.mark.parametrize("name", ["mini", "laptop"])
@pytest.mark.parametrize("mode", ["disabled", "permanent"])
def test_guided_explicit_dry_run_is_read_only_even_with_permanent_policy(
    tmp_path, monkeypatch, name, mode
):
    media, config = machine(tmp_path, name, mode)
    before = {str(p): p.read_bytes() for p in media.rglob("*") if p.is_file()}
    monkeypatch.setattr("homeops.cli.Adb", FakeAdb)
    result = CliRunner().invoke(
        app, ["android", "cleanup", "--config", str(config), "--within", "batch", "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert "subdirectory" in result.output and "batch" in result.output
    assert "Deletion-eligible Pixel originals: 1" in result.output
    assert "5 bytes" in result.output
    assert "Read-only review complete; no media changed." in result.output
    assert "Execute this" not in result.output
    assert {str(p): p.read_bytes() for p in media.rglob("*") if p.is_file()} == before
    plan = json.loads((config.parent / "cleanup-plan.json").read_text())
    assert plan["scope"]["within"] == "batch"
    if mode == "disabled":
        assert 'delete_mode = "permanent"' in result.output
        assert config.read_text().find('delete_mode = "disabled"') >= 0


def test_advanced_verify_plan_and_dry_apply_keep_config_context(tmp_path, monkeypatch):
    _, config = machine(tmp_path)
    monkeypatch.setattr("homeops.cli.Adb", FakeAdb)
    runner = CliRunner()
    for args in [
        ["android", "backup", "verify", "--within", "batch"],
        ["android", "cleanup", "plan"],
        ["android", "cleanup", "apply"],
    ]:
        result = runner.invoke(app, args + ["--config", str(config)])
        assert result.exit_code == 0, result.output


def test_help_never_runs_verification(monkeypatch):
    monkeypatch.setattr("homeops.cli.Adb", lambda: pytest.fail("help is read-only"))
    result = CliRunner().invoke(app, ["android", "cleanup", "--help"])
    assert result.exit_code == 0 and "--within" in result.output


def test_genuine_coverage_error_shows_zero_eligibility(tmp_path, monkeypatch):
    _, config = machine(tmp_path)
    monkeypatch.setattr(
        "homeops.cli.Adb",
        lambda: FakeAdb(
            coverage=[
                {"root": "DCIM", "status": "error-incomplete", "error": "unreadable"},
                {"root": "Movies", "status": "absent-optional"},
            ]
        ),
    )
    result = CliRunner().invoke(app, ["android", "cleanup", "--config", str(config)])
    assert result.exit_code == 1, result.output
    assert "Deletion-eligible Pixel originals: 0" in result.output
    assert "1 inventory/root errors" in result.output


def test_state_cannot_be_written_into_media(tmp_path, monkeypatch):
    media, config = machine(tmp_path)
    monkeypatch.setattr("homeops.cli.Adb", lambda: pytest.fail("reject before device access"))
    result = CliRunner().invoke(
        app,
        [
            "android",
            "backup",
            "verify",
            "--config",
            str(config),
            "--output",
            str(media / "report.json"),
        ],
    )
    assert result.exit_code != 0
    assert not (media / "report.json").exists()

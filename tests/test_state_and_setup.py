import json
from pathlib import Path
from subprocess import CompletedProcess

from typer.testing import CliRunner

from homeops.cli import app
from homeops.config import load_config
from homeops.state import resolve_config_path, state_paths


def test_state_config_precedence(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "state"
    root.mkdir()
    configured = root / "config.toml"
    configured.write_text("[defaults]\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert resolve_config_path(root=root) == configured
    explicit = tmp_path / "explicit.toml"
    explicit.write_text("[defaults]\n", encoding="utf-8")
    assert resolve_config_path(explicit, root=root) == explicit


def test_setup_persists_profile_without_media_changes(tmp_path: Path, monkeypatch) -> None:
    destination = tmp_path / "backup"
    destination.mkdir()
    config = tmp_path / "state" / "config.toml"
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        return CompletedProcess(args, 0, "List of devices attached\npixel device\n", "")

    monkeypatch.setattr("homeops.cli.Adb", lambda: __import__("homeops.adb", fromlist=["Adb"]).Adb(runner=runner))
    result = CliRunner().invoke(
        app,
        [
            "android",
            "setup",
            "--destination",
            str(destination),
            "--config",
            str(config),
            "--delete-mode",
            "permanent",
        ],
    )
    assert result.exit_code == 0, result.output
    settings, source = load_config(config)
    assert settings.profiles["pixel"].destination == destination.resolve()
    assert settings.profiles["pixel"].delete_mode == "permanent"
    assert source == str(config)
    assert calls == [["adb", "devices"]]


def test_state_paths_are_local_to_application_root(tmp_path: Path) -> None:
    paths = state_paths(tmp_path)
    assert paths.verification_report == tmp_path / "verification-report.json"
    assert paths.cleanup_plan == tmp_path / "cleanup-plan.json"
    assert json.loads('{"ok": true}')["ok"]

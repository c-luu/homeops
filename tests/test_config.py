from pathlib import Path

from homeops.config import load_config


def test_config_expands_destination(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[backups.pixel]\ndestination = "~/Movies"\n', encoding="utf-8")
    settings, source = load_config(config)
    assert settings.profiles["pixel"].destination.is_absolute()
    assert source == str(config)

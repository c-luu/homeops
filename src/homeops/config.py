from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .state import resolve_config_path


class ConfigError(ValueError):
    """Raised when configuration is missing or unsafe."""


@dataclass(frozen=True)
class Profile:
    name: str
    destination: Path
    roots: tuple[str, ...] = ("DCIM", "Movies", "Pictures", "Download")
    recursive: bool = True
    delete_mode: str = "disabled"


@dataclass(frozen=True)
class Config:
    dry_run: bool
    verification: str
    profiles: dict[str, Profile]


def _path(value: str) -> Path:
    expanded = Path(os.path.expandvars(os.path.expanduser(value))).resolve()
    if not expanded.is_absolute():
        raise ConfigError(f"path must be absolute after expansion: {value}")
    return expanded


def load_config(path: Path | None = None, *, profile: str = "pixel") -> tuple[Config, str]:
    source = resolve_config_path(path)
    if not source.exists():
        if path:
            raise ConfigError(f"configuration file does not exist: {source}")
        return Config(dry_run=True, verification="sha256", profiles={}), "defaults"
    try:
        raw: dict[str, Any] = tomllib.loads(source.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"invalid configuration {source}: {exc}") from exc
    defaults = raw.get("defaults", {})
    backups = raw.get("backups", {})
    profiles: dict[str, Profile] = {}
    for name, item in backups.items():
        if not isinstance(item, dict) or "destination" not in item:
            raise ConfigError(f"backup profile {name!r} requires destination")
        delete_mode = str(item.get("delete_mode", defaults.get("delete_mode", "disabled")))
        if delete_mode not in {"disabled", "trash", "permanent"}:
            raise ConfigError(f"unsupported delete_mode: {delete_mode}")
        profiles[name] = Profile(
            name=name,
            destination=_path(str(item["destination"])),
            roots=tuple(str(root) for root in item.get("roots", Profile.roots)),
            recursive=bool(item.get("recursive", True)),
            delete_mode=delete_mode,
        )
    if profile not in profiles and profiles:
        raise ConfigError(f"profile not found: {profile}")
    return Config(
        dry_run=bool(defaults.get("dry_run", True)),
        verification=str(defaults.get("verification", "sha256")),
        profiles=profiles,
    ), str(source)


def write_config(
    path: Path,
    *,
    destination: Path,
    roots: tuple[str, ...],
    delete_mode: str,
    recursive: bool = True,
) -> None:
    if delete_mode not in {"disabled", "trash", "permanent"}:
        raise ConfigError(f"unsupported delete_mode: {delete_mode}")
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "[defaults]",
        "dry_run = true",
        'verification = "sha256"',
        f'delete_mode = "{delete_mode}"',
        "",
        "[backups.pixel]",
        f'destination = "{destination}"',
        f"recursive = {str(recursive).lower()}",
        f"roots = [{', '.join(repr(root) for root in roots)}]",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")

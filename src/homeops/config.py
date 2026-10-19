from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .adb import validate_roots
from .state import atomic_text, resolve_config_path


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
    expanded = Path(os.path.expandvars(os.path.expanduser(value)))
    if not expanded.is_absolute():
        raise ConfigError(f"path must be absolute after expansion: {value}")
    return expanded.resolve()


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
    if not isinstance(defaults, dict) or not isinstance(backups, dict):
        raise ConfigError("defaults and backups must be TOML tables")
    if type(defaults.get("dry_run", True)) is not bool:
        raise ConfigError("dry_run must be a boolean")
    if defaults.get("verification", "sha256") != "sha256":
        raise ConfigError("only SHA-256 verification is supported")
    profiles: dict[str, Profile] = {}
    for name, item in backups.items():
        if not isinstance(item, dict) or "destination" not in item:
            raise ConfigError(f"backup profile {name!r} requires destination")
        delete_mode = str(item.get("delete_mode", defaults.get("delete_mode", "disabled")))
        if delete_mode not in {"disabled", "trash", "permanent"}:
            raise ConfigError(f"unsupported delete_mode: {delete_mode}")
        roots = item.get("roots", list(Profile.roots))
        try:
            if not isinstance(roots, list):
                raise ConfigError("roots must be an array")
            roots = validate_roots(roots)
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        if type(item.get("recursive", True)) is not bool:
            raise ConfigError("recursive must be a boolean")
        if not isinstance(item["destination"], str):
            raise ConfigError("destination must be a path string")
        profiles[name] = Profile(
            name=name,
            destination=_path(str(item["destination"])),
            roots=roots,
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
    try:
        validate_roots(roots)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    lines = [
        "[defaults]",
        "dry_run = true",
        'verification = "sha256"',
        'delete_mode = "disabled"',
        "",
        "[backups.pixel]",
        f"destination = {json.dumps(str(destination))}",
        f'delete_mode = "{delete_mode}"',
        f"recursive = {str(recursive).lower()}",
        f"roots = {json.dumps(list(roots))}",
        "",
    ]
    atomic_text(path, "\n".join(lines))

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StatePaths:
    root: Path
    config: Path
    verification_report: Path
    cleanup_plan: Path
    cleanup_result: Path
    verification_checkpoint: Path


def default_root() -> Path:
    return Path(os.path.expanduser("~/Library/Application Support/HomeOps"))


def resolve_root(override: Path | None = None) -> Path:
    return (override or default_root()).expanduser().resolve()


def resolve_config_path(override: Path | None = None, *, root: Path | None = None) -> Path:
    if override:
        return override.expanduser()
    state_root = resolve_root(root)
    configured = state_root / "config.toml"
    if configured.exists():
        return configured
    development = Path("examples/config.toml")
    return development if development.exists() else configured


def state_paths(root: Path | None = None) -> StatePaths:
    resolved = resolve_root(root)
    return StatePaths(
        root=resolved,
        config=resolved / "config.toml",
        verification_report=resolved / "verification-report.json",
        cleanup_plan=resolved / "cleanup-plan.json",
        cleanup_result=resolved / "cleanup-plan.result.json",
        verification_checkpoint=resolved / "verification-checkpoint.json",
    )


def paths_for_config(config: Path | None = None) -> StatePaths:
    effective = resolve_config_path(config)
    return state_paths(effective.expanduser().resolve().parent)


def check_artifacts(paths: list[Path], destination: Path) -> None:
    canonical = [path.expanduser().resolve() for path in paths]
    if len(set(canonical)) != len(canonical):
        raise ValueError("operational artifact paths must be distinct")
    if any(path.is_relative_to(destination.resolve()) for path in canonical):
        raise ValueError("operational state must be outside the entire LocalSend destination")


def atomic_text(path: Path, content: str) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".homeops-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_json(path: Path, payload: dict) -> None:
    atomic_text(path, json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n")

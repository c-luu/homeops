from __future__ import annotations

import os
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

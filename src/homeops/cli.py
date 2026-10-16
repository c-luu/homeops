from __future__ import annotations

import json
import os
from pathlib import Path

import typer

from .adb import Adb, AdbError
from .cleanup import CleanupError, create_plan
from .config import ConfigError, load_config, write_config
from .executor import apply_plan
from .media import MediaFile, hash_file, inventory_local
from .reports import write_checkpoint, write_human_report, write_report
from .state import state_paths

app = typer.Typer(help="Safe, reproducible personal operations.")
android = typer.Typer()
backup = typer.Typer()
cleanup = typer.Typer()
app.add_typer(android, name="android")
android.add_typer(backup, name="backup")
android.add_typer(cleanup, name="cleanup")


@app.command()
def doctor() -> None:
    """Report local prerequisites without changing the system."""
    try:
        devices = Adb().devices()
        typer.echo(f"ADB: available ({len(devices)} device(s))")
    except AdbError as exc:
        typer.echo(f"ADB: {exc}")
    typer.echo(f"Python: {__import__('sys').version.split()[0]}")


@android.command("devices")
def devices() -> None:
    try:
        for device in Adb().devices():
            typer.echo(f"{device.serial}\t{device.state}")
    except AdbError as exc:
        raise typer.BadParameter(str(exc)) from exc


@android.command("setup")
def setup(
    destination: Path = typer.Option(..., "--destination", help="Writable LocalSend backup directory."),
    config: Path | None = typer.Option(None, "--config"),
    roots: str = typer.Option("DCIM,Movies,Pictures,Download", "--roots"),
    delete_mode: str = typer.Option("disabled", "--delete-mode"),
) -> None:
    try:
        device = Adb().find_single_authorized()
        destination = destination.expanduser().resolve()
        if not destination.exists() or not destination.is_dir():
            raise ConfigError(f"backup destination is not an existing directory: {destination}")
        if not os.access(destination, os.W_OK):
            raise ConfigError(f"backup destination is not writable: {destination}")
        selected_roots = tuple(root.strip().strip("/") for root in roots.split(",") if root.strip())
        if not selected_roots:
            raise ConfigError("at least one Android source root is required")
        target = config or state_paths().config
        write_config(
            target,
            destination=destination,
            roots=selected_roots,
            delete_mode=delete_mode,
        )
        typer.echo(f"Configured device {device.serial} in {target}")
    except (ConfigError, AdbError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@backup.command("verify")
def verify(
    profile: str = typer.Option("pixel"),
    config: Path | None = typer.Option(None, "--config"),
    output: Path | None = typer.Option(None, "--output"),
) -> None:
    try:
        settings, _ = load_config(config, profile=profile)
        selected = settings.profiles[profile]
        paths = _paths_for_config(config)
        output = output or paths.verification_report
        device = Adb().find_single_authorized()
        local = inventory_local(selected.destination)
        remote = Adb().inventory(device.serial, selected.roots)
        sources = []
        for item in remote:
            source = MediaFile(item.path, Path(item.path).name, item.size, item.size is not None)
            sources.append(source)
        source_sizes = {item.size for item in sources if item.accessible and item.size is not None}
        backups = [
            hash_file(item)
            for item in local
            if item.accessible and item.size in source_sizes
        ]
        results = []
        checkpoint = paths.verification_checkpoint
        for source in sources:
            candidates_by_size = [
                backup for backup in backups
                if source.accessible and backup.accessible and source.size == backup.size
            ]
            if candidates_by_size:
                try:
                    source.sha256 = Adb().hash_file(device.serial, source.path)
                except AdbError as exc:
                    source.accessible = False
                    source.error = str(exc)
            candidates = [
                backup for backup in backups
                if source.accessible and backup.accessible
                and source.size == backup.size and source.sha256 == backup.sha256
            ]
            results.append(
                {
                    "source": source.json(),
                    "candidates": [candidate.json() for candidate in candidates],
                    "status": "verified" if len(candidates) == 1 else (
                        "ambiguous" if len(candidates) > 1 else "unmatched"
                    ),
                }
            )
            write_checkpoint(results, checkpoint)
        write_report(
            results,
            output,
            device=device.serial,
            destination_root=selected.destination,
            source_roots=selected.roots,
        )
        write_human_report(results, output.with_suffix(".txt"))
        counts = _status_counts(results)
        typer.echo(
            f"Matched: {counts['verified']}; unmatched: {counts['unmatched']}; "
            f"ambiguous: {counts['ambiguous']}; cleanup-eligible: {counts['eligible']}"
        )
        typer.echo(f"Wrote {output}")
    except (ConfigError, AdbError, KeyError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@cleanup.command("plan")
def plan(
    report: Path | None = typer.Option(None, "--report"),
    output: Path | None = typer.Option(None, "--output"),
) -> None:
    paths = state_paths()
    report = report or paths.verification_report
    output = output or paths.cleanup_plan
    data = json.loads(report.read_text(encoding="utf-8"))
    root = Path(str(data.get("destination_root", Path.cwd())))
    payload = create_plan(data, destination_root=root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    typer.echo(f"Cleanup-eligible: {len(payload['files'])}; wrote {output}")


@cleanup.command("apply")
def apply(
    plan: Path | None = typer.Option(None, "--plan"),
    config: Path | None = typer.Option(None, "--config"),
    profile: str = typer.Option("pixel"),
    confirm: bool = typer.Option(False, "--confirm"),
    permanent: bool = typer.Option(False, "--permanent"),
    acknowledge_permanent: bool = typer.Option(False, "--acknowledge-permanent"),
    dry_run: bool = typer.Option(True, "--dry-run/--execute"),
) -> None:
    try:
        settings, _ = load_config(config, profile=profile)
        selected = settings.profiles[profile]
        plan = plan or _paths_for_config(config).cleanup_plan
        if selected.delete_mode == "disabled" and not dry_run:
            raise CleanupError("configured deletion policy is disabled")
        if selected.delete_mode == "trash" and not dry_run:
            raise CleanupError("configured trash deletion is not supported")
        if not dry_run and not permanent:
            raise CleanupError("no supported recoverable Android trash operation is configured")
        if not dry_run and not acknowledge_permanent:
            raise CleanupError("permanent deletion requires --acknowledge-permanent")
        payload = json.loads(plan.read_text(encoding="utf-8"))
        device = Adb().find_single_authorized()
        planned_device = payload.get("device")
        if planned_device != device.serial:
            raise CleanupError("cleanup plan belongs to a different Android device")
        if not payload.get("files"):
            typer.echo("Cleanup plan contains no eligible files; no files changed.")
            return

        adb = Adb()

        def source_check(path: str, size: int, digest: str) -> bool:
            return (
                adb.file_size(device.serial, path) == size
                and adb.hash_file(device.serial, path) == digest
            )

        results = apply_plan(
            payload,
            plan.with_suffix(".result.json"),
            source_check=source_check,
            delete_source=lambda path: adb.delete_file(device.serial, path),
            confirm=confirm,
            permanent_acknowledged=acknowledge_permanent,
            dry_run=dry_run,
        )
        counts = _status_counts(results)
        typer.echo(
            f"Cleanup results: dry-run {counts['dry-run']}; deleted {counts['deleted']}; "
            f"already deleted {counts['already-deleted']}; uncertain {counts['unknown']}"
        )
    except (ConfigError, CleanupError, AdbError, KeyError, OSError, json.JSONDecodeError) as exc:
        raise typer.BadParameter(str(exc)) from exc


def _status_counts(results: list[dict[str, object]]) -> dict[str, int]:
    counts = {
        "verified": 0,
        "unmatched": 0,
        "ambiguous": 0,
        "eligible": 0,
        "dry-run": 0,
        "deleted": 0,
        "already-deleted": 0,
        "unknown": 0,
    }
    for result in results:
        status = str(result.get("status"))
        if status in counts:
            counts[status] += 1
    counts["eligible"] = counts["verified"]
    return counts


def _paths_for_config(config: Path | None):
    return state_paths(config.expanduser().parent if config else None)


if __name__ == "__main__":
    app()

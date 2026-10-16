from __future__ import annotations

import json
from pathlib import Path

import typer

from .adb import Adb, AdbError
from .cleanup import create_plan
from .config import ConfigError, load_config
from .media import MediaFile, hash_file, inventory_local
from .reports import write_human_report, write_report

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


@backup.command("verify")
def verify(
    profile: str = typer.Option("pixel"),
    config: Path | None = typer.Option(None, "--config"),
    output: Path = typer.Option(Path("verification-report.json"), "--output"),
) -> None:
    try:
        settings, _ = load_config(config, profile=profile)
        selected = settings.profiles[profile]
        device = Adb().find_single_authorized()
        local = inventory_local(selected.destination)
        remote = Adb().inventory(device.serial, selected.roots)
        sources = []
        for item in remote:
            source = MediaFile(item.path, Path(item.path).name, item.size, item.size is not None)
            try:
                source.sha256 = Adb().hash_file(device.serial, item.path)
            except AdbError as exc:
                source.accessible = False
                source.error = str(exc)
            sources.append(source)
        backups = [hash_file(item) for item in local]
        results = []
        for source in sources:
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
        write_report(results, output, device=device.serial, destination_root=selected.destination)
        write_human_report(results, output.with_suffix(".txt"))
        typer.echo(f"Wrote {output}")
    except (ConfigError, AdbError, KeyError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@cleanup.command("plan")
def plan(
    report: Path = typer.Option(..., "--report"),
    output: Path = typer.Option(Path("cleanup-plan.json"), "--output"),
) -> None:
    data = json.loads(report.read_text(encoding="utf-8"))
    root = Path(str(data.get("destination_root", Path.cwd())))
    payload = create_plan(data, destination_root=root)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    typer.echo(f"Wrote {output} ({len(payload['files'])} eligible file(s))")


@cleanup.command("apply")
def apply(
    plan: Path = typer.Option(..., "--plan"),
    permanent: bool = typer.Option(False, "--permanent"),
    confirm: bool = typer.Option(False, "--confirm"),
) -> None:
    if not confirm:
        raise typer.BadParameter("cleanup requires --confirm; no files were changed")
    if permanent:
        raise typer.BadParameter("permanent deletion is not enabled until trash support is verified")
    typer.echo("No supported recoverable Android trash operation is configured; no files changed.")


if __name__ == "__main__":
    app()

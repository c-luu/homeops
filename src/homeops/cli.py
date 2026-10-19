from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import typer

from .adb import Adb, AdbError
from .cleanup import CleanupError, create_plan, revalidate, validate_plan
from .config import ConfigError, load_config, write_config
from .executor import execute_plan_file, unresolved_journals
from .media import resolve_scope
from .reports import save_verification, summary_lines
from .state import atomic_json, check_artifacts, paths_for_config, state_paths
from .verification import verify_media

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
        adb = Adb()
        devices = adb.devices()
        typer.echo(f"ADB: available ({len(devices)} device(s))")
        if len(devices) == 1 and devices[0].state == "device":
            try:
                adb.check_protocol(devices[0].serial)
            except AdbError as exc:
                typer.echo(f"Android shell protocol: check failed: {exc}")
            else:
                typer.echo(
                    "Android shell protocol: compatible "
                    "(read-only system metadata and known-input SHA-256; no video reads)"
                )
        else:
            typer.echo("Android shell protocol: not checked; connect exactly one authorized Pixel.")
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
    destination: Path = typer.Option(
        ..., "--destination", help="Existing readable LocalSend destination."
    ),
    config: Path | None = typer.Option(None, "--config"),
    roots: str = typer.Option("DCIM,Movies,Pictures,Download", "--roots"),
    delete_mode: str = typer.Option("disabled", "--delete-mode"),
) -> None:
    try:
        device = Adb().find_single_authorized()
        destination = destination.expanduser().resolve()
        if not destination.exists() or not destination.is_dir():
            raise ConfigError(f"backup destination is not an existing directory: {destination}")
        if not os.access(destination, os.R_OK | os.X_OK):
            raise ConfigError(f"backup destination is not readable: {destination}")
        selected_roots = tuple(root.strip().strip("/") for root in roots.split(",") if root.strip())
        if not selected_roots:
            raise ConfigError("at least one Android source root is required")
        target = config or state_paths().config
        check_artifacts([target], destination)
        write_config(
            target,
            destination=destination,
            roots=selected_roots,
            delete_mode=delete_mode,
        )
        typer.echo(f"Configured device {device.serial} in {target}")
    except (ConfigError, AdbError, OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc


def _context(config: Path | None, profile: str):
    settings, source = load_config(config, profile=profile)
    if settings.verification != "sha256" or profile not in settings.profiles:
        raise ConfigError("configure a real profile with SHA-256 verification using android setup")
    selected = settings.profiles[profile]
    resolve_scope(selected.destination)
    return selected, Path(source), paths_for_config(config)


def _policy_message(selected, config_path: Path) -> None:
    typer.echo(
        f"Deletion policy: {selected.delete_mode}; profile: {selected.name}; config: {config_path}"
    )
    if selected.delete_mode == "disabled":
        typer.echo(
            "Execution unavailable: deletion policy is disabled. After reviewing permanent "
            'deletion, intentionally set delete_mode = "permanent" in the existing '
            f"[backups.{selected.name}] table of that local config. Keep dry_run = true; "
            "every run still requires separate approvals. See README: Enable permanent "
            "deletion. No policy was changed."
        )
    elif selected.delete_mode == "trash":
        typer.echo(
            "Execution unavailable: recoverable Android trash is unsupported. "
            "No fallback to permanent deletion is allowed."
        )


def _review(report: dict, payload: dict) -> None:
    for line in summary_lines(report, payload):
        typer.echo(line)
    if payload.get("blocked_reason"):
        typer.echo(f"Cleanup blocked: {payload['blocked_reason']}")
    for excluded in payload.get("exclusions", []):
        typer.echo(f"Skipped plan entry: {excluded['path']}; {excluded['reason']}")
    typer.echo(f"Plan: {payload['plan_id']}; fingerprint: {payload['fingerprint']}")
    for entry in payload["files"]:
        typer.echo(f"  Pixel: {entry['source']}\n    Local: {entry['destination']}")


def _verify_run(
    config: Path | None,
    profile: str,
    output: Path | None,
    within: str | None,
    *,
    plan_output: Path | None = None,
):
    selected, config_path, paths = _context(config, profile)
    output = output or paths.verification_report
    artifacts = [config_path, output, output.with_suffix(".txt"), paths.verification_checkpoint]
    if plan_output:
        artifacts.append(plan_output)
    check_artifacts(artifacts, selected.destination)
    root, scope, _ = resolve_scope(selected.destination, within)
    typer.echo(
        f"Verifying LocalSend scope: {scope}; configured destination: {root}; "
        f"{'recursive' if selected.recursive else 'top-level only'}"
    )
    report = verify_media(
        selected,
        Adb(),
        within=within,
        checkpoint=lambda data: atomic_json(paths.verification_checkpoint, data),
    )
    payload = create_plan(report, destination_root=selected.destination, profile=selected)
    save_verification(report, output, payload)
    return selected, config_path, paths, report, payload, output


@backup.command("verify")
def verify(
    profile: str = typer.Option("pixel"),
    config: Path | None = typer.Option(None, "--config"),
    output: Path | None = typer.Option(None, "--output"),
    within: str | None = typer.Option(
        None, "--within", help="Existing relative LocalSend subfolder."
    ),
) -> None:
    try:
        _, _, _, report, payload, output = _verify_run(config, profile, output, within)
        _review(report, payload)
        typer.echo(f"Detailed reports: {output} and {output.with_suffix('.txt')}")
        if not report["coverage_complete"] or any(e["status"] == "error" for e in report["files"]):
            raise typer.Exit(1)
    except (ConfigError, CleanupError, AdbError, ValueError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@cleanup.callback(invoke_without_command=True)
def guided_cleanup(
    ctx: typer.Context,
    config: Path | None = typer.Option(None, "--config"),
    profile: str = typer.Option("pixel", "--profile"),
    within: str | None = typer.Option(
        None, "--within", help="Existing relative LocalSend subfolder."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Review only; never offer execution."),
) -> None:
    if ctx.invoked_subcommand is not None:
        if config is not None or within is not None or profile != "pixel" or dry_run:
            raise typer.BadParameter(
                "put advanced options after plan/apply; --within belongs to verify"
            )
        return
    try:
        plan_output = paths_for_config(config).cleanup_plan
        selected, config_path, _, report, payload, output = _verify_run(
            config, profile, None, within, plan_output=plan_output
        )
        atomic_json(plan_output, payload)
        revalidate(payload, profile=selected, source_check=_source_check(Adb(), payload["device"]))
        _review(report, payload)
        typer.echo(f"Detailed reports: {output} and {output.with_suffix('.txt')}")
        typer.echo(f"Reviewed plan: {plan_output}")
        _policy_message(selected, config_path)
        if not report["coverage_complete"] or any(e["status"] == "error" for e in report["files"]):
            raise typer.Exit(1)
        for prior in unresolved_journals(plan_output.parent):
            typer.echo(
                f"Prior unresolved deletion journal retained: {prior}; this fresh plan "
                "requires new review and approvals before any further execution."
            )
        if not payload["files"] or selected.delete_mode != "permanent" or dry_run:
            typer.echo("Read-only review complete; no media changed.")
            return
        if not is_interactive():
            typer.echo(
                "Read-only review complete; no media changed. Noninteractive execution "
                "requires advanced cleanup apply with all explicit approval flags."
            )
            return
        approval_path = plan_output.with_name(
            f"{plan_output.stem}.{payload['plan_id']}.authorization.json"
        )
        check_artifacts([approval_path], selected.destination)
        authorization = {
            "schema": "homeops.cleanup-authorization.v2",
            "plan_id": payload["plan_id"],
            "plan_fingerprint": payload["fingerprint"],
            "execution_confirmed": False,
            "permanent_acknowledged": False,
            "state": "awaiting-approval",
        }
        try:
            atomic_json(approval_path, authorization)
            if not typer.confirm("Execute this reviewed Pixel cleanup plan?", default=False):
                authorization["state"] = "declined"
                atomic_json(approval_path, authorization)
                typer.echo("Execution declined; no media changed.")
                return
            authorization["execution_confirmed"] = True
            phrase = f"DELETE {len(payload['files'])} PIXEL VIDEOS PERMANENTLY"
            acknowledgement = typer.prompt(
                f"Irreversible deletion has no HomeOps trash recovery. Type {phrase}"
            )
            if acknowledgement != phrase:
                authorization["state"] = "declined"
                atomic_json(approval_path, authorization)
                typer.echo("Permanent deletion was not acknowledged; no media changed.")
                return
            authorization.update(state="approved", permanent_acknowledged=True)
            atomic_json(approval_path, authorization)
        except (typer.Abort, EOFError, KeyboardInterrupt):
            authorization["state"] = "cancelled"
            atomic_json(approval_path, authorization)
            typer.echo("Approval cancelled; no media changed.")
            return
        try:
            results, result_path = execute_plan_file(
                plan_output,
                config=config_path,
                profile_name=profile,
                adb=Adb(),
                confirm=True,
                permanent=True,
                permanent_acknowledged=True,
                dry_run=False,
                expected_fingerprint=payload["fingerprint"],
            )
        except (ConfigError, CleanupError, AdbError, ValueError, OSError) as exc:
            authorization.update(state="blocked", error=str(exc))
            atomic_json(approval_path, authorization)
            raise
        typer.echo(f"Cleanup results: {results_summary(results)}; result: {result_path}")
        if any(item["status"] in {"unknown", "failed"} for item in results):
            raise typer.Exit(1)

    except (ConfigError, CleanupError, AdbError, ValueError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def results_summary(results: list[dict]) -> dict[str, int]:
    return {
        status: sum(item["status"] == status for item in results)
        for status in ("dry-run", "deleted", "already-deleted", "unknown", "failed")
    }


def _source_check(adb: Adb, device: str):
    def check(path: str, size: int, digest: str) -> bool:
        return (
            adb.find_single_authorized().serial == device
            and adb.file_size(device, path) == size
            and adb.hash_file(device, path) == digest
        )

    return check


@cleanup.command("plan")
def plan(
    report: Path | None = typer.Option(None, "--report"),
    output: Path | None = typer.Option(None, "--output"),
    config: Path | None = typer.Option(None, "--config"),
    profile: str = typer.Option("pixel", "--profile"),
) -> None:
    try:
        selected, config_path, paths = _context(config, profile)
        report = report or paths.verification_report
        output = output or paths.cleanup_plan
        check_artifacts([config_path, report, output], selected.destination)
        data = json.loads(report.read_text(encoding="utf-8"))
        payload = create_plan(data, destination_root=selected.destination, profile=selected)
        atomic_json(output, payload)
        _review(data, payload)
        typer.echo(f"Reviewed plan: {output}")
    except (ConfigError, CleanupError, AdbError, ValueError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc


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
        selected, config_path, paths = _context(config, profile)
        plan = plan or paths.cleanup_plan
        check_artifacts([config_path, plan, plan.with_suffix(".result.json")], selected.destination)
        payload = json.loads(plan.read_text(encoding="utf-8"))
        validate_plan(payload, selected)
        for prior in unresolved_journals(plan.parent):
            typer.echo(
                f"Prior unresolved deletion journal retained: {prior}; reconcile it or "
                "use a fresh verified plan and new approval."
            )
        results, result_path = execute_plan_file(
            plan,
            config=config,
            profile_name=profile,
            adb=Adb(),
            confirm=confirm,
            permanent=permanent,
            permanent_acknowledged=acknowledge_permanent,
            dry_run=dry_run,
        )
        if not results:
            typer.echo("Cleanup plan contains no eligible files; no files changed.")
        else:
            counts = results_summary(results)
            typer.echo(f"Cleanup results: {counts}; result: {result_path}")
            if counts["unknown"] or counts["failed"]:
                raise typer.Exit(1)

    except (ConfigError, CleanupError, AdbError, ValueError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc


if __name__ == "__main__":
    app()

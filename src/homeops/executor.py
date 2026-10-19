from __future__ import annotations

import fcntl
import json
from collections.abc import Callable
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path

from .adb import Adb
from .cleanup import CleanupError, fingerprint, revalidate_entry, validate_plan
from .config import Profile, load_config
from .media import _identity, contained_path
from .state import atomic_json, check_artifacts, resolve_config_path


@contextmanager
def operation_lock(result_path: Path, device: str):
    result_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = sha256(device.encode()).hexdigest()
    lock = result_path.parent / f".homeops-device-{key}.lock"
    with lock.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CleanupError(
                "another cleanup is using this device/state; no action taken"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _prior_result(path: Path, plan: dict, dry_run: bool) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        prior = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(prior, dict)
            or prior.get("schema") != "homeops.cleanup-result.v2"
            or prior.get("plan_fingerprint") != plan["fingerprint"]
            or prior.get("plan_id") != plan["plan_id"]
            or prior.get("device") != plan["device"]
            or prior.get("dry_run") is not dry_run
            or type(prior.get("complete")) is not bool
            or not isinstance(prior.get("files"), list)
        ):
            raise CleanupError("existing result belongs to another plan or mode; preserve it")
        expected = {entry["source"]: entry for entry in plan["files"]}
        results = {}
        for item in prior["files"]:
            if (
                not isinstance(item, dict)
                or item.get("source") not in expected
                or item["source"] in results
                or any(
                    item.get(key) != expected[item["source"]][key]
                    for key in ("destination", "size", "sha256")
                )
                or item.get("status")
                not in {"intent", "unknown", "failed", "deleted", "already-deleted", "dry-run"}
            ):
                raise CleanupError("existing result has invalid action evidence")
            if item["status"] in {"intent", "unknown"}:
                raise CleanupError(
                    "uncertain prior deletion intent/outcome; reconcile or create "
                    "a fresh verified plan with new approval"
                )
            results[item["source"]] = item
        if prior["complete"] and (
            len(results) != len(expected)
            or any(
                item["status"] not in ({"dry-run"} if dry_run else {"deleted", "already-deleted"})
                for item in results.values()
            )
        ):
            raise CleanupError("existing result has invalid completion state")
        return results
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, CleanupError):
            raise
        raise CleanupError(
            "invalid existing cleanup result; preserve it for reconciliation"
        ) from exc


def apply_plan(
    plan: dict,
    result_path: Path,
    *,
    profile: Profile,
    source_check: Callable[[str, int, str], bool],
    delete_source: Callable[[str], None],
    confirm: bool,
    permanent_acknowledged: bool,
    permanent: bool = False,
    dry_run: bool = True,
    source_absent: Callable[[str], bool] | None = None,
    context_check: Callable[[], Profile] | None = None,
    lock_directory: Path | None = None,
) -> list[dict]:
    """The sole deletion loop. Every caller supplies current configuration and evidence checks."""
    validate_plan(plan, profile)
    if not dry_run:
        if profile.delete_mode != "permanent":
            raise CleanupError(
                f"configured deletion policy is {profile.delete_mode}; execution blocked"
            )
        if not confirm:
            raise CleanupError("explicit execution confirmation is required")
        if not permanent or not permanent_acknowledged:
            raise CleanupError("permanent deletion requires a separate request and acknowledgement")
        if source_absent is None:
            raise CleanupError(
                "confirmed source-absence checks are required for permanent deletion"
            )
    check_artifacts([result_path], profile.destination)
    reviewed = plan["fingerprint"]

    def current() -> Profile:
        if plan.get("fingerprint") != reviewed or fingerprint(plan) != reviewed:
            raise CleanupError("reviewed plan changed; fresh review and approval are required")
        selected = context_check() if context_check else profile
        validate_plan(plan, selected)
        return selected

    lock_target = lock_directory / result_path.name if lock_directory is not None else result_path
    check_artifacts([lock_target], profile.destination)
    with operation_lock(lock_target, plan["device"]):
        prior = _prior_result(result_path, plan, dry_run)
        results: list[dict] = []
        journal = {
            "schema": "homeops.cleanup-result.v2",
            "plan_id": plan["plan_id"],
            "plan_fingerprint": reviewed,
            "device": plan["device"],
            "dry_run": dry_run,
            "complete": False,
            "files": results,
        }
        # Keep confirmed later entries durable if a resumed run stops earlier.
        results.extend(
            dict(item)
            for item in prior.values()
            if item["status"] in {"deleted", "already-deleted"}
        )
        for raw in plan["files"]:
            source = raw["source"]
            previous = prior.get(source)
            result = dict(raw)
            request_issued = False
            appended = False
            try:
                selected = current()
                if previous and previous["status"] in {"deleted", "already-deleted"}:
                    if source_absent is None or not source_absent(source):
                        raise CleanupError(
                            "previously deleted source reappeared or absence is "
                            "unproven; verify a fresh plan"
                        )
                    next(item for item in results if item["source"] == source)["status"] = (
                        "already-deleted"
                    )
                    continue
                entry = revalidate_entry(
                    raw,
                    root=selected.destination.resolve(),
                    source_roots=selected.roots,
                    source_check=source_check,
                )
                if dry_run:
                    result["status"] = "dry-run"
                    results.append(result)
                    appended = True
                    atomic_json(result_path, journal)
                    continue
                result["status"] = "intent"
                results.append(result)
                appended = True
                atomic_json(result_path, journal)  # Must succeed before any delete request.
                selected = current()
                local = contained_path(Path(entry.destination), selected.destination.resolve())
                if _identity(local.lstat()) != entry.local_identity:
                    raise CleanupError("local copy changed at the deletion boundary")
                request_issued = True
                delete_source(source)
                if not source_absent(source):
                    raise CleanupError("source absence could not be confirmed after deletion")
                result["status"] = "deleted"
                atomic_json(result_path, journal)
            except BaseException as exc:
                if previous and previous["status"] in {"deleted", "already-deleted"}:
                    journal["error"] = str(exc)
                else:
                    result["status"] = "unknown" if request_issued else "failed"
                    result["error"] = str(exc) or type(exc).__name__
                    if not appended:
                        results.append(result)
                try:
                    atomic_json(result_path, journal)
                except OSError as write_error:
                    raise CleanupError(
                        "cleanup state persistence failed; stop and inspect the "
                        "retained journal before retrying"
                    ) from write_error
                if not isinstance(exc, Exception):
                    raise
                if request_issued:
                    return results
                raise CleanupError(str(exc)) from exc
        journal["complete"] = True
        atomic_json(result_path, journal)
        return results


def result_path_for(plan_path: Path, plan: dict, *, dry_run: bool) -> Path:
    suffix = ".dry-run.json" if dry_run else ".result.json"
    return plan_path.with_name(f"{plan_path.stem}.{plan['plan_id']}{suffix}")


def unresolved_journals(directory: Path) -> list[Path]:
    results = []
    for path in sorted(directory.glob("*.result.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if (
                isinstance(payload, dict)
                and payload.get("schema") == "homeops.cleanup-result.v2"
                and any(
                    isinstance(entry, dict) and entry.get("status") in {"intent", "unknown"}
                    for entry in payload.get("files", [])
                )
            ):
                results.append(path)
        except (OSError, ValueError, TypeError):
            results.append(path)
    return results


def execute_plan_file(
    plan_path: Path,
    *,
    config: Path | None,
    profile_name: str,
    adb: Adb,
    confirm: bool = False,
    permanent: bool = False,
    permanent_acknowledged: bool = False,
    dry_run: bool = True,
    expected_fingerprint: str | None = None,
) -> tuple[list[dict], Path]:
    """Shared CLI execution service for advanced flags and guided approvals."""
    config_path = resolve_config_path(config).expanduser().resolve()
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    reviewed = expected_fingerprint or (
        payload.get("fingerprint") if isinstance(payload, dict) else None
    )

    def current() -> Profile:
        settings, _ = load_config(config_path, profile=profile_name)
        if settings.verification != "sha256" or profile_name not in settings.profiles:
            raise CleanupError("current verification/profile configuration is invalid")
        selected = settings.profiles[profile_name]
        saved = json.loads(plan_path.read_text(encoding="utf-8"))
        if (
            not isinstance(saved, dict)
            or saved.get("fingerprint") != reviewed
            or fingerprint(saved) != reviewed
        ):
            raise CleanupError(
                "persisted plan changed after review; create and review a fresh plan"
            )
        validate_plan(payload, selected)
        return selected

    selected = current()
    destination = result_path_for(plan_path, payload, dry_run=dry_run)
    check_artifacts([config_path, plan_path, destination], selected.destination)
    metadata: dict[str, str] = {}

    def device() -> str:
        serial = adb.find_single_authorized().serial
        if serial != payload["device"]:
            raise CleanupError("cleanup plan belongs to a different Android device")
        return serial

    def source_check(path: str, size: int, digest: str) -> bool:
        observed_size, observed_digest, observed_metadata = adb.evidence(device(), path)
        metadata[path] = observed_metadata
        return observed_size == size and observed_digest == digest

    def delete(path: str) -> None:
        current()
        adb.delete_file(device(), path, expected_metadata=metadata[path])

    device()
    results = apply_plan(
        payload,
        destination,
        profile=selected,
        source_check=source_check,
        delete_source=delete,
        source_absent=lambda path: adb.is_absent(device(), path),
        context_check=current,
        lock_directory=config_path.parent,
        confirm=confirm,
        permanent=permanent,
        permanent_acknowledged=permanent_acknowledged,
        dry_run=dry_run,
    )
    return results, destination

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from .adb import Adb, AdbError, validate_roots
from .config import Profile
from .media import (
    DIGEST,
    VIDEO_SUFFIXES,
    MediaFile,
    _identity,
    contained_path,
    hash_file,
    resolve_scope,
)
from .reports import validate_verification


class CleanupError(ValueError):
    """Raised when a cleanup safety precondition fails."""


@dataclass(frozen=True)
class PlanEntry:
    source: str
    destination: str
    size: int
    sha256: str
    local_identity: tuple[int, ...] | None = None


def fingerprint(payload: dict) -> str:
    content = {key: value for key, value in payload.items() if key != "fingerprint"}
    return sha256(
        json.dumps(
            content, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _authorized_source(path: str, roots: tuple[str, ...]) -> bool:
    try:
        Adb._validate_media_path(path)
        validate_roots(roots)
    except (ValueError, AdbError):
        return False
    return any(path.startswith("/sdcard/" + root + "/") for root in roots)


def _bindings(payload: dict, profile: Profile) -> tuple[Path, Path, bool]:
    try:
        if (
            not isinstance(payload.get("scope"), dict)
            or type(payload["scope"].get("recursive")) is not bool
        ):
            raise CleanupError("invalid scope/depth binding")
        root, scope, relative = resolve_scope(profile.destination, payload["scope"]["within"])
        validate_roots(profile.roots)
        if (
            payload["destination_root"] != str(root)
            or payload["profile"] != profile.name
            or payload["delete_mode"] != profile.delete_mode
            or payload["source_roots"] != list(profile.roots)
            or payload["scope"]
            != {"within": relative, "path": str(scope), "recursive": profile.recursive}
        ):
            raise CleanupError("plan/report belongs to a different current profile, root, or scope")
        if (
            not isinstance(payload["device"], str)
            or not payload["device"]
            or payload["device"] == "[redacted]"
        ):
            raise CleanupError("no exact Android device identity")
        return root, scope, profile.recursive
    except (KeyError, TypeError, ValueError, OSError) as exc:
        if isinstance(exc, CleanupError):
            raise
        raise CleanupError("invalid profile, scope, or device binding") from exc


def _entry(
    raw: dict, root: Path, scope: Path, recursive: bool, roots: tuple[str, ...]
) -> PlanEntry:
    try:
        if (
            not isinstance(raw, dict)
            or type(raw.get("size")) is not int
            or raw["size"] <= 0
            or not isinstance(raw.get("sha256"), str)
            or not DIGEST.fullmatch(raw["sha256"])
            or not isinstance(raw.get("source"), str)
            or not _authorized_source(raw["source"], roots)
            or not isinstance(raw.get("destination"), str)
        ):
            raise CleanupError("invalid cleanup entry evidence")
        local = contained_path(Path(raw["destination"]), root)
        if (
            not local.is_relative_to(scope)
            or not recursive
            and local.parent != scope
            or local.suffix.lower() not in VIDEO_SUFFIXES
            or not local.is_file()
        ):
            raise CleanupError(
                "local evidence is outside reviewed scope or is not a supported video"
            )
        return PlanEntry(raw["source"], str(local), raw["size"], raw["sha256"])
    except (ValueError, OSError) as exc:
        if isinstance(exc, CleanupError):
            raise
        raise CleanupError("invalid or escaped local evidence") from exc


def validate_plan(plan: dict, profile: Profile) -> list[PlanEntry]:
    if (
        not isinstance(plan, dict)
        or plan.get("schema") != "homeops.cleanup-plan.v2"
        or plan.get("verification_schema") != "homeops.verification.v2"
    ):
        raise CleanupError("unsupported cleanup plan; rerun verification and planning")
    try:
        for name in ("plan_id", "verification_run_id", "created_at"):
            if not isinstance(plan.get(name), str) or not plan[name]:
                raise CleanupError("cleanup plan is missing run identity")
        if plan.get("fingerprint") != fingerprint(plan):
            raise CleanupError("cleanup plan fingerprint changed; review a fresh plan")
        root, scope, recursive = _bindings(plan, profile)
        if not isinstance(plan.get("files"), list):
            raise CleanupError("invalid cleanup file list")
        entries = [_entry(raw, root, scope, recursive, profile.roots) for raw in plan["files"]]
        if (
            len({entry.source for entry in entries}) != len(entries)
            or len({entry.destination for entry in entries}) != len(entries)
            or len({(entry.size, entry.sha256) for entry in entries}) != len(entries)
        ):
            raise CleanupError("duplicate source, destination, or content actions in cleanup plan")
        if (
            type(plan.get("eligible_bytes")) is not int
            or plan["eligible_bytes"] != sum(entry.size for entry in entries)
            or type(plan.get("coverage_complete")) is not bool
            or entries
            and not plan["coverage_complete"]
        ):
            raise CleanupError("invalid cleanup eligibility totals or coverage")
        return entries
    except (TypeError, ValueError, KeyError) as exc:
        if isinstance(exc, CleanupError):
            raise
        raise CleanupError("invalid cleanup plan") from exc


def create_plan(report: dict, *, destination_root: Path, profile: Profile | None = None) -> dict:
    try:
        validate_verification(report)
        if profile is None:
            raise CleanupError("current profile is required for cleanup planning")
        root, scope, recursive = _bindings(report, profile)
        if destination_root.resolve() != root:
            raise CleanupError("destination does not match current profile")
        roots = validate_roots(report["source_roots"])
        if report["files"] and {item.get("root") for item in report["coverage"]} != set(roots):
            raise CleanupError("verification root coverage is missing")
        if len({item["root"] for item in report["coverage"]}) != len(report["coverage"]):
            raise CleanupError("verification repeats Android root coverage")
        coverage_ok = (
            report["coverage_complete"]
            and not report["inventory_errors"]
            and all(
                item.get("status") in {"complete", "absent-optional"} for item in report["coverage"]
            )
        )
        local_counts = Counter(
            (item.get("local", {}).get("size"), item.get("local", {}).get("sha256"))
            for item in report["files"]
        )
        entries, exclusions = [], []
        seen_local, seen_remote = set(), set()
        for item in report["files"]:
            local = item.get("local", {})
            if not isinstance(local, dict) or not isinstance(item.get("sources"), list):
                raise CleanupError("invalid local verification evidence")
            if local.get("path") in seen_local:
                raise CleanupError("verification repeats a local path")
            seen_local.add(local.get("path"))
            if not item.get("eligible") or item.get("status") != "verified" or not coverage_ok:
                continue
            sources = item["sources"]
            if (
                len(sources) != 1
                or item.get("local_count") != 1
                or item.get("remote_count") != 1
                or local_counts[(local.get("size"), local.get("sha256"))] != 1
            ):
                raise CleanupError("verification does not prove one-to-one content mapping")
            source = sources[0]
            if (
                not isinstance(source, dict)
                or source.get("accessible") is not True
                or local.get("accessible") is not True
                or source.get("sha256") != local.get("sha256")
                or source.get("size") != local.get("size")
            ):
                raise CleanupError("source and local evidence disagree")
            complete_roots = tuple(
                item["root"] for item in report["coverage"] if item["status"] == "complete"
            )
            if not _authorized_source(source.get("path"), complete_roots):
                raise CleanupError("source evidence contradicts Android coverage")
            raw = {
                "source": source.get("path"),
                "destination": local.get("path"),
                "size": local.get("size"),
                "sha256": local.get("sha256"),
            }
            entry = _entry(raw, root, scope, recursive, roots)
            if entry.source in seen_remote:
                raise CleanupError("verification repeats a remote original")
            seen_remote.add(entry.source)
            current = hash_file(
                MediaFile(entry.destination, Path(entry.destination).name, entry.size, True)
            )
            if not current.accessible or current.sha256 != entry.sha256:
                exclusions.append({"path": entry.destination, "reason": "local copy changed"})
                continue
            entries.append(raw)
        plan = {
            "schema": "homeops.cleanup-plan.v2",
            "verification_schema": report["schema"],
            "plan_id": uuid4().hex,
            "verification_run_id": report["run_id"],
            "created_at": datetime.now(UTC).isoformat(),
            **{
                key: report[key]
                for key in (
                    "device",
                    "profile",
                    "delete_mode",
                    "destination_root",
                    "source_roots",
                    "scope",
                )
            },
            "coverage_complete": bool(coverage_ok),
            "files": entries,
            "exclusions": exclusions,
            "blocked_reason": None if coverage_ok else "incomplete inventory coverage",
            "eligible_bytes": sum(entry["size"] for entry in entries),
        }
        plan["fingerprint"] = fingerprint(plan)
        validate_plan(plan, profile)
        return plan
    except (ValueError, TypeError, KeyError, OSError) as exc:
        if isinstance(exc, CleanupError):
            raise
        raise CleanupError(f"invalid verification evidence; rerun backup verify: {exc}") from exc


def _hash(path: Path) -> str:
    item = hash_file(MediaFile(str(path), path.name, path.stat().st_size, True))
    if not item.accessible or not item.sha256:
        raise CleanupError(f"local file failed stable hashing: {path}")
    return item.sha256


def revalidate(
    plan: dict[str, object],
    *,
    source_check: Callable[[str, int, str], bool],
    profile: Profile | None = None,
) -> list[PlanEntry]:
    if profile is None:
        raise CleanupError("current profile is required for revalidation")
    validate_plan(plan, profile)
    root = profile.destination.resolve()
    return [
        revalidate_entry(raw, root=root, source_roots=profile.roots, source_check=source_check)
        for raw in plan.get("files", [])
    ]


def revalidate_entry(
    raw: object,
    *,
    root: Path,
    source_roots: tuple[str, ...],
    source_check: Callable[[str, int, str], bool],
) -> PlanEntry:
    if not isinstance(raw, dict):
        raise CleanupError("cleanup plan contains an invalid entry")
    destination = Path(str(raw.get("destination", "")))
    try:
        contained_path(destination, root)
        identity = _identity(destination.lstat())
        size = int(raw["size"])
        expected = str(raw["sha256"])
        if destination.stat().st_size != size or _hash(destination) != expected:
            raise CleanupError(f"destination changed: {destination}")
        source = str(raw["source"])
        if not _authorized_source(source, source_roots):
            raise CleanupError(f"source path is outside configured roots: {source}")
        if not source_check(source, size, expected):
            raise CleanupError(f"source failed revalidation: {source}")
        contained_path(destination, root)
        if _identity(destination.lstat()) != identity:
            raise CleanupError("local copy changed while checking the source")
    except CleanupError:
        raise
    except (KeyError, OSError, ValueError) as exc:
        raise CleanupError(f"invalid cleanup entry: {destination}") from exc
    return PlanEntry(source, str(destination), size, expected, identity)

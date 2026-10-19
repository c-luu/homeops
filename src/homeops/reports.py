from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from .state import atomic_json, atomic_text


def validate_verification(report: dict) -> None:
    if (
        not isinstance(report, dict)
        or report.get("schema") != "homeops.verification.v2"
        or report.get("complete") is not True
    ):
        raise ValueError("unsupported or incomplete verification; rerun backup verify")
    for name in ("run_id", "created_at", "device", "profile", "destination_root", "delete_mode"):
        if (
            not isinstance(report.get(name), str)
            or not report[name]
            or report[name] == "[redacted]"
        ):
            raise ValueError(f"verification has no valid {name}; rerun backup verify")
    for name in ("files", "coverage", "skipped", "inventory_errors", "source_roots"):
        if not isinstance(report.get(name), list):
            raise TypeError(f"invalid verification {name}; rerun backup verify")
    if type(report.get("coverage_complete")) is not bool or not isinstance(
        report.get("scope"), dict
    ):
        raise ValueError("verification is missing coverage or scope evidence")
    if (
        type(report.get("scanned_mac_files")) is not int
        or report["scanned_mac_files"] < 0
        or any(not isinstance(entry, dict) for entry in report["files"])
    ):
        raise ValueError("invalid verification file evidence")
    scope = report["scope"]
    if (
        not isinstance(scope.get("within"), str)
        or not isinstance(scope.get("path"), str)
        or type(scope.get("recursive")) is not bool
    ):
        raise ValueError("invalid verification scope/depth")
    for coverage in report["coverage"]:
        if (
            not isinstance(coverage, dict)
            or not isinstance(coverage.get("root"), str)
            or coverage.get("status") not in {"complete", "absent-optional", "error-incomplete"}
        ):
            raise ValueError("invalid verification root coverage")
    for entry in report["files"]:
        if (
            not isinstance(entry.get("local"), dict)
            or not isinstance(entry.get("sources"), list)
            or entry.get("status") not in {"verified", "unmatched", "ambiguous", "error"}
            or type(entry.get("eligible")) is not bool
            or type(entry.get("local_count")) is not int
            or entry["local_count"] < 0
            or type(entry.get("remote_count")) is not int
            or entry["remote_count"] != len(entry["sources"])
        ):
            raise ValueError("invalid verification outcome or cardinality")
        for item in [entry["local"], *entry["sources"]]:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("path"), str)
                or type(item.get("accessible")) is not bool
                or item.get("size") is not None
                and (type(item["size"]) is not int or item["size"] < 0)
                or item.get("sha256") is not None
                and not isinstance(item["sha256"], str)
            ):
                raise ValueError("invalid file evidence")
    if any(
        not isinstance(item, dict) for item in [*report["skipped"], *report["inventory_errors"]]
    ):
        raise ValueError("invalid skipped/error evidence")


def summary_lines(report: dict, plan: dict | None = None) -> list[str]:
    scope = report["scope"]
    label = (
        "entire configured LocalSend destination (includes older arrivals)"
        if scope["within"] == "."
        else f"subdirectory {scope['within']!r}"
    )
    depth = "recursive" if scope["recursive"] else "top-level only"
    counts = Counter(entry["status"] for entry in report["files"])
    eligible = len(plan["files"]) if plan else sum(bool(e["eligible"]) for e in report["files"])
    total = plan["eligible_bytes"] if plan else 0
    absent = sum(item.get("status") == "absent-optional" for item in report["coverage"])
    return [
        (
            f"Mac scope: {label}; {depth}\nConfigured destination: {report['destination_root']}"
            f"\nEffective scope: {scope['path']}"
        ),
        (
            f"Scanned Mac files: {report['scanned_mac_files']}; supported videos: "
            f"{len(report['files'])}; skipped entries: {len(report['skipped'])}"
        ),
        (
            f"Verified matches: {counts['verified']}; unmatched Mac videos: {counts['unmatched']}; "
            f"ambiguous Mac videos: {counts['ambiguous']}"
        ),
        (
            f"Errors: {counts['error']} video files; {len(report['inventory_errors'])} inventory/root "
            f"errors; absent optional Android roots: {absent}"
        ),
        (
            f"Deletion-eligible Pixel originals: {eligible}; estimated eligible savings: "
            f"{total / (1024**3):.2f} GiB ({total} bytes)"
        ),
    ]


def human_report(report: dict, plan: dict | None = None) -> str:
    lines = [
        "HomeOps verification report",
        *summary_lines(report, plan),
        f"Run: {report['run_id']}; complete: {report['complete']}",
        f"Coverage complete: {report['coverage_complete']}",
    ]
    for item in report["files"]:
        lines.append(f"{item['status']}: {item['local']['path']}; {item.get('reason') or ''}")
        lines.append(
            f"  Mac evidence: size={item['local']['size']}; SHA-256={item['local'].get('sha256')}"
        )
        for source in item["sources"]:
            lines.append(
                f"  Pixel: {source['path']}; size={source['size']}; SHA-256={source.get('sha256')}"
            )
        for source in item.get("source_errors", []):
            lines.append(f"  Pixel error: {source['path']}; {source.get('error')}")
    for item in report["skipped"]:
        lines.append(f"Skipped: {item['path']}; {item['reason']}")
    for item in report["coverage"]:
        lines.append(f"Android root: {item}")
    for item in report["inventory_errors"]:
        lines.append(f"Inventory error: {item}")
    return "\n".join(lines) + "\n"


def save_verification(report: dict, output: Path, plan: dict | None = None) -> None:
    validate_verification(report)
    atomic_json(output, report)
    atomic_text(output.with_suffix(".txt"), human_report(report, plan))


def write_report(
    results: Iterable[dict],
    output: Path,
    *,
    device: str | None = None,
    destination_root: Path | None = None,
    source_roots: Iterable[str] = (),
) -> None:
    """Legacy diagnostic writer. Its incomplete evidence never authorizes deletion."""
    atomic_json(
        output,
        {
            "schema": "homeops.verification.v1",
            "complete": False,
            "created_at": datetime.now(UTC).isoformat(),
            "device": device or "[redacted]",
            "destination_root": str(destination_root) if destination_root else None,
            "source_roots": list(source_roots),
            "files": list(results),
        },
    )


def write_human_report(results: Iterable[dict], output: Path) -> None:
    atomic_text(output, "HomeOps verification report\n" + "\n".join(str(r) for r in results) + "\n")


def write_checkpoint(results: Iterable[dict], output: Path, *, complete: bool = False) -> None:
    atomic_json(
        output,
        {
            "schema": "homeops.verification.checkpoint.v1",
            "complete": complete,
            "files": list(results),
        },
    )
